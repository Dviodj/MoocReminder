"""Source entry point compatible with Python 3.9, including runtime repair."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

# This file lives in src/system/, so the folder holding paths.py is one level up.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paths

from desktop_shortcut import (BACKGROUND_ARGUMENT, create_desktop_shortcut,
    desktop_shortcut_ready, owns_shortcut, remove_desktop_shortcut)
import autostart


def probe_output(args, cancelled, timeout, workspace=None):
    process = subprocess.Popen([str(a) for a in args],cwd=workspace,stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    until = time.monotonic()+timeout
    try:
        while process.poll() is None:
            if cancelled():
                raise InterruptedError('启动已取消')
            if time.monotonic() >= until:
                raise subprocess.TimeoutExpired(args,timeout)
            time.sleep(.025)
        return process.returncode, process.stdout.read().decode('utf-8')
    except BaseException:
        stop_started_app(process)
        raise
    finally:
        process.stdout.close()


def probe_python(executable, cancelled=None):
    if not executable:
        return None
    try:
        args = [str(executable), '-c',
            'import sys,struct,json; print(json.dumps(dict(version=list(sys.version_info[:3]),bits=struct.calcsize("P")*8,base_python=getattr(sys,"_base_executable",sys.executable))))']
        if cancelled is not None:
            code, output = probe_output(args,cancelled,5)
        else:
            result = subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=5,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            code, output = result.returncode, result.stdout
        data = json.loads(output)
        if code == 0 and tuple(data['version'][:2]) >= (3, 11) and data['bits'] == 64:
            return data
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        pass
    return None


def bundled_python(workspace):
    """The interpreter shipped inside the package, if it is present."""
    return artifact_root(workspace, 'runtime-bundle', 'python', 'python.exe')


def candidates(workspace):
    # The interpreter shipped in the package comes first: it is the only one the
    # release is verified against, and it needs nothing installed on the machine.
    yield bundled_python(workspace)
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


def runtime_plan(workspace, cancelled=None):
    seen = set()
    for candidate in candidates(workspace):
        if not candidate:
            continue
        candidate = Path(candidate).resolve()
        if str(candidate).lower() in seen:
            continue
        seen.add(str(candidate).lower())
        info = probe_python(candidate,cancelled=cancelled)
        if info:
            return dict(python=str(candidate), base_python=info['base_python'], version=info['version'],
                venv_ready=candidate == (workspace/'.venv/Scripts/python.exe').resolve() or bool(probe_python(workspace/'.venv/Scripts/python.exe',cancelled=cancelled)))
    raise RuntimeError('未找到 Python 3.11 以上的 64 位运行环境；原生网页组件需要 Python 3.11 或更高版本。')


def artifact_root(workspace, *relative):
    """Locate a build artifact under workspace/.build first, then fall back to
    the pre-0.1.2 root layout (where the folder name kept its leading dot), so
    already published offline packages keep working. Paths resolve against the
    workspace, never the process CWD."""
    workspace = Path(workspace)
    relative = tuple(relative)
    current = workspace/'.build'/Path(*relative)
    candidates = [current, workspace/Path(*relative)]
    if relative and not str(relative[0]).startswith('.'):
        candidates.append(workspace/Path('.' + relative[0], *relative[1:]))
    return next((path for path in candidates if path.exists()), current)


# PowerShell snippets with {path} substituted. Reading the Zone.Identifier
# stream from Python is not possible: Windows denies deleting or rewriting it,
# and its text listing is localised. The path is inlined rather than passed as
# an argument because `powershell -Command` does not populate $args with the
# trailing parameters.
_BLOCKED_QUERY = """param([string]$Root)
Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { Get-Item -LiteralPath $_.FullName -Stream Zone.Identifier -ErrorAction SilentlyContinue } |
  ForEach-Object { $_.FullName }
"""

def blocked_files(workspace):
    """Files Windows still marks as downloaded from the internet.

    That mark is an alternate data stream named Zone.Identifier. Windows shows a
    publisher warning for such files and refuses to run the launcher scripts, so
    a fresh download appears broken for no visible reason.
    """
    if os.name != 'nt':
        return []
    listing, _ = _run_script(_BLOCKED_QUERY, workspace)
    return [Path(line.strip()) for line in listing.splitlines()
            if line.strip() and Path(line.strip()).is_file()]


def _run_script(script, workspace):
    """Run a PowerShell script file against a workspace path.

    The script is written to a file rather than passed with -Command: the
    command line mangles paths containing quotes and Windows caps its length.
    """
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    handle = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8-sig', suffix='.ps1', delete=False) as stream:
            handle = Path(stream.name)
            stream.write(script)
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-File', str(handle), str(Path(workspace))],
            capture_output=True, timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        text = result.stdout.decode('utf-8', errors='replace')
    except (OSError, subprocess.TimeoutExpired):
        return '', 0
    finally:
        if handle is not None:
            try:
                handle.unlink()
            except OSError:
                pass
    return text, len([line for line in text.splitlines() if line.strip()])


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


def cache_fingerprint(workspace, python):
    workspace, python = Path(workspace), Path(python)
    prefix = python.parent.parent if python.parent.name.lower() == 'scripts' else python.parent
    bundle = artifact_root(workspace, 'runtime-bundle')
    site = bundle/'common' if (bundle/'bundle.json').is_file() else prefix/'Lib/site-packages'
    required = [python, workspace/'requirements-runtime.txt',
        paths.script('src/system/qt_bootstrap.py', workspace), site/'PySide6/QtCore.pyd',
        site/'PySide6/QtWidgets.pyd',
        site/'PySide6/QtWebEngineCore.pyd', site/'PySide6/QtWebEngineWidgets.pyd',
        site/'PySide6/QtWebEngineProcess.exe']
    try:
        stamps = [(str(file.resolve()), file.stat().st_size, file.stat().st_mtime_ns) for file in required]
        optional = [prefix/'pyvenv.cfg', paths.launcher_script(workspace), bundle/'bundle.json']
        optional += list(site.glob('pyside6_essentials-*.dist-info/METADATA'))
        optional += list(site.glob('pyside6_webengine-*.dist-info/METADATA'))
        stamps += [(str(file.resolve()), file.stat().st_size, file.stat().st_mtime_ns) for file in optional if file.exists()]
        payload = [stamps, os.environ.get('MOOC_PYTHON',''),
            hashlib.sha256((workspace/'requirements-runtime.txt').read_bytes()).hexdigest()]
        return hashlib.sha256(json.dumps(payload).encode('utf-8')).hexdigest()
    except OSError:
        return None


def probe_app_runtime(python, workspace, cancelled=None):
    if not Path(python).is_file():
        return None
    expected = {}
    try:
        for line in (workspace/'requirements-runtime.txt').read_text(encoding='utf-8-sig').splitlines():
            if '==' in line and not line.startswith('#'):
                name, version = line.strip().split('==',1)
                expected[name] = version
        code = ('import sys,struct,json,importlib.metadata as m; '
            + paths.bootstrap_code() +
            'from qt_bootstrap import prepare_qt; prepare_qt(' + repr(str(Path(workspace)/'data')) + '); '
            'from PySide6 import QtCore,QtWidgets,QtWebEngineCore,QtWebEngineWidgets; '
            'print(json.dumps(dict(version=list(sys.version_info[:3]),bits=struct.calcsize("P")*8,'
            'base_python=getattr(sys,"_base_executable",sys.executable),deps={n:m.version(n) for n in '+repr(list(expected))+'})))')
        if cancelled is not None:
            status, output = probe_output([str(python),'-c',code],cancelled,8,workspace)
        else:
            result = subprocess.run([str(python),'-c',code],cwd=workspace,capture_output=True,
                text=True,encoding='utf-8',timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            status, output = result.returncode, result.stdout
        info = json.loads(output)
        if status == 0 and info['bits'] == 64 and tuple(info['version'][:2]) >= (3,11) and info['deps'] == expected:
            return info
    except (OSError,ValueError,KeyError,subprocess.TimeoutExpired):
        pass
    return None


def application_runtime(workspace, plan, cancelled=None):
    own = workspace/'.venv/Scripts/python.exe'
    if plan['venv_ready']:
        return own, False
    options = []
    try:
        options.append(json.loads((workspace/'.launcher-state/runtime.json').read_text())['app_python'])
    except (OSError,ValueError,KeyError):
        pass
    options += [plan['python'], workspace.parent/'mooc_deadline_reminder/.venv/Scripts/python.exe']
    for candidate in options:
        info = probe_app_runtime(Path(candidate), workspace,cancelled=cancelled)
        if info:
            plan['app_info'] = info
            return Path(candidate), True
    return own, False


def run_command(args, workspace, log, cancelled=lambda:False, timeout=120, env=None):
    if cancelled():
        raise InterruptedError('启动已取消')
    process = subprocess.Popen([str(a) for a in args],cwd=workspace,env=env,
        stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    until = time.monotonic()+timeout
    try:
        while process.poll() is None:
            if cancelled():
                raise InterruptedError('启动已取消')
            if time.monotonic() >= until:
                raise TimeoutError('准备运行文件超时，请查看启动日志。')
            time.sleep(.05)
        return process.returncode
    except BaseException:
        stop_started_app(process)
        raise


def bundled_environment(workspace, version):
    bundle = artifact_root(workspace, 'runtime-bundle')
    tag = 'cp'+str(version[0])+str(version[1])
    try:
        manifest = json.loads((bundle/'bundle.json').read_text(encoding='utf-8'))
        if manifest['platform'] != 'win_amd64' or tag not in manifest.get('python_tags', [tag]):
            return None
        # The interpreter ships inside the bundle, with the Qt wheels next to it.
        python = bundle/'python'
        site = python/'Lib/site-packages'
        if (not (python/'python.exe').is_file()
                or not (site/'PySide6/QtWidgets.pyd').is_file()
                or not (site/'PySide6/QtWebEngineCore.pyd').is_file()
                or not (site/'PySide6/QtWebEngineProcess.exe').is_file()):
            return None
        paths = [str(site)]
        if os.environ.get('PYTHONPATH'):
            paths.append(os.environ['PYTHONPATH'])
        # A machine may have another Qt distribution (for example Anaconda).
        # Explicit paths keep the bundled Qt DLLs and platform plugins together.
        return dict(os.environ, PYTHONPATH=os.pathsep.join(paths), PYTHONIOENCODING='utf-8',
            QT_PLUGIN_PATH=str(site/'PySide6/plugins'),
            QT_QPA_PLATFORM_PLUGIN_PATH=str(site/'PySide6/plugins/platforms'))
    except (OSError, ValueError, KeyError, TypeError):
        return None


@contextmanager
def startup_lock(state):
    state.mkdir(parents=True,exist_ok=True)
    handle = (state/'startup.lock').open('a+b')
    locked = False
    try:
        if handle.seek(0,os.SEEK_END) == 0:
            handle.write(b'\0');handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise RuntimeError('另一个启动窗口正在准备运行环境，请等待它完成后再打开。') from exc
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        handle.close()


def run_app(workspace, app_args, wait=False, progress=None, cancelled=None, confirm=None):
    workspace=Path(workspace).resolve()
    if cancelled and cancelled():
        raise InterruptedError('启动已取消')
    with startup_lock(workspace/'.launcher-state'):
        return _run_app(workspace,app_args,wait,progress,cancelled,confirm)


def _run_app(workspace, app_args, wait=False, progress=None, cancelled=None, confirm=None):
    workspace = Path(workspace).resolve()
    cancelled = cancelled or (lambda:False)
    if BACKGROUND_ARGUMENT in app_args:
        # Started by Windows at sign-in: there is nobody to answer a question,
        # so never ask one and never create an entry the user did not request.
        # Repairing a shortcut they already agreed to stays silent and allowed.
        confirm = lambda question: False
    def update(percent, phase, detail='', indeterminate=False, kind='progress'):
        if cancelled():
            raise InterruptedError('启动已取消')
        if progress:
            progress(dict(percent=percent,phase=phase,detail=detail,indeterminate=indeterminate,kind=kind))
    update(4,'检查电脑环境','正在准备 MoocReminder。')
    state = workspace/'.launcher-state'
    state.mkdir(parents=True, exist_ok=True)
    temp = workspace/'.build/temp';temp.mkdir(parents=True,exist_ok=True)
    preparation_env = dict(os.environ,TEMP=str(temp),TMP=str(temp),PYTHONIOENCODING='utf-8')
    log_path = state/'startup.log'
    with log_path.open('a', encoding='utf-8') as log:
        log.write('\n=== '+datetime.now().isoformat()+' ===\n')
        cache_path = state/'ready-runtime.json'
        warm = False
        try:
            cache = json.loads(cache_path.read_text(encoding='utf-8'))
            python = Path(cache['python'])
            signature = cache_fingerprint(workspace,python)
            warm = bool(signature and signature == cache['signature'] and tuple(cache['version'][:2]) >= (3,11))
        except (OSError,ValueError,KeyError,TypeError):
            pass
        if warm:
            plan = dict(python=str(python),base_python=cache['base_python'],version=cache['version'],venv_ready=True)
        else:
            plan = runtime_plan(workspace,cancelled=cancelled)
        bundle_env = bundled_environment(workspace,plan['version'])
        if artifact_root(workspace, 'runtime-bundle', 'bundle.json').is_file() and bundle_env is None:
            raise RuntimeError('离线运行组件不完整，或 Python 版本不受支持。请完整解压运行包并使用 64 位 Python 3.11–3.15。')
        if bundle_env:
            python = Path(plan['python'])
            reused = True
        elif not warm:
            python, reused = application_runtime(workspace, plan,cancelled=cancelled)
        log.write(json.dumps(plan, ensure_ascii=False)+'\n')
        log.flush()
        if not warm and not plan['venv_ready'] and not reused:
            update(18,'准备启动组件','首次准备运行环境，完成后会保留在本机。',True)
            print('正在修复程序运行环境…', flush=True)
            result = run_command([plan['python'],'-m','venv',workspace/'.venv'],workspace,log,cancelled,env=preparation_env)
            if result:
                raise RuntimeError('运行环境修复失败，详情已写入启动日志。')
        update(35,'准备运行文件','运行组件已随包准备，无需下载和安装。' if bundle_env else '已复用现有组件，无需重新下载。' if warm or (not warm and reused) else '正在检查运行文件。')
        info = plan.get('app_info',plan)
        (state/'runtime.json').write_text(json.dumps({'python':str(Path(info['base_python']).resolve()),'app_python':str(python)}), encoding='utf-8')
        expected = dict(line.strip().split('==',1) for line in (workspace/'requirements-runtime.txt').read_text(encoding='utf-8-sig').splitlines() if '==' in line and not line.startswith('#')) if (workspace/'requirements-runtime.txt').exists() else {}
        check = (paths.bootstrap_code() +
            'from qt_bootstrap import prepare_qt; prepare_qt(' + repr(str(Path(workspace)/'data')) + '); '
            'from PySide6 import QtCore,QtWidgets,QtWebEngineCore,QtWebEngineWidgets; '
            'import importlib.metadata as m; assert {n:m.version(n) for n in '+repr(list(expected))+'} == '+repr(expected))
        if not warm and (not reused or bundle_env) and run_command([python,'-c',check],workspace,log,cancelled,timeout=25,env=bundle_env or preparation_env):
            if bundle_env:
                raise RuntimeError('随包运行组件未能加载，请完整重新解压运行包，详情见启动日志。')
            update(40,'下载首次运行文件','第一次需要下载界面组件。完成后再次启动会直接复用。',True)
            print('正在安装或修复桌面组件…', flush=True)
            temp = workspace/'.build/temp'
            temp.mkdir(parents=True, exist_ok=True)
            install_env = dict(os.environ, TEMP=str(temp), TMP=str(temp))
            result = run_command([python,'-m','pip','install','--no-cache-dir','--no-build-isolation',
                '--only-binary=:all:','--timeout','15','--retries','1','--progress-bar','off','-r','requirements-runtime.txt'],
                workspace,log,cancelled,timeout=600,env=install_env)
            if result or run_command([python,'-c',check],workspace,log,cancelled,timeout=25,env=preparation_env):
                raise RuntimeError('桌面组件未能加载，详情已写入启动日志。')
        update(65,'准备网页组件','已验证的组件会在后续启动时直接复用。',not warm)
        update(78,'创建桌面入口','以后可以直接从桌面打开。')
        try:
            want_link = True
            if not desktop_shortcut_ready(workspace,python):
                # Only ask when there is nothing of ours to repair: a stale
                # shortcut from a previous location is fixed silently, because
                # the user already agreed to having one.
                if owns_shortcut(workspace):
                    update(78,'更新桌面入口','程序位置已变化，正在修正快捷方式。')
                else:
                    want_link = confirm('要在桌面上创建 MoocReminder 快捷方式吗？\n\n'
                        '以后可以从桌面直接打开，不需要每次进文件夹。') if confirm else True
            if want_link:
                create_desktop_shortcut(workspace,python=python,cancelled=cancelled)
            else:
                if owns_shortcut(workspace):
                    # Only retract a shortcut of ours.  A same-named one
                    # belonging to another program must be left alone, and
                    # trying to delete it would raise a warning about a
                    # shortcut the user just refused.
                    remove_desktop_shortcut(workspace)
                update(78,'未创建桌面入口','需要时可以从程序文件夹打开。')
        except InterruptedError:
            raise
        except Exception as exc:
            log.write('快捷方式：'+str(exc)+'\n');log.flush()
            update(78,'桌面入口暂未创建','可以继续从文件夹打开；主程序正在启动。',kind='warning')
        try:
            # Only ever repair.  Turning autostart on is the user's decision, made
            # from the prompt on the overview page or the box in the settings, and
            # asking here would put a second question in front of them at startup.
            if autostart.enabled(workspace) and not autostart.ready(workspace, python):
                update(84,'修正开机启动','程序位置已变化，正在修正开机启动项。')
                autostart.enable(workspace, python=python, cancelled=cancelled)
        except InterruptedError:
            raise
        except Exception as exc:
            log.write('开机启动：'+str(exc)+'\n');log.flush()
            update(84,'开机启动项未修正','不影响本次使用；可在设置里重新勾选。',kind='warning')
        update(88,'打开学习空间','正在启动主窗口。')
        ready = state/('ready-'+uuid.uuid4().hex+'.json')
        gui_python = python.with_name('pythonw.exe')
        env = dict(bundle_env or os.environ, PYTHONIOENCODING='utf-8')
        env.pop('__PYVENV_LAUNCHER__', None)
        process = subprocess.Popen([str(gui_python), str(paths.gui_script(workspace)), '--startup-report', str(ready)]+app_args,
            cwd=workspace, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            until = time.monotonic()+20
            while time.monotonic() < until:
                if cancelled():
                    raise InterruptedError('启动已取消')
                if ready.exists():
                    report = json.loads(ready.read_text(encoding='utf-8'))
                    # A background start has no window on purpose, and a second
                    # launch hands over to the copy that is already running;
                    # both are successes, not a window that failed to appear.
                    if report.get('ready') or report.get('native_window'):
                        if report.get('already_running'):
                            log.write('已有实例在运行，已切换到该窗口\n');log.flush()
                            update(100,'已在运行','MoocReminder 已经打开，已切换到那个窗口。')
                            return 0
                        log.write('主窗口启动成功\n' if not report.get('background') else '已在后台启动\n');log.flush()
                        signature = cache_fingerprint(workspace,python)
                        if signature:
                            temporary = state/('ready-runtime-'+uuid.uuid4().hex+'.tmp')
                            try:
                                temporary.write_text(json.dumps(dict(python=str(python),base_python=info['base_python'],
                                    version=info.get('version',[3,11,0]),signature=signature)),encoding='utf-8')
                                temporary.replace(cache_path)
                            except OSError as exc:
                                log.write('运行缓存未保存：'+str(exc)+'\n');log.flush()
                            finally:
                                try:temporary.unlink(missing_ok=True)
                                except OSError:pass
                        if report.get('background'):
                            update(100,'已在后台运行','MoocReminder 正在托盘里运行，提醒照常。')
                        else:
                            update(100,'准备就绪','主窗口已打开。')
                        if wait:
                            while process.poll() is None:
                                if cancelled():
                                    raise InterruptedError('启动已取消')
                                time.sleep(.05)
                            return process.returncode
                        return 0
                if process.poll() is not None:
                    raise RuntimeError('程序在主窗口出现前退出，详情已写入启动日志。')
                time.sleep(0.1)
            raise RuntimeError('未收到主窗口启动确认，详情已写入启动日志。')
        except BaseException:
            try:cache_path.unlink(missing_ok=True)
            except OSError:pass
            stop_started_app(process)
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', default=str(paths.PROGRAM_ROOT))
    parser.add_argument('--check-runtime', action='store_true')
    parser.add_argument('--probe-python')
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--unblock', action='store_true')
    args, app_args = parser.parse_known_args()
    workspace = Path(args.workspace).resolve()
    if args.unblock:
        blocked = blocked_files(workspace)
        if not blocked:
            print('没有发现被 Windows 标记为“来自网络”的文件。')
            return 0
        print(f'发现 {len(blocked)} 个文件带有“来自网络”的标记：')
        for path in blocked[:10]:
            print('  ' + str(path))
        if len(blocked) > 10:
            print(f'  …以及另外 {len(blocked) - 10} 个')
        print('请重新解压发布包，或右键这些文件→属性→勾选“解除锁定”。')
        return 0
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
