"""比赛方大模型适配与混合知识检索测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np

from app.config import get_settings
from app.knowledge.retriever import search_knowledge
from app.knowledge.vector_index import load_or_build_index
from app.llm.client import create_chat_model, format_llm_error
from app.llm.rate_limit import FileIntervalRateLimiter


def _test_settings(tmp_path: Path, **changes):
    """复制真实配置，但将知识和输出目录隔离到 pytest 临时目录。"""

    return replace(
        get_settings(),
        knowledge_dir=tmp_path / "knowledge",
        output_dir=tmp_path / "outputs",
        llm_api_key="test-key",
        llm_embedding_model="test-embedding",
        **changes,
    )


def test_vector_similarity_can_recall_without_keyword_overlap(tmp_path, monkeypatch) -> None:
    """关键词不重叠时，语义向量仍应召回含义相近的故障知识。"""

    settings = _test_settings(tmp_path)
    settings.knowledge_dir.mkdir()
    (settings.knowledge_dir / "manual.md").write_text(
        "# 泵体异常\n\n轴承温升通常伴随润滑不足，应检查油路和振动。\n\n"
        "# 阀门异常\n\n阀门卡滞会造成压力波动，应检查执行机构。",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.knowledge.retriever.get_settings", lambda: settings)

    def fake_embed(texts, _settings):
        return [
            [1.0, 0.0] if "轴承温升" in text or "过热" in text else [0.0, 1.0]
            for text in texts
        ]

    monkeypatch.setattr("app.knowledge.vector_index.embed_texts", fake_embed)
    monkeypatch.setattr("app.knowledge.retriever.embed_texts", fake_embed)

    results = search_knowledge("设备过热怎么办", top_k=1)

    assert results
    assert "轴承温升" in results[0].text


def test_embedding_failure_falls_back_to_keyword_search(tmp_path, monkeypatch) -> None:
    """比赛接口限流或断网时，关键词检索仍应返回工程知识。"""

    settings = _test_settings(tmp_path)
    settings.knowledge_dir.mkdir()
    (settings.knowledge_dir / "manual.md").write_text(
        "# 压力异常\n\n压力波动时应检查阀门开度、执行机构状态以及上下游管路是否泄漏。",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.knowledge.retriever.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.knowledge.vector_index.embed_texts",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("限流")),
    )

    results = search_knowledge("压力波动", top_k=1)

    assert results
    assert "阀门开度" in results[0].text


def test_vector_index_reuses_disk_cache(tmp_path, monkeypatch) -> None:
    """相同模型和知识内容第二次加载时不应再次消耗 Embedding 请求。"""

    settings = _test_settings(tmp_path)
    chunks = [type("Chunk", (), {"source": "a.md", "text": "压力波动故障处置"})()]
    calls = 0

    def fake_embed(_texts, _settings):
        nonlocal calls
        calls += 1
        return [[3.0, 4.0]]

    monkeypatch.setattr("app.knowledge.vector_index.embed_texts", fake_embed)
    first = load_or_build_index(chunks, settings)
    second = load_or_build_index(chunks, settings)

    assert calls == 1
    assert np.allclose(first.vectors, second.vectors)
    assert np.allclose(second.vectors[0], [0.6, 0.8])


def test_empty_query_and_empty_knowledge_return_no_results(tmp_path, monkeypatch) -> None:
    """空问题或空知识库不应调用外部接口。"""

    settings = _test_settings(tmp_path)
    monkeypatch.setattr("app.knowledge.retriever.get_settings", lambda: settings)

    assert search_knowledge("", top_k=4) == []
    assert search_knowledge("压力异常", top_k=4) == []


def test_glm5_disables_reasoning_content_mode(tmp_path) -> None:
    """GLM-5 应通过顶层 extra_body 关闭思考模式，保证正文可被 Agent 读取。"""

    settings = _test_settings(tmp_path, llm_chat_model="glm-5")
    model = create_chat_model(settings)

    assert model.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert "extra_body" not in (model.model_kwargs or {})
    assert model.max_retries == 0


def test_safe_error_message_does_not_expose_original_exception() -> None:
    """未知异常只返回固定提示，不显示可能包含密钥的原始错误文本。"""

    message = format_llm_error(RuntimeError("secret-key-in-error"))

    assert "secret-key-in-error" not in message


def test_file_rate_limiter_waits_for_next_request_slot(tmp_path, monkeypatch) -> None:
    """连续请求应等待最小间隔，并在多个限流器实例之间共享状态。"""

    clock = {"now": 100.0}

    def fake_time() -> float:
        return clock["now"]

    def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds

    monkeypatch.setattr("app.llm.rate_limit.time.time", fake_time)
    monkeypatch.setattr("app.llm.rate_limit.time.sleep", fake_sleep)
    state_path = tmp_path / "chat.timestamp"

    first = FileIntervalRateLimiter(state_path, requests_per_minute=5)
    second = FileIntervalRateLimiter(state_path, requests_per_minute=5)
    assert first.acquire()
    assert not second.acquire(blocking=False)
    assert second.acquire()

    assert clock["now"] == 112.2
