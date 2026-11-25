"""
Metabase API key lookup (same approach as retention_calls/metabase_key.py).

Order:
1. METABASE_API_KEY environment variable (used on servers).
2. ~/.metabase_key -- a Windows DPAPI-encrypted file made with
   Read-Host -AsSecureString | ConvertFrom-SecureString | Set-Content "$HOME\\.metabase_key"
   Only the same Windows user on the same machine can decrypt it.

The key is never written anywhere or printed.
"""

import os
import sys

KEY_FILE = os.path.join(os.path.expanduser("~"), ".metabase_key")


def _dpapi_decrypt(hex_blob: str) -> str:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    raw = bytes.fromhex(hex_blob.strip())
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("Could not decrypt ~/.metabase_key (made by another Windows user or machine?)")
    try:
        data = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return data.decode("utf-16-le")  # SecureString text is UTF-16


def get_metabase_key() -> str:
    key = os.getenv("METABASE_API_KEY", "")
    if key:
        return key
    if sys.platform == "win32" and os.path.exists(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8-sig") as f:
            return _dpapi_decrypt(f.read())
    return ""
