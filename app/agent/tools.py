"""供 LangChain Agent 调用的确定性工具。

工具函数是大模型与工业算法之间的边界。大模型只能选择工具和解释结果，不能自行伪造
异常分数或修改算法输出。
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import tool

from app.analysis import analyze_file, analyze_folder
from app.config import get_settings
from app.knowledge.retriever import format_knowledge_context


@tool
def analyze_industrial_file(file_path: str = "") -> str:
    """分析一个工业时序 CSV，返回数据画像、异常事件、评估、趋势和运维建议。"""

    path = Path(file_path) if file_path.strip() else get_settings().default_skab_file
    result = analyze_file(path)
    return json.dumps(result.to_summary(), ensure_ascii=False, default=str)


@tool
def analyze_industrial_folder(directory_path: str = "", max_files: int = 5) -> str:
    """批量分析一个工业时序 CSV 文件夹，适合比较多个工况文件。"""

    path = Path(directory_path) if directory_path.strip() else get_settings().default_skab_dir
    batch = analyze_folder(path, max_files=max_files or None)
    summary = {
        "数据目录": str(batch.source_dir),
        "成功文件数": len(batch.results),
        "失败文件": batch.failed_files,
        "数据点总数": batch.total_rows,
        "异常事件总数": batch.total_events,
        "平均点级F1": batch.average_f1,
        "平均事件级F1": batch.average_event_f1,
        "各文件摘要": [result.to_summary() for result in batch.results],
    }
    return json.dumps(summary, ensure_ascii=False, default=str)


@tool
def search_industrial_knowledge(query: str) -> str:
    """检索本地工业机理、异常原因和运维处置知识。"""

    return format_knowledge_context(query)


@tool
def get_project_data_paths() -> str:
    """返回项目默认 SKAB 文件与文件夹路径。"""

    settings = get_settings()
    return json.dumps(
        {
            "默认文件": str(settings.default_skab_file),
            "默认目录": str(settings.default_skab_dir),
        },
        ensure_ascii=False,
    )
