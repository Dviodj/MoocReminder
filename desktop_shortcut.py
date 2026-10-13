"""Create the user-requested Windows desktop entry without a console window."""
import base64
import ctypes
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def desktop_directory():
    if os.name != 'nt':
        return Path.home()/'Desktop'
    class GUID(ctypes.Structure):
        _fields_=[('a',ctypes.c_uint32),('b',ctypes.c_uint16),('c',ctypes.c_uint16),('d',ctypes.c_ubyte*8)]
    folder=GUID.from_buffer_copy(uuid.UUID('B4BFCC3A-DB2C-424C-B029-7FE99A87C641').bytes_le)
    pointer=ctypes.c_void_p()
    shell=ctypes.WinDLL('shell32')
    shell.SHGetKnownFolderPath.argtypes=[ctypes.POINTER(GUID),ctypes.c_uint32,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    code=shell.SHGetKnownFolderPath(ctypes.byref(folder),0,None,ctypes.byref(pointer))
    if code:
        raise OSError('无法定位 Windows 桌面目录。')
    try:
        return Path(ctypes.wstring_at(pointer))
    finally:
        ole=ctypes.WinDLL('ole32');ole.CoTaskMemFree.argtypes=[ctypes.c_void_p];ole.CoTaskMemFree(pointer)


def literal(value):
    return "'"+str(value).replace("'","''")+"'"


def create_desktop_shortcut(workspace, desktop=None, cancelled=lambda:False):
    if cancelled():
        raise InterruptedError('启动已取消')
    workspace=Path(workspace).resolve()
    if not (workspace/'launch.vbs').is_file():
        raise RuntimeError('缺少快捷方式启动入口。')
    desktop=Path(desktop) if desktop is not None else desktop_directory()
    link=desktop/'MoocReminder.lnk'
    icon=workspace/'assets/MoocReminder.ico'
    state=workspace/'.launcher-state';state.mkdir(parents=True,exist_ok=True)
    stamp=dict(workspace=str(workspace),link=str(link),icon=icon.stat().st_mtime_ns if icon.exists() else None,
        link_mtime=link.stat().st_mtime_ns if link.exists() else None)
    cache=state/'shortcut.json'
    try:
        if link.is_file() and json.loads(cache.read_text(encoding='utf-8'))==stamp:
            return link
    except (OSError,ValueError):
        pass
    target=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/wscript.exe'
    arguments='"'+str(workspace/'launch.vbs')+'"'
    script=("$ErrorActionPreference='Stop';$path="+literal(link)+";$shell=New-Object -ComObject WScript.Shell;"
        "$shortcut=$shell.CreateShortcut($path);"
        "if((Test-Path -LiteralPath $path) -and $shortcut.Description -notlike 'MoocReminder*' -and $shortcut.WorkingDirectory -ne "+literal(workspace)+"){throw '同名快捷方式属于其他程序，未覆盖。'};"
        "$shortcut.TargetPath="+literal(target)+";$shortcut.Arguments="+literal(arguments)+";"
        "$shortcut.WorkingDirectory="+literal(workspace)+";$shortcut.Description='MoocReminder - MOOC assignment reminders';"
        "$shortcut.WindowStyle=1;"
        +("$shortcut.IconLocation="+literal(str(icon)+',0')+";" if icon.exists() else '')+
        "$shortcut.Save()")
    encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
    process=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    until=time.monotonic()+12
    try:
        while process.poll() is None:
            if cancelled():
                raise InterruptedError('启动已取消')
            if time.monotonic()>until:
                raise TimeoutError('创建桌面快捷方式超时。')
            time.sleep(.05)
        if process.returncode or not link.is_file():
            reason=process.stderr.read().decode('utf-8',errors='replace')[-1800:]
            raise RuntimeError('桌面快捷方式未能创建：'+reason)
        stamp['link_mtime']=link.stat().st_mtime_ns
        cache.write_text(json.dumps(stamp),encoding='utf-8')
        return link
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
        if process.stderr:process.stderr.close()
