"""PyInstaller 打包入口。

单独放在 packaging/ 下，避免把包目录本身当作脚本入口。
"""

import sys

from usbcopyer.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
