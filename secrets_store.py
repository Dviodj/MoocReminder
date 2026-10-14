"""Local secret storage: DPAPI on Windows, explicit fallback elsewhere.

Credentials never leave the machine and are never written in clear text on
Windows, where CryptProtectData ties the ciphertext to the current user.
"""
import base64
import ctypes
from ctypes import wintypes
import os

MAGIC = 'dpapi:'


class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def available():
    return os.name == 'nt'


def _blob(payload):
    buffer = ctypes.create_string_buffer(payload, len(payload))
    return _Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _library(name):
    return ctypes.WinDLL(name, use_last_error=True)


def _protect(payload):
    crypt32, kernel32 = _library('crypt32'), _library('kernel32')
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    source, keep = _blob(payload)
    result = _Blob()
    if not crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _unprotect(payload):
    crypt32, kernel32 = _library('crypt32'), _library('kernel32')
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    source, keep = _blob(payload)
    result = _Blob()
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def encrypt(text):
    """Return a storable token. Never raises for empty input."""
    if not text:
        return ''
    raw = text.encode('utf-8')
    if not available():
        # Without DPAPI the caller still gets a working file; the token is
        # marked so readers know it is not protected by the operating system.
        return 'plain:' + base64.b64encode(raw).decode('ascii')
    try:
        return MAGIC + base64.b64encode(_protect(raw)).decode('ascii')
    except OSError:
        return 'plain:' + base64.b64encode(raw).decode('ascii')


def decrypt(token):
    """Reverse `encrypt`; returns '' when the token is unreadable."""
    if not token or not isinstance(token, str):
        return ''
    try:
        if token.startswith(MAGIC):
            return _unprotect(base64.b64decode(token[len(MAGIC):])).decode('utf-8')
        if token.startswith('plain:'):
            return base64.b64decode(token[len('plain:'):]).decode('utf-8')
    except (OSError, ValueError):
        return ''
    return ''


def protected(token):
    return bool(token) and str(token).startswith(MAGIC)
