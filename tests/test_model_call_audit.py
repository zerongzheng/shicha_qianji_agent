"""模型调用脱敏审计测试。"""

from __future__ import annotations

import json

from app.observability.model_calls import ModelCallAudit
from app.storage.repository import IndustrialRepository


def test_model_call_audit_stores_metadata_without_content(tmp_path, monkeypatch) -> None:
    """日志应包含耗时与 Token，但不能保存提示词、回答或密钥。"""

    repository = IndustrialRepository(tmp_path / "audit.db")
    monkeypatch.setattr("app.storage.get_repository", lambda: repository)
    audit = ModelCallAudit(
        operation="automatic_diagnosis",
        provider="test-provider",
        model="test-model",
        input_character_count=123,
        run_id="run_test",
        output_dir=tmp_path,
    )
    record = audit.finish(
        "success",
        output_character_count=45,
        usage={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
    )

    stored = repository.list_model_calls(run_id="run_test")
    assert stored[0]["total_tokens"] == 30
    assert stored[0]["content_stored"] == 0
    text = (tmp_path / "model_calls.jsonl").read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload == record
    assert "prompt_text" not in text.casefold()
    assert "messages" not in text.casefold()
    assert "response_content" not in text.casefold()
    assert "api_key" not in text.casefold()
