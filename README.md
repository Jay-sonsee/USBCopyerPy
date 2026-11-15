# USBCopyerPy

把原 C# 版 [USBCopyer](https://github.com/kenvix/USBCopyer) 用 Python 重写：U 盘插入后自动把整盘文件备份到指定目录。

**只用标准库**（tkinter 随 Python 自带），不需要 `pip install` 任何东西。

## 快速开始

```bat
python -m usbcopyer --settings     :: 先配置备份目录
python -m usbcopyer                :: 打开控制面板，开始监听
```

或者双击 `run.bat`。

命令行：

```bat
python -m usbcopyer --list         :: 看当前所有盘符、卷标、序列号
python -m usbcopyer --headless     :: 无界面常驻，日志打到控制台
python -m usbcopyer --once E:      :: 立即把 E: 备份一次（跳过筛选）
python -m usbcopyer --once E: --dry-run   :: 只统计，不写文件
python -m usbcopyer --reset        :: 恢复默认配置
```

## 功能对照

| 功能 | 原 C# 版 | 本版 |
|---|---|---|
| 插入检测 | `WM_DEVICECHANGE` 消息 | 轮询盘符（默认 1 秒） |
| 目录规则 | `[name] [serial] [letter] [type] [fs]` | 相同 |
| 扩展名黑白名单 | ✅ | ✅ |
| 无扩展名文件 | ❌ 抛异常被跳过 | ✅ |
| 冲突策略 | 较新覆盖 / 重命名 / 覆盖 | 相同（重命名已修复非法路径） |
| 停止复制 | `Thread.Abort()` | 协作式取消，分块检查 |
| 磁盘黑白名单 | ✅（按序列号/盘符） | ✅ |
| 延迟复制 | ✅ | ✅ |
| 回调脚本 | `.bat` + 占位符 | 相同 |
| 磁盘日志 | `Disks.csv` | `Disks.csv`（带表头，UTF-8 BOM） |
| 单实例 | 按 exe 进程名 | 命名互斥体（改名也有效） |
| 托盘图标 | ✅ | ❌ 用"后台运行 + 再启动唤出"代替（零依赖） |
| 复制进度条 | ❌ 只有托盘气泡 | ✅ 百分比 + 文件数 + 字节数 + 当前文件 |
| 排除文件夹 | ❌ | ✅ 支持通配符，任意层级匹配 |
| 后台运行 | ✅ 隐藏到托盘 | ✅ 关窗口不退出，再启动一次唤出 |

## 配置

配置文件：**程序目录**下的 `config.json`（绿色版：整个文件夹拷走，配置跟着走）。
只有当程序目录不可写（例如放在 `C:\Program Files` 下）时，才退回
`%APPDATA%\USBCopyerPy\config.json`。旧版本残留在 `%APPDATA%` 的配置会在首次运行时自动读取。

| 字段 | 说明 |
|---|---|
| `destination` | 备份根目录 |
| `folder_rule` | 子目录命名规则，默认 `[serial]` |
| `conflict` | `skip_if_newer` / `overwrite` / `rename` |
| `delay_seconds` | 插入后延迟开始 |
| `ext_mode` / `black_exts` / `white_exts` | 扩展名过滤 |
| `size_mode` / `size_limit_mb` | 大小限制（`none`/`max`/`min`） |
| `skip_types` | 跳过哪些驱动器类型（默认跳过网络盘、光驱、本地硬盘） |
| `disk_mode` / `black_serials` / `black_letters` | 黑白名单 |
| `poll_interval` | 轮询间隔（秒） |
| `exclude_dirs` | 排除的文件夹，逗号/换行分隔，支持 `*` 通配符 |
| `close_to_background` | 关闭窗口时是否保持后台运行 |

### 分类规则语法

```
[serial]               -> 1A2B3C4D
"备份-" [letter]        -> 备份-E
[name]_[fs]            -> MYUSB_exFAT
```

- `[name]` 卷标、`[serial]` 序列号、`[letter]` 盘符、`[type]` 驱动器类型、`[fs]` 文件系统
- 双引号内是字面量；`@"` 表示字面量里的 `"`，`@@` 表示 `@`
- `[[` 表示字面量 `[`

## 复制速度

单个文件按大小自动选择最快的方式：

| 文件大小 | 方式 | 说明 |
|---|---|---|
| ≤ 64 MB | 系统 API `CopyFileW` | 内核态复制，实测比 Python 分块快 **1.3~1.7 倍** |
| > 64 MB | 1MB 分块读写 | 保留随时取消的能力，避免取消一个 4GB 视频要等几十秒 |

实测（本机 SSD，600 个文件共 100MB）：约 **0.95 秒**、100+ MB/s。
U 盘场景通常受设备本身读取速度限制（USB2 约 20~30MB/s，USB3 约 80~120MB/s），
此时瓶颈在硬件而非程序。

其他提速点：
- 预扫描只读元数据，不做文件内容读取
- 已存在且不比源新的文件直接跳过（默认冲突策略），不浪费 IO

## 进度条与后台运行

- 复制时主窗口顶部显示进度条：百分比、已复制文件数、已复制字节数、当前文件名。
  百分比基于**预扫描**得出的总字节数（只读元数据，不读文件内容）。
- 关闭窗口默认不退出，转入后台继续监听；**再启动一次程序**（或双击 exe）会自动
  唤出已有窗口（命名事件实现，无需托盘图标）。
- 要彻底退出：点界面右上角的「退出」按钮。

## 排除文件夹

在设置 → 过滤 → 排除的文件夹里填写，逗号或换行分隔，支持 `*` `?` 通配符：

| 写法 | 效果 |
|---|---|
| `node_modules` | 任意层级的同名文件夹都跳过 |
| `.git` | 同上 |
| `photos\2020` | 只跳过该相对路径 |
| `cache*` | 以 cache 开头的文件夹 |

被排除的文件夹不会创建、不会读取，也不会计入进度总量。

## 回调脚本

放在 `%APPDATA%\USBCopyerPy\` 下：

- `DiskDetectedCallback.bat` —— 检测到磁盘时执行
- `AllCompletedCallback.bat` —— 该磁盘复制完成后执行

可用占位符：`{$VolumeSerialNumber}` `{$VolumeName}` `{$Volume}` `{$DriveType}` `{$USBDir}`

## 与原版的行为差异

1. **轮询代替窗口消息**：原版靠 `WM_DEVICECHANGE`，盘符掩码异常时会抛 `IndexOutOfRangeException` 并丢掉整次插入事件；轮询没有这类解析风险，代价是最多延迟一个周期。
2. **取消是协作式的**：原版用 `Thread.Abort()`，取消要等当前 `File.Copy` 返回，拔盘后还会继续遍历；本版每次读 1MB 就检查一次取消标志，取消或失败都会删掉没拷完的半截文件。
3. **状态全部加锁**：原版 `Dictionary` 在多线程间裸读裸写，点"停止"时偶发 `Collection was modified`。
4. **单实例用互斥体**：原版按进程名判断，改了 exe 名字就能起两个实例。
5. **复制目标不能位于源盘内**：直接拒绝，避免自我递归。

## 测试

```bat
python -m unittest discover -s tests -t .
```

49 个用例，覆盖规则解析、扩展名过滤、冲突策略、取消、时间戳保留、配置读写、驱动器事件差分。

## 下载已打包的 exe

在 [Releases](https://github.com/Jay-sonsee/USBCopyerPy/releases) 页面下载：

| 文件 | 适用 |
|---|---|
| `USBCopyerPy-x64.exe` | Win7 SP1 x64 / Win10 x64（推荐） |
| `USBCopyerPy-x86.exe` | 32 位系统 |
| `USBCopyerPy-console-x64.exe` | 带控制台，方便看日志 / 跑 `--headless` |

**Win7 用户注意**：exe 由 Python 3.8 打包，需要系统已安装 **Universal C Runtime**
（更新 `KB2999226`）。打过补丁的 Win7 SP1 一般都有；若启动报缺少
`api-ms-win-crt-runtime-l1-1-0.dll`，装一下该更新即可。Win10 无需额外操作。

首次运行 exe 会有 2~3 秒解压时间（PyInstaller onefile 特性），之后正常。

## 打包成 exe（可选）

本地打包：

```bat
pip install pyinstaller==5.13.2
pyinstaller -F -w -n USBCopyerPy --paths . --hidden-import usbcopyer.ui packaging\entry.py
```

`-w` 表示不带控制台窗口；如果要用 `--headless` 就别加 `-w`。

仓库自带 GitHub Actions（`.github/workflows/build-exe.yml`），推 tag 即自动
产出 x64/x86 双架构并发布 Release。


## 已知限制

- 只能装在 Windows（依赖 `GetLogicalDrives` 等 API）
- 没有托盘图标：关闭主窗口等于退出
- 轮询间隔过小（<0.5 秒）会更耗 CPU
- 未做开机自启注册表项（如需可以自己加计划任务）
