"""Streamlit 启动入口。

根目录只保留这个很薄的启动文件，方便在 PyCharm 终端直接执行：
    uv run streamlit run streamlit_app.py
真正的页面逻辑位于 app/ui/streamlit_app.py，避免把业务代码堆在项目根目录。
"""

from app.ui.streamlit_app import run_app

if __name__ == "__main__":
    run_app()
