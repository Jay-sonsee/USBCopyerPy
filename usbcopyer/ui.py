"""tkinter 界面：控制面板 + 设置对话框。

只依赖标准库。所有来自工作线程的日志通过队列回到主线程刷新，
避免 tkinter 跨线程调用崩溃。
"""

from __future__ import annotations

import queue
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__
from .app import UsbCopyerApp
from .copier import format_size
from .winapi import ShowSignal
from .config import (
    CONFLICT_CHOICES,
    DISK_BLACK,
    DISK_WHITE,
    EXT_ALL,
    EXT_BLACK,
    EXT_WHITE,
    SIZE_MAX,
    SIZE_MIN,
    SIZE_NONE,
    Config,
)
from .rules import RULE_TOKENS, parse_folder_rule

def format_duration(seconds: float) -> str:
    """把秒数格式化成 mm:ss 或 h:mm:ss。"""
    seconds = int(max(0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


CONFLICT_LABELS = {
    "skip_if_newer": "仅当源文件更新时覆盖（推荐）",
    "overwrite": "总是覆盖",
    "rename": "重命名后另存",
}
CONFLICT_LABELS_REVERSE = {v: k for k, v in CONFLICT_LABELS.items()}

EXT_LABELS = {"all": "复制所有文件", "black": "黑名单（排除）", "white": "白名单（仅复制）"}
EXT_REVERSE = {v: k for k, v in EXT_LABELS.items()}

SIZE_LABELS = {"none": "不限制", "max": "只复制小于", "min": "只复制大于"}
SIZE_REVERSE = {v: k for k, v in SIZE_LABELS.items()}

TYPE_LABELS = {
    "removable": "可移动磁盘（U 盘）",
    "fixed": "本地硬盘",
    "remote": "网络驱动器",
    "cdrom": "光驱 / 虚拟光驱",
    "ramdisk": "虚拟磁盘",
    "unknown": "未知类型",
    "no_root": "无根目录",
}


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, cfg: Config, on_saved):
        super().__init__(master)
        self.title(f"{APP_NAME} 设置 v{__version__}")
        self.cfg = cfg
        self.on_saved = on_saved
        self.vars: dict[str, tk.Variable] = {}
        self.resizable(False, False)
        self._build()

    # ---------------------------------------------------------------- UI
    def _build(self) -> None:
        pad = {"padx": 6, "pady": 3}
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self._tab_basic(nb, pad)
        self._tab_filter(nb, pad)
        self._tab_disk(nb, pad)
        self._tab_adv(nb, pad)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bar, text="恢复默认", command=self._reset).pack(side="left")
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="保存", command=self._save).pack(side="right", padx=6)

    def _row(self, parent, row: int, label: str, widget) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=3)
        widget.grid(row=row, column=1, columnspan=2, sticky="we", padx=6, pady=3)

    def _tab_basic(self, nb, pad) -> None:
        f = ttk.Frame(nb, padding=6)
        nb.add(f, text="基本")
        f.columnconfigure(1, weight=1)

        self.vars["destination"] = tk.StringVar(value=self.cfg.destination)
        dest = ttk.Frame(f)
        ttk.Entry(dest, textvariable=self.vars["destination"], width=48).pack(side="left", fill="x", expand=True)
        ttk.Button(dest, text="浏览…", command=self._pick_dir).pack(side="left", padx=4)
        self._row(f, 0, "备份目录", dest)

        self.vars["folder_rule"] = tk.StringVar(value=self.cfg.folder_rule)
        self._row(f, 1, "分类规则", ttk.Entry(f, textvariable=self.vars["folder_rule"], width=40))
        hint = "占位符：" + "  ".join(f"[{k}]={v}" for k, v in RULE_TOKENS.items())
        ttk.Label(f, text=hint, foreground="#666").grid(row=2, column=1, sticky="w", padx=6)

        self.vars["conflict"] = tk.StringVar(value=CONFLICT_LABELS.get(self.cfg.conflict, "仅当源文件更新时覆盖（推荐）"))
        self._row(f, 3, "文件冲突", ttk.Combobox(f, textvariable=self.vars["conflict"], values=list(CONFLICT_LABELS.values()), state="readonly", width=30))

        self.vars["delay_seconds"] = tk.IntVar(value=self.cfg.delay_seconds)
        self._row(f, 4, "延迟复制（秒）", ttk.Spinbox(f, from_=0, to=3600, textvariable=self.vars["delay_seconds"], width=10))

        self.vars["clear_target_first"] = tk.BooleanVar(value=self.cfg.clear_target_first)
        ttk.Checkbutton(f, text="复制前先清空该设备的输出目录", variable=self.vars["clear_target_first"]).grid(row=5, column=1, sticky="w", padx=6, pady=3)

        self.vars["skip_empty_dirs"] = tk.BooleanVar(value=self.cfg.skip_empty_dirs)
        ttk.Checkbutton(f, text="跳过空文件夹", variable=self.vars["skip_empty_dirs"]).grid(row=6, column=1, sticky="w", padx=6, pady=3)

        self.vars["use_manifest"] = tk.BooleanVar(value=self.cfg.use_manifest)
        ttk.Checkbutton(f, text="使用增量清单（跳过已备份且未变化的文件）",
                        variable=self.vars["use_manifest"]).grid(row=7, column=1, sticky="w", padx=6, pady=3)

    def _tab_filter(self, nb, pad) -> None:
        f = ttk.Frame(nb, padding=6)
        nb.add(f, text="过滤")
        f.columnconfigure(1, weight=1)

        self.vars["ext_mode"] = tk.StringVar(value=EXT_LABELS.get(self.cfg.ext_mode, "复制所有文件"))
        self._row(f, 0, "扩展名模式", ttk.Combobox(f, textvariable=self.vars["ext_mode"], values=list(EXT_LABELS.values()), state="readonly", width=24))

        self.vars["black_exts"] = tk.StringVar(value=self.cfg.black_exts)
        self._row(f, 1, "黑名单扩展名", ttk.Entry(f, textvariable=self.vars["black_exts"], width=48))
        self.vars["white_exts"] = tk.StringVar(value=self.cfg.white_exts)
        self._row(f, 2, "白名单扩展名", ttk.Entry(f, textvariable=self.vars["white_exts"], width=48))
        ttk.Label(f, text="用逗号分隔，不用写点，例如：jpg,png,mp4", foreground="#666").grid(row=3, column=1, sticky="w", padx=6)

        self.vars["copy_no_ext"] = tk.BooleanVar(value=self.cfg.copy_no_ext)
        ttk.Checkbutton(f, text="复制没有扩展名的文件（如 LICENSE、README）", variable=self.vars["copy_no_ext"]).grid(row=4, column=1, sticky="w", padx=6, pady=(8, 3))

        self.vars["size_mode"] = tk.StringVar(value=SIZE_LABELS.get(self.cfg.size_mode, "不限制"))
        self._row(f, 5, "大小限制", ttk.Combobox(f, textvariable=self.vars["size_mode"], values=list(SIZE_LABELS.values()), state="readonly", width=18))
        self.vars["size_limit_mb"] = tk.IntVar(value=self.cfg.size_limit_mb)
        self._row(f, 6, "阈值（MB）", ttk.Spinbox(f, from_=0, to=1024 * 1024, textvariable=self.vars["size_limit_mb"], width=10))

        ttk.Separator(f, orient="horizontal").grid(row=7, column=0, columnspan=3, sticky="we", padx=6, pady=(10, 6))
        ttk.Label(f, text="排除的文件夹（不备份，逗号或换行分隔，支持 * 通配符）").grid(
            row=8, column=0, columnspan=3, sticky="w", padx=6)
        self.exclude_text = tk.Text(f, height=4, width=52, wrap="word")
        self.exclude_text.grid(row=9, column=0, columnspan=3, sticky="we", padx=6, pady=4)
        self.exclude_text.insert("1.0", self.cfg.exclude_dirs)
        ttk.Label(
            f,
            text="例：node_modules、.git、System Volume Information、photos\\2020、*.tmp",
            foreground="#666",
        ).grid(row=10, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 4))

    def _tab_disk(self, nb, pad) -> None:
        f = ttk.Frame(nb, padding=6)
        nb.add(f, text="磁盘筛选")
        ttk.Label(f, text="跳过的驱动器类型：").grid(row=0, column=0, sticky="nw", padx=6, pady=6)

        box = ttk.Frame(f)
        box.grid(row=0, column=1, sticky="w", padx=6, pady=6)
        self.type_vars = {}
        for i, key in enumerate(("removable", "fixed", "remote", "cdrom", "ramdisk", "unknown")):
            var = tk.BooleanVar(value=key in self.cfg.skip_types)
            self.type_vars[key] = var
            ttk.Checkbutton(box, text=TYPE_LABELS.get(key, key), variable=var).grid(row=i // 3, column=i % 3, sticky="w", padx=4, pady=2)

        mode = ttk.LabelFrame(f, text="黑白名单")
        mode.grid(row=1, column=0, columnspan=2, sticky="we", padx=6, pady=10)
        self.vars["disk_mode"] = tk.StringVar(value=self.cfg.disk_mode)
        ttk.Radiobutton(mode, text="黑名单：复制除名单外的所有设备", variable=self.vars["disk_mode"], value=DISK_BLACK).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=2)
        ttk.Radiobutton(mode, text="白名单：只复制名单内的设备", variable=self.vars["disk_mode"], value=DISK_WHITE).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=2)

        self.vars["black_serials"] = tk.StringVar(value=self.cfg.black_serials)
        ttk.Label(mode, text="序列号").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(mode, textvariable=self.vars["black_serials"], width=44).grid(row=2, column=1, sticky="we", padx=6, pady=3)
        self.vars["black_letters"] = tk.StringVar(value=self.cfg.black_letters)
        ttk.Label(mode, text="盘符").grid(row=3, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(mode, textvariable=self.vars["black_letters"], width=44).grid(row=3, column=1, sticky="we", padx=6, pady=3)
        ttk.Label(mode, text="多个用逗号分隔，例如：E,F 或 1A2B3C4D", foreground="#666").grid(row=4, column=1, sticky="w", padx=6)

    def _tab_adv(self, nb, pad) -> None:
        f = ttk.Frame(nb, padding=6)
        nb.add(f, text="高级")
        f.columnconfigure(1, weight=1)

        self.vars["callbacks_enabled"] = tk.BooleanVar(value=self.cfg.callbacks_enabled)
        ttk.Checkbutton(f, text="启用回调脚本（DiskDetectedCallback.bat / AllCompletedCallback.bat）", variable=self.vars["callbacks_enabled"]).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=3)
        self.vars["wait_callback"] = tk.BooleanVar(value=self.cfg.wait_callback)
        ttk.Checkbutton(f, text="等待回调完成；返回非 0 时取消复制", variable=self.vars["wait_callback"]).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=3)

        self.vars["poll_interval"] = tk.DoubleVar(value=self.cfg.poll_interval)
        self._row(f, 2, "轮询间隔（秒）", ttk.Spinbox(f, from_=0.2, to=10, increment=0.2, textvariable=self.vars["poll_interval"], width=8))

        self.vars["max_parallel_copies"] = tk.IntVar(value=self.cfg.max_parallel_copies)
        self._row(f, 3, "同时复制设备数", ttk.Spinbox(f, from_=1, to=4, textvariable=self.vars["max_parallel_copies"], width=8))

        self.vars["single_instance"] = tk.BooleanVar(value=self.cfg.single_instance)
        ttk.Checkbutton(f, text="只允许运行一个实例", variable=self.vars["single_instance"]).grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=3)

        self.vars["data_dir"] = tk.StringVar(value=self.cfg.data_dir)
        self._row(f, 5, "数据目录（留空用默认）", ttk.Entry(f, textvariable=self.vars["data_dir"], width=46))

        self.vars["copy_threads"] = tk.IntVar(value=self.cfg.copy_threads)
        self._row(f, 6, "复制线程数（1=串行）",
                  ttk.Spinbox(f, from_=1, to=16, textvariable=self.vars["copy_threads"], width=8))

        self.vars["close_to_background"] = tk.BooleanVar(value=self.cfg.close_to_background)
        ttk.Checkbutton(f, text="关闭窗口时保持后台运行（再启动一次可唤出窗口）",
                        variable=self.vars["close_to_background"]).grid(
            row=7, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 3))

        self.vars["tray_icon"] = tk.BooleanVar(value=self.cfg.tray_icon)
        ttk.Checkbutton(f, text="显示系统托盘图标（右键菜单：显示/暂停/停止/退出）",
                        variable=self.vars["tray_icon"]).grid(
            row=8, column=0, columnspan=2, sticky="w", padx=6, pady=3)

        ttk.Label(f, text="轮询间隔、单实例需要重启程序后生效", foreground="#a15c00").grid(row=9, column=0, columnspan=2, sticky="w", padx=6, pady=(10, 0))

    # ---------------------------------------------------------------- 动作
    def _pick_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=self.vars["destination"].get() or None)
        if path:
            self.vars["destination"].set(path)

    def _reset(self) -> None:
        if not messagebox.askyesno("恢复默认", "所有设置将恢复为默认值，确定吗？"):
            return
        self.cfg = Config()
        self.destroy()
        SettingsDialog(self.master, self.cfg, self.on_saved).grab_set()

    def _collect(self) -> Config:
        cfg = self.cfg
        cfg.destination = self.vars["destination"].get().strip()
        cfg.folder_rule = self.vars["folder_rule"].get().strip() or "[serial]"
        cfg.conflict = CONFLICT_LABELS_REVERSE.get(self.vars["conflict"].get(), "skip_if_newer")
        cfg.delay_seconds = int(self.vars["delay_seconds"].get())
        cfg.clear_target_first = bool(self.vars["clear_target_first"].get())
        cfg.skip_empty_dirs = bool(self.vars["skip_empty_dirs"].get())
        cfg.use_manifest = bool(self.vars["use_manifest"].get())

        cfg.ext_mode = EXT_REVERSE.get(self.vars["ext_mode"].get(), EXT_ALL)
        cfg.black_exts = self.vars["black_exts"].get().strip()
        cfg.white_exts = self.vars["white_exts"].get().strip()
        cfg.copy_no_ext = bool(self.vars["copy_no_ext"].get())

        cfg.size_mode = SIZE_REVERSE.get(self.vars["size_mode"].get(), SIZE_NONE)
        cfg.size_limit_mb = int(self.vars["size_limit_mb"].get())
        cfg.exclude_dirs = self.exclude_text.get("1.0", "end").strip()

        cfg.skip_types = [k for k, v in self.type_vars.items() if v.get()]
        cfg.disk_mode = self.vars["disk_mode"].get()
        cfg.black_serials = self.vars["black_serials"].get().strip()
        cfg.black_letters = self.vars["black_letters"].get().strip()

        cfg.callbacks_enabled = bool(self.vars["callbacks_enabled"].get())
        cfg.wait_callback = bool(self.vars["wait_callback"].get())
        cfg.poll_interval = float(self.vars["poll_interval"].get())
        cfg.max_parallel_copies = int(self.vars["max_parallel_copies"].get())
        cfg.single_instance = bool(self.vars["single_instance"].get())
        cfg.copy_threads = int(self.vars["copy_threads"].get())
        cfg.close_to_background = bool(self.vars["close_to_background"].get())
        cfg.tray_icon = bool(self.vars["tray_icon"].get())
        cfg.data_dir = self.vars["data_dir"].get().strip()
        return cfg

    def _save(self) -> None:
        try:
            parse_folder_rule(self.vars["folder_rule"].get().strip() or "[serial]")
        except ValueError as exc:
            messagebox.showerror("分类规则无效", str(exc), parent=self)
            return

        cfg = self._collect()
        problems = cfg.validate()
        if problems:
            messagebox.showerror("设置有问题", "\n".join(f"• {p}" for p in problems), parent=self)
            return

        try:
            path = cfg.save()
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return

        self.on_saved(cfg)
        messagebox.showinfo("已保存", f"配置已写入：\n{path}", parent=self)
        self.destroy()


class ControlPanel(tk.Tk):
    """主窗口：状态 + 日志 + 按钮。"""

    def __init__(self, cfg: Config):
        super().__init__()
        self.title(f"{APP_NAME} v{__version__}")
        self.geometry("720x460")
        self.minsize(560, 360)
        self.cfg = cfg
        self.app = UsbCopyerApp(cfg, cfg.log_path)
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._actions: queue.Queue[str] = queue.Queue()
        self.tray = None
        self.app.add_listener(lambda level, msg: self._queue.put((level, msg)))
        self._build()
        self.app.start()
        self._init_tray()
        self._signal = ShowSignal()
        self._signal.listen(lambda: self.after(0, self._restore_window))
        self.after(200, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        self.status_var = tk.StringVar(value="空闲")
        ttk.Label(top, textvariable=self.status_var, font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")

        btns = ttk.Frame(top)
        btns.pack(side="right")
        self.pause_btn = ttk.Button(btns, text="暂停", command=self._toggle_pause)
        self.pause_btn.pack(side="left", padx=3)
        ttk.Button(btns, text="停止复制", command=lambda: self.app.cancel_all()).pack(side="left", padx=3)
        ttk.Button(btns, text="设置…", command=self._open_settings).pack(side="left", padx=3)
        ttk.Button(btns, text="打开目录", command=self._open_dest).pack(side="left", padx=3)
        ttk.Button(btns, text="退出", command=self.quit_app).pack(side="left", padx=3)

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100.0)
        self.progress.pack(fill="x", padx=10, pady=(8, 0))
        self.progress_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.progress_var, foreground="#555").pack(anchor="w", padx=10, pady=(2, 4))

        self.log = tk.Text(self, height=18, state="disabled", wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=10)
        self.log.tag_config("warn", foreground="#a15c00")
        self.log.tag_config("error", foreground="#c0392b")
        self.log.tag_config("info", foreground="#222")

        ttk.Label(self, text=f"备份目录：{self.cfg.destination or '(未设置)'}", foreground="#555").pack(anchor="w", padx=10, pady=(4, 8))

    # ---------------------------------------------------------------- 托盘
    def _init_tray(self) -> None:
        if not self.cfg.tray_icon:
            return
        try:
            from .tray import TrayIcon
        except Exception as exc:
            self.app.log("warn", f"托盘图标模块不可用：{exc}")
            return
        try:
            tray = TrayIcon(
                tooltip=f"{APP_NAME} v{__version__}",
                on_show=lambda: self._actions.put("show"),
                on_toggle=lambda: self._actions.put("toggle"),
                on_stop=lambda: self._actions.put("stop"),
                on_open_dir=lambda: self._actions.put("open"),
                on_exit=lambda: self._actions.put("exit"),
            )
            if tray.start():
                self.tray = tray
                self.app.log("info", "托盘图标已启动：右键可操作，双击显示窗口")
            else:
                self.app.log("warn", f"托盘图标启动失败：{tray.error}")
        except Exception as exc:
            self.app.log("warn", f"托盘图标不可用：{exc}")

    def _handle_actions(self) -> None:
        while True:
            try:
                action = self._actions.get_nowait()
            except queue.Empty:
                return
            if action == "show":
                self._restore_window()
            elif action == "toggle":
                self._toggle_pause()
            elif action == "stop":
                self.app.cancel_all()
            elif action == "open":
                self._open_dest()
            elif action == "exit":
                self.quit_app()
                return

    def _update_tray(self, tasks: list[dict]) -> None:
        if not self.tray:
            return
        if tasks:
            first = tasks[0]
            if len(tasks) == 1:
                text = f"{APP_NAME}：{first['letter']}: {first['percent']:.0f}%  {format_size(int(first['speed']))}/s"
            else:
                text = f"{APP_NAME}：正在复制 {len(tasks)} 个设备"
        elif self.app.paused:
            text = f"{APP_NAME}：已暂停"
        else:
            text = f"{APP_NAME}：空闲，等待设备插入"
        self.tray.update_tooltip(text[:120])
        self.tray.set_toggle_label("恢复监听" if self.app.paused else "暂停监听")

    # ---------------------------------------------------------------- 事件
    def _toggle_pause(self) -> None:
        if self.app.paused:
            self.app.resume()
            self.pause_btn.config(text="暂停")
        else:
            self.app.pause()
            self.pause_btn.config(text="恢复")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self, self.cfg, self._on_settings_saved)
        dlg.transient(self)
        dlg.grab_set()

    def _on_settings_saved(self, cfg: Config) -> None:
        self.app.cfg = cfg
        self.cfg = cfg
        for child in self.winfo_children():
            if isinstance(child, ttk.Label) and str(child.cget("text")).startswith("备份目录"):
                child.config(text=f"备份目录：{cfg.destination or '(未设置)'}")
        self._append("info", "设置已更新（轮询间隔、单实例需重启生效）")

    def _open_dest(self) -> None:
        import subprocess
        dest = self.cfg.destination.strip()
        if not dest or not Path(dest).is_dir():
            messagebox.showwarning("提示", "备份目录不存在")
            return
        subprocess.Popen(["explorer.exe", dest])

    def _on_close(self) -> None:
        if self.cfg.close_to_background:
            self.withdraw()
            if self.tray:
                self.app.log("info", "已最小化到托盘，双击托盘图标可恢复窗口；右键菜单可退出")
            else:
                self._append("info", "已转入后台运行：再次启动程序即可唤出窗口")
            return
        self.quit_app()

    def _restore_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()
        self._append("info", "窗口已唤出")

    def quit_app(self) -> None:
        try:
            self.app.stop()
        finally:
            try:
                if self.tray:
                    self.tray.stop()
            except Exception:
                pass
            try:
                self._signal.stop()
            except Exception:
                pass
            self.destroy()

    # ---------------------------------------------------------------- 刷新
    def _drain(self) -> None:
        self._handle_actions()
        while True:
            try:
                level, msg = self._queue.get_nowait()
            except queue.Empty:
                break
            self._append(level, msg)

        tasks = self.app.active_tasks()
        if tasks:
            letters = "、".join(f"{t['letter']}:" for t in tasks)
            self.status_var.set(f"正在复制 {letters}（{len(tasks)} 个任务）")
            if len(tasks) == 1:
                task = tasks[0]
                self.progress["value"] = task["percent"]
                speed = task["speed"]
                speed_text = f"{format_size(int(speed))}/s" if speed > 0 else "计算中…"
                eta_text = format_duration(task["eta"]) if speed > 0 else "--:--"
                self.progress_var.set(
                    f"{task['letter']}: {task['percent']:.0f}%   "
                    f"{task['files']}/{task['total_files']} 个文件   "
                    f"{format_size(task['bytes'])} / {format_size(task['total_bytes'])}   "
                    f"{speed_text}   剩余 {eta_text}   "
                    f"{task['current']}"
                )
            else:
                self.progress["value"] = sum(x["percent"] for x in tasks) / len(tasks)
                self.progress_var.set("多个任务同时进行：" + "、".join(
                    f"{x['letter']} {x['percent']:.0f}%" for x in tasks))
        elif self.app.paused:
            self.status_var.set("已暂停")
            self.progress["value"] = 0
            self.progress_var.set("")
        else:
            self.status_var.set("空闲 · 等待设备插入")
            self.progress["value"] = 0
            self.progress_var.set("")

        self._update_tray(tasks)
        self.after(400, self._drain)

    def _append(self, level: str, message: str) -> None:
        from datetime import datetime
        self.log.config(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S} {message}\n", level if level in ("info", "warn", "error") else "info")
        self.log.see("end")
        if int(self.log.index("end-1c").split(".")[0]) > 2000:
            self.log.delete("1.0", "500.0")
        self.log.config(state="disabled")
