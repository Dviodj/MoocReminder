"""Build a source ZIP from tracked release files, excluding local account data."""
from pathlib import Path
import argparse
import subprocess
import zipfile
from version import APP_NAME, VERSION

ROOT = Path(__file__).resolve().parent


def package(with_runtime=False):
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True)
    names = [name for name in result.stdout.decode('utf-8').split('\0') if name]
    if not names:
        raise RuntimeError('Stage the release source files before packaging.')
    forbidden = ('data/', '.venv/', '.launcher-state/', 'webview2-profiles/', 'releases/', '.runtime-bundle/', '.bundle-downloads/')
    if any(name.startswith(forbidden) for name in names):
        raise RuntimeError('Local runtime or account data must not be tracked.')
    destination = ROOT/'releases'
    destination.mkdir(exist_ok=True)
    if with_runtime:
        import json
        bundle=ROOT/'.runtime-bundle'
        manifest=json.loads((bundle/'bundle.json').read_text(encoding='utf-8'))
        if manifest['platform']!='win_amd64':
            raise RuntimeError('Only a completed Windows x64 runtime bundle can be packaged.')
        names += [file.relative_to(ROOT).as_posix() for file in bundle.rglob('*')
            if file.is_file() and '__pycache__' not in file.parts and file.suffix not in ('.pyc','.pyo')]
    archive_path = destination/(f'{APP_NAME}-{VERSION}-'+('offline-x64.zip' if with_runtime else 'source.zip'))
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(ROOT/name, f'{APP_NAME}-{VERSION}/{name}')
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Source archive verification failed.')
        for name in names:
            assert archive.read(f'{APP_NAME}-{VERSION}/{name}') == (ROOT/name).read_bytes()
    print(f'Verified {len(names)} release files: {archive_path.name}')
    return archive_path


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--with-runtime',action='store_true')
    package(parser.parse_args().with_runtime)
