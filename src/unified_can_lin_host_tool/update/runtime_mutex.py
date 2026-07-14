"""维护安装器可探测的产品运行命名互斥量。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import sys
from threading import Lock


PRODUCT_RUN_MUTEX = r"Local\EcuFirmwareReleaseTool.Run"
_SYNCHRONIZE = 0x00100000
_local_lock = Lock()
_local_handle_count = 0


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateMutexW = _kernel32.CreateMutexW
    _CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    _CreateMutexW.restype = wintypes.HANDLE
    _OpenMutexW = _kernel32.OpenMutexW
    _OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    _OpenMutexW.restype = wintypes.HANDLE
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = (wintypes.HANDLE,)
    _CloseHandle.restype = wintypes.BOOL


def _create_mutex(name: str):
    if sys.platform != "win32":
        return None
    handle = _CreateMutexW(None, False, name)
    if not handle:
        raise OSError(ctypes.get_last_error(), "无法创建产品运行互斥量")
    return handle


def _close_handle(handle) -> None:
    if sys.platform == "win32" and not _CloseHandle(handle):
        raise OSError(ctypes.get_last_error(), "无法关闭产品运行互斥量")


@contextmanager
def product_run_mutex() -> Iterator[None]:
    global _local_handle_count
    handle = _create_mutex(PRODUCT_RUN_MUTEX) if sys.platform == "win32" else None
    if sys.platform != "win32":
        with _local_lock:
            _local_handle_count += 1
    try:
        yield
    finally:
        if handle is not None:
            _close_handle(handle)
        else:
            with _local_lock:
                _local_handle_count -= 1


def is_product_mutex_present() -> bool:
    if sys.platform != "win32":
        with _local_lock:
            return _local_handle_count > 0
    handle = _OpenMutexW(_SYNCHRONIZE, False, PRODUCT_RUN_MUTEX)
    if not handle:
        return False
    _close_handle(handle)
    return True
