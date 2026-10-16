import _bootstrap  # noqa: F401  (puts src/ on sys.path)
from _bootstrap import launcher_entry, stub_launcher

import subprocess
import tempfile
import unittest
import os
from pathlib import Path

import autostart
from desktop_shortcut import read_link

# The real Startup folder is outside the writable sandbox in some environments,
# so every test below builds the entry in a temporary directory and passes it as
# `folder`.

def make_workspace(root, name="O'Brien app"):
    workspace = Path(root)/name
    (workspace/'assets').mkdir(parents=True)
    stub_launcher(workspace)
    (workspace/'assets'/'MoocReminder.ico').write_bytes(b'\x00\x00\x01\x00\x00\x00')
    return workspace


def make_startup(root):
    """A stand-in for the real Startup folder, which always already exists."""
    folder = Path(root)/'Startup'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class FakePython:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder/'python.exe').write_bytes(b'MZ')
        (self.folder/'pythonw.exe').write_bytes(b'MZ')

    def __call__(self, name='python.exe'):
        return self.folder/name

    def __fspath__(self):
        return str(self.folder/'python.exe')


def write_foreign_entry(link):
    """A same-named .lnk created by another program, using native PowerShell.

    Built this way on purpose: our own writer always stamps the MoocReminder
    description, which would make the fixture look like ours and hide the bug
    these tests are watching for.
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='C:\\Windows\\System32\\notepad.exe';"
        "$s.WorkingDirectory='C:\\Windows';"
        "$s.Description='Some other program';"
        "$s.Save()" % str(link).replace("'", "''"))
    subprocess.run(['powershell', '-NoProfile', '-Command', script],
                   capture_output=True, timeout=60, check=False)


@unittest.skipUnless(os.name == 'nt', 'Windows shortcut')
class AutostartTests(unittest.TestCase):
    def test_enabling_creates_the_entry_in_the_startup_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            interpreter = FakePython(Path(folder)/'runtime')
            startup = make_startup(folder)
            link = autostart.enable(workspace, python=interpreter(), folder=startup)
            self.assertEqual(link, startup/autostart.LINK_NAME)
            self.assertTrue(link.is_file())

    def test_the_startup_copy_asks_for_background_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            interpreter = FakePython(Path(folder)/'runtime')
            startup = make_startup(folder)
            link = autostart.enable(workspace, python=interpreter(), folder=startup)
            fields = read_link(link)
            # Without this the window would pop up over whatever the user was
            # doing every time they signed in.
            self.assertIn(autostart.BACKGROUND_ARGUMENT, fields['arguments'])
            self.assertIn(str(launcher_entry(workspace)), fields['arguments'])
            self.assertEqual(Path(fields['target']).name.lower(), 'pythonw.exe')

    def test_enabled_follows_the_entry_being_created_and_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            interpreter = FakePython(Path(folder)/'runtime')
            startup = make_startup(folder)
            self.assertFalse(autostart.enabled(workspace, startup))
            autostart.enable(workspace, python=interpreter(), folder=startup)
            self.assertTrue(autostart.enabled(workspace, startup))
            self.assertTrue(autostart.disable(workspace, startup))
            self.assertFalse(autostart.enabled(workspace, startup))

    def test_a_moved_project_is_repaired_rather_than_duplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            interpreter = FakePython(Path(folder)/'runtime')
            startup = make_startup(folder)
            autostart.enable(workspace, python=interpreter(), folder=startup)
            moved = workspace.rename(workspace.parent/'Renamed app')
            # The old location no longer resolves, so the entry is "on" but not
            # usable -- exactly the state the launcher repairs silently.
            self.assertTrue(autostart.enabled(moved, startup))
            self.assertFalse(autostart.ready(moved, interpreter(), startup))
            autostart.enable(moved, python=interpreter(), folder=startup)
            self.assertTrue(autostart.ready(moved, interpreter(), startup))
            self.assertEqual([p.name for p in startup.glob('*.lnk')],
                             [autostart.LINK_NAME])

    def test_an_entry_pointing_at_an_old_runtime_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            old = FakePython(Path(folder)/'old-runtime')
            new = FakePython(Path(folder)/'new-runtime')
            startup = make_startup(folder)
            autostart.enable(workspace, python=old(), folder=startup)
            self.assertTrue(autostart.ready(workspace, old(), startup))
            self.assertFalse(autostart.ready(workspace, new(), startup))
            # Still reported as on: the user did opt in, and the launcher fixes
            # the interpreter path on the next start.
            self.assertTrue(autostart.enabled(workspace, startup))

    def test_disabling_leaves_another_programs_entry_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            startup = make_startup(folder)
            link = startup/autostart.LINK_NAME
            write_foreign_entry(link)
            self.assertFalse(autostart.enabled(workspace, startup))
            with self.assertRaises(RuntimeError):
                autostart.disable(workspace, startup)
            self.assertTrue(link.is_file())
            self.assertEqual(Path(read_link(link)['target']).name.lower(),
                             'notepad.exe')

    def test_enabling_never_overwrites_another_programs_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            interpreter = FakePython(Path(folder)/'runtime')
            startup = make_startup(folder)
            link = startup/autostart.LINK_NAME
            write_foreign_entry(link)
            with self.assertRaises(RuntimeError):
                autostart.enable(workspace, python=interpreter(), folder=startup)
            self.assertEqual(Path(read_link(link)['target']).name.lower(),
                             'notepad.exe')

    def test_disabling_when_nothing_is_installed_is_harmless(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            self.assertFalse(autostart.disable(workspace, make_startup(folder)))

    def test_checks_never_raise_when_the_folder_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            # A file where a directory is expected: the guards must answer
            # "off" rather than break the launcher or the settings dialog.
            blocked = Path(folder)/'not-a-folder'
            blocked.write_text('x', encoding='utf-8')
            self.assertFalse(autostart.enabled(workspace, blocked))
            self.assertFalse(autostart.ready(workspace, None, blocked))

    def test_startup_directory_is_the_real_one_and_absolute(self):
        found = autostart.startup_directory()
        self.assertTrue(found.is_absolute())
        self.assertEqual(found.name.lower(), 'startup')


if __name__ == '__main__':
    unittest.main()
