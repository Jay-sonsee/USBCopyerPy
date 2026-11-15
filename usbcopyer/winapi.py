"""Win32 API 封装（纯 ctypes，不依赖 pywin32）。

只做三件事：枚举驱动器、读取卷信息、单实例互斥体。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import string
from dataclasses import dataclass

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.GetLogicalDrives.restype = wt.DWORD
kernel32.GetLogicalDrives.argtypes = []

kernel32.GetDriveTypeW.restype = wt.UINT
kernel32.GetDriveTypeW.argtypes = [wt.LPCWSTR]

kernel32.GetVolumeInformationW.restype = wt.BOOL
kernel32.GetVolumeInformationW.argtypes = [
    wt.LPCWSTR, wt.LPWSTR, wt.DWORD,
    ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
    ctypes.POINTER(wt.DWORD), wt.LPWSTR, wt.DWORD,
]

kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]

kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]

ERROR_ALREADY_EXISTS = 183

# GetDriveType 返回值 -> 语义键
DRIVE_TYPE_KEYS = {
    0: "unknown",
    1: "no_root",
    2: "removable",
    3: "fixed",
    4: "remote",
    5: "cdrom",
    6: "ramdisk",
}

DRIVE_TYPE_LABELS = {
    "unknown": "未知",
    "no_root": "无根目录",
    "removable": "可移动磁盘",
    "fixed": "本地磁盘",
    "remote": "网络驱动器",
    "cdrom": "光盘驱动器",
    "ramdisk": "虚拟磁盘",
}


@dataclass(frozen=True)
class VolumeInfo:
    letter: str          # "E"
    root: str            # "E:\\"
    label: str           # 卷标，可能为空
    serial: str          # 8 位大写十六进制
    filesystem: str      # "NTFS" / "exFAT" / ...
    drive_type: str      # DRIVE_TYPE_KEYS 之一

    @property
    def type_label(self) -> str:
        return DRIVE_TYPE_LABELS.get(self.drive_type, "未知")


def list_drive_roots() -> list[str]:
    """返回当前已挂载的盘符根路径，如 ['C:\\\\', 'E:\\\\']。"""
    mask = kernel32.GetLogicalDrives()
    roots = []
    for i, ch in enumerate(string.ascii_uppercase):
        if mask & (1 << i):
            roots.append(f"{ch}:\\")
    return roots


def drive_type_key(root: str) -> str:
    return DRIVE_TYPE_KEYS.get(kernel32.GetDriveTypeW(root), "unknown")


def volume_info(root: str) -> VolumeInfo | None:
    """读取卷信息；设备未就绪（无介质、刚拔出）时返回 None。"""
    root = root.rstrip("\\") + "\\"
    letter = root[0].upper()

    label_buf = ctypes.create_unicode_buffer(261)
    fs_buf = ctypes.create_unicode_buffer(261)
    serial = wt.DWORD(0)
    max_len = wt.DWORD(0)
    flags = wt.DWORD(0)

    ok = kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root),
        label_buf, len(label_buf),
        ctypes.byref(serial), ctypes.byref(max_len), ctypes.byref(flags),
        fs_buf, len(fs_buf),
    )
    if not ok:
        return None

    return VolumeInfo(
        letter=letter,
        root=root,
        label=label_buf.value or "",
        serial=f"{serial.value:08X}",
        filesystem=fs_buf.value or "",
        drive_type=drive_type_key(root),
    )


class SingleInstance:
    """命名互斥体实现单实例，不依赖 exe 文件名。

    用法：
        guard = SingleInstance("USBCopyerPy.SingleInstance")
        if not guard.acquired: ...
    """

    def __init__(self, name: str = "USBCopyerPy.SingleInstance"):
        self._handle = None
        self.acquired = False
        for prefix in ("Global\\", "Local\\", ""):
            handle = kernel32.CreateMutexW(None, True, prefix + name)
            err = ctypes.get_last_error()
            if not handle:
                continue
            if err == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                self.acquired = False
                return
            self._handle = handle
            self.acquired = True
            return

    def release(self) -> None:
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None
            self.acquired = False

    def __enter__(self) -> "SingleInstance":
        return self

    def __exit__(self, *exc) -> None:
        self.release()
