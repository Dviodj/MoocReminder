"""Build a source ZIP from tracked release files, excluding local account data."""
from pathlib import Path
import argparse
import json
import subprocess
import zipfile
from version import APP_NAME, VERSION

ROOT = Path(__file__).resolve().parent

# Text of the Qt licenses the bundled PySide6 and shiboken6 are used under.
# Their official wheels ship only LicenseRef-Qt-Commercial.txt, so LGPL-3.0
# requires that the text travels with the distribution instead.
LICENSE_TEXTS = ('licenses/LGPL-3.0.txt', 'licenses/GPL-3.0.txt')
# Release inputs that are added explicitly because they may not be staged yet.
UNTRACKED_RELEASE_INPUTS = ('THIRD-PARTY-NOTICES.md',)


def package(with_runtime=False):
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True)
    names = [name for name in result.stdout.decode('utf-8').split('\0') if name]
    if not names:
        raise RuntimeError('Stage the release source files before packaging.')
    forbidden = ('data/', '.venv/', '.launcher-state/', 'web-profiles/', 'releases/', '.runtime-bundle/', '.bundle-downloads/', '.build/', '.superpowers/')
    if any(name.startswith(forbidden) for name in names):
        raise RuntimeError('Local runtime or account data must not be tracked.')
    destination = ROOT/'.build/releases'
    destination.mkdir(parents=True, exist_ok=True)
    # Archive path -> source path. Names must never carry the local .build/
    # prefix: an extracted offline package has to look exactly like a published
    # one, or the launcher cannot find its runtime bundle.
    entries = {name: name for name in names}
    if with_runtime:
        bundle = next((path for path in (ROOT/'.build/runtime-bundle', ROOT/'.runtime-bundle') if path.is_dir()), ROOT/'.build/runtime-bundle')
        manifest=json.loads((bundle/'bundle.json').read_text(encoding='utf-8'))
        if manifest['platform']!='win_amd64':
            raise RuntimeError('Only a completed Windows x64 runtime bundle can be packaged.')
        # Every distributed component keeps its own license text next to it.
        component_licenses = {}
        for distribution in sorted(bundle.glob('common/*.dist-info')):
            component_licenses[distribution.name] = [file.name for folder in sorted(distribution.glob('licenses'))
                if folder.is_dir() for file in sorted(folder.rglob('*')) if file.is_file()]
        if not component_licenses:
            raise RuntimeError('The runtime bundle has no component license notices; refusing to publish it.')
        missing = [name for name, found in component_licenses.items() if not found]
        if missing:
            raise RuntimeError('Missing license text for: ' + ', '.join(missing))
        for required in LICENSE_TEXTS + UNTRACKED_RELEASE_INPUTS:
            if not (ROOT/required).is_file():
                raise RuntimeError('Missing release input: ' + required)
        for name in names:
            if name.startswith(('.build/', '.runtime-bundle/')):
                raise RuntimeError('Build output must not be a tracked release entry: ' + name)
        bundle_files = [file.relative_to(ROOT).as_posix() for file in bundle.rglob('*')
            if file.is_file() and '__pycache__' not in file.parts and file.suffix not in ('.pyc','.pyo')]
        # Strip the local .build/ folder: inside the archive the runtime bundle
        # must sit where the launcher looks for it, exactly as in a published
        # package, so an extracted offline package starts offline.
        bundle_prefix = bundle.relative_to(ROOT).as_posix() + '/'
        # License texts and the notices file are release inputs added here, so
        # they ship even before they are staged in git.
        for extra in LICENSE_TEXTS + UNTRACKED_RELEASE_INPUTS:
            if extra in entries:
                raise RuntimeError('Release input would overwrite a tracked file: ' + extra)
            entries[extra] = extra
        for name in bundle_files:
            # rglob yields paths relative to the bundle, so the bundle's own
            # folder name has to be restored on the archive path.
            target = (bundle_prefix + name) if not name.startswith(bundle_prefix) else name
            target = target[len('.build/'):] if target.startswith('.build/') else target
            if target in entries:
                raise RuntimeError('Duplicate release entries would overwrite each other: ' + target)
            entries[target] = name
    archive_path = destination/(f'{APP_NAME}-{VERSION}-'+('offline-x64.zip' if with_runtime else 'source.zip'))
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, source in sorted(entries.items()):
            archive.write(ROOT/source, f'{APP_NAME}-{VERSION}/{name}')
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Source archive verification failed.')
        for name, source in entries.items():
            assert archive.read(f'{APP_NAME}-{VERSION}/{name}') == (ROOT/source).read_bytes()
    print(f'Verified {len(entries)} release files: {archive_path.name}')
    return archive_path


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--with-runtime',action='store_true')
    package(parser.parse_args().with_runtime)
