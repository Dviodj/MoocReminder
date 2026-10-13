import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import source_launcher as launcher


class LauncherRuntimeTests(unittest.TestCase):
    def test_startup_lock_rejects_overlap_and_releases_after_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)
            with self.assertRaisesRegex(ValueError,'fixture'):
                with launcher.startup_lock(state):
                    with self.assertRaisesRegex(RuntimeError,'另一个启动窗口'):
                        with launcher.startup_lock(state):pass
                    raise ValueError('fixture')
            with launcher.startup_lock(state):pass

    def test_ready_app_survives_cache_write_failure(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);python=self.fixture(root)
            (root/'.launcher-state').mkdir()
            (root/'.launcher-state/ready-runtime.json').write_text(json.dumps(dict(
                python=str(python),base_python=str(python),version=[3,13,9],signature=launcher.cache_fingerprint(root,python))))
            def launched(args,**kwargs):
                Path(args[args.index('--startup-report')+1]).write_text('{"native_window":true}')
                return SimpleNamespace(poll=lambda:None)
            with patch.object(launcher,'create_desktop_shortcut'),patch.object(launcher.subprocess,'Popen',side_effect=launched),patch.object(Path,'replace',side_effect=PermissionError('cache readonly')),patch.object(launcher,'stop_started_app') as stop:
                self.assertEqual(launcher.run_app(root,[]),0)
                stop.assert_not_called()

    def fixture(self, root):
        python = root/'.venv/Scripts/python.exe'
        artifacts = [python, root/'.venv/pyvenv.cfg', root/'requirements-runtime.txt',
            root/'webview_runtime.py', root/'qt_bootstrap.py',
            root/'.venv/Lib/site-packages/PySide6/QtCore.pyd',
            root/'.venv/Lib/site-packages/PySide6/QtWidgets.pyd',
            root/'.venv/Lib/site-packages/pythonnet/__init__.py',
            root/'.native-webview/Microsoft.Web.WebView2.Core.dll',
            root/'.native-webview/Microsoft.Web.WebView2.WinForms.dll',
            root/'.native-webview/WebView2Loader.dll']
        for file in artifacts:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'fixture')
        return python

    def test_cache_invalidates_when_dependency_or_sdk_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); python = self.fixture(root)
            initial = launcher.cache_fingerprint(root, python)
            self.assertTrue(initial)
            (root/'requirements-runtime.txt').write_text('changed requirements')
            self.assertNotEqual(initial, launcher.cache_fingerprint(root, python))
            before = launcher.cache_fingerprint(root, python)
            (root/'.native-webview/WebView2Loader.dll').write_bytes(b'new SDK')
            self.assertNotEqual(before, launcher.cache_fingerprint(root, python))
            (root/'.venv/Lib/site-packages/PySide6/QtWidgets.pyd').unlink()
            self.assertIsNone(launcher.cache_fingerprint(root, python))

    def test_warm_start_skips_dependency_install_and_clr_preflight(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); python = self.fixture(root)
            (root/'.launcher-state').mkdir()
            (root/'.launcher-state/ready-runtime.json').write_text(json.dumps(dict(
                python=str(python), base_python=str(python), version=[3,13,9],
                signature=launcher.cache_fingerprint(root, python))))
            from types import SimpleNamespace
            def launched(args, **kwargs):
                target = Path(args[args.index('--startup-report')+1])
                target.write_text('{"native_window":true}')
                return SimpleNamespace(poll=lambda:None,wait=lambda:0)
            updates = []
            with patch.object(launcher, 'runtime_plan', side_effect=AssertionError('slow probe')), patch.object(launcher, 'run_command', side_effect=AssertionError('unexpected installation')), patch.object(launcher, 'create_desktop_shortcut', return_value=None), patch.object(launcher.subprocess, 'Popen', side_effect=launched):
                self.assertEqual(launcher.run_app(root, [], progress=updates.append), 0)
            self.assertEqual(updates[-1]['percent'], 100)

    def test_cancelled_start_does_not_launch_a_process(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(launcher.subprocess, 'Popen') as process:
                with self.assertRaises(InterruptedError):
                    launcher.run_app(Path(folder), [], cancelled=lambda:True)
                process.assert_not_called()

    def test_command_cancellation_stops_only_its_own_child(self):
        import subprocess,sys,threading
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);flag=threading.Event();timer=threading.Timer(.15,flag.set)
            timer.start()
            with (root/'log').open('w') as log:
                with self.assertRaises(InterruptedError):
                    launcher.run_command([getattr(sys,'_base_executable',sys.executable),'-c','import time;time.sleep(20)'],root,log,flag.is_set,timeout=5)
            timer.join()

    def test_existing_compatible_environment_avoids_new_venv(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); existing=root.parent/'mooc_deadline_reminder/.venv/Scripts/python.exe'
            plan=dict(python='base-python',base_python='base-python',venv_ready=False)
            with patch.object(launcher,'probe_app_runtime',side_effect=lambda path,workspace,**kwargs: {'base_python':'base-python'} if Path(path)==existing else None):
                selected, reused = launcher.application_runtime(root, plan)
            self.assertEqual(selected,existing)
            self.assertTrue(reused)
