"""回调脚本执行：占位符替换 + 子进程调用。

与原版一致，回调脚本放在程序配置目录，文件名固定：
- DiskDetectedCallback.bat   检测到磁盘时
- AllCompletedCallback.bat   该磁盘复制完成时
占位符：{$VolumeSerialNumber} {$VolumeName} {$Volume} {$DriveType} {$USBDir}
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from .winapi import DRIVE_TYPE_LABELS

CALLBACK_TIMEOUT = 300  # 秒


def build_script(template: str, volume, usb_dir: str) -> str:
    replacements = {
        "{$VolumeSerialNumber}": volume.serial,
        "{$VolumeName}": volume.label,
        "{$Volume}": f"{volume.letter}:",
        "{$DriveType}": str(DRIVE_TYPE_LABELS.get(volume.drive_type, "")),
        "{$USBDir}": usb_dir,
    }
    result = template
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result


def run_callback(
    conf_dir: Path,
    name: str,
    volume,
    usb_dir: str,
    log: Callable[[str, str], None],
    wait: bool = False,
) -> int | None:
    """执行回调脚本，返回退出码；脚本不存在时返回 None。"""
    script = conf_dir / name
    if not script.exists():
        return None

    try:
        template = script.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log("warn", f"读取回调 {name} 失败：{exc}")
        return None

    body = build_script(template, volume, usb_dir)
    tmp = conf_dir / f"__cb_{os.getpid()}_{abs(hash(name)) % 10000}.bat"
    try:
        tmp.write_text(body, encoding="utf-8", errors="replace")
        proc = subprocess.run(
            ["cmd.exe", "/c", str(tmp)],
            cwd=str(conf_dir),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=CALLBACK_TIMEOUT,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        log("info", f"回调 {name} 退出码 {proc.returncode}" + (f"，输出：{output.strip()[:500]}" if output.strip() else ""))
        return proc.returncode
    except subprocess.TimeoutExpired:
        log("warn", f"回调 {name} 超时（>{CALLBACK_TIMEOUT}s）")
        return -1
    except OSError as exc:
        log("warn", f"回调 {name} 执行失败：{exc}")
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
