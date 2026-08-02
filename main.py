"""PyCharm 直接运行入口。

该文件只负责转交给命令行模块。核心业务代码统一放在 app 包中，避免根目录出现
第二套实现。直接点击 PyCharm 的运行按钮时，会分析 `.env` 指定的默认 SKAB 文件。
"""

from app.cli import main

if __name__ == "__main__":
    main()
