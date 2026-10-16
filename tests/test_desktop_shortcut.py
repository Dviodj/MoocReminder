import _bootstrap  # noqa: F401  (puts src/ on sys.path)
from _bootstrap import launcher_entry, stub_launcher

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from desktop_shortcut import (create_desktop_shortcut, desktop_directory,
    desktop_shortcut_ready, launch_target, owns_shortcut, read_link,
    remove_desktop_shortcut, shortcut_state)

# The desktop is outside the writable sandbox in some environments, so these
# tests build the shortcut in a temporary directory and pass it as `desktop`.
PNG = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 32)


def make_workspace(root):
    workspace = Path(root)/'O\'Brien app'
    (workspace/'assets').mkdir(parents=True)
    stub_launcher(workspace)
    # A real ICO header (0 images is legal enough for WScript.Shell to record).
    (workspace/'assets'/'MoocReminder.ico').write_bytes(
        b'\x00\x00\x01\x00\x00\x00')
    return workspace


class FakePython:
    """A stand-in for the resolved runtime interpreter.

    Callable so a test can say `interpreter()` for the default python.exe or
    `interpreter('pythonw.exe')` for a specific binary.
    """

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder/'python.exe').write_bytes(b'MZ')
        (self.folder/'pythonw.exe').write_bytes(b'MZ')

    def __call__(self, name='python.exe'):
        return self.folder/name

    def remove(self, name):
        (self.folder/name).unlink()

    def __fspath__(self):
        return str(self.folder/'python.exe')


@unittest.skipUnless(os.name == 'nt', 'Windows shortcut')
class ShortcutTests(unittest.TestCase):
    def test_target_is_a_real_interpreter_and_arguments_point_at_the_launcher(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            interpreter = FakePython(Path(folder)/'runtime')
            python = interpreter()
            target, arguments = launch_target(workspace, python)
            # wscript.exe is what made the icon and the move-repair impossible.
            self.assertEqual(target.name.lower(), 'pythonw.exe')
            self.assertEqual(Path(target).parent, Path(python).parent)
            self.assertIn(str(launcher_entry(workspace)), arguments)
            self.assertIn(str(workspace), arguments)

    def test_target_falls_back_when_pythonw_is_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            interpreter = FakePython(Path(folder)/'runtime')
            interpreter.remove('pythonw.exe')
            target, _ = launch_target(workspace, interpreter())
            self.assertEqual(target.name.lower(), 'python.exe')

    def test_creating_then_reading_back_records_the_expected_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            link = create_desktop_shortcut(workspace, python=interpreter(),
                                           desktop=desktop)
            self.assertTrue(link.is_file())
            info = read_link(link)
            self.assertEqual(Path(info['target']).name.lower(), 'pythonw.exe')
            self.assertEqual(Path(info['cwd']), workspace)
            self.assertIn(str(launcher_entry(workspace)), info['arguments'])
            self.assertTrue(info['description'].startswith('MoocReminder'))
            self.assertIn('MoocReminder.ico', info['icon'])

    def test_a_correct_shortcut_is_reused_rather_than_rewritten(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            python = interpreter()
            link = create_desktop_shortcut(workspace, python=python, desktop=desktop)
            stamp = link.stat().st_mtime_ns
            self.assertTrue(desktop_shortcut_ready(workspace, python, desktop))
            self.assertEqual(create_desktop_shortcut(workspace, python=python,
                                                     desktop=desktop), link)
            self.assertEqual(link.stat().st_mtime_ns, stamp)

    def test_a_moved_project_repairs_its_shortcut(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            python = interpreter()
            create_desktop_shortcut(workspace, python=python, desktop=desktop)

            moved = Path(folder)/'moved-app'
            workspace.rename(moved)
            self.assertFalse(desktop_shortcut_ready(moved, python, desktop))

            link = create_desktop_shortcut(moved, python=python, desktop=desktop)
            info = read_link(link)
            self.assertEqual(Path(info['cwd']), moved)
            self.assertIn(str(launcher_entry(moved)), info['arguments'])
            self.assertNotIn(str(workspace), info['arguments'])
            self.assertTrue(desktop_shortcut_ready(moved, python, desktop))

    def test_a_shortcut_pointing_at_an_old_runtime_is_repaired(self):
        # Only the interpreter's *path* changes here; its file name stays
        # pythonw.exe.  Comparing names alone would call this shortcut fine and
        # leave the user with an entry that launches nothing.
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            old = FakePython(Path(folder)/'old-runtime')
            new = FakePython(Path(folder)/'new-runtime')
            create_desktop_shortcut(workspace, python=old(), desktop=desktop)
            self.assertTrue(desktop_shortcut_ready(workspace, old(), desktop))
            self.assertFalse(desktop_shortcut_ready(workspace, new(), desktop))
            link = create_desktop_shortcut(workspace, python=new(), desktop=desktop)
            self.assertEqual(Path(read_link(link)['target']), new('pythonw.exe'))
            self.assertTrue(desktop_shortcut_ready(workspace, new(), desktop))

    def test_a_shortcut_made_before_a_new_icon_is_rewritten_once(self):
        # The link stores the icon's *path*, so a shortcut built for the
        # previous picture still looks correct and the desktop keeps showing
        # what Explorer cached.  Rewriting the link is what makes the new icon
        # appear, and it must happen exactly once per new icon.
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            icon = workspace/'assets'/'MoocReminder.ico'
            create_desktop_shortcut(workspace, python=interpreter(), desktop=desktop)
            cache = workspace/'.launcher-state'/'shortcut.json'
            first = json.loads(cache.read_text(encoding='utf-8'))
            self.assertEqual(first['icon_stamp'], icon.stat().st_mtime_ns)

            icon.write_bytes(icon.read_bytes() + b'\x00')
            stamp = icon.stat().st_mtime_ns
            os.utime(icon, ns=(stamp, stamp + 2_000_000_000))
            create_desktop_shortcut(workspace, python=interpreter(), desktop=desktop)
            second = json.loads(cache.read_text(encoding='utf-8'))
            self.assertGreater(second['written'], first['written'])
            self.assertEqual(second['icon_stamp'], icon.stat().st_mtime_ns)

            # Settled: the next launch must not rewrite the link again.
            create_desktop_shortcut(workspace, python=interpreter(), desktop=desktop)
            self.assertEqual(json.loads(cache.read_text(encoding='utf-8'))['written'],
                             second['written'])

    def test_a_shortcut_pointing_elsewhere_is_repaired_not_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            python = interpreter()
            link = create_desktop_shortcut(workspace, python=python, desktop=desktop)
            # Simulate a stale link: same name, different working directory.
            other = Path(folder)/'elsewhere'; other.mkdir()
            script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
                      "'" + str(link).replace("'", "''") + "');"
                      "$s.TargetPath='C:\\Windows\\System32\\notepad.exe';"
                      "$s.WorkingDirectory='" + str(other).replace("'", "''") + "';"
                      "$s.Description='MoocReminder - stale';$s.Save()")
            import base64
            subprocess.check_call(['powershell.exe', '-NoProfile', '-NonInteractive',
                '-EncodedCommand', base64.b64encode(script.encode('utf-16le')).decode()])
            self.assertFalse(desktop_shortcut_ready(workspace, python, desktop))
            create_desktop_shortcut(workspace, python=python, desktop=desktop)
            self.assertEqual(Path(read_link(link)['cwd']), workspace)

    def test_another_programs_shortcut_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            link = desktop/'MoocReminder.lnk'
            script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
                      "'" + str(link).replace("'", "''") + "');"
                      "$s.TargetPath='C:\\Windows\\System32\\notepad.exe';"
                      "$s.WorkingDirectory='C:\\Windows';"
                      "$s.Description='Somebody else';$s.Save()")
            import base64
            subprocess.check_call(['powershell.exe', '-NoProfile', '-NonInteractive',
                '-EncodedCommand', base64.b64encode(script.encode('utf-16le')).decode()])
            with self.assertRaises(RuntimeError):
                create_desktop_shortcut(workspace, python=interpreter(),
                                        desktop=desktop)
            self.assertEqual(Path(read_link(link)['target']).name.lower(), 'notepad.exe')

    def test_removing_deletes_only_our_own_shortcut(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            python = interpreter()
            link = create_desktop_shortcut(workspace, python=python, desktop=desktop)
            self.assertTrue(remove_desktop_shortcut(workspace, desktop=desktop))
            self.assertFalse(link.is_file())
            self.assertFalse(remove_desktop_shortcut(workspace, desktop=desktop))

    def test_cancel_prevents_shortcut_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            with self.assertRaises(InterruptedError):
                create_desktop_shortcut(workspace, python=interpreter(),
                                        desktop=desktop, cancelled=lambda: True)
            self.assertFalse(list(desktop.glob('*.lnk')))

    def test_a_missing_launcher_is_reported_rather_than_half_created(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)/'bare'; workspace.mkdir()
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            with self.assertRaises(RuntimeError):
                create_desktop_shortcut(workspace, python=interpreter(),
                                        desktop=desktop)
            self.assertFalse(list(desktop.glob('*.lnk')))

    def test_ready_check_never_raises_on_an_unreadable_link(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            (desktop/'MoocReminder.lnk').write_bytes(b'not a real lnk')
            self.assertFalse(desktop_shortcut_ready(workspace, interpreter(),
                                                    desktop))

    def test_state_carries_the_icon_path_when_the_icon_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            interpreter = FakePython(Path(folder)/'runtime')
            state = shortcut_state(workspace, interpreter())
            self.assertEqual(state['link'].name, 'MoocReminder.lnk')
            self.assertIn('MoocReminder.ico,0', state['icon'])
            # The workspace appears twice: as the script's folder and as the
            # --workspace argument. It is quoted because the path has a space.
            self.assertEqual(state['arguments'].count(str(workspace)), 2)
            self.assertTrue(state['arguments'].startswith('"'))

    def test_actual_desktop_is_resolved(self):
        self.assertTrue(desktop_directory().is_absolute())


@unittest.skipUnless(os.name == 'nt', 'Windows shortcut')
class OwnershipTests(unittest.TestCase):
    """Distinguishing "nothing yet" from "ours, but stale" drives the prompt."""

    def _foreign_shortcut(self, link, description='Somebody else'):
        script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
                  "'" + str(link).replace("'", "''") + "');"
                  "$s.TargetPath='C:\\Windows\\System32\\notepad.exe';"
                  "$s.WorkingDirectory='C:\\Windows';"
                  "$s.Description='" + description + "';$s.Save()")
        import base64
        subprocess.check_call(['powershell.exe', '-NoProfile', '-NonInteractive',
            '-EncodedCommand', base64.b64encode(script.encode('utf-16le')).decode()])

    def test_nothing_on_the_desktop_is_not_ours(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            self.assertFalse(owns_shortcut(workspace, desktop))

    def test_a_shortcut_we_created_is_ours(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            create_desktop_shortcut(workspace, python=interpreter(), desktop=desktop)
            self.assertTrue(owns_shortcut(workspace, desktop))

    def test_a_moved_project_still_recognises_its_own_shortcut(self):
        """So the relocation is repaired silently instead of prompting again."""
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            create_desktop_shortcut(workspace, python=interpreter(), desktop=desktop)

            moved = Path(folder)/'moved-app'
            workspace.rename(moved)
            self.assertFalse(desktop_shortcut_ready(moved, interpreter(), desktop))
            self.assertTrue(owns_shortcut(moved, desktop))

    def test_another_programs_shortcut_is_not_ours(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            self._foreign_shortcut(desktop/'MoocReminder.lnk')
            self.assertFalse(owns_shortcut(workspace, desktop))

    def test_refusal_to_overwrite_explains_the_reason(self):
        """A generic 'check failed' message reads like a bug in this program."""
        with tempfile.TemporaryDirectory() as folder:
            workspace = make_workspace(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            interpreter = FakePython(Path(folder)/'runtime')
            self._foreign_shortcut(desktop/'MoocReminder.lnk')
            with self.assertRaises(RuntimeError) as caught:
                create_desktop_shortcut(workspace, python=interpreter(), desktop=desktop)
            self.assertIn('其他程序', str(caught.exception))
            self.assertNotIn('Objs', str(caught.exception))

    def test_powershell_error_text_is_readable_not_mojibake(self):
        """PowerShell encodes its CLIXML error stream with the console code page.

        PowerShell 5.1 writes GBK on a Chinese system, so decoding as UTF-8
        turned '同名快捷方式属于其他程序，未覆盖。' into mojibake and the user
        never saw why the shortcut was refused.
        """
        from desktop_shortcut import _script_error
        message = '同名快捷方式属于其他程序，未覆盖。'
        for encoding in ('utf-8', 'gbk'):
            if encoding == 'gbk':
                try:
                    payload = message.encode('gbk')
                except UnicodeEncodeError:
                    continue
            else:
                payload = message.encode('utf-8')
            stderr = (b'#< CLIXML\r\n<Objs Version="1.1.0.1">'
                      b'<S S="Error">' + payload + b'_x000D__x000A_</S>'
                      b'<S S="Error">At line:1 char:314_x000D__x000A_</S></Objs>')
            self.assertEqual(_script_error(stderr), message,
                'failed to decode a %s error stream' % encoding)

    def test_plain_error_text_is_returned_unchanged(self):
        from desktop_shortcut import _script_error
        self.assertEqual(_script_error('启动已取消\r\n'.encode('gbk')), '启动已取消')


if __name__ == '__main__':
    unittest.main()
