"""Put the program's module folders on sys.path for every test module.

The modules live in ``src/`` and ``tools/`` rather than the repository root, so
a bare ``import core`` only works once those folders are importable.  Importing
this module does that, and also exposes the paths a test needs.

``unittest discover`` puts ``tests/`` itself on ``sys.path``, so
``import _bootstrap`` works without any packaging.
"""
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'

sys.path.insert(0, str(SRC))
import paths  # noqa: E402  (needs SRC on the path first)

#: The program root: where data/, assets/, .launcher-state/ and the
#: configuration files live.  Named ``ROOT`` for the tests that already use it.
PROGRAM_ROOT = paths.PROGRAM_ROOT


#: Entry points relative to the program root, for fixtures that build a folder
#: shaped like a real install.  A test that hard-codes ``source_launcher.py``
#: keeps passing while testing a layout the program no longer uses.
LAUNCHER_RELATIVE = Path(paths.LAUNCHER_ENTRY)
STARTUP_UI_RELATIVE = Path(paths.STARTUP_UI_ENTRY)


def entry(relative, workspace=None):
    """Absolute path of an entry point, e.g. ``entry('src/system/source_launcher.py')``.

    Pass ``workspace`` for a fixture that builds its own install folder.
    """
    return paths.script(relative, workspace)


def launcher_entry(workspace=None):
    """Absolute path of the launcher inside ``workspace`` (the real one by default)."""
    return paths.launcher_script(workspace)


def stub_launcher(workspace):
    """Create the launcher file a fixture needs, where the program looks for it."""
    target = Path(workspace) / LAUNCHER_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('# stub', encoding='utf-8')
    return target


_scratch = None


def scratch_folder(name):
    """An empty scratch directory under the gitignored ``.build/temp``.

    ``tempfile`` cannot be trusted for this: the program points TEMP at the
    checkout, and a confined run may refuse the usual location, in which case
    ``tempfile`` silently falls back to the working directory and litters the
    repository root with ``tmp*`` folders.
    """
    global _scratch
    if _scratch is None:
        _scratch = ROOT / '.build' / 'temp' / 'tests'
        _scratch.mkdir(parents=True, exist_ok=True)
    folder = _scratch / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    return folder
