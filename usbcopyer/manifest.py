"""增量清单：记住"哪些文件已经备份过"，让重复备份只处理变化的文件。

清单放在备份目标目录里（`.usbcopyer-manifest.json`），跟着备份走：
换一台电脑、重装系统后依然知道哪些文件已经拷过。

判定"没变"要求三件事同时成立：清单里的 size 和 mtime_ns 与源一致，
且目标文件仍然存在、大小相同。任何一项不成立就重新复制。
"""

from __future__ import annotations

import ctypes
import json
import os
from datetime import datetime
from pathlib import Path
from threading import Lock

MANIFEST_NAME = ".usbcopyer-manifest.json"
MANIFEST_VERSION = 1
FILE_ATTRIBUTE_HIDDEN = 0x02


def _hide_on_windows(path: Path) -> None:
    """Windows 下给清单文件加隐藏属性，免得用户被这个点文件困惑。"""
    try:
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


class Manifest:
    def __init__(self, path: Path, source: str = ""):
        self.path = Path(path)
        self.source = source
        self._files: dict[str, dict] = {}
        self._lock = Lock()
        self._dirty = False

    # ---------------------------------------------------------------- 创建
    @classmethod
    def for_target(cls, target_dir, source: str = "") -> "Manifest":
        return cls(Path(target_dir) / MANIFEST_NAME, source)

    def load(self) -> "Manifest":
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self
        if not isinstance(raw, dict) or raw.get("version") != MANIFEST_VERSION:
            return self
        files = raw.get("files")
        if isinstance(files, dict):
            with self._lock:
                self._files = {k: v for k, v in files.items() if isinstance(v, dict)}
        if raw.get("source"):
            self.source = raw.get("source") or self.source
        return self

    # ---------------------------------------------------------------- 查询
    def is_unchanged(self, rel: str, size: int, mtime_ns: int, dest) -> bool:
        with self._lock:
            entry = self._files.get(rel)
        if not entry:
            return False
        if entry.get("size") != size or entry.get("mtime") != mtime_ns:
            return False
        try:
            return os.path.getsize(dest) == size
        except OSError:
            return False

    # ---------------------------------------------------------------- 记录
    def record(self, rel: str, size: int, mtime_ns: int) -> None:
        with self._lock:
            self._files[rel] = {"size": size, "mtime": mtime_ns}
            self._dirty = True

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._files)

    def save(self) -> bool:
        with self._lock:
            if not self._dirty:
                return False
            payload = {
                "version": MANIFEST_VERSION,
                "source": self.source,
                "updated": datetime.now().isoformat(timespec="seconds"),
                "files": dict(self._files),
            }
            self._dirty = False

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            _hide_on_windows(self.path)
            return True
        except OSError:
            return False