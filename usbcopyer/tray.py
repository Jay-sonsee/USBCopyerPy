"""系统托盘图标（纯 ctypes，无第三方依赖）。

做法：在后台线程里建一个隐藏窗口 + 消息循环，用 Shell_NotifyIcon 挂图标，
右键弹原生菜单。所有点击都通过回调抛给调用方，由它决定怎么处理。

注意：窗口必须在跑消息循环的那个线程里创建，所以 start() 会把整套东西
放到线程里做，并用一个 Event 等它就绪。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
from typing import Callable, Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_NULL = 0x0000
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20

WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x0100, 0x0002
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
IDI_APPLICATION = 32512

MENU_SHOW, MENU_TOGGLE, MENU_STOP, MENU_OPEN, MENU_EXIT = 1, 2, 3, 4, 5


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CreateWindowExW.restype = wt.HWND
user32.RegisterClassW.restype = wt.ATOM
user32.LoadIconW.restype = wt.HICON
user32.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.CreatePopupMenu.restype = wt.HMENU
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, ctypes.c_void_p]
shell32.Shell_NotifyIconW.restype = wt.BOOL
shell32.ExtractIconExW.restype = wt.UINT


class TrayIcon:
    """一个托盘图标 + 右键菜单。

    回调都在调用方线程之外（窗口线程）触发，调用方自行转成队列/消息。
    """

    def __init__(
        self,
        tooltip: str = "USBCopyerPy",
        on_show: Optional[Callable[[], None]] = None,
        on_toggle: Optional[Callable[[], None]] = None,
        on_stop: Optional[Callable[[], None]] = None,
        on_open_dir: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        toggle_label: str = "暂停监听",
    ):
        self._tooltip = tooltip
        self._toggle_label = toggle_label
        self._on_show = on_show
        self._on_toggle = on_toggle
        self._on_stop = on_stop
        self._on_open_dir = on_open_dir
        self._on_exit = on_exit

        self._hwnd: Optional[int] = None
        self._icon: Optional[int] = None
        self._nid: Optional[NOTIFYICONDATAW] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._class_name = f"USBCopyerPyTray_{os.getpid()}"
        self._wndproc = WNDPROC(self._handle_message)
        self.error: Optional[str] = None

    # ---------------------------------------------------------------- 生命周期
    def start(self, timeout: float = 3.0) -> bool:
        self._thread = threading.Thread(target=self._run, name="tray-icon", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self._hwnd is not None

    def stop(self) -> None:
        if self._hwnd:
            try:
                user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    # ---------------------------------------------------------------- 提示文字
    def update_tooltip(self, text: str) -> None:
        self._tooltip = text
        if not self._nid:
            return
        try:
            self._nid.szTip = text[:127]
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))
        except Exception:
            pass

    def set_toggle_label(self, label: str) -> None:
        self._toggle_label = label

    # ---------------------------------------------------------------- 内部
    def _run(self) -> None:
        try:
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = self._class_name
            user32.RegisterClassW(ctypes.byref(wc))  # 已注册会返回 0，忽略

            hwnd = user32.CreateWindowExW(
                0, self._class_name, "USBCopyerPy", 0, 0, 0, 0, 0, None, None, hinst, None
            )
            if not hwnd:
                self.error = f"CreateWindowEx 失败：{ctypes.get_last_error()}"
                return

            self._hwnd = hwnd
            self._icon = self._load_icon()
            self._add_icon()
            self._ready.set()   # 图标挂上就算就绪，不等消息循环

            msg = wt.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:  # 托盘失败不能拖垮主程序
            self.error = str(exc)
        finally:
            self._ready.set()

    def _load_icon(self):
        large = wt.HICON()
        small = wt.HICON()
        try:
            if shell32.ExtractIconExW(sys.executable, 0, ctypes.byref(large), ctypes.byref(small), 1) > 0 and small:
                return small
        except Exception:
            pass
        return user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

    def _add_icon(self) -> None:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._icon
        nid.szTip = self._tooltip[:127]
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise OSError(f"Shell_NotifyIcon 添加失败：{ctypes.get_last_error()}")
        self._nid = nid

    def _delete_icon(self) -> None:
        if self._nid:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:
                pass
            self._nid = None

    def _handle_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAYICON:
                event = lparam & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._fire(self._on_show)
                elif event == WM_RBUTTONUP:
                    self._show_menu(hwnd)
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._delete_icon()
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self, hwnd) -> None:
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, MENU_SHOW, "显示窗口")
        user32.AppendMenuW(menu, MF_STRING, MENU_TOGGLE, self._toggle_label)
        user32.AppendMenuW(menu, MF_STRING, MENU_STOP, "停止复制")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, MENU_OPEN, "打开备份目录")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, MENU_EXIT, "退出")

        point = wt.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(hwnd)  # 不加这句，点菜单外部菜单不会消失
        try:
            cmd = user32.TrackPopupMenu(
                menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                point.x, point.y, 0, hwnd, None,
            )
        finally:
            user32.DestroyMenu(menu)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)

        actions = {
            MENU_SHOW: self._on_show,
            MENU_TOGGLE: self._on_toggle,
            MENU_STOP: self._on_stop,
            MENU_OPEN: self._on_open_dir,
            MENU_EXIT: self._on_exit,
        }
        self._fire(actions.get(cmd))

    @staticmethod
    def _fire(callback) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception:
            pass