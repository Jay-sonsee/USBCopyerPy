"""Win32 API 封装（纯 ctypes，不依赖 pywin32）。

只做三件事：枚举驱动器、读取卷信息、单实例互斥体。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import string
import threading
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

kernel32.CreateEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [wt.LPVOID, wt.BOOL, wt.BOOL, wt.LPCWSTR]

kernel32.OpenEventW.restype = wt.HANDLE
kernel32.OpenEventW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]

kernel32.SetEvent.restype = wt.BOOL
kernel32.SetEvent.argtypes = [wt.HANDLE]

kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]

EVENT_MODIFY_STATE = 0x0002
INFINITE = 0xFFFFFFFF

kernel32.CopyFileW.restype = wt.BOOL
kernel32.CopyFileW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.BOOL]


def copy_file_native(src: str, dst: str, overwrite: bool = True) -> None:
    """用系统 API 复制单个文件（比 Python 分块快很多）。

    CopyFileW 会一并保留时间戳与属性；失败抛 OSError。
    它本身不可中断，因此只用于中小文件，超大文件仍走分块复制。
    """
    if not kernel32.CopyFileW(src, dst, not overwrite):
        raise OSError(ctypes.get_last_error(), f"CopyFileW 失败：{src}")

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

class ShowSignal:
    """用于"再启动一次程序即唤出已有窗口"。

    第一个实例创建命名事件并在后台线程等待；后续实例 SetEvent 一下，
    后台实例收到后把窗口显示出来。比托盘图标简单，且不依赖第三方库。
    """

    NAME = "USBCopyerPy.ShowWindow"

    def __init__(self, name: str = NAME):
        self._name = name
        self._handle = kernel32.CreateEventW(None, False, False, name)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def listen(self, callback) -> None:
        if not self._handle:
            return

        def loop() -> None:
            while not self._stop.is_set():
                result = kernel32.WaitForSingleObject(self._handle, 500)
                if result == 0:
                    try:
                        callback()
                    except Exception:
                        pass

        self._thread = threading.Thread(target=loop, name="show-signal", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None

    @classmethod
    def signal_existing(cls, name: str = NAME) -> bool:
        handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, name)
        if not handle:
            return False
        try:
            return bool(kernel32.SetEvent(handle))
        finally:
            kernel32.CloseHandle(handle)
