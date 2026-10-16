"""Build the portable runtime: an embedded Python plus the Qt wheels it drives.

The result needs nothing pre-installed on the target machine — no system Python,
no .NET Desktop Runtime and no WebView2 — because QtWebEngine renders the MOOC
pages and the interpreter travels with the program.

Output (all under .build/, which is not tracked):

    .build/runtime-bundle/python/python.exe        the interpreter
    .build/runtime-bundle/python/Lib/site-packages PySide6, shiboken6
    .build/runtime-bundle/bundle.json              versions and SHA-256
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import urllib.request
import zipfile

# This file lives in tools/, so the folder holding paths.py is one level up.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import paths

ROOT = paths.PROGRAM_ROOT
PYTHON_VERSION = '3.13.9'
PYTHON_URL = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.zip'
# PySide6-Addons is required even though the program never imports it: Qt's own
# libraries are split across wheels, and QtWebEngineCore links against DLLs that
# only Addons ships (Qt6Positioning.dll, Qt6WebChannel.dll and others). Only its
# Python bindings are dropped; every Qt library is kept.
PACKAGES = {'PySide6-Essentials': '6.12.0', 'PySide6-Addons': '6.12.0',
            'shiboken6': '6.12.0', 'PySide6-WebEngine': '6.12.0.140'}

# Parts of a full CPython distribution a shipped program never uses.
PYTHON_DROP = ('Lib/test', 'Lib/idlelib', 'Lib/turtledemo', 'Lib/lib2to3', 'include', 'Doc',
               'Tools', 'Scripts', 'libs',
               # The portable package never installs anything, so pip and its
               # bundled wheels are dead weight.
               'Lib/ensurepip', 'Lib/site-packages/pip', 'Lib/site-packages/pip-*.dist-info')
# Optional Qt modules the program does not import. Only their Python bindings
# (.pyd) and type stubs (.pyi) are removed: the matching Qt6*.dll files stay,
# because other modules link against them — QtWebEngineCore, for example, needs
# Qt6Positioning.dll and Qt6WebChannel.dll even though the program never opens
# those bindings. Trimming the DLLs as well breaks the embedded browser.
QT_DROP_BINDINGS = ('Qt3DAnimation', 'Qt3DCore', 'Qt3DExtras', 'Qt3DInput', 'Qt3DLogic', 'Qt3DRender',
                    'QtBluetooth', 'QtCharts', 'QtDataVisualization', 'QtDesigner', 'QtGraphs',
                    'QtGraphsWidgets', 'QtHelp', 'QtHttpServer', 'QtLocation', 'QtMultimedia',
                    'QtMultimediaWidgets', 'QtNetworkAuth', 'QtNfc', 'QtQuick3D', 'QtRemoteObjects',
                    'QtScxml', 'QtSensors', 'QtSerialPort', 'QtSpatialAudio', 'QtSql', 'QtStateMachine',
                    'QtTest', 'QtUiTools', 'QtWebSockets')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def download(url, target, description):
    target = Path(target)
    if target.is_file():
        print(f'  reusing {target.name}', flush=True)
        return target
    print(f'  downloading {description}', flush=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'MoocReminder-bundle'})
    with urllib.request.urlopen(request, timeout=120) as response, target.open('wb') as output:
        shutil.copyfileobj(response, output, 1024 * 1024)
    return target


def wheel_url(name, version):
    with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as response:
        metadata = json.load(response)
    wheels = [item for item in metadata['urls'] if item['filename'].endswith('win_amd64.whl')]
    if not wheels:
        raise RuntimeError(f'{name} {version} has no win_amd64 wheel')
    return wheels[0]


def safe_members(archive):
    """Reject entries that would escape the destination folder."""
    for name in archive.namelist():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name:
            raise RuntimeError('Unsafe archive entry: ' + name)
        yield name


def extract_python(archive, target):
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    for relative in PYTHON_DROP:
        for match in target.glob(relative):
            if match.is_dir():
                shutil.rmtree(match, ignore_errors=True)
            else:
                match.unlink(missing_ok=True)
    for stray in target.glob('*.pdb'):
        stray.unlink(missing_ok=True)


def extract_wheel(wheel, site_packages):
    with zipfile.ZipFile(wheel) as archive:
        for name in safe_members(archive):
            if name.endswith('/'):
                continue
            output = site_packages/Path(*PurePosixPath(name).parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(archive.read(name))


def trim_qt(site_packages):
    """Drop unused Python bindings; every Qt library stays, because the Qt
    libraries are shared and trimming them breaks QtWebEngine."""
    package = site_packages/'PySide6'
    if not package.is_dir():
        return
    for name in QT_DROP_BINDINGS:
        for suffix in ('.pyd', '.pyi'):
            (package/(name + suffix)).unlink(missing_ok=True)


def verify_qt(site_packages):
    """Fail the build when a library the embedded browser needs is missing."""
    package = site_packages/'PySide6'
    required = ['QtCore.pyd', 'QtGui.pyd', 'QtWidgets.pyd', 'QtWebEngineCore.pyd',
                'QtWebEngineWidgets.pyd', 'QtWebEngineProcess.exe',
                'Qt6WebEngineCore.dll', 'Qt6Positioning.dll', 'Qt6WebChannel.dll']
    missing = [name for name in required if not (package/name).is_file()]
    if missing:
        raise RuntimeError('The runtime bundle is incomplete, missing: ' + ', '.join(missing))


def build():
    # Downloads are build inputs; only the unpacked tree and the manifest remain.
    with tempfile.TemporaryDirectory(prefix='moocreminder-build-') as scratch:
        cache = Path(scratch)
        bundle = ROOT/'.build/runtime-bundle'
        manifest = bundle/'bundle.json'
        manifest.unlink(missing_ok=True)  # no completed marker while preparing
        shutil.rmtree(bundle, ignore_errors=True)
        python_dir = bundle/'python'
        site_packages = python_dir/'Lib/site-packages'
        site_packages.mkdir(parents=True, exist_ok=True)

        python_zip = download(PYTHON_URL, cache/f'python-{PYTHON_VERSION}-amd64.zip', f'CPython {PYTHON_VERSION}')
        extract_python(python_zip, python_dir)

        packages = []
        for name, version in PACKAGES.items():
            item = wheel_url(name, version)
            wheel = download(item['url'], cache/item['filename'], item['filename'])
            if digest(wheel) != item['digests']['sha256']:
                raise RuntimeError('Official wheel checksum did not match: ' + item['filename'])
            extract_wheel(wheel, site_packages)
            packages.append(dict(package=name, version=version, filename=item['filename'],
                                 sha256=item['digests']['sha256']))
        trim_qt(site_packages)
        verify_qt(site_packages)

        major, minor = PYTHON_VERSION.split('.')[:2]
        manifest.write_text(json.dumps(dict(
            platform='win_amd64', python_version=PYTHON_VERSION, python_tags=[f'cp{major}{minor}'],
            python_sha256=digest(python_zip), packages=packages), indent=2), encoding='utf-8')

    size = sum(path.stat().st_size for path in bundle.rglob('*') if path.is_file())
    print(f'Portable runtime ready: {round(size/1024/1024, 1)} MiB unpacked', flush=True)
    return bundle


if __name__ == '__main__':
    build()
