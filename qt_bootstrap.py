"""Keep Qt's Windows dependencies independent of other Python distributions.

Also sets up everything QtWebEngine needs before Qt is imported: it renders the
embedded MOOC pages, so its write locations must stay inside the program folder
and it must not rely on a Chromium sandbox that is unavailable in this process.
"""
import ctypes
import os
from pathlib import Path

_icu = None


def prepare_qt(folder):
    global _icu
    folder = Path(folder).resolve()
    temp = folder / 'runtime-temp'
    temp.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = str(temp)
    os.environ['TMP'] = str(temp)
    prepare_webengine(folder)
    if os.name == 'nt':
        system = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'icuuc.dll'
        if system.exists():
            _icu = ctypes.WinDLL(str(system))


def prepare_webengine(folder):
    """Point QtWebEngine at writable folders and turn off its sandbox.

    Chromium otherwise creates its profile under %APPDATA% (or the interpreter
    folder), where writing can be denied, and then fails to render any page.
    Every path below is prepared here, before QtWebEngine is imported.
    """
    folder = Path(folder).resolve()
    profile = folder / 'webengine'
    for name in ('profile', 'cache'):
        (profile / name).mkdir(parents=True, exist_ok=True)
    # A stable per-user folder keeps logins across restarts.
    os.environ.setdefault('QTWEBENGINE_DISK_CACHE', str(profile / 'cache'))
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
    # The launcher already provides a controllable process; a Chromium sandbox
    # inside it is unavailable, so rendering must not require one.
    os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')
    return profile
