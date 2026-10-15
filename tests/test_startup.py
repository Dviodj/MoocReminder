"""Exercise the source launcher with a real incompatible Windows runtime."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import source_launcher
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODERN = Path('D:/Software/anaconda/python.exe')
LEGACY = Path('D:/vs2/Python39_64/python.exe')


@unittest.skipUnless(MODERN.exists() and LEGACY.exists(), 'Windows runtime fixtures unavailable')
class StartupTests(unittest.TestCase):
    def test_gui_uses_prepared_virtual_environment(self):
        captured = {}
        def launch(args, **kwargs):
            captured.update(args=args, env=kwargs['env'])
            ready = Path(args[args.index('--startup-report')+1])
            ready.write_text(json.dumps(dict(native_window=True)), encoding='utf-8')
            return SimpleNamespace(poll=lambda: None, wait=lambda: 0)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            plan = dict(python=str(MODERN),base_python=str(MODERN),version=[3,13,9],venv_ready=True)
            with patch('source_launcher.runtime_plan',return_value=plan), patch('source_launcher.run_command',return_value=0), patch('source_launcher.create_desktop_shortcut',return_value=None), patch('source_launcher.subprocess.Popen',side_effect=launch):
                self.assertEqual(source_launcher.run_app(Path(folder),[]),0)
            self.assertEqual(Path(captured['args'][0]),Path(folder)/'.venv/Scripts/pythonw.exe')
            self.assertNotIn('__PYVENV_LAUNCHER__', captured['env'])

    def test_python39_bootstrap_selects_supported_runtime(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            old = subprocess.run([str(LEGACY), '-m', 'venv', '--without-pip', str(Path(folder)/'.venv')], capture_output=True)
            self.assertEqual(old.returncode, 0)
            env = dict(os.environ, MOOC_PYTHON=str(MODERN))
            result = subprocess.run([str(LEGACY), str(ROOT/'source_launcher.py'), '--check-runtime', '--workspace', folder], env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(Path(report['python']).resolve(), MODERN.resolve())
            self.assertFalse(report['venv_ready'])
            self.assertGreaterEqual(tuple(report['version'][:2]), (3,10))

    def test_incompatible_runtime_probe_is_rejected(self):
        result = subprocess.run([str(MODERN), str(ROOT/'source_launcher.py'), '--probe-python', str(LEGACY)], capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['supported'], False)


class StartupCleanupTests(unittest.TestCase):
    def test_python310_is_rejected_by_native_component_requirement(self):
        info = json.dumps(dict(version=[3,10,11], bits=64, base_python='python.exe'))
        with patch('source_launcher.subprocess.run', return_value=subprocess.CompletedProcess([],0,info,'')):
            self.assertIsNone(source_launcher.probe_python('python.exe'))

    def test_startup_timeout_stops_the_process_it_started(self):
        original_run, original_popen = subprocess.run, subprocess.Popen
        child = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-c', 'import time; time.sleep(30)'])
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args,0) if '-c' in args or '--prepare' in args else original_run(args, **kwargs)
        def popen(args, **kwargs):
            return child if str(args[0]).endswith('pythonw.exe') else original_popen(args, **kwargs)
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
                plan = dict(python=sys.executable, base_python=sys.executable,version=[3,13,9], venv_ready=True)
                # Replace only the launcher's view of the clock.  Patching the
                # global time module also reached desktop_shortcut, whose
                # shortcut-write loop calls monotonic(), so the two fought over
                # this finite side_effect and the test failed at random.
                ticks = iter([0, 21])
                clock = SimpleNamespace(monotonic=lambda: next(ticks))
                with patch('source_launcher.runtime_plan', return_value=plan), patch('source_launcher.run_command',return_value=0), patch('source_launcher.create_desktop_shortcut',return_value=None), patch('source_launcher.subprocess.run', side_effect=run), patch('source_launcher.subprocess.Popen', side_effect=popen), patch('source_launcher.time', clock):
                    with self.assertRaisesRegex(RuntimeError, '启动确认'):
                        source_launcher.run_app(Path(folder), [])
                self.assertIsNotNone(child.poll(), 'startup failure left its process running')
        finally:
            if child.poll() is None:
                child.terminate();child.wait(timeout=3)

    def test_declining_the_shortcut_leaves_the_desktop_untouched(self):
        """Answering no must not delete anything.

        The retraction used to run unconditionally, so a same-named shortcut
        belonging to another program made the launcher warn about a shortcut
        the user had just refused.
        """
        original_run, original_popen = subprocess.run, subprocess.Popen
        child = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-c', 'import time; time.sleep(30)'])
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args,0) if '-c' in args or '--prepare' in args else original_run(args, **kwargs)
        def popen(args, **kwargs):
            return child if str(args[0]).endswith('pythonw.exe') else original_popen(args, **kwargs)
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
                plan = dict(python=sys.executable, base_python=sys.executable,version=[3,13,9], venv_ready=True)
                ticks = iter([0, 21])
                clock = SimpleNamespace(monotonic=lambda: next(ticks))
                created, removed = [], []
                def record_create(*args, **kwargs):
                    created.append((args, kwargs))
                def record_remove(*args, **kwargs):
                    removed.append((args, kwargs))
                with patch('source_launcher.runtime_plan', return_value=plan), patch('source_launcher.run_command',return_value=0), patch('source_launcher.desktop_shortcut_ready',return_value=False), patch('source_launcher.owns_shortcut',return_value=False), patch('source_launcher.create_desktop_shortcut',side_effect=record_create), patch('source_launcher.remove_desktop_shortcut',side_effect=record_remove), patch('source_launcher.subprocess.run', side_effect=run), patch('source_launcher.subprocess.Popen', side_effect=popen), patch('source_launcher.time', clock):
                    with self.assertRaisesRegex(RuntimeError, '启动确认'):
                        source_launcher.run_app(Path(folder), [], confirm=lambda question: False)
                self.assertEqual(created, [], 'declining still created a shortcut')
                self.assertEqual(removed, [], 'declining tried to delete a shortcut it does not own')
        finally:
            if child.poll() is None:
                child.terminate();child.wait(timeout=3)

    def test_accepting_creates_the_shortcut_once(self):
        original_run, original_popen = subprocess.run, subprocess.Popen
        child = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-c', 'import time; time.sleep(30)'])
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args,0) if '-c' in args or '--prepare' in args else original_run(args, **kwargs)
        def popen(args, **kwargs):
            return child if str(args[0]).endswith('pythonw.exe') else original_popen(args, **kwargs)
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
                plan = dict(python=sys.executable, base_python=sys.executable,version=[3,13,9], venv_ready=True)
                ticks = iter([0, 21])
                clock = SimpleNamespace(monotonic=lambda: next(ticks))
                created, asked = [], []
                def record_create(*args, **kwargs):
                    created.append((args, kwargs))
                with patch('source_launcher.runtime_plan', return_value=plan), patch('source_launcher.run_command',return_value=0), patch('source_launcher.desktop_shortcut_ready',return_value=False), patch('source_launcher.owns_shortcut',return_value=False), patch('source_launcher.create_desktop_shortcut',side_effect=record_create), patch('source_launcher.remove_desktop_shortcut',return_value=None), patch('source_launcher.subprocess.run', side_effect=run), patch('source_launcher.subprocess.Popen', side_effect=popen), patch('source_launcher.time', clock):
                    def confirm(question):
                        asked.append(question)
                        return True
                    with self.assertRaisesRegex(RuntimeError, '启动确认'):
                        source_launcher.run_app(Path(folder), [], confirm=confirm)
                self.assertEqual(len(created), 1, 'accepting should create exactly one shortcut')
                self.assertEqual(len(asked), 1, 'the user should be asked exactly once')
                self.assertIn('快捷方式', asked[0])
        finally:
            if child.poll() is None:
                child.terminate();child.wait(timeout=3)

    def test_a_shortcut_we_own_is_repaired_without_asking(self):
        """A moved project must not re-prompt: the user already agreed."""
        original_run, original_popen = subprocess.run, subprocess.Popen
        child = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-c', 'import time; time.sleep(30)'])
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args,0) if '-c' in args or '--prepare' in args else original_run(args, **kwargs)
        def popen(args, **kwargs):
            return child if str(args[0]).endswith('pythonw.exe') else original_popen(args, **kwargs)
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
                plan = dict(python=sys.executable, base_python=sys.executable,version=[3,13,9], venv_ready=True)
                ticks = iter([0, 21])
                clock = SimpleNamespace(monotonic=lambda: next(ticks))
                created, asked = [], []
                def record_create(*args, **kwargs):
                    created.append((args, kwargs))
                with patch('source_launcher.runtime_plan', return_value=plan), patch('source_launcher.run_command',return_value=0), patch('source_launcher.desktop_shortcut_ready',return_value=False), patch('source_launcher.owns_shortcut',return_value=True), patch('source_launcher.create_desktop_shortcut',side_effect=record_create), patch('source_launcher.remove_desktop_shortcut',return_value=None), patch('source_launcher.subprocess.run', side_effect=run), patch('source_launcher.subprocess.Popen', side_effect=popen), patch('source_launcher.time', clock):
                    def confirm(question):
                        asked.append(question)
                        return True
                    with self.assertRaisesRegex(RuntimeError, '启动确认'):
                        source_launcher.run_app(Path(folder), [], confirm=confirm)
                self.assertEqual(len(created), 1, 'a stale shortcut of ours should be repaired')
                self.assertEqual(asked, [], 'repairing our own shortcut must not prompt again')
        finally:
            if child.poll() is None:
                child.terminate();child.wait(timeout=3)

    def _launch_writing(self, report_fields, workspace_args):
        """Run the launcher against a child that reports the given readiness."""
        captured = {}
        def launch(args, **kwargs):
            captured.update(args=args, kwargs=kwargs)
            ready = Path(args[args.index('--startup-report') + 1])
            ready.write_text(json.dumps(report_fields), encoding='utf-8')
            return SimpleNamespace(poll=lambda: None, wait=lambda: 0)
        plan = dict(python=sys.executable, base_python=sys.executable, version=[3, 13, 9], venv_ready=True)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            with patch('source_launcher.runtime_plan', return_value=plan), \
                 patch('source_launcher.run_command', return_value=0), \
                 patch('source_launcher.create_desktop_shortcut', return_value=None), \
                 patch('source_launcher.remove_desktop_shortcut', return_value=None), \
                 patch('source_launcher.subprocess.Popen', side_effect=launch):
                code = source_launcher.run_app(Path(folder), workspace_args)
        return code, captured

    def test_a_sign_in_copy_is_accepted_without_a_window(self):
        """At sign-in there is no window, and that must not be read as failure.

        The readiness check used to demand a visible window, so the launcher
        declared the background copy dead and shut it down -- the autostart
        entry could never work.
        """
        code, captured = self._launch_writing(
            dict(ready=True, native_window=False, background=True), ['--background'])
        self.assertEqual(code, 0)
        self.assertIn('--background', captured['args'])

    def test_a_second_copy_hands_over_and_exits(self):
        code, _ = self._launch_writing(
            dict(ready=True, already_running=True, background=False), [])
        self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
