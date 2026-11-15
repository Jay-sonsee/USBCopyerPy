"""驱动器变化监听：轮询式实现。

原版靠窗口消息 WM_DEVICECHANGE，一旦消息里的盘符掩码异常就会抛
IndexOutOfRangeException 并丢掉整次事件。轮询没有这类解析风险，
代价是最多延迟一个轮询周期（默认 1 秒）。
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from .winapi import VolumeInfo, list_drive_roots, volume_info

# 设备刚插入时可能还没就绪，最多重试这么多轮再放弃
PENDING_RETRY_LIMIT = 10


class DriveWatcher:
    def __init__(
        self,
        interval: float,
        on_arrive: Callable[[VolumeInfo], None],
        on_remove: Callable[[str], None],
        log: Callable[[str, str], None],
        lister: Callable[[], list[str]] = list_drive_roots,
        prober: Callable[[str], VolumeInfo | None] = volume_info,
    ):
        self.interval = max(0.2, float(interval))
        self._on_arrive = on_arrive
        self._on_remove = on_remove
        self._log = log
        self._lister = lister
        self._prober = prober

        self._known: dict[str, VolumeInfo] = {}
        self._pending: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self._known = self._snapshot(skip_pending=True)
        self._thread = threading.Thread(target=self._loop, name="drive-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    # ------------------------------------------------------------ 内部
    def _snapshot(self, skip_pending: bool = False) -> dict[str, VolumeInfo]:
        result: dict[str, VolumeInfo] = {}
        for root in self._lister():
            info = self._prober(root)
            if info is None:
                if not skip_pending:
                    letter = root[0].upper()
                    self._pending[letter] = self._pending.get(letter, 0) + 1
                continue
            result[info.letter] = info
        return result

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # 监听线程不能因为单次异常退出
                self._log("error", f"驱动器监听出错：{exc}")
            self._stop.wait(self.interval)

    def _tick(self) -> None:
        current = self._snapshot()

        # 新出现的盘符
        for letter, info in current.items():
            old = self._known.get(letter)
            if old is None:
                self._known[letter] = info
                self._log("info", f"检测到设备 {letter}: {info.label or '无卷标'} [{info.serial}] {info.type_label}")
                self._safe(self._on_arrive, info)
            elif old.serial != info.serial:
                # 同一盘符换了另一块盘
                self._known[letter] = info
                self._safe(self._on_remove, letter)
                self._log("info", f"检测到设备更换 {letter}: [{info.serial}]")
                self._safe(self._on_arrive, info)

        # 消失的盘符
        for letter in list(self._known):
            if letter not in current:
                self._safe(self._on_remove, letter)
                self._known.pop(letter, None)
                self._log("info", f"设备已移除 {letter}:")

        # 未就绪的设备最多重试若干轮
        for letter, count in list(self._pending.items()):
            if letter in current or count >= PENDING_RETRY_LIMIT:
                self._pending.pop(letter, None)

    def _safe(self, callback: Callable, arg) -> None:
        try:
            callback(arg)
        except Exception as exc:
            self._log("error", f"事件处理失败：{exc}")
