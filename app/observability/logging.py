"""工业分析运行日志。

日志只保存任务摘要、算法参数、耗时和错误，不写入 API Key，也不默认保存完整原始 CSV。
这份日志既可供本地排障，也可作为万悟模型调用日志的算法侧补充材料。
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings


class RunLogger:
    """将一次工业分析任务写成 JSON Lines 记录。"""

    def __init__(self, run_id: str | None = None, output_dir: Path | None = None) -> None:
        self.run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        self._started_at = time.perf_counter()
        self._output_path = (output_dir or get_settings().output_dir / "logs") / "runs.jsonl"

    def finish(
        self,
        status: str,
        operation: str,
        input_summary: dict[str, Any],
        output_summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """写入任务结束记录并返回记录内容。"""

        record = {
            "run_id": self.run_id,
            "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "operation": operation,
            "status": status,
            "duration_ms": round((time.perf_counter() - self._started_at) * 1000, 2),
            "input_summary": input_summary,
            "output_summary": output_summary or {},
            "error": error,
        }
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._output_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record
