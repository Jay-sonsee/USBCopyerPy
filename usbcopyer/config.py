"""配置：dataclass 定义 + JSON 读写 + 迁移默认值。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR_NAME = "USBCopyerPy"

# 冲突策略
CONFLICT_SKIP_IF_NEWER = "skip_if_newer"   # 目标不存在或源更新才复制（默认，最安全）
CONFLICT_OVERWRITE = "overwrite"           # 总是覆盖
CONFLICT_RENAME = "rename"                 # 重命名后另存

CONFLICT_CHOICES = (CONFLICT_SKIP_IF_NEWER, CONFLICT_OVERWRITE, CONFLICT_RENAME)

# 扩展名过滤模式
EXT_ALL = "all"
EXT_BLACK = "black"
EXT_WHITE = "white"
EXT_CHOICES = (EXT_ALL, EXT_BLACK, EXT_WHITE)

# 大小限制模式
SIZE_NONE = "none"
SIZE_MAX = "max"      # 只复制小于限制的文件
SIZE_MIN = "min"      # 只复制大于限制的文件
SIZE_CHOICES = (SIZE_NONE, SIZE_MAX, SIZE_MIN)

# 磁盘黑白名单模式
DISK_BLACK = "black"   # 复制除名单外的磁盘
DISK_WHITE = "white"   # 只复制名单内的磁盘
DISK_CHOICES = (DISK_BLACK, DISK_WHITE)

# 驱动器类型键（对应 GetDriveType）
DRIVE_TYPE_KEYS = ("unknown", "no_root", "removable", "fixed", "remote", "cdrom", "ramdisk")

# 默认跳过：网络盘、光盘、本地硬盘、其它（保留可移动磁盘）
DEFAULT_SKIP_TYPES = ["remote", "cdrom", "fixed", "unknown", "no_root", "ramdisk"]


def app_base_dir() -> Path:
    """程序所在目录：打包后是 exe 所在目录，源码运行时是项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


_PORTABLE_DIR: Path | None = None


def default_config_dir() -> Path:
    """便携优先：配置与数据放在程序目录；目录只读时退回 %APPDATA%。

    U 盘/绿色版场景下整个目录可以直接拷走，配置跟着走。
    """
    global _PORTABLE_DIR
    if _PORTABLE_DIR is not None:
        return _PORTABLE_DIR

    base = app_base_dir()
    probe = base / ".usbcopyer_write_test"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        _PORTABLE_DIR = base
    except OSError:
        fallback = Path(os.environ.get("APPDATA") or Path.home()) / APP_DIR_NAME
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        _PORTABLE_DIR = fallback
    return _PORTABLE_DIR


@dataclass
class Config:
    # —— 输出 ——
    destination: str = ""
    folder_rule: str = "[serial]"
    clear_target_first: bool = False      # 复制前清空该设备的输出目录

    # —— 复制行为 ——
    conflict: str = CONFLICT_SKIP_IF_NEWER
    delay_seconds: int = 0                # 插入后延迟多少秒再开始
    skip_empty_dirs: bool = True
    max_parallel_copies: int = 1
    exclude_dirs: str = ""                # 排除的文件夹，逗号/换行分隔，支持通配符

    # —— 扩展名过滤 ——
    ext_mode: str = EXT_ALL
    black_exts: str = ""
    white_exts: str = ""
    copy_no_ext: bool = True

    # —— 大小限制 ——
    size_mode: str = SIZE_NONE
    size_limit_mb: int = 1000

    # —— 磁盘筛选 ——
    skip_types: list[str] = field(default_factory=lambda: list(DEFAULT_SKIP_TYPES))
    disk_mode: str = DISK_BLACK
    black_serials: str = ""
    black_letters: str = ""

    # —— 回调 ——
    callbacks_enabled: bool = True
    wait_callback: bool = False

    # —— 运行 ——
    poll_interval: float = 1.0
    single_instance: bool = True
    minimize_to_tray: bool = True
    show_balloon: bool = True
    close_to_background: bool = True      # 关窗口时保持后台运行，再启动一次即可唤出

    # —— 路径（空表示用默认）——
    data_dir: str = ""
    config_dir: str = ""

    # ---------------- 派生属性 ----------------
    @property
    def root_dir(self) -> Path:
        """程序数据根目录（日志、缓存、默认输出）。"""
        if self.config_dir:
            return Path(self.config_dir).expanduser()
        return default_config_dir()

    @property
    def data_root(self) -> Path:
        if self.data_dir:
            return Path(self.data_dir).expanduser()
        return self.root_dir / "USBCopyerData"

    @property
    def log_path(self) -> Path:
        return self.root_dir / "usbcopyer.log"

    @property
    def disk_log_path(self) -> Path:
        return self.data_root / "Disks.csv"

    # ---------------- 校验 ----------------
    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.destination.strip():
            problems.append("尚未设置备份目录")
        if self.conflict not in CONFLICT_CHOICES:
            problems.append("冲突策略无效")
        if self.ext_mode not in EXT_CHOICES:
            problems.append("扩展名模式无效")
        if self.size_mode not in SIZE_CHOICES:
            problems.append("大小限制模式无效")
        if self.disk_mode not in DISK_CHOICES:
            problems.append("黑白名单模式无效")
        if self.delay_seconds < 0:
            problems.append("延迟时间不能为负")
        if self.size_limit_mb < 0:
            problems.append("大小限制不能为负")
        if self.poll_interval < 0.2:
            problems.append("轮询间隔不应小于 0.2 秒")
        try:
            from .rules import parse_folder_rule
            parse_folder_rule(self.folder_rule or "[serial]")
        except ValueError as exc:
            problems.append(f"分类规则无效：{exc}")
        return problems

    # ---------------- 持久化 ----------------
    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        p = Path(path) if path else default_config_dir() / "config.json"
        if not p.exists() and path is None:
            # 旧版本把配置放在 %APPDATA%，首次运行时平滑迁移过来
            legacy = Path(os.environ.get("APPDATA") or Path.home()) / APP_DIR_NAME / "config.json"
            if legacy.exists():
                p = legacy
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "Config":
        known = {f.name for f in fields(cls)}
        cfg = cls()
        for k, v in (raw or {}).items():
            if k not in known:
                continue
            current = getattr(cfg, k)
            # 宽容处理类型不一致的旧配置
            if isinstance(current, bool):
                setattr(cfg, k, bool(v))
            elif isinstance(current, int) and not isinstance(current, bool):
                try:
                    setattr(cfg, k, int(v))
                except (TypeError, ValueError):
                    pass
            elif isinstance(current, float):
                try:
                    setattr(cfg, k, float(v))
                except (TypeError, ValueError):
                    pass
            elif isinstance(current, list):
                setattr(cfg, k, [str(x) for x in v] if isinstance(v, (list, tuple)) else list(DEFAULT_SKIP_TYPES))
            else:
                setattr(cfg, k, "" if v is None else str(v))
        return cfg

    def save(self, path: str | os.PathLike | None = None) -> Path:
        p = Path(path) if path else self.root_dir / "config.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
        return p

    @staticmethod
    def default_path() -> Path:
        return default_config_dir() / "config.json"
