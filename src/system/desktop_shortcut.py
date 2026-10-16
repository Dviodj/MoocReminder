"""Create and repair the Windows shortcuts MoocReminder owns.

Two shortcuts are built from the same code here: the desktop entry the user
agrees to on first start, and the Startup-folder entry that makes the program
run when they sign in.  They differ only in which folder holds the ``.lnk`` and
in the extra argument the Startup copy carries (``--background``), so the
reading, writing and "is this ours?" logic is shared rather than duplicated.

The shortcut targets the same ``pythonw.exe`` the launcher uses, with
``source_launcher.py`` as its argument.  Targeting a real executable (rather
than ``wscript.exe``) is deliberate:

* Explorer treats the shortcut's icon as belonging to a real program, so the
  bundled ``.ico`` actually shows up instead of the script host's icon.
* No console window flashes, because ``pythonw.exe`` is a GUI-subsystem binary.
* ``source_launcher.py`` resolves the project from its own location, so a moved
  project repairs its shortcut on the next start instead of silently breaking.

A shortcut pointing straight at ``launch.vbs`` through ``wscript.exe`` cannot do
any of that: the absolute path in its arguments dies the moment the folder is
moved or renamed.
"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import paths

LINK_NAME = 'MoocReminder.lnk'
DESCRIPTION = 'MoocReminder - MOOC assignment reminders'
#: Passed to the Startup copy only, so signing in lands in the tray instead of
#: opening a window on top of whatever the user is doing.
BACKGROUND_ARGUMENT = '--background'

_DESKTOP = 'B4BFCC3A-DB2C-424C-B029-7FE99A87C641'
_STARTUP = 'B97D20BB-F46A-4C97-BA10-5E3608430854'


def known_folder(identifier, message):
    """Resolve a Windows known folder, honouring a redirected profile.

    OneDrive and roaming profiles move both the desktop and the Startup folder,
    so asking Windows is the only reliable way to find them.
    """
    if os.name != 'nt':
        raise OSError(message)
    class GUID(ctypes.Structure):
        _fields_=[('a',ctypes.c_uint32),('b',ctypes.c_uint16),('c',ctypes.c_uint16),('d',ctypes.c_ubyte*8)]
    folder=GUID.from_buffer_copy(uuid.UUID(identifier).bytes_le)
    pointer=ctypes.c_void_p()
    shell=ctypes.WinDLL('shell32')
    shell.SHGetKnownFolderPath.argtypes=[ctypes.POINTER(GUID),ctypes.c_uint32,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    code=shell.SHGetKnownFolderPath(ctypes.byref(folder),0,None,ctypes.byref(pointer))
    if code:
        raise OSError(message)
    try:
        return Path(ctypes.wstring_at(pointer))
    finally:
        ole=ctypes.WinDLL('ole32');ole.CoTaskMemFree.argtypes=[ctypes.c_void_p];ole.CoTaskMemFree(pointer)


def desktop_directory():
    """The real desktop, honouring a redirected or OneDrive-managed profile."""
    if os.name != 'nt':
        return Path.home()/'Desktop'
    return known_folder(_DESKTOP, '无法定位 Windows 桌面目录。')


def startup_directory():
    """The per-user Startup folder Windows runs at sign-in."""
    if os.name != 'nt':
        raise OSError('无法定位 Windows 启动文件夹。')
    return known_folder(_STARTUP, '无法定位 Windows 启动文件夹。')



def literal(value):
    return "'"+str(value).replace("'","''")+"'"


def launch_target(workspace, python, extra=()):
    """The executable and argument list the shortcut should launch."""
    workspace = Path(workspace).resolve()
    gui = Path(python).resolve().with_name('pythonw.exe')
    # A source checkout may be using an interpreter without pythonw (rare, but
    # a stripped embeddable build omits it).  Fall back rather than break the
    # shortcut: python.exe is only worse in that it flashes a console.
    if not gui.is_file():
        gui = Path(python).resolve()
    return gui, [str(paths.launcher_script(workspace)), '--workspace', str(workspace), *extra]



def _quote(argument):
    """Quote one argument for a Windows command line.

    Windows doubles backslashes that precede a quote, and WScript.Shell
    preserves that doubling when the shortcut is read back, so writing the
    plain path keeps the stored text predictable.
    """
    argument = str(argument)
    if argument and not any(character in argument for character in ' \t"'):
        return argument
    return '"' + argument.replace('"', '\\"') + '"'


def command_line(arguments):
    return ' '.join(_quote(str(argument)) for argument in arguments)


def read_link(link):
    """Current fields of an existing .lnk, or None when unreadable."""
    link = Path(link)
    if not link.is_file() or os.name != 'nt':
        return None
    script = ("$ErrorActionPreference='Stop';"
              "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + literal(link) + ");"
              "ConvertTo-Json -Compress @{target=$s.TargetPath;arguments=$s.Arguments;"
              "cwd=$s.WorkingDirectory;icon=$s.IconLocation;description=$s.Description;"
              "style=$s.WindowStyle}")
    result = _powershell(script, timeout=15)
    if result is None:
        return None
    try:
        return json.loads(result)
    except ValueError:
        return None


def _powershell(script, timeout=12):
    import base64
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    process = subprocess.Popen(
        ['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return None
    if process.returncode:
        # Report the script's own message when it threw one: the refusal to
        # overwrite a same-named shortcut is actionable, and losing it left
        # users staring at a generic failure.
        return _script_error(err)
    return _decode_output(out).strip()


def _script_error(err):
    """Pull the thrown message out of PowerShell's stderr, or None.

    A redirected stderr makes PowerShell emit CLIXML rather than plain text, so
    the message lives in <S S="Error"> elements.  Reading the raw text would
    hand back XML markup.
    """
    raw = _decode_output(err)
    if raw.lstrip().startswith('#< CLIXML'):
        for line in _clixml_text(raw).splitlines():
            line = line.strip()
            # The first element is the thrown message; the rest is the call
            # site, the echoed script and CategoryInfo noise.
            if not line or line.startswith(('At line', 'At ', '+', 'CategoryInfo',
                                            'FullyQualifiedErrorId', 'OperationStopped:')):
                continue
            if line.startswith('Exception:'):
                line = line[len('Exception:'):].strip()
            if line:
                return line
        return None
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line or line.startswith(('+', 'At ', 'CategoryInfo', 'FullyQualifiedErrorId')):
            continue
        if line.startswith('Exception:'):
            line = line[len('Exception:'):].strip()
        if line:
            return line
    return None


def _decode_output(payload):
    """Decode PowerShell output, whose encoding follows the console code page.

    PowerShell 5.1 writes its CLIXML error stream using [Console]::OutputEncoding,
    which is the ANSI code page (GBK on a Chinese system) rather than UTF-8.
    Decoding as UTF-8 there turns every Chinese message into mojibake, so try
    the BOM/UTF-8 reading first and fall back to the ANSI code page.
    """
    if isinstance(payload, str):
        return payload
    payload = payload or b''
    if payload[:3] == b'\xef\xbb\xbf':
        return payload.decode('utf-8-sig', errors='replace')
    try:
        return payload.decode('utf-8')
    except UnicodeDecodeError:
        return payload.decode(_ansi_encoding(), errors='replace')


def _ansi_encoding():
    """The console code page name, e.g. 'gbk' on a Chinese Windows."""
    import locale
    try:
        return locale.getpreferredencoding(False) or 'utf-8'
    except Exception:
        return 'utf-8'


def _clixml_text(text):
    """Extract the human-readable part of a PowerShell CLIXML error stream."""
    import xml.etree.ElementTree as ElementTree
    start = text.find('<Objs')
    if start < 0:
        return ''
    try:
        root = ElementTree.fromstring(text[start:])
    except ElementTree.ParseError:
        return ''
    lines = []
    for node in root.iter():
        if node.tag.endswith('S') and node.get('S') == 'Error' and node.text:
            # CLIXML escapes real newlines as _x000D_/_x000A_.
            lines.append(node.text.replace('_x000D_', '').replace('_x000A_', '\n').strip())
    return '\n'.join(lines)


def _write_link(link, target, arguments, workspace, icon):
    script = (
        "$ErrorActionPreference='Stop';$path=" + literal(link) + ";"
        "$shell=New-Object -ComObject WScript.Shell;"
        "$shortcut=$shell.CreateShortcut($path);"
        # Never silently overwrite someone else's shortcut that happens to share
        # the name.  Ours is identifiable by its working directory or description.
        "if((Test-Path -LiteralPath $path) -and $shortcut.Description -notlike 'MoocReminder*'"
        " -and $shortcut.WorkingDirectory -ne " + literal(workspace) + "){"
        "throw '同名快捷方式属于其他程序，未覆盖。'};"
        "$shortcut.TargetPath=" + literal(target) + ";"
        "$shortcut.Arguments=" + literal(command_line(arguments)) + ";"
        "$shortcut.WorkingDirectory=" + literal(workspace) + ";"
        "$shortcut.Description=" + literal(DESCRIPTION) + ";"
        "$shortcut.WindowStyle=1;"
        + ("$shortcut.IconLocation=" + literal(str(icon) + ',0') + ";" if Path(icon).exists() else '') +
        "$shortcut.Save()")
    return _powershell(script, timeout=20)


def shortcut_state(workspace, python, folder=None, extra=()):
    """What a shortcut for this workspace *should* contain right now."""
    workspace = Path(workspace).resolve()
    folder = Path(folder) if folder is not None else desktop_directory()
    extra = tuple(extra)
    target, arguments = launch_target(workspace, python, extra)
    icon = workspace/'assets/MoocReminder.ico'
    return dict(workspace=str(workspace), link=folder/LINK_NAME, target=str(target),
        arguments=command_line(arguments), extra=extra,
        icon=str(icon)+',0' if icon.exists() else '',
        icon_stamp=icon.stat().st_mtime_ns if icon.exists() else None)


def _unquote(text):
    """Normalise an Arguments field for comparison.

    WScript.Shell doubles backslashes and may re-quote paths, so the raw text
    never matches the string we wrote.  Undo both before comparing.
    """
    return (text or '').replace('"', '').replace('\\\\', '\\').lower()


def _cache_path(workspace):
    """Where the last shortcut we wrote is described."""
    return Path(workspace)/'.launcher-state'/'shortcut.json'


def icon_stamp_changed(workspace, expected):
    """Whether the link was written for an older picture than the one on disk.

    A shortcut stores the icon's *path*, so replacing the picture inside
    ``MoocReminder.ico`` leaves every existing link looking perfectly correct
    while the desktop keeps showing the artwork Explorer cached.  The stamp
    recorded when the link was written is what tells the two apart.  No record
    means nothing of ours to compare against, and a missing key means the
    record predates the stamp: repairing once is cheap and then settles.
    """
    try:
        recorded = json.loads(_cache_path(workspace).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return recorded.get('icon_stamp') != expected.get('icon_stamp')


def _matches(link, expected):
    current = read_link(link)
    if not current:
        return False
    # Compare the whole interpreter path, not just "pythonw.exe": a runtime that
    # moved (a .venv replaced by the bundled one, say) leaves a shortcut that
    # looks right but launches nothing.  Windows stores TargetPath verbatim, so
    # a normalised comparison is safe here.
    if os.path.normcase(str(current.get('target',''))) != os.path.normcase(str(expected['target'])):
        return False

    if Path(current.get('cwd','') or '\\') != Path(expected['workspace']):
        return False
    # Compare the script path rather than the exact command line: Windows
    # rewrites quoting and backslashes, so a literal compare gives false alarms.
    # The script lives inside the workspace, so its presence already proves the
    # arguments point at this project and not another folder.
    arguments = _unquote(current.get('arguments',''))
    script = _unquote(str(paths.launcher_script(expected['workspace'])))
    if script not in arguments:
        return False
    # Extra arguments are not rewritten by Windows, so they can be checked
    # literally.  This is what tells a Startup entry that opens a window apart
    # from one that quietly stays in the tray.
    if any(_unquote(argument) not in arguments for argument in expected.get('extra', ())):
        return False
    if expected['icon'] and (current.get('icon','') or '').strip().lower() != expected['icon'].lower():
        return False
    return (current.get('description','') or '').startswith('MoocReminder')


def shortcut_ready(workspace, python=None, folder=None, extra=()):
    """Whether an up-to-date shortcut already exists in that folder.

    Used to decide between repairing silently and asking the user, so it must
    never raise: an unreadable folder simply means "not ready".
    """
    try:
        workspace = Path(workspace).resolve()
        python = Path(python) if python else Path(os.sys.executable)
        expected = shortcut_state(workspace, python, folder, extra)
        return _matches(expected['link'], expected)
    except (OSError, ValueError):
        return False


def owns_shortcut(workspace, folder=None):
    """Whether an existing shortcut in that folder was created by us.

    Distinguishes "no shortcut yet" (ask the user) from "our shortcut, but it
    points at an old location" (repair silently).  A shortcut belonging to a
    different program never counts.  Never raises.
    """
    try:
        workspace = Path(workspace).resolve()
        folder = Path(folder) if folder is not None else desktop_directory()
        link = folder / LINK_NAME
        if not link.is_file():
            return False
        current = read_link(link)
        description = (current.get('description') or '')
        if description.startswith('MoocReminder'):
            return True
        script = str(paths.launcher_script(workspace))
        return _unquote(script) in _unquote(current.get('arguments') or '')
    except (OSError, ValueError):
        return False


def create_shortcut(workspace, python=None, folder=None, extra=(), cancelled=lambda:False,
                    force=False, label='快捷方式'):
    """Create the shortcut, or repair one that no longer points here.

    Returns the link path.  Raises only for genuine failures -- a shortcut that
    is already correct is a fast no-op, which matters because this runs on every
    launch so that a moved project can repair itself.
    """
    if cancelled():
        raise InterruptedError('启动已取消')
    workspace=Path(workspace).resolve()
    if not paths.launcher_script(workspace).is_file():
        raise RuntimeError('缺少快捷方式启动入口。')
    python = Path(python) if python else Path(os.sys.executable)
    extra = tuple(extra)
    expected = shortcut_state(workspace, python, folder, extra)
    link = expected['link']
    state=workspace/'.launcher-state';state.mkdir(parents=True,exist_ok=True)
    cache=_cache_path(workspace)
    if not force and _matches(link, expected) and not icon_stamp_changed(workspace, expected):
        return link
    if cancelled():
        raise InterruptedError('启动已取消')
    reason = _write_link(link, expected['target'],
        [str(paths.launcher_script(workspace)), '--workspace', str(workspace), *extra],
        workspace, workspace/'assets/MoocReminder.ico')
    deadline = time.monotonic() + 12
    while reason is None and not link.is_file() and time.monotonic() < deadline:
        if cancelled():
            raise InterruptedError('启动已取消')
        time.sleep(.05)
    if not link.is_file():
        raise RuntimeError(label + '未能创建：' + (reason or '未知原因'))
    if not _matches(link, expected):
        # _write_link explains refusals such as a same-named shortcut that
        # belongs to another program; surfacing that beats a generic message
        # that reads like a bug in this program.
        raise RuntimeError(reason or (label + '已创建，但内容与预期不符。'))
    cache.write_text(json.dumps(dict(workspace=str(workspace), link=str(link),
        target=expected['target'], arguments=expected['arguments'],
        icon_stamp=expected['icon_stamp'], written=time.time()), ensure_ascii=False, indent=2),
        encoding='utf-8')
    return link


def remove_shortcut(workspace, folder=None, label='快捷方式'):
    """Delete only our own shortcut; a foreign one is left alone."""
    workspace=Path(workspace).resolve()
    folder = Path(folder) if folder is not None else desktop_directory()
    link = folder/LINK_NAME
    if read_link(link) is None:
        return False
    if not owns_shortcut(workspace, folder):
        raise RuntimeError('同名' + label + '不属于本程序，未删除。')
    link.unlink()
    cache = _cache_path(workspace)
    try:
        cache.unlink(missing_ok=True)
    except OSError:
        pass
    return True


# --- Desktop entry ---------------------------------------------------------
# The names the launcher and the tests use.  Kept as thin wrappers so the shared
# implementation above can also serve the Startup folder.

def desktop_shortcut_ready(workspace, python=None, desktop=None):
    return shortcut_ready(workspace, python, desktop)


def create_desktop_shortcut(workspace, python=None, desktop=None, cancelled=lambda:False, force=False):
    return create_shortcut(workspace, python, desktop, (), cancelled, force, '桌面快捷方式')


def remove_desktop_shortcut(workspace, desktop=None):
    return remove_shortcut(workspace, desktop, '桌面快捷方式')
