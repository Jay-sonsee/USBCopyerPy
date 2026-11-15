"""USBCopyerPy —— U 盘插入自动备份工具（Python 重写版）。

对标原 C# 版 USBCopyer 的功能：插入检测、按规则建目录、过滤、
冲突策略、回调脚本、磁盘日志、单实例、设置界面。

与原版相比的关键改进见 README：用轮询替代 WM_DEVICECHANGE、
分块复制 + 协作式取消、全部状态访问加锁。
"""

__version__ = "1.0.2"
APP_NAME = "USBCopyerPy"
