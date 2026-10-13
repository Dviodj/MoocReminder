"""Keep Qt's Windows dependencies independent of Anaconda's older ICU."""
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
    if os.name == 'nt':
        system = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'icuuc.dll'
        if system.exists():
            _icu = ctypes.WinDLL(str(system))
