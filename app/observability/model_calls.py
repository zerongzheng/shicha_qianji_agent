"""大模型与 Embedding 调用审计。

日志只记录调用元数据和规模，不保存 API Key、完整提示词、模型回答或原始工业数据。
JSONL 可作为竞赛附件，PostgreSQL 由仓储层同步保存，便于 API 查询和统计。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from app.config import get_settings


@dataclass
class ModelCallAudit:
    """一次外部模型请求的计时器和脱敏审计记录。"""

    operation: str
    provider: str
    model: str
    input_character_count: int
    run_id: str | None = None
    output_dir: Path | None = None

    def __post_init__(self) -> None:
        self.call_id = f"model_{uuid.uuid4().hex[:16]}"
        self._started = time.perf_counter()

    def finish(
        self,
        status: str,
        *,
        output_character_count: int = 0,
        usage: dict[str, Any] | None = None,
        error_type: str | None = None,
    ) -> dict[str, Any]:
        """写入 JSONL，并尽力同步 PostgreSQL；审计失败不覆盖模型调用结果。"""

        record = {
            "call_id": self.call_id,
            "run_id": self.run_id,
            "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "operation": self.operation,
            "provider": self.provider,
            "model": self.model,
            "status": status,
            "duration_ms": round((time.perf_counter() - self._started) * 1000, 2),
            "input_character_count": max(0, int(self.input_character_count)),
            "output_character_count": max(0, int(output_character_count)),
            "prompt_tokens": _usage_value(usage, "prompt_tokens", "input_tokens"),
            "completion_tokens": _usage_value(
                usage, "completion_tokens", "output_tokens"
            ),
            "total_tokens": _usage_value(usage, "total_tokens"),
            "error_type": error_type,
            "content_stored": False,
        }
        target = (self.output_dir or get_settings().output_dir / "logs") / "model_calls.jsonl"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
        try:
            # 延迟导入避免 storage 初始化反向依赖 observability。
            from app.storage import get_repository

            get_repository().record_model_call(record)
        except (OSError, RuntimeError, ValueError, psycopg.Error):
            # 审计属于旁路能力，数据库锁定、只读或临时不可用时不能影响模型主调用。
            pass
        return record


def response_usage_metadata(response: Any) -> dict[str, Any]:
    """兼容 LangChain 和 OpenAI SDK 的 Token 使用字段。"""

    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        return usage
    metadata = getattr(response, "response_metadata", None)
    if isinstance(metadata, dict):
        token_usage = metadata.get("token_usage") or metadata.get("usage")
        if isinstance(token_usage, dict):
            return token_usage
    return {}


def _usage_value(usage: dict[str, Any] | None, *keys: str) -> int | None:
    for key in keys:
        if usage and usage.get(key) is not None:
            try:
                return int(usage[key])
            except (TypeError, ValueError):
                return None
    return None
