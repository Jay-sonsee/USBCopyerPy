"""复制引擎：分块复制、可取消、冲突策略、过滤、进度回调。

设计要点（对应原 C# 版踩过的坑）：
- 用 1MB 分块读写，`cancel_event` 在每个分块间检查，取消立即生效
- 取消或失败时删除写了一半的目标文件，避免"半截文件比源文件新、永远不再覆盖"
- 目标目录按需创建，天然跳过空目录
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Callable

from .config import (
    CONFLICT_OVERWRITE,
    CONFLICT_RENAME,
    SIZE_MAX,
    SIZE_MIN,
    Config,
)
from .rules import ext_allowed, parse_exclude_patterns, should_exclude_dir

CHUNK_SIZE = 1024 * 1024  # 1MB


class Cancelled(Exception):
    """用户取消或设备被拔出。"""


@dataclass
class CopyStats:
    files_copied: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    bytes_copied: int = 0
    dirs_created: int = 0
    excluded_dirs: int = 0
    cancelled: bool = False
    errors: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"复制 {self.files_copied} 个文件", f"{format_size(self.bytes_copied)}"]
        if self.files_skipped:
            parts.append(f"跳过 {self.files_skipped}")
        if self.files_failed:
            parts.append(f"失败 {self.files_failed}")
        if self.cancelled:
            parts.append("已取消")
        return "，".join(parts)


def format_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}TB"


def _copy_file_chunked(src: str, dst: str, cancel: Event) -> int:
    """分块复制并保留时间戳；取消或异常时删除半成品。返回字节数。"""
    written = 0
    created = False
    try:
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            created = True
            while True:
                if cancel.is_set():
                    raise Cancelled()
                chunk = fin.read(CHUNK_SIZE)
                if not chunk:
                    break
                fout.write(chunk)
                written += len(chunk)
        st = os.stat(src)
        os.utime(dst, (st.st_atime, st.st_mtime))
        return written
    except BaseException:
        if created:
            try:
                os.unlink(dst)
            except OSError:
                pass
        raise


def assert_target_outside_source(src_root: str, dst_root: str) -> None:
    """拒绝把备份目录放在被备份的磁盘内部（避免自我递归）。

    注意：在 Windows 上 os.path.commonpath 对跨盘符路径会抛 ValueError，
    这里必须先比较盘符，否则把目标放到另一个盘就会误报。
    """
    src_drive, _ = os.path.splitdrive(src_root)
    dst_drive, _ = os.path.splitdrive(dst_root)
    if src_drive.lower() != dst_drive.lower():
        return
    try:
        common = os.path.commonpath([src_root, dst_root])
    except ValueError:
        return
    if os.path.normcase(common) == os.path.normcase(src_root):
        raise ValueError("备份目录不能位于被备份的磁盘内部")

def _unique_name(path: str) -> str:
    parent, name = os.path.split(path)
    return os.path.join(parent, f"{uuid.uuid4().hex[:8]}-{name}")


def scan_volume(volume, cfg: Config) -> tuple[int, int]:
    """预扫描：统计实际会被复制的文件数与字节数，供进度条计算百分比。

    与 copy_volume 使用同一套过滤规则（排除文件夹 / 扩展名 / 大小限制），
    只读元数据，不读文件内容，几万个文件通常一两秒内完成。
    """
    src_root = os.path.abspath(volume.root)
    patterns = parse_exclude_patterns(cfg.exclude_dirs)
    size_limit = cfg.size_limit_mb * 1024 * 1024
    files = 0
    total = 0

    stack: list[tuple[str, str]] = [(src_root, "")]
    while stack:
        current, rel = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue

        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    child_rel = f"{rel}\\{entry.name}" if rel else entry.name
                    if not should_exclude_dir(child_rel, entry.name, patterns):
                        stack.append((entry.path, child_rel))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if not ext_allowed(entry.path, cfg.ext_mode, cfg.black_exts, cfg.white_exts, cfg.copy_no_ext):
                    continue
                size = entry.stat().st_size
            except OSError:
                continue

            if cfg.size_mode == SIZE_MAX and size >= size_limit:
                continue
            if cfg.size_mode == SIZE_MIN and size <= size_limit:
                continue
            files += 1
            total += size

    return files, total

def copy_volume(
    volume,
    target_dir: Path,
    cfg: Config,
    cancel: Event,
    log: Callable[[str, str], None],
    progress: Callable[[int, int, str], None] | None = None,
    dry_run: bool = False,
) -> CopyStats:
    """把整卷复制到 target_dir。volume 需有 .root / .letter。"""
    src_root = os.path.abspath(volume.root)
    dst_root = os.path.abspath(str(target_dir))
    stats = CopyStats()

    assert_target_outside_source(src_root, dst_root)

    if cfg.clear_target_first and os.path.isdir(dst_root) and not dry_run:
        log("info", f"清空输出目录：{dst_root}")
        shutil.rmtree(dst_root, ignore_errors=True)

    if not cfg.skip_empty_dirs and not dry_run:
        os.makedirs(dst_root, exist_ok=True)

    size_limit = cfg.size_limit_mb * 1024 * 1024
    exclude_patterns = parse_exclude_patterns(cfg.exclude_dirs)

    def walk(current_src: str, current_dst: str, rel_path: str) -> None:
        if cancel.is_set():
            raise Cancelled()
        try:
            entries = list(os.scandir(current_src))
        except OSError as exc:
            stats.files_failed += 1
            stats.errors.append((current_src, str(exc)))
            log("warn", f"无法读取目录 {current_src}：{exc}")
            return

        for entry in entries:
            if cancel.is_set():
                raise Cancelled()
            try:
                if entry.is_dir(follow_symlinks=False):
                    child_rel = f"{rel_path}\\{entry.name}" if rel_path else entry.name
                    if should_exclude_dir(child_rel, entry.name, exclude_patterns):
                        stats.excluded_dirs += 1
                        continue
                    walk(entry.path, os.path.join(current_dst, entry.name), child_rel)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError as exc:
                stats.files_failed += 1
                stats.errors.append((entry.path, str(exc)))
                continue

            if not ext_allowed(entry.path, cfg.ext_mode, cfg.black_exts, cfg.white_exts, cfg.copy_no_ext):
                stats.files_skipped += 1
                continue

            try:
                stat = entry.stat()
            except OSError:
                stats.files_skipped += 1
                continue

            if cfg.size_mode == SIZE_MAX and stat.st_size >= size_limit:
                stats.files_skipped += 1
                continue
            if cfg.size_mode == SIZE_MIN and stat.st_size <= size_limit:
                stats.files_skipped += 1
                continue

            dest_path = os.path.join(current_dst, entry.name)

            if os.path.exists(dest_path):
                if cfg.conflict == CONFLICT_RENAME:
                    dest_path = _unique_name(dest_path)
                elif cfg.conflict == CONFLICT_OVERWRITE:
                    pass
                else:  # skip_if_newer
                    try:
                        if stat.st_mtime <= os.path.getmtime(dest_path):
                            stats.files_skipped += 1
                            continue
                    except OSError:
                        pass

            if dry_run:
                stats.files_copied += 1
                stats.bytes_copied += stat.st_size
                continue

            try:
                parent = os.path.dirname(dest_path)
                if not os.path.isdir(parent):
                    os.makedirs(parent, exist_ok=True)
                    stats.dirs_created += 1
                written = _copy_file_chunked(entry.path, dest_path, cancel)
                stats.files_copied += 1
                stats.bytes_copied += written
                if progress:
                    progress(stats.files_copied, stats.bytes_copied, entry.name)
            except Cancelled:
                raise
            except OSError as exc:
                stats.files_failed += 1
                stats.errors.append((entry.path, str(exc)))
                log("warn", f"复制失败 {entry.path}：{exc}")

    try:
        walk(src_root, dst_root, "")
    except Cancelled:
        stats.cancelled = True
        log("warn", f"{volume.letter}: 复制已取消")

    return stats
