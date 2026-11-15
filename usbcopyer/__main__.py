"""命令行入口。

用法：
    python -m usbcopyer                 以窗口模式运行（默认）
    python -m usbcopyer --headless      无界面运行，日志打到控制台
    python -m usbcopyer --settings      只打开设置窗口
    python -m usbcopyer --list          列出当前所有驱动器及序列号
    python -m usbcopyer --once E:       立即把 E: 复制一次（测试用）
    python -m usbcopyer --once E: --dry-run   只统计不写文件
    python -m usbcopyer --reset         恢复默认配置
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import APP_NAME, __version__
from .app import UsbCopyerApp
from .config import Config
from .winapi import SingleInstance, list_drive_roots, volume_info


def cmd_list() -> int:
    roots = list_drive_roots()
    if not roots:
        print("没有检测到任何驱动器")
        return 0
    print(f"{'盘符':<5}{'卷标':<16}{'序列号':<12}{'文件系统':<10}{'类型':<12}可用空间")
    for root in roots:
        info = volume_info(root)
        if info is None:
            print(f"{root[0]:<5}{'(未就绪)':<16}")
            continue
        free = "-"
        try:
            import shutil
            usage = shutil.disk_usage(root)
            free = f"{usage.free / (1024 ** 3):.1f}GB / {usage.total / (1024 ** 3):.1f}GB"
        except OSError:
            pass
        print(f"{info.letter + ':':<5}{info.label or '(无卷标)':<16}{info.serial:<12}{info.filesystem:<10}{info.type_label:<12}{free}")
    return 0


def cmd_once(cfg: Config, letter: str, dry_run: bool) -> int:
    letter = letter.strip().rstrip("\\").rstrip(":").upper()[:1]
    root = f"{letter}:\\"
    info = volume_info(root)
    if info is None:
        print(f"无法读取 {root}，设备可能未就绪")
        return 2

    if not cfg.destination.strip():
        print("尚未设置备份目录，请先用 --settings 配置")
        return 2

    print(f"设备：{info.letter}: {info.label or '无卷标'} [{info.serial}] {info.filesystem} {info.type_label}")
    app = UsbCopyerApp(cfg, cfg.log_path)
    stats = app.copy_once(info, dry_run=dry_run)
    print(("（演练，未写文件）" if dry_run else "") + stats.summary())
    for path, err in stats.errors[:20]:
        print(f"  失败 {path}: {err}")
    return 0 if stats.files_failed == 0 else 1


def cmd_headless(cfg: Config) -> int:
    app = UsbCopyerApp(cfg, cfg.log_path)
    app.add_listener(lambda level, msg: print(f"[{level.upper():<5}] {msg}", flush=True))
    print(f"{APP_NAME} v{__version__} 无界面模式，Ctrl+C 退出")
    print(f"备份目录：{cfg.destination or '(未设置！请用 --settings 配置)'}")
    print(f"配置位置：{cfg.root_dir / 'config.json'}")
    app.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        app.stop()
    return 0


def cmd_gui(cfg: Config) -> int:
    try:
        from .ui import ControlPanel
    except ImportError as exc:
        print(f"无法加载图形界面（{exc}），改用无界面模式", file=sys.stderr)
        return cmd_headless(cfg)

    panel = ControlPanel(cfg)
    panel.mainloop()
    return 0


def cmd_settings(cfg: Config) -> int:
    import tkinter as tk
    from .ui import SettingsDialog

    root = tk.Tk()
    root.withdraw()

    def saved(new_cfg: Config) -> None:
        nonlocal cfg
        cfg = new_cfg

    dlg = SettingsDialog(root, cfg, saved)
    dlg.grab_set()
    root.mainloop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="usbcopyer", description=f"{APP_NAME} v{__version__}")
    p.add_argument("--config", metavar="PATH", help="指定配置文件路径")
    p.add_argument("--headless", action="store_true", help="无界面运行")
    p.add_argument("--settings", action="store_true", help="只打开设置窗口")
    p.add_argument("--list", action="store_true", help="列出所有驱动器信息")
    p.add_argument("--once", metavar="盘符", help="立即复制指定磁盘一次")
    p.add_argument("--dry-run", action="store_true", help="配合 --once，只统计不写文件")
    p.add_argument("--reset", action="store_true", help="恢复默认配置后退出")
    p.add_argument("--selftest", action="store_true", help="自检：验证打包后的 tkinter 与模块可用")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return p


def _configure_console() -> None:
    """控制台编码兜底。

    英文版 Windows 的控制台代码页是 1252，直接 print 中文会抛
    UnicodeEncodeError 把进程打死。这里保留原编码、只把错误策略改成
    replace：中文系统照常显示，非中文系统最多显示成 ?，不会崩。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

def cmd_selftest() -> int:
    """在打包后的 exe 里验证 tkinter 与界面模块是否被正确打进去。"""
    import tkinter as tk

    from . import ui  # noqa: F401  确保 ui 模块可导入

    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
    print("selftest OK: tkinter available, ui module bundled")
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config)

    if args.selftest:
        return cmd_selftest()

    if args.reset:
        path = cfg.save(args.config)
        print(f"已恢复默认配置：{path}")
        return 0

    if args.list:
        return cmd_list()

    if args.once:
        return cmd_once(cfg, args.once, args.dry_run)

    if args.settings:
        return cmd_settings(cfg)

    guard = None
    if cfg.single_instance and not args.headless:
        guard = SingleInstance("USBCopyerPy.SingleInstance")
        if not guard.acquired:
            print("已经有一个实例在运行。", file=sys.stderr)
            return 9

    try:
        return cmd_headless(cfg) if args.headless else cmd_gui(cfg)
    finally:
        if guard:
            guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
