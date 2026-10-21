"""Updating in place: fetch the published package and swap the program files.

The program is a folder, not an installer, so updating it means replacing that
folder's files while keeping what belongs to the user.  Doing that from inside
the running program is the risky part -- the executable that would have to be
deleted is the one currently executing -- so the work is split in two:

* **Here**: find the published offline package, download it, verify it, unpack
  it to a staging folder, and copy the *new* parts into a prepared folder beside
  the program.  Everything up to this point is reversible: a failure at any step
  deletes only what was just created and leaves the running program untouched.
* **In a short script**: close the program, move the few folders that change
  wholesale (``src``, ``assets``, the runtime bundle) out of the way, copy the
  prepared files in, and -- if anything went wrong -- put them back.  That part
  is a plain script run by the bundled interpreter, so it cannot be blocked by
  the program the way an in-process rename would be.

What is never touched: the user's ``data`` folder, ``.launcher-state``, their
credentials, the program folder's own ``.venv`` (an environment built on this
machine) and anything else the release does not ship.

Why one-click rather than "download and unzip it yourself": asked, the
publisher chose this, accepting that a source checkout may be overwritten too --
the new files come from the published package, and ``data`` still survives.
"""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile

try:  # imported both as ``self_update`` (tests, the switch script) and as part of ``system``
    import paths
except ImportError:  # pragma: no cover - only the packaged layout takes this branch
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import paths

try:
    from version import APP_NAME, VERSION
except ImportError:  # pragma: no cover - version.py always ships
    APP_NAME, VERSION = 'MoocReminder', 'v0.0.0'

#: Which artifact is the program itself. The source package cannot be installed
#: over an existing install: it has no runtime bundle and needs a Python of its
#: own, so a one-click update always means the offline package.
ASSET_PATTERN = re.compile(r'^' + re.escape(APP_NAME) + r'-(?P<tag>v?\d+\.\d+\.\d+)-offline-x64\.zip$')
OFFLINE_SUFFIXES = ('-offline-x64.zip',)
EXPANDED_ASSETS = '/releases/expanded_assets/'
TIMEOUT_SECONDS = 30.0
USER_AGENT = APP_NAME
CHUNK = 256 * 1024
#: Left in place so a failed switch can be undone by hand; pruned on the next run.
WORK_FOLDER = '.update'
#: Names that are replaced wholesale. Renaming a folder is instant and either
#: works or does not, which is not true of overwriting files one by one while an
#: antivirus or a stuck handle holds one of them.
SWAPPED = ('src', 'assets', 'runtime-bundle')
#: Top-level names the release ships that are also the user's, and must survive.
PROTECTED = ('data', '.launcher-state', '.venv', '.git', 'email_sender.json',
             'usage_report.json', 'web-profiles',
             # A source checkout starts from the interpreter the .vbs and
             # launch.bat name, which lives in .build; swapping anything here
             # would leave that folder holding the old program.
             '.build')


class UpdateError(Exception):
    """Something stopped the update; the message is what the user is shown."""


# --------------------------------------------------------------------------
# Finding the package


def asset_name(tag):
    """The offline package's file name for a version, e.g. ``v0.2.3``."""
    tag = str(tag or '').strip()
    if not tag:
        raise UpdateError('不知道要下载哪个版本。')
    if not tag.startswith('v'):
        tag = 'v' + tag
    return f'{APP_NAME}-{tag}-offline-x64.zip'


def offline_assets(page):
    """Every offline package linked from a rendered release page.

    Returns ``[{name, url, tag}]`` newest-looking first (the page lists the
    current release's assets before anything else). GitHub serves them from
    ``/releases/download/<tag>/<name>``; the expanded-assets fragment holds the
    same links, so either copy of the markup is enough.
    """
    if isinstance(page, (bytes, bytearray)):
        page = page.decode('utf-8', 'replace')
    if not isinstance(page, str):
        return []
    found, seen = [], set()
    for href in re.findall(r'href="([^"]+)"', page):
        href = href.replace('&amp;', '&')
        name = href.split('?')[0].rsplit('/', 1)[-1]
        match = ASSET_PATTERN.match(name)
        if not match or name in seen:
            continue
        if '/releases/download/' not in href:
            continue
        seen.add(name)
        if href.startswith('/'):
            href = 'https://github.com' + href
        if not href.startswith('https://'):
            continue
        found.append(dict(name=name, url=href, tag=match.group('tag')))
    return found


def asset_for(page, tag):
    """The offline package to install for ``tag``, or ``None``.

    The download page and the expanded-assets fragment are fetched separately in
    the UI, so both may be passed; the name has to match the version exactly, or
    an update would install something other than what was announced.
    """
    wanted = asset_name(tag)
    for entry in offline_assets(page):
        if entry['name'] == wanted:
            return entry
    return None


# --------------------------------------------------------------------------
# Downloading and verifying


def _request(url, timeout):
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/octet-stream'}
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def download(asset, folder, progress=None, opener=None):
    """Fetch one asset into ``folder``; returns ``(path, sha256)``.

    The partial file keeps a ``.part`` name so an interrupted download can never
    be mistaken for a finished one. The digest is computed on the way through
    rather than in a second pass over 250 MB.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / asset['name']
    partial = target.with_name(target.name + '.part')
    digest = hashlib.sha256()
    total = 0
    expected = 0
    opener = opener or _request
    try:
        with opener(asset['url'], TIMEOUT_SECONDS) as response, partial.open('wb') as stream:
            expected = int(response.headers.get('Content-Length') or 0) if response.headers else 0
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                stream.write(block)
                digest.update(block)
                total += len(block)
                if progress:
                    progress(total, expected)
        if expected and total != expected:
            raise UpdateError('下载不完整，请稍后重试。')
        partial.replace(target)
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as error:
        partial.unlink(missing_ok=True)
        raise UpdateError('下载失败：' + str(error)) from error
    return target, digest.hexdigest()


#: A real offline package is about 250 MB, so a few kilobytes cannot be it.
#: Tests pass a smaller floor rather than fabricating a quarter of a gigabyte.
MINIMUM_PACKAGE_BYTES = 1_000_000


def verify(archive, minimum=MINIMUM_PACKAGE_BYTES):
    """Check that a downloaded file really is the release package.

    A captive portal or an error page saved under the right name would otherwise
    be unpacked and executed, so the file has to be a readable ZIP that contains
    the program it claims to.
    """
    archive = Path(archive)
    try:
        with zipfile.ZipFile(archive) as package:
            names = package.namelist()
            broken = package.testzip()
    except (OSError, zipfile.BadZipFile) as error:
        raise UpdateError('下载到的文件不是有效的压缩包，请稍后重试。') from error
    if broken is not None:
        raise UpdateError('下载到的压缩包已损坏，请稍后重试。')
    if minimum and archive.stat().st_size < minimum:
        raise UpdateError('下载到的文件太小，可能没有下载完整。')
    roots = {name.split('/', 1)[0] for name in names if '/' in name}
    if len(roots) != 1:
        raise UpdateError('压缩包的内容和预期不符，已停止安装。')
    root = roots.pop()
    if f'{root}/src/ui/qt_desktop.py' not in names or f'{root}/src/version.py' not in names:
        raise UpdateError('压缩包里没有 MoocReminder 的程序文件，已停止安装。')
    return root


def unpacked_version(archive, minimum=MINIMUM_PACKAGE_BYTES):
    """The version recorded inside a package, read without unpacking it."""
    root = verify(archive, minimum)
    with zipfile.ZipFile(archive) as package:
        text = package.read(f'{root}/src/version.py').decode('utf-8', 'replace')
    found = re.search(r"^VERSION\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
    return found.group(1) if found else ''


def expand(archive, folder, minimum=MINIMUM_PACKAGE_BYTES):
    """Unpack a verified package into ``folder``; returns the folder holding it."""
    folder = Path(folder)
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    root = verify(archive, minimum)
    with zipfile.ZipFile(archive) as package:
        for name in package.namelist():
            relative = name.split('/', 1)[1] if '/' in name else ''
            if not relative or relative.endswith('/'):
                continue
            # The archive is the publisher's, but it is still data from the
            # network: a '../' in a name would write outside the staging folder.
            target = (folder / relative).resolve()
            if folder.resolve() not in target.parents:
                raise UpdateError('压缩包里有可疑的路径，已停止安装。')
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(name) as source, target.open('wb') as stream:
                shutil.copyfileobj(source, stream)
    return folder


# --------------------------------------------------------------------------
# Preparing the switch


def shipped_names(root):
    """Top-level names a package ships, which are therefore replaceable."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(child.name for child in root.iterdir() if not child.name.startswith('.'))


def plan(workspace, staged):
    """What the switch will do, as a dict the UI and the script both use.

    ``copy`` holds every shipped name that is neither replaced wholesale nor the
    user's, which is what keeps a hand-placed extra file from being deleted.
    A ``preexisting`` flag records whether the name was there before, so a file
    that the update introduces can be removed again if the switch is undone.
    """
    workspace, staged = Path(workspace), Path(staged)
    names = [name for name in shipped_names(staged) if name not in PROTECTED]
    swap = [name for name in SWAPPED if name in names]
    copy = [dict(name=name, preexisting=(workspace / name).exists())
            for name in names if name not in swap]
    return dict(workspace=str(workspace), staged=str(staged), swap=swap, copy=copy)


def prepare(workspace, archive, work=None, minimum=MINIMUM_PACKAGE_BYTES):
    """Unpack a package and stage everything the switch needs.

    Returns the plan. The staging folder lives inside the program folder so that
    the final move stays on one volume, where renaming is atomic.
    """
    workspace = Path(workspace)
    work = Path(work) if work else workspace / WORK_FOLDER
    stage = work / 'stage'
    root = expand(archive, stage, minimum)
    made = plan(workspace, root)
    # The new files are assembled next to the old ones, so the switch is a
    # sequence of renames rather than a sequence of long writes.
    ready = work / 'ready'
    if ready.exists():
        shutil.rmtree(ready, ignore_errors=True)
    ready.mkdir(parents=True)
    for name in made['swap'] + [item['name'] for item in made['copy']]:
        source, target = stage / name, ready / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    made['ready'] = str(ready)
    (work / 'plan.json').write_text(json.dumps(made, ensure_ascii=False, indent=1), encoding='utf-8')
    return made


# --------------------------------------------------------------------------
# The switch, run while the program is closing


def _next_to(workspace, name):
    """A free sibling folder name for something moved out of the way."""
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
    return Path(workspace) / f'.{name}-old-{stamp}'


def apply(plan_data, relaunch=()):
    """Swap the prepared files in. Returns ``(ok, error, replaced)``.

    Steps are ordered so that the expensive copy of small files happens while the
    switched folders are still moved aside -- not after, which would leave the
    program without ``src`` for as long as a copy takes. Any failure before the
    end undoes everything already done.
    """
    workspace = Path(plan_data['workspace'])
    ready = Path(plan_data['ready'])
    replaced = []   # (old path, stash path or None)
    written = []    # (path, existed before)
    try:
        for name in plan_data['swap']:
            current = workspace / name
            stash = None
            if current.exists():
                stash = _next_to(workspace, name)
                current.rename(stash)
            replaced.append((name, stash))
            # ``move`` merges a folder into an existing one, so a file left at
            # this name would be kept and the program would keep running on it.
            current_target = workspace / name
            if current_target.exists():
                if current_target.is_dir():
                    shutil.rmtree(current_target)
                else:
                    current_target.unlink()
            shutil.move(str(ready / name), str(current_target))
        for item in plan_data['copy']:
            source, target = ready / item['name'], workspace / item['name']
            written.append((target, item['preexisting']))
            _place(source, target)
    except Exception as error:
        undo(replaced, written)
        return False, str(error), replaced
    for _, stash in replaced:
        if stash is not None:
            shutil.rmtree(stash, ignore_errors=True)
    return True, '', replaced


def _place(source, target):
    """Put one prepared file where it belongs, whether or not a file is there.

    ``copy2`` onto an existing *folder* of the same name does not raise -- it
    copies into it, which would bury the new file one level too deep and report
    success. The name is therefore removed first and then written.
    """
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def undo(replaced, written):
    """Put the old program back, as far as that is still possible."""
    for target, existed in reversed(written):
        try:
            if existed:
                continue  # the old file is inside a stashed folder, restored below
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        except OSError:
            pass
    for name, stash in reversed(replaced):
        current = Path(stash).parent / name if stash is not None else None
        try:
            if current is not None and current.exists():
                shutil.rmtree(current, ignore_errors=True) if current.is_dir() else current.unlink()
            if stash is not None and Path(stash).exists():
                Path(stash).rename(current)
        except OSError:
            pass


#: The switch is run by a short script instead of by this process: the folder
#: being renamed holds the program currently running, and a separate interpreter
#: holds no handle on it.
#:
#: The script imports the new program's own copy of this module, so a switch
#: written by an older version reads its plan with the new rules. A module that
#: fails to import -- missing, or newer than the running interpreter -- is no
#: reason to leave the old program half moved, so the retry falls back to the
#: program that asked for the switch.
SCRIPT = '''import json, sys
sys.path.insert(0, {source})
from pathlib import Path
plan = json.loads(Path({plan_path}).read_text(encoding='utf-8'))
report = Path({report_path})
try:
    from self_update import apply
except Exception:
    sys.path.insert(0, {fallback})
    from self_update import apply
ok, error, _ = apply(plan)
report.write_text(json.dumps(dict(ok=ok, error=error)), encoding='utf-8')
'''


def script_command(workspace, plan_path, report_path):
    """The command line that performs the switch, using the program's interpreter."""
    source = Path(workspace) / 'src' / 'system'
    program = SCRIPT.format(source=repr(str(source)), plan_path=repr(str(plan_path)),
                            report_path=repr(str(report_path)),
                            fallback=repr(str(Path(__file__).resolve().parent)))
    python = Path(sys.executable)
    windowless = python.with_name('pythonw.exe')
    if windowless.is_file():
        python = windowless
    return [str(python), '-c', program]


def restart_command(workspace):
    """How to start the program again afterwards."""
    python = Path(sys.executable)
    windowless = python.with_name('pythonw.exe')
    if windowless.is_file():
        python = windowless
    return [str(python), str(paths.launcher_script(workspace)),
            '--workspace', str(workspace), '--background']


def work_folder(workspace):
    return Path(workspace) / WORK_FOLDER


def clean(workspace):
    """Remove what a previous, finished update left behind."""
    shutil.rmtree(work_folder(workspace), ignore_errors=True)
    for leftover in Path(workspace).glob('.*-old-*'):
        if leftover.is_dir():
            shutil.rmtree(leftover, ignore_errors=True)


def supports_in_place(workspace=None):
    """Whether an in-place update can work here at all.

    Needs a runtime bundle: that is what the offline package carries, and it is
    also the interpreter the switch and the restart use. A source checkout with
    only a .venv still gets the download, but the switch needs the bundle.
    """
    workspace = Path(workspace or paths.PROGRAM_ROOT)
    for candidate in (workspace / '.build' / 'runtime-bundle', workspace / 'runtime-bundle'):
        if (candidate / 'python' / 'python.exe').is_file():
            return True
    return False
