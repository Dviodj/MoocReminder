"""The in-place update: find the package, verify it, swap the files, undo on failure.

Nothing here touches the network for real: every request goes through an opener
the test supplies, and every "package" is a ZIP this file builds. The switch is
exercised against a folder shaped exactly like an install, so a mistake in the
order of renames shows up here instead of on a user's machine.
"""
from pathlib import Path
import io
import json
import shutil
import tempfile
import unittest
import zipfile

import _bootstrap
import self_update


PAGE = '''<div class="Box-body">
<a href="/Dviodj/MoocReminder/releases/download/v0.2.3/MoocReminder-v0.2.3-offline-x64.zip">offline</a>
<a href="/Dviodj/MoocReminder/releases/download/v0.2.3/MoocReminder-v0.2.3-source.zip">source</a>
<a href="https://github.com/Dviodj/MoocReminder/releases/download/v0.2.2/MoocReminder-v0.2.2-offline-x64.zip">old</a>
<a href="/Dviodj/MoocReminder/releases/download/v0.2.3/MoocReminder-v0.2.3-offline-x64.zip.sha256">sum</a>
</div>'''

#: Test packages are a couple of kilobytes, not the 250 MB a real one weighs.
SMALL = 1


def make_package(folder, version='v0.2.3', extra=None, root=None, corrupt=False):
    """Build a ZIP shaped like a published offline package."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'MoocReminder-{version}-offline-x64.zip'
    name = root or f'MoocReminder-{version}'
    files = {
        'src/version.py': f"VERSION = '{version}'\n",
        'src/ui/qt_desktop.py': '# window\n',
        'src/system/self_update.py': '# update\n',
        'assets/MoocReminder.ico': b'icon',
        'runtime-bundle/python/python.exe': b'binary',
        'README.md': '# readme\n',
        'requirements-runtime.txt': 'PySide6==6.0\n',
    }
    files.update(extra or {})
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(f'{name}/{path}', content)
    if corrupt:
        data = bytearray(target.read_bytes())
        data[len(data) // 2] ^= 0xFF
        target.write_bytes(bytes(data))
    return target


class Opener:
    """Stands in for ``urlopen``: serves canned bytes and records the URLs."""

    def __init__(self, body=b'', length=None, fail=None):
        self.body = body if isinstance(body, bytes) else body.encode('utf-8')
        self.length = len(self.body) if length is None else length
        self.fail = fail
        self.urls = []

    def __call__(self, request, timeout=None):
        url = getattr(request, 'full_url', request)
        self.urls.append(url)
        if self.fail is not None:
            raise self.fail

        class Answer(io.BytesIO):
            headers = {'Content-Length': str(self.length)}

        return Answer(self.body)


class AssetTests(unittest.TestCase):
    def test_the_name_is_built_from_the_version(self):
        self.assertEqual('MoocReminder-v0.2.3-offline-x64.zip', self_update.asset_name('v0.2.3'))
        self.assertEqual('MoocReminder-v0.2.3-offline-x64.zip', self_update.asset_name('0.2.3'))

    def test_a_missing_version_is_refused(self):
        self.assertRaises(self_update.UpdateError, self_update.asset_name, '')

    def test_the_page_yields_every_offline_package_and_nothing_else(self):
        found = self_update.offline_assets(PAGE)
        self.assertEqual(['MoocReminder-v0.2.3-offline-x64.zip', 'MoocReminder-v0.2.2-offline-x64.zip'],
                         [item['name'] for item in found])
        self.assertEqual(['v0.2.3', 'v0.2.2'], [item['tag'] for item in found])
        self.assertTrue(all(item['url'].startswith('https://github.com/') for item in found))

    def test_the_source_package_is_never_an_update(self):
        names = [item['name'] for item in self_update.offline_assets(PAGE)]
        self.assertNotIn('MoocReminder-v0.2.3-source.zip', names)
        self.assertNotIn('MoocReminder-v0.2.3-offline-x64.zip.sha256', names)

    def test_the_asset_must_match_the_announced_version(self):
        self.assertEqual('v0.2.3', self_update.asset_for(PAGE, 'v0.2.3')['tag'])
        self.assertIsNone(self_update.asset_for(PAGE, 'v0.9.9'))

    def test_garbage_in_yields_nothing(self):
        self.assertEqual([], self_update.offline_assets(None))
        self.assertEqual([], self_update.offline_assets('<html>nothing here</html>'))
        self.assertIsNone(self_update.asset_for(b'<html></html>', 'v0.2.3'))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = _bootstrap.scratch_folder('self-update-download')

    def test_the_bytes_land_under_the_asset_name(self):
        asset = dict(name='pkg.zip', url='https://example.com/pkg.zip')
        path, digest = self_update.download(asset, self.temp, opener=Opener(b'hello world'))
        self.assertEqual(b'hello world', path.read_bytes())
        self.assertEqual(64, len(digest))
        self.assertFalse((self.temp / 'pkg.zip.part').exists())

    def test_progress_is_reported_as_bytes_then_the_whole(self):
        seen = []
        asset = dict(name='pkg.zip', url='https://example.com/pkg')
        self_update.download(asset, self.temp, progress=lambda done, total: seen.append((done, total)),
                             opener=Opener(b'x' * 600000))
        self.assertEqual(600000, seen[-1][0])
        self.assertEqual(600000, seen[-1][1])

    def test_a_short_body_is_an_error_and_leaves_no_file(self):
        asset = dict(name='pkg.zip', url='https://example.com/pkg')
        with self.assertRaises(self_update.UpdateError):
            self_update.download(asset, self.temp, opener=Opener(b'x' * 10, length=999))
        self.assertEqual([], list(self.temp.iterdir()))

    def test_a_network_error_is_reported_in_words(self):
        asset = dict(name='pkg.zip', url='https://example.com/pkg')
        with self.assertRaises(self_update.UpdateError) as caught:
            self_update.download(asset, self.temp, opener=Opener(fail=OSError('no route')))
        self.assertIn('no route', str(caught.exception))
        self.assertEqual([], list(self.temp.iterdir()))


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = _bootstrap.scratch_folder('self-update-verify')

    def test_a_real_package_is_accepted_and_its_version_read(self):
        archive = make_package(self.temp / 'pkg')
        self.assertEqual('v0.2.3', self_update.unpacked_version(archive, SMALL))

    def test_a_damaged_file_is_refused(self):
        archive = make_package(self.temp / 'pkg', corrupt=True)
        with self.assertRaises(self_update.UpdateError):
            self_update.verify(archive)

    def test_an_error_page_saved_under_the_right_name_is_refused(self):
        fake = self.temp / 'fake.zip'
        fake.write_bytes(b'<html>404</html>')
        with self.assertRaises(self_update.UpdateError):
            self_update.verify(fake)

    def test_a_zip_without_the_program_is_refused(self):
        other = self.temp / 'other.zip'
        with zipfile.ZipFile(other, 'w') as archive:
            archive.writestr('MoocReminder-v0.2.3/notes.txt', 'hi')
        with self.assertRaises(self_update.UpdateError):
            self_update.verify(other)

    def test_a_zip_with_two_roots_is_refused(self):
        mixed = self.temp / 'mixed.zip'
        with zipfile.ZipFile(mixed, 'w') as archive:
            archive.writestr('a/src/ui/qt_desktop.py', 'x')
            archive.writestr('b/src/version.py', "VERSION = 'v1.0.0'")
        with self.assertRaises(self_update.UpdateError):
            self_update.verify(mixed)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = _bootstrap.scratch_folder('self-update-plan')
        self.workspace = self.temp / 'program'
        (self.workspace / 'data').mkdir(parents=True)
        (self.workspace / 'email_sender.json').write_text('{}', encoding='utf-8')
        (self.workspace / 'README.md').write_text('old', encoding='utf-8')

    def test_wholesale_folders_are_swapped_and_the_rest_copied(self):
        staged = self.temp / 'stage'
        for name in ('src', 'assets', 'runtime-bundle', 'README.md'):
            (staged / name).mkdir(parents=True) if name != 'README.md' else None
            if name == 'README.md':
                (staged / name).write_text('new', encoding='utf-8')
        made = self_update.plan(self.workspace, staged)
        self.assertEqual(['src', 'assets', 'runtime-bundle'], made['swap'])
        self.assertEqual(['README.md'], [item['name'] for item in made['copy']])

    def test_the_users_files_are_never_shipped_over(self):
        staged = self.temp / 'stage'
        for name in ('src', 'data', 'email_sender.json', '.venv'):
            (staged / name).mkdir(parents=True)
        made = self_update.plan(self.workspace, staged)
        names = made['swap'] + [item['name'] for item in made['copy']]
        for protected in ('data', 'email_sender.json', '.venv'):
            self.assertNotIn(protected, names)

    def test_a_file_that_already_exists_is_remembered_as_such(self):
        staged = self.temp / 'stage'
        (staged / 'README.md').parent.mkdir(parents=True, exist_ok=True)
        (staged / 'README.md').write_text('new', encoding='utf-8')
        (staged / 'NEWS.md').write_text('new', encoding='utf-8')
        item = {entry['name']: entry['preexisting'] for entry in
                self_update.plan(self.workspace, staged)['copy']}
        self.assertTrue(item['README.md'])
        self.assertFalse(item['NEWS.md'])

    def test_the_folder_the_program_is_started_from_is_never_replaced(self):
        # A source checkout runs on .build/runtime-bundle/python, which the
        # shortcut names: swapping anything there leaves it holding the old code.
        staged = self.temp / 'stage'
        for name in ('src', '.build', 'runtime-bundle'):
            (staged / name).mkdir(parents=True)
        names = self_update.plan(self.workspace, staged)['swap']
        self.assertNotIn('.build', names)
        self.assertIn('src', names)


class SwitchTests(unittest.TestCase):
    """The switch itself: what is replaced, what survives, and what undo does."""

    def setUp(self):
        self.temp = _bootstrap.scratch_folder('self-update-switch')
        self.workspace = self.temp / 'program'
        for name in ('src', 'assets', 'runtime-bundle', 'data', '.launcher-state'):
            (self.workspace / name).mkdir(parents=True)
        (self.workspace / 'src' / 'version.py').write_text("VERSION = 'v0.2.2'", encoding='utf-8')
        (self.workspace / 'data' / 'app.json').write_text('{"active": "11"}', encoding='utf-8')
        (self.workspace / '.launcher-state' / 'runtime.json').write_text('{}', encoding='utf-8')
        (self.workspace / 'README.md').write_text('old readme', encoding='utf-8')
        (self.workspace / 'email_sender.json').write_text('{"sender": "x"}', encoding='utf-8')
        self.archive = make_package(self.temp / 'pkg', version='v0.2.3',
                                    extra={'src/version.py': "VERSION = 'v0.2.3'\n"})

    def test_the_new_files_replace_the_old_ones(self):
        made = self_update.prepare(self.workspace, self.archive, minimum=SMALL)
        ok, error, _ = self_update.apply(made)
        self.assertTrue(ok, error)
        self.assertIn('v0.2.3', (self.workspace / 'src' / 'version.py').read_text(encoding='utf-8'))
        self.assertTrue((self.workspace / 'runtime-bundle' / 'python' / 'python.exe').is_file())

    def test_what_belongs_to_the_user_survives(self):
        made = self_update.prepare(self.workspace, self.archive, minimum=SMALL)
        self_update.apply(made)
        self.assertTrue((self.workspace / 'data' / 'app.json').is_file())
        self.assertTrue((self.workspace / '.launcher-state' / 'runtime.json').is_file())
        self.assertEqual('{"sender": "x"}', (self.workspace / 'email_sender.json').read_text(encoding='utf-8'))

    def test_a_file_the_release_does_not_ship_is_kept(self):
        extra = self.workspace / 'my-notes.txt'
        extra.write_text('mine', encoding='utf-8')
        made = self_update.prepare(self.workspace, self.archive, minimum=SMALL)
        self_update.apply(made)
        self.assertEqual('mine', extra.read_text(encoding='utf-8'))

    def test_the_old_folders_are_cleaned_up_after_a_good_switch(self):
        made = self_update.prepare(self.workspace, self.archive, minimum=SMALL)
        self_update.apply(made)
        leftovers = [path.name for path in self.workspace.iterdir()
                     if path.name.endswith('-old') or '-old-' in path.name]
        self.assertEqual([], leftovers)

    def test_a_failure_part_way_puts_everything_back(self):
        made = self_update.prepare(self.workspace, self.archive, minimum=SMALL)
        # The prepared folder vanishes between staging and switching -- the same
        # effect as something holding or deleting it. Half a switch may not stay.
        shutil.rmtree(Path(made['ready']) / 'runtime-bundle')
        ok, error, _ = self_update.apply(made)
        self.assertFalse(ok)
        self.assertTrue(error)
        self.assertIn("VERSION = 'v0.2.2'", (self.workspace / 'src' / 'version.py').read_text(encoding='utf-8'))
        self.assertTrue((self.workspace / 'assets').is_dir())
        self.assertTrue((self.workspace / 'data' / 'app.json').is_file())

    def test_a_folder_where_a_file_belongs_is_replaced_not_buried(self):
        # ``copy2`` onto a folder of the same name does not raise; it copies into
        # it. That would leave the new README one level too deep and call it a
        # success.
        (self.workspace / 'README.md').unlink()
        (self.workspace / 'README.md').mkdir()
        made = self_update.prepare(self.workspace, self.archive, minimum=SMALL)
        ok, error, _ = self_update.apply(made)
        self.assertTrue(ok, error)
        self.assertTrue((self.workspace / 'README.md').is_file())

    def test_a_missing_staged_file_is_reported_rather_than_crashing(self):
        made = self_update.prepare(self.workspace, self.archive, minimum=SMALL)
        shutil.rmtree(Path(made['ready']) / 'assets')
        ok, error, _ = self_update.apply(made)
        self.assertFalse(ok)
        self.assertTrue(error)

    def test_prepare_unpacks_in_a_folder_of_its_own(self):
        made = self_update.prepare(self.workspace, self.archive, minimum=SMALL)
        self.assertTrue((Path(made['workspace']) / '.update' / 'plan.json').is_file())
        self.assertTrue((Path(made['ready']) / 'src' / 'version.py').is_file())

    def test_clean_removes_the_working_folder_and_old_copies(self):
        made = self_update.prepare(self.workspace, self.archive, minimum=SMALL)
        self_update.apply(made)
        stale = self.workspace / '.src-old-20240101-000000-abcdef'
        stale.mkdir()
        self_update.clean(self.workspace)
        self.assertFalse((self.workspace / '.update').exists())
        self.assertFalse(stale.exists())


class CommandTests(unittest.TestCase):
    def test_the_script_carries_the_paths_verbatim(self):
        workspace = Path('C:/Program Files/MoocReminder')
        command = self_update.script_command(workspace, workspace / 'plan.json', workspace / 'report.json')
        text = command[-1]
        self.assertIn(repr(str(workspace / 'src' / 'system')), text)
        self.assertIn('apply(plan)', text)

    def test_the_restart_goes_through_the_launcher(self):
        command = self_update.restart_command(Path('C:/x'))
        self.assertIn(str(Path('C:/x') / 'src/system/source_launcher.py'), command)
        self.assertIn('--background', command)

    def test_the_switch_script_can_still_switch_if_the_new_module_will_not_load(self):
        # The script runs the *new* program's module; if that one cannot be
        # imported, the old one has to finish the job rather than leave a half
        # moved program behind.
        command = self_update.script_command(Path('C:/x'), Path('C:/x/plan.json'), Path('C:/x/report.json'))
        text = command[-1]
        self.assertIn('except Exception:', text)
        self.assertIn(repr(str(Path(self_update.__file__).resolve().parent)), text)

    def test_a_source_checkout_without_a_bundle_can_still_download(self):
        temp = _bootstrap.scratch_folder('self-update-support')
        self.assertFalse(self_update.supports_in_place(temp))


if __name__ == '__main__':
    unittest.main()

