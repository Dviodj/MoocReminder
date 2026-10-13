"""Source entry point compatible with Python 3.9, including runtime repair."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid


def probe_python(executable):
    if not executable:
        return None
    try:
        result = subprocess.run([str(executable), '-c',
            'import sys,struct,json; print(json.dumps(dict(version=list(sys.version_info[:3]),bits=struct.calcsize("P")*8,base_python=getattr(sys,"_base_executable",sys.executable))))'],
            capture_output=True, text=True, encoding='utf-8', timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = json.loads(result.stdout)
        if result.returncode == 0 and tuple(data['version'][:2]) >= (3, 11) and data['bits'] == 64:
            return data
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        pass
    return None


def candidates(workspace):
    yield os.environ.get('MOOC_PYTHON')
    try:
        yield json.loads((workspace/'.launcher-state/runtime.json').read_text(encoding='utf-8'))['python']
    except (OSError, ValueError, KeyError):
        pass
    yield workspace/'.venv/Scripts/python.exe'
    yield getattr(sys, '_base_executable', sys.executable)
    yield shutil.which('python')
    for prefix in (os.environ.get('CONDA_PREFIX'), Path.home()/'anaconda3', Path.home()/'miniconda3', Path('C:/ProgramData/anaconda3')):
        if prefix:
            yield Path(prefix)/'python.exe'
    if os.name == 'nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, 'Software\\Python') as root:
                    for i in range(winreg.QueryInfoKey(root)[0]):
                        with winreg.OpenKey(root, winreg.EnumKey(root, i)) as company:
                            for j in range(winreg.QueryInfoKey(company)[0]):
                                try:
                                    with winreg.OpenKey(company, winreg.EnumKey(company, j)+'\\InstallPath') as path:
                                        yield Path(winreg.QueryValue(path, None))/'python.exe'
                                except OSError:
                                    pass
            except OSError:
                pass


def runtime_plan(workspace):
    seen = set()
    for candidate in candidates(workspace):
        if not candidate:
            continue
        candidate = Path(candidate).resolve()
        if str(candidate).lower() in seen:
            continue
        seen.add(str(candidate).lower())
        info = probe_python(candidate)
        if info:
            return dict(python=str(candidate), base_python=info['base_python'], version=info['version'],
                venv_ready=bool(probe_python(workspace/'.venv/Scripts/python.exe')))
    raise RuntimeError('未找到 Python 3.11 以上的 64 位运行环境；原生网页组件需要 Python 3.11 或更高版本。')


def stop_started_app(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        # The venv executable can create a base-Python child. Stop only the
        # process tree rooted at the application this launcher just created.
        taskkill = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/taskkill.exe'
        try:
            subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                capture_output=True, timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def run_app(workspace, app_args, wait=False):
    state = workspace/'.launcher-state'
    state.mkdir(parents=True, exist_ok=True)
    log_path = state/'startup.log'
    with log_path.open('a', encoding='utf-8') as log:
        log.write('\n=== '+datetime.now().isoformat()+' ===\n')
        plan = runtime_plan(workspace)
        log.write(json.dumps(plan, ensure_ascii=False)+'\n')
        log.flush()
        if not plan['venv_ready']:
            print('正在修复程序运行环境…', flush=True)
            result = subprocess.run([plan['python'], '-m', 'venv', str(workspace/'.venv')], stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError('运行环境修复失败，详情已写入启动日志。')
        python = workspace/'.venv/Scripts/python.exe'
        (state/'runtime.json').write_text(json.dumps({'python':str(Path(plan['base_python']).resolve())}), encoding='utf-8')
        check = 'from qt_bootstrap import prepare_qt; prepare_qt("data"); from PySide6 import QtCore,QtWidgets; import pythonnet'
        if subprocess.run([str(python), '-c', check], cwd=workspace, stdout=log, stderr=subprocess.STDOUT).returncode:
            print('正在安装或修复桌面组件…', flush=True)
            temp = workspace/'.build-temp'
            temp.mkdir(parents=True, exist_ok=True)
            install_env = dict(os.environ, TEMP=str(temp), TMP=str(temp))
            result = subprocess.run([str(python), '-m', 'pip', 'install', '--no-build-isolation', '-r', 'requirements-runtime.txt'], cwd=workspace, env=install_env, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode or subprocess.run([str(python), '-c', check], cwd=workspace, stdout=log, stderr=subprocess.STDOUT).returncode:
                raise RuntimeError('桌面组件未能加载，详情已写入启动日志。')
        if subprocess.run([str(python), 'webview_runtime.py', '--prepare'], cwd=workspace, stdout=log, stderr=subprocess.STDOUT).returncode:
            raise RuntimeError('原生网页组件未能加载，详情已写入启动日志。')
        ready = state/('ready-'+uuid.uuid4().hex+'.json')
        gui_python = python.with_name('pythonw.exe')
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        env.pop('__PYVENV_LAUNCHER__', None)
        process = subprocess.Popen([str(gui_python), str(workspace/'qt_desktop.py'), '--startup-report', str(ready)]+app_args,
            cwd=workspace, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            until = time.monotonic()+20
            while time.monotonic() < until:
                if ready.exists():
                    report = json.loads(ready.read_text(encoding='utf-8'))
                    if report.get('native_window'):
                        log.write('主窗口启动成功\n');log.flush()
                        return process.wait() if wait else 0
                if process.poll() is not None:
                    raise RuntimeError('程序在主窗口出现前退出，详情已写入启动日志。')
                time.sleep(0.1)
            raise RuntimeError('未收到主窗口启动确认，详情已写入启动日志。')
        except BaseException:
            stop_started_app(process)
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', default=str(Path(__file__).resolve().parent))
    parser.add_argument('--check-runtime', action='store_true')
    parser.add_argument('--probe-python')
    parser.add_argument('--wait', action='store_true')
    args, app_args = parser.parse_known_args()
    workspace = Path(args.workspace).resolve()
    if args.probe_python:
        info = probe_python(args.probe_python)
        print(json.dumps(dict(supported=bool(info), info=info)))
        return 0 if info else 1
    if args.check_runtime:
        print(json.dumps(runtime_plan(workspace)))
        return 0
    try:
        return run_app(workspace, app_args, args.wait)
    except Exception as exc:
        print('启动失败：'+str(exc), flush=True)
        print('日志位置：'+str(workspace/'.launcher-state/startup.log'), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
