"""应用编排：监听 -> 筛选 -> 延迟 -> 复制 -> 回调 -> 记录。

所有共享状态（正在复制的任务表、暂停标志）都用一把 RLock 保护，
避免并发插拔时出现原版那种"字典被并发修改"的问题。
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event, RLock
from typing import Callable

from . import APP_NAME
from .callbacks import run_callback
from .config import Config
from .copier import CopyStats, copy_volume
from .rules import build_folder_name, parse_ext_list
from .watcher import DriveWatcher
from .winapi import VolumeInfo

COPY_LOG_NAME = "usbcopyer.log"


@dataclass
class CopyTask:
    volume: VolumeInfo
    target: Path
    cancel: Event = field(default_factory=Event)
    thread: threading.Thread | None = None
    started_at: float = field(default_factory=time.time)
    stats: CopyStats | None = None

    @property
    def letter(self) -> str:
        return self.volume.letter


class UsbCopyerApp:
    def __init__(self, cfg: Config, log_file: Path | None = None):
        self.cfg = cfg
        self._lock = RLock()
        self._copies: dict[str, CopyTask] = {}
        self._paused = False
        self._watcher: DriveWatcher | None = None
        self._listeners: list[Callable[[str, str], None]] = []
        self._recent: list[tuple[str, str, str]] = []

        self._logger = logging.getLogger(APP_NAME)
        self._logger.setLevel(logging.INFO)
        self._logger.handlers.clear()
        self._logger.propagate = False
        self._log_file = log_file
        if log_file:
            try:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                handler = logging.FileHandler(log_file, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
                self._logger.addHandler(handler)
            except OSError:
                pass

    # ------------------------------------------------------------ 日志
    def add_listener(self, fn: Callable[[str, str], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def log(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._recent.append((timestamp, level, message))
            del self._recent[:-500]
            listeners = list(self._listeners)
        getattr(self._logger, level if level in ("debug", "info", "warning", "error") else "info", self._logger.info)(message)
        for fn in listeners:
            try:
                fn(level, message)
            except Exception:
                pass

    def recent_log(self) -> list[tuple[str, str, str]]:
        with self._lock:
            return list(self._recent)

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self.cfg.data_root.mkdir(parents=True, exist_ok=True)
        self._watcher = DriveWatcher(
            interval=self.cfg.poll_interval,
            on_arrive=self._on_arrive,
            on_remove=self._on_remove,
            log=self.log,
        )
        self._watcher.start()
        self.log("info", f"{APP_NAME} 已启动，备份目录：{self.cfg.destination or '(未设置)'}")

    def stop(self) -> None:
        self.cancel_all("程序退出")
        if self._watcher:
            self._watcher.stop()
            self._watcher = None

    def pause(self) -> None:
        with self._lock:
            self._paused = True
        self.log("info", "已暂停：新插入的设备不会被复制")

    def resume(self) -> None:
        with self._lock:
            self._paused = False
        self.log("info", "已恢复监听")

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    # ------------------------------------------------------------ 状态
    def active_tasks(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "letter": task.letter,
                    "label": task.volume.label,
                    "serial": task.volume.serial,
                    "target": str(task.target),
                    "elapsed": round(time.time() - task.started_at, 1),
                }
                for task in self._copies.values()
            ]

    def is_busy(self) -> bool:
        with self._lock:
            return bool(self._copies)

    # ------------------------------------------------------------ 事件
    def _on_arrive(self, volume: VolumeInfo) -> None:
        with self._lock:
            paused = self._paused
        if paused:
            self.log("info", f"{volume.letter}: 处于暂停状态，忽略本次插入")
            return

        ok, reason = self._accept(volume)
        if not ok:
            self.log("info", f"{volume.letter}: {reason}")
            return

        if not self.cfg.destination.strip():
            self.log("warn", "尚未设置备份目录，跳过复制")
            return

        try:
            folder = build_folder_name(self.cfg.folder_rule, volume)
        except ValueError as exc:
            self.log("error", f"分类规则无效：{exc}")
            return

        target = Path(self.cfg.destination).expanduser() / folder
        cancel = Event()
        task = CopyTask(volume=volume, target=target, cancel=cancel)
        with self._lock:
            running = len(self._copies)
        if running >= max(1, self.cfg.max_parallel_copies):
            self.log("warn", f"{volume.letter}: 同时复制数已达上限，本次排队等待")
            # 简单排队：等待已有任务结束（最多等 10 分钟）
            deadline = time.time() + 600
            while time.time() < deadline and self.is_busy():
                if cancel.wait(0.5):
                    return
    # 无任务时继续

        with self._lock:
            self._copies[volume.letter] = task
        task.thread = threading.Thread(
            target=self._copy_worker, args=(task,), name=f"copy-{volume.letter}", daemon=True
        )
        task.thread.start()

    def _on_remove(self, letter: str) -> None:
        with self._lock:
            task = self._copies.get(letter)
        if task:
            self.log("warn", f"{letter}: 设备已移除，正在停止复制")
            task.cancel.set()

    def cancel(self, letter: str) -> None:
        with self._lock:
            task = self._copies.get(letter)
        if task:
            task.cancel.set()

    def cancel_all(self, reason: str = "用户请求停止") -> None:
        with self._lock:
            tasks = list(self._copies.values())
        for task in tasks:
            task.cancel.set()
        if tasks:
            self.log("info", f"{reason}：已请求停止 {len(tasks)} 个任务")

    # ------------------------------------------------------------ 筛选
    def _accept(self, volume: VolumeInfo) -> tuple[bool, str]:
        if volume.drive_type in self.cfg.skip_types:
            return False, f"按设置跳过该驱动器类型（{volume.type_label}）"

        serials = {s.strip().upper() for s in self.cfg.black_serials.replace(";", ",").split(",") if s.strip()}
        letters = {s.strip().upper().rstrip(":") for s in self.cfg.black_letters.replace(";", ",").split(",") if s.strip()}

        hit = volume.serial.upper() in serials or volume.letter.upper() in letters
        if self.cfg.disk_mode == "white":
            return (True, "") if hit else (False, "不在白名单中，跳过")
        return (False, "在黑名单中，跳过") if hit else (True, "")

    # ------------------------------------------------------------ 复制
    def _copy_worker(self, task: CopyTask) -> None:
        volume = task.volume
        try:
            if self.cfg.delay_seconds > 0:
                self.log("info", f"{volume.letter}: 延迟 {self.cfg.delay_seconds} 秒后开始复制")
                if task.cancel.wait(self.cfg.delay_seconds):
                    self.log("info", f"{volume.letter}: 延迟期间被取消")
                    return

            if self.cfg.callbacks_enabled:
                code = run_callback(
                    self.cfg.root_dir, "DiskDetectedCallback.bat", volume, "", self.log,
                    self.cfg.wait_callback,
                )
                if code not in (None, 0) and self.cfg.wait_callback:
                    self.log("warn", f"{volume.letter}: 回调返回 {code}，取消本次复制")
                    return

            self.log("info", f"{volume.letter}: 开始复制到 {task.target}")
            stats = copy_volume(
                volume, task.target, self.cfg, task.cancel, self.log,
                progress=lambda n, b, name: self._on_progress(volume, n, b, name),
            )
            task.stats = stats
            self.log("info", f"{volume.letter}: {stats.summary()}")
            self._append_disk_log(volume, task.target, stats)

            if self.cfg.callbacks_enabled and not stats.cancelled:
                run_callback(
                    self.cfg.root_dir, "AllCompletedCallback.bat", volume, str(task.target),
                    self.log, self.cfg.wait_callback,
                )
        except Exception as exc:
            self.log("error", f"{volume.letter}: 复制任务异常 {exc}")
        finally:
            with self._lock:
                self._copies.pop(volume.letter, None)
            self.log("info", f"{volume.letter}: 任务结束")

    def _on_progress(self, volume: VolumeInfo, count: int, total: int, name: str) -> None:
        # 每 50 个文件打一条，避免日志爆炸
        if count % 50 == 0:
            from .copier import format_size
            self.log("info", f"{volume.letter}: 已复制 {count} 个文件 / {format_size(total)}")

    def _append_disk_log(self, volume: VolumeInfo, target: Path, stats: CopyStats) -> None:
        path = self.cfg.disk_log_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not path.exists()
            with open(path, "a", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                if new_file:
                    writer.writerow(["时间", "序列号", "卷标", "盘符", "文件系统", "类型", "目标目录", "复制文件数", "字节数", "状态"])
                writer.writerow([
                    datetime.now().isoformat(timespec="seconds"),
                    volume.serial, volume.label, f"{volume.letter}:",
                    volume.filesystem, volume.type_label, str(target),
                    stats.files_copied, stats.bytes_copied,
                    "cancelled" if stats.cancelled else "ok",
                ])
        except OSError as exc:
            self.log("warn", f"写入磁盘日志失败：{exc}")

    # ------------------------------------------------------------ 测试/单次模式
    def copy_once(self, volume: VolumeInfo, dry_run: bool = False) -> CopyStats:
        """立即复制一次（不做筛选），供 --once 与单元测试使用。"""
        folder = build_folder_name(self.cfg.folder_rule, volume)
        target = Path(self.cfg.destination).expanduser() / folder
        stats = copy_volume(volume, target, self.cfg, Event(), self.log, dry_run=dry_run)
        if not dry_run:
            self._append_disk_log(volume, target, stats)
        return stats
