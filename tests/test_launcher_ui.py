import importlib.util
import pathlib
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / 'launcher_ui.py').exists(), 'startup UI implementation missing')
        spec = importlib.util.spec_from_file_location('launcher_ui', ROOT / 'launcher_ui.py')
        self.ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ui)

    def test_worker_delivers_progress_and_success_without_tk(self):
        identity = []
        def runner(workspace, args, **kwargs):
            identity.append(threading.get_ident())
            kwargs['progress']({'phase': '下载组件', 'detail': '等待下载', 'percent': 35, 'indeterminate': True})
            return 0
        worker = self.ui.StartupWorker(ROOT, [], runner=runner)
        worker.start(); worker.thread.join(2)
        events = worker.drain()
        self.assertEqual([e['kind'] for e in events], ['progress', 'success'])
        self.assertNotEqual(identity[0], threading.get_ident())
        self.assertTrue(events[0]['indeterminate'])

    def test_failure_retry_and_cooperative_cancellation(self):
        def runner(workspace, args, **kwargs):
            if kwargs['cancelled']():
                raise InterruptedError('cancelled')
            raise RuntimeError('network unavailable')
        worker = self.ui.StartupWorker(ROOT, [], runner=runner)
        worker.cancel(); worker.start(); worker.thread.join(2)
        self.assertEqual(worker.drain()[0]['kind'], 'cancelled')
        worker.retry(); worker.thread.join(2)
        event = worker.drain()[0]
        self.assertEqual(event['kind'], 'error')
        self.assertIn('network unavailable', event['detail'])

    def test_real_app_smoke_takes_priority_over_ui_report(self):
        args, _ = self.ui.parse_args(['--smoke-report', 'ui.json', '--app-smoke-report', 'app.json'])
        self.assertIsNone(self.ui.select_runner(args))
        args, _ = self.ui.parse_args(['--smoke-report', 'ui.json'])
        self.assertIs(self.ui.select_runner(args), self.ui.preview_runner)

    def test_arguments_forward_full_app_smoke(self):
        args, forwarded = self.ui.parse_args(['--app-smoke-report', 'report.json', '--wait', '--other'])
        self.assertTrue(args.wait)
        self.assertEqual(forwarded, ['--other', '--smoke-report', 'report.json'])

class EntryPreflightTests(unittest.TestCase):
    def test_broken_local_executable_falls_back_to_working_path(self):
        import os
        import shutil
        import subprocess
        import sys
        import tempfile
        if os.name != 'nt': self.skipTest('Windows entry point')
        with tempfile.TemporaryDirectory() as folder:
            workspace = pathlib.Path(folder)
            shutil.copy2(ROOT / 'launch.vbs', workspace / 'launch.vbs')
            local = workspace / '.venv' / 'Scripts'
            local.mkdir(parents=True)
            (local / 'pythonw.exe').write_bytes(b'broken executable fixture')
            env = dict(os.environ, PATH=str(pathlib.Path(sys.executable).parent))
            result = subprocess.run(['cscript.exe', '//nologo', str(workspace / 'launch.vbs'), '--preflight-only'], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(str(pathlib.Path(sys.executable).with_name('pythonw.exe')), result.stdout)
    def test_all_broken_candidates_fail_with_clear_diagnostic(self):
        import os
        import shutil
        import subprocess
        import tempfile
        if os.name != 'nt': self.skipTest('Windows entry point')
        with tempfile.TemporaryDirectory() as folder:
            workspace = pathlib.Path(folder)
            shutil.copy2(ROOT / 'launch.vbs', workspace / 'launch.vbs')
            local = workspace / '.venv' / 'Scripts'; local.mkdir(parents=True)
            (local / 'pythonw.exe').write_bytes(b'broken executable fixture')
            result = subprocess.run(['cscript.exe', '//nologo', str(workspace / 'launch.vbs'), '--preflight-only'], env=dict(os.environ, PATH=''), capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertIn('No usable Python/Tk interpreter', result.stdout)

if __name__ == '__main__':
    unittest.main()

class WindowTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('launcher_ui_window', ROOT / 'launcher_ui.py')
        self.ui = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.ui)
        self.root = self.ui.tk.Tk()
    def tearDown(self):
        try: self.root.destroy()
        except self.ui.tk.TclError: pass
    def pump(self, seconds):
        import time
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update(); time.sleep(.01)
    def test_destroy_cancels_owned_timers(self):
        worker = self.ui.StartupWorker(ROOT, [], runner=lambda *a, **kw: 0)
        window = self.ui.StartupWindow(self.root, worker)
        timer_ids = tuple(window.after_ids)
        self.assertEqual(len(timer_ids), 2)
        self.root.destroy()
        self.assertFalse(window.after_ids)
        remaining = self.root.tk.call('after', 'info')
        self.assertTrue(all(timer not in remaining for timer in timer_ids))

    def test_cancel_before_delayed_start_does_not_launch(self):
        worker = self.ui.StartupWorker(ROOT, [], runner=lambda *a, **kw: 0)
        window = self.ui.StartupWindow(self.root, worker)
        window.cancel()
        window.start_worker()
        self.assertIsNone(worker.thread)

    def test_smoke_error_sets_nonzero_status(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            report = pathlib.Path(folder) / 'failed.json'
            worker = self.ui.StartupWorker(ROOT, [])
            window = self.ui.StartupWindow(self.root, worker, str(report))
            self.root.update()
            window.finish('error')
            self.assertEqual(window.exit_code, 1)
            self.assertIn('error', report.read_text(encoding='utf-8'))

    def test_real_window_responds_during_worker_and_cancel(self):
        import time
        def runner(workspace, args, **kwargs):
            while not kwargs['cancelled'](): time.sleep(.01)
            raise InterruptedError()
        worker = self.ui.StartupWorker(ROOT, [], runner=runner)
        window = self.ui.StartupWindow(self.root, worker)
        self.pump(.35)
        self.assertGreater(window.ticks, 2)
        self.assertTrue(worker.thread.is_alive())
        window.cancel()
        worker.thread.join(2)
        self.assertFalse(worker.thread.is_alive())
    def test_failure_shows_retry_and_retry_recovers(self):
        attempts = []
        def runner(workspace, args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1: raise RuntimeError('测试失败')
            kwargs['progress'](dict(phase='已重试', detail='恢复成功', percent=90))
            while not kwargs['cancelled']():
                import time; time.sleep(.01)
            raise InterruptedError()
        worker = self.ui.StartupWorker(ROOT, [], runner=runner)
        window = self.ui.StartupWindow(self.root, worker)
        self.pump(.35)
        self.assertEqual(window.phase.get(), '启动未完成')
        self.assertTrue(window.retry_button.winfo_ismapped())
        window.retry(); self.pump(.2)
        self.assertEqual(window.phase.get(), '已重试')
        self.assertEqual(len(attempts), 2)
        window.cancel(); worker.thread.join(2)

class EntryPreflightTests(unittest.TestCase):
    def test_broken_local_executable_falls_back_to_working_path(self):
        import os
        import shutil
        import subprocess
        import sys
        import tempfile
        if os.name != 'nt': self.skipTest('Windows entry point')
        with tempfile.TemporaryDirectory() as folder:
            workspace = pathlib.Path(folder)
            shutil.copy2(ROOT / 'launch.vbs', workspace / 'launch.vbs')
            local = workspace / '.venv' / 'Scripts'
            local.mkdir(parents=True)
            (local / 'pythonw.exe').write_bytes(b'broken executable fixture')
            env = dict(os.environ, PATH=str(pathlib.Path(sys.executable).parent))
            result = subprocess.run(['cscript.exe', '//nologo', str(workspace / 'launch.vbs'), '--preflight-only'], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(str(pathlib.Path(sys.executable).with_name('pythonw.exe')), result.stdout)
    def test_all_broken_candidates_fail_with_clear_diagnostic(self):
        import os
        import shutil
        import subprocess
        import tempfile
        if os.name != 'nt': self.skipTest('Windows entry point')
        with tempfile.TemporaryDirectory() as folder:
            workspace = pathlib.Path(folder)
            shutil.copy2(ROOT / 'launch.vbs', workspace / 'launch.vbs')
            local = workspace / '.venv' / 'Scripts'; local.mkdir(parents=True)
            (local / 'pythonw.exe').write_bytes(b'broken executable fixture')
            result = subprocess.run(['cscript.exe', '//nologo', str(workspace / 'launch.vbs'), '--preflight-only'], env=dict(os.environ, PATH=''), capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertIn('No usable Python/Tk interpreter', result.stdout)

if __name__ == '__main__':
    unittest.main()
