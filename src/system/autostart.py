"""Start MoocReminder when the user signs in to Windows, once they agree.

Windows launches everything in the per-user Startup folder at sign-in.  Putting
the same kind of shortcut there as on the desktop means the entry carries the
program's own icon, repairs itself when the project moves, and shows up in
任务管理器 → 启动, which is where people look for this sort of thing.  A
registry ``Run`` value would do none of that.

The Startup copy carries ``--background``, so signing in lands quietly in the
system tray instead of opening a window on top of whatever the user was doing;
the desktop copy deliberately omits it and opens the window as usual.

Nothing here is ever called without the user asking for it: the launcher only
*repairs* an entry that already exists, and creates one only from the consent
card or the settings checkbox.
"""
from pathlib import Path

import desktop_shortcut
from desktop_shortcut import BACKGROUND_ARGUMENT, LINK_NAME

LABEL = '开机启动项'
#: Kept out of the "we own this" test, which matches on the description and the
#: pointed-at script instead.
__all__ = ['LABEL', 'LINK_NAME', 'BACKGROUND_ARGUMENT', 'startup_directory',
           'enabled', 'ready', 'enable', 'disable']


def startup_directory():
    """The per-user Startup folder Windows runs at sign-in."""
    return desktop_shortcut.startup_directory()


def _folder(folder):
    return Path(folder) if folder is not None else startup_directory()


def enabled(workspace, folder=None):
    """Whether a Startup entry of ours is in place, stale or not.

    This is what the settings checkbox reflects: an entry pointing at an old
    location is still "on" from the user's point of view -- the launcher quietly
    fixes the path the next time it runs.  Never raises.
    """
    try:
        return desktop_shortcut.owns_shortcut(workspace, _folder(folder))
    except OSError:
        return False


def ready(workspace, python=None, folder=None):
    """Whether the Startup entry exists *and* already points at the right place.

    Used to decide between "nothing to do" and "repair it silently".  Never
    raises: an unreadable Startup folder just means "not ready".
    """
    try:
        return desktop_shortcut.shortcut_ready(
            workspace, python, _folder(folder), (BACKGROUND_ARGUMENT,))
    except OSError:
        return False


def enable(workspace, python=None, folder=None, cancelled=lambda: False):
    """Create or repair the Startup entry and return its path.

    Raises only on genuine failure -- a same-named shortcut belonging to another
    program is refused rather than overwritten, exactly as on the desktop.
    """
    return desktop_shortcut.create_shortcut(
        workspace, python, _folder(folder), (BACKGROUND_ARGUMENT,),
        cancelled, False, LABEL)


def disable(workspace, folder=None):
    """Remove our Startup entry.  Returns whether one was actually removed.

    Never deletes a same-named shortcut that belongs to another program.
    """
    return desktop_shortcut.remove_shortcut(workspace, _folder(folder), LABEL)
