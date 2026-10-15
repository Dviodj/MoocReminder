"""The launcher's shortcut decision, run against the real desktop_shortcut code.

The unit tests mock desktop_shortcut, so the seam between the two modules --
where the resolved interpreter and the ownership check meet -- was only ever
covered by hand.  This drives `source_launcher.run_app` with the real shortcut
functions and a redirected desktop.
"""
import base64
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import desktop_shortcut
import source_launcher

ROOT = Path(__file__).resolve().parents[1]


def make_install(folder):
    """A folder shaped like a real install, so the shortcut writer accepts it."""
    install = Path(folder)/'install'
    (install/'assets').mkdir(parents=True)
    (install/'source_launcher.py').write_text('# stub', encoding='utf-8')
    (install/'assets'/'MoocReminder.ico').write_bytes(b'\x00\x00\x01\x00\x00\x00')
    # application_runtime returns <install>/.venv/Scripts/python.exe when the
    # plan reports venv_ready, so the fake install needs one.
    scripts = install/'.venv'/'Scripts'
    scripts.mkdir(parents=True)
    (scripts/'python.exe').write_bytes(b'MZ')
    (scripts/'pythonw.exe').write_bytes(b'MZ')
    return install


def write_foreign_shortcut(link):
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
              "'" + str(link).replace("'", "''") + "');"
              "$s.TargetPath='C:\\Windows\\System32\\notepad.exe';"
              "$s.WorkingDirectory='C:\\Windows';$s.Description='Notepad';$s.Save()")
    subprocess.check_call(['powershell.exe', '-NoProfile', '-NonInteractive',
        '-EncodedCommand', base64.b64encode(script.encode('utf-16le')).decode()])


class LauncherShortcutIntegrationTests(unittest.TestCase):
    def run_startup(self, folder, install, desktop, answer, pre_existing=None):
        """Drive one launcher startup; return (asked, link exists, target name)."""
        if pre_existing == 'ours':
            desktop_shortcut.create_desktop_shortcut(
                install, python=install/'.venv/Scripts/python.exe', desktop=desktop)
        elif pre_existing == 'foreign':
            write_foreign_shortcut(desktop/desktop_shortcut.LINK_NAME)

        original_popen = subprocess.Popen
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0)
        def popen(args, **kwargs):
            return child if str(args[0]).endswith('pythonw.exe') else original_popen(args, **kwargs)
        asked = []
        ticks = iter([0, 21])
        clock = SimpleNamespace(monotonic=lambda: next(ticks))
        try:
            with patch.object(desktop_shortcut, 'desktop_directory', lambda: desktop), \
                 patch('source_launcher.runtime_plan', return_value=dict(
                     python=sys.executable, base_python=sys.executable,
                     version=[3, 13, 9], venv_ready=True)), \
                 patch('source_launcher.run_command', return_value=0), \
                 patch('source_launcher.subprocess.run', side_effect=run), \
                 patch('source_launcher.subprocess.Popen', side_effect=popen), \
                 patch('source_launcher.time', clock):
                def confirm(question):
                    asked.append(question)
                    return answer
                # The GUI never reports ready here, so startup ends by raising.
                with self.assertRaises(RuntimeError):
                    source_launcher.run_app(install, [], confirm=confirm)
        finally:
            if child.poll() is None:
                child.terminate(); child.wait(timeout=3)

        link = desktop/desktop_shortcut.LINK_NAME
        target = Path(desktop_shortcut.read_link(link)['target']).name if link.is_file() else None
        return asked, link.is_file(), target

    @unittest.skipUnless(sys.platform == 'win32', 'Windows shortcut')
    def test_accepting_creates_a_shortcut_pointing_at_the_app(self):
        with tempfile.TemporaryDirectory() as folder:
            install = make_install(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            asked, exists, target = self.run_startup(folder, install, desktop, answer=True)
            self.assertEqual(len(asked), 1, 'the user should be asked exactly once')
            self.assertIn('快捷方式', asked[0])
            self.assertTrue(exists)
            # pythonw.exe, not wscript.exe: that is what made the icon work.
            self.assertEqual(target, 'pythonw.exe')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows shortcut')
    def test_declining_leaves_the_desktop_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            install = make_install(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            asked, exists, _ = self.run_startup(folder, install, desktop, answer=False)
            self.assertEqual(len(asked), 1)
            self.assertFalse(exists, 'declining must not create a shortcut')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows shortcut')
    def test_our_own_stale_shortcut_is_repaired_without_asking_again(self):
        with tempfile.TemporaryDirectory() as folder:
            install = make_install(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            asked, exists, target = self.run_startup(folder, install, desktop,
                                                     answer=False, pre_existing='ours')
            self.assertEqual(asked, [], 'the user already agreed; do not re-prompt')
            self.assertTrue(exists)
            self.assertEqual(target, 'pythonw.exe')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows shortcut')
    def test_another_programs_shortcut_is_neither_overwritten_nor_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            install = make_install(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            self.run_startup(folder, install, desktop, answer=True, pre_existing='foreign')
            target = Path(desktop_shortcut.read_link(
                desktop/desktop_shortcut.LINK_NAME)['target']).name
            self.assertEqual(target, 'notepad.exe', 'a foreign shortcut was overwritten')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows shortcut')
    def test_declining_does_not_delete_a_foreign_shortcut(self):
        with tempfile.TemporaryDirectory() as folder:
            install = make_install(folder)
            desktop = Path(folder)/'Desktop'; desktop.mkdir()
            self.run_startup(folder, install, desktop, answer=False, pre_existing='foreign')
            self.assertTrue((desktop/desktop_shortcut.LINK_NAME).is_file(),
                            'declining deleted a shortcut belonging to another program')


if __name__ == '__main__':
    unittest.main()
