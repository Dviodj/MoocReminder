"""Load Microsoft's native WebView2 using the installed .NET Desktop runtime."""
import argparse
import io
import json
import os
from pathlib import Path
import re
import threading
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
SDK_VERSION = '1.0.4258.31'
SDK_MEMBERS = {
    'lib_manual/netcoreapp3.0/Microsoft.Web.WebView2.Core.dll': 'Microsoft.Web.WebView2.Core.dll',
    'lib_manual/netcoreapp3.0/Microsoft.Web.WebView2.WinForms.dll': 'Microsoft.Web.WebView2.WinForms.dll',
    'runtimes/win-x64/native/WebView2Loader.dll': 'WebView2Loader.dll',
}
_lock = threading.Lock()
_initialized = False
_dll_handles = []


def select_runtime(roots):
    available = []
    for root in map(Path, roots):
        desktop = root/'Microsoft.WindowsDesktop.App'
        if not desktop.is_dir():
            continue
        for version in desktop.iterdir():
            if not version.is_dir() or not re.fullmatch(r'\d+\.\d+\.\d+', version.name):
                continue
            numeric = tuple(map(int, version.name.split('.')))
            if numeric[0] >= 8 and (root/'Microsoft.NETCore.App'/version.name).is_dir():
                available.append((numeric, root, version.name))
    if not available:
        raise RuntimeError('未找到匹配的 64 位 .NET Desktop Runtime（8.0 或以上），请安装后重新启动。')
    _, root, version = max(available, key=lambda entry: entry[0])
    return root, version


def runtime_roots():
    roots = []
    for value in (os.environ.get('DOTNET_ROOT_X64'), os.environ.get('DOTNET_ROOT')):
        if value:
            roots.append(Path(value)/'shared')
    roots.append(Path(os.environ.get('ProgramW6432', os.environ.get('ProgramFiles', 'C:/Program Files')))/'dotnet/shared')
    return roots


def extract_sdk(data, target):
    target = Path(target)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        try:
            assets = {name: archive.read(member) for member, name in SDK_MEMBERS.items()}
        except KeyError as exc:
            raise RuntimeError('WebView2 SDK 文件不完整。') from exc
    target.mkdir(parents=True, exist_ok=True)
    for name, content in assets.items():
        temporary = target/(name+'.tmp')
        temporary.write_bytes(content)
        temporary.replace(target/name)


def prepare_assets():
    target = ROOT/'.native-webview'
    if all((target/name).is_file() for name in SDK_MEMBERS.values()):
        return target
    url = f'https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/{SDK_VERSION}/microsoft.web.webview2.{SDK_VERSION}.nupkg'
    with urllib.request.urlopen(url, timeout=45) as response:
        extract_sdk(response.read(), target)
    return target


def initialize_webview():
    global _initialized
    with _lock:
        if _initialized:
            return
        shared, version = select_runtime(runtime_roots())
        sdk = ROOT/'.native-webview'
        if not all((sdk/name).is_file() for name in SDK_MEMBERS.values()):
            raise RuntimeError('原生网页组件未准备好，请通过“启动程序.bat”启动以安装组件。')
        state = ROOT/'.launcher-state'
        state.mkdir(parents=True, exist_ok=True)
        config = state/'webview.runtimeconfig.json'
        config.write_text(json.dumps({'runtimeOptions': {'frameworks': [
            {'name': 'Microsoft.NETCore.App', 'version': version},
            {'name': 'Microsoft.WindowsDesktop.App', 'version': version}]}}), encoding='utf-8')
        from pythonnet import load
        load('coreclr', runtime_config=str(config), dotnet_root=str(shared.parent))
        import clr
        if hasattr(os, 'add_dll_directory'):
            _dll_handles.append(os.add_dll_directory(str(sdk)))
        clr.AddReference('System.Windows.Forms')
        clr.AddReference(str(sdk/'Microsoft.Web.WebView2.Core.dll'))
        clr.AddReference(str(sdk/'Microsoft.Web.WebView2.WinForms.dll'))
        _initialized = True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        prepare_assets()
    initialize_webview()
    print('Windows 原生网页组件已就绪。')
