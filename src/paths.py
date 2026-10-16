"""Where everything lives, and the one place that knows it.

The program is split across folders by responsibility (``domain``, ``mail``,
``web``, ``ui``, ``system``) instead of piling every module in the repository
root.  That makes two different questions separable, which they were not while
the modules sat next to the data:

* **Where is the program root?**  The folder holding ``data/``, ``assets/``,
  ``.launcher-state/``, ``email_sender.json`` and ``requirements-runtime.txt``.
  That is the repository root in a checkout and the extracted folder in a
  published package -- exactly what ``--workspace`` names.
* **Where does this module live?**  A subfolder of ``src/``, which says nothing
  about where the program keeps its data.

Before this module existed, both were spelled ``Path(__file__).parent``.  They
only agreed because every module sat in the root, so moving one would have
silently pointed ``data/``, ``assets/`` and the sender configuration at the
wrong folder.

Importing this module also puts every module folder on ``sys.path``, so the
program keeps using flat imports (``import core``) rather than turning into a
package with relative imports.
"""
from pathlib import Path
import sys

#: The folder that holds data/, assets/, .launcher-state/ and the configuration
#: files.  ``paths.py`` lives in ``src/``, so this is one level above its parent.
PROGRAM_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = PROGRAM_ROOT / 'src'
TOOLS_ROOT = PROGRAM_ROOT / 'tools'

#: Entry points, as paths relative to the program root.  Shortcuts and the
#: launcher resolve them through the helpers below, so relocating a file is a
#: one-line change here instead of a hunt through the codebase.
LAUNCHER_ENTRY = 'src/system/source_launcher.py'
STARTUP_UI_ENTRY = 'src/ui/launcher_ui.py'
GUI_ENTRY = 'src/ui/qt_desktop.py'


def module_folders():
    """Every folder holding importable modules, derived rather than listed.

    A new responsibility folder is picked up automatically; hard-coding the list
    would mean a silent ``ModuleNotFoundError`` the first time someone adds one.
    """
    folders = [SOURCE_ROOT, TOOLS_ROOT]
    for child in sorted(SOURCE_ROOT.iterdir()):
        if child.is_dir() and not child.name.startswith(('.', '__')):
            folders.append(child)
    return folders


def install():
    """Make every module folder importable.  Safe to call more than once."""
    for folder in reversed(module_folders()):
        text = str(folder)
        if text in sys.path:
            sys.path.remove(text)
        sys.path.insert(0, text)
    return module_folders()


def script(relative, workspace=None):
    """Absolute path of an entry point inside a program folder."""
    return (Path(workspace) if workspace else PROGRAM_ROOT) / relative


def launcher_script(workspace=None):
    return script(LAUNCHER_ENTRY, workspace)


def startup_ui_script(workspace=None):
    return script(STARTUP_UI_ENTRY, workspace)


def gui_script(workspace=None):
    return script(GUI_ENTRY, workspace)


def data_dir(workspace=None):
    return script('data', workspace)


def assets_dir(workspace=None):
    return script('assets', workspace)


def icon_path(workspace=None):
    return script('assets/MoocReminder.ico', workspace)


def state_dir(workspace=None):
    return script('.launcher-state', workspace)


def bootstrap_code():
    """Python source that makes the module folders importable in a child process.

    Used for the ``-c`` probes the launcher runs with an arbitrary interpreter:
    they import ``qt_bootstrap`` by name, which only works once every module
    folder -- not just ``src/`` -- is on the path.
    """
    folders = ', '.join(repr(str(folder)) for folder in module_folders())
    return 'import sys; sys.path[:0] = [' + folders + ']; '


install()
