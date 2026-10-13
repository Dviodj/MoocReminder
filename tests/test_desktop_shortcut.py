import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from desktop_shortcut import create_desktop_shortcut, desktop_directory


@unittest.skipUnless(os.name=='nt', 'Windows shortcut')
class ShortcutTests(unittest.TestCase):
    def test_link_handles_spaces_and_apostrophe_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);workspace=base/"O'Brien app";desktop=base/'Desktop'
            workspace.mkdir();desktop.mkdir()
            (workspace/'launch.vbs').write_text('WScript.Quit')
            link=create_desktop_shortcut(workspace, desktop=desktop)
            self.assertTrue(link.is_file())
            self.assertEqual(create_desktop_shortcut(workspace,desktop=desktop),link)
            self.assertEqual(list(desktop.glob('*.lnk')),[link])
            escaped=str(link).replace("'","''")
            code="$s=(New-Object -ComObject WScript.Shell).CreateShortcut('"+escaped+"'); @{target=$s.TargetPath;args=$s.Arguments;cwd=$s.WorkingDirectory}|ConvertTo-Json -Compress"
            import base64
            result=subprocess.check_output(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(code.encode('utf-16le')).decode()])
            info=json.loads(result.decode('utf-8-sig'))
            self.assertEqual(Path(info['cwd']),workspace)
            self.assertEqual(Path(info['target']).name.lower(),'wscript.exe')
            self.assertIn(str(workspace/'launch.vbs'), info['args'])

    def test_cancel_prevents_shortcut_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(InterruptedError):
                create_desktop_shortcut(Path(folder),desktop=Path(folder),cancelled=lambda:True)
            self.assertFalse(list(Path(folder).glob('*.lnk')))

    def test_actual_desktop_is_resolved(self):
        self.assertTrue(desktop_directory().is_absolute())
