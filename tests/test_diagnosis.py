"""单次大模型自动诊断链路测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from app.config import get_settings
from app.diagnosis.service import AutomaticDiagnosisService
from app.knowledge.retriever import KnowledgeChunk
from app.models import AnalysisResult, AnomalyEvent, DataProfile, SensorProfile


def _analysis_result(tmp_path: Path) -> AnalysisResult:
    """构造不依赖 SKAB 文件的最小完整分析结果。"""

    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=20, freq="s"),
            "Pressure": np.linspace(10.0, 13.0, 20),
        }
    )
    profile = DataProfile(
        source_name="sample.csv",
        row_count=20,
        start_time=dataframe["datetime"].iloc[0],
        end_time=dataframe["datetime"].iloc[-1],
        sampling_seconds=1.0,
        sensor_columns=["Pressure"],
        label_columns=[],
        sensors=[
            SensorProfile(
                name="Pressure",
                missing_count=0,
                missing_rate=0.0,
                min_value=10.0,
                max_value=13.0,
                mean_value=11.5,
                std_value=0.9,
            )
        ],
        missing_total=0,
    )
    event = AnomalyEvent(
        start_index=10,
        end_index=15,
        start_time=dataframe["datetime"].iloc[10],
        end_time=dataframe["datetime"].iloc[15],
        duration_points=6,
        peak_score=8.4,
        severity="高风险",
        dominant_sensors=["Pressure"],
        sensor_scores={"Pressure": 8.4},
    )
    return AnalysisResult(
        source_path=tmp_path / "sample.csv",
        detector_name="window_autoencoder",
        dataframe=dataframe,
        profile=profile,
        anomaly_scores=pd.DataFrame({"Pressure": np.zeros(20)}),
        combined_score=pd.Series(np.zeros(20)),
        predicted_labels=pd.Series(np.zeros(20), dtype=int),
        events=[event],
        metrics=None,
        trend_summary={"Pressure": {"方向": "持续上升", "风险": "需关注"}},
        recommendations=["核对阀门控制指令并检查压力传感器。"],
        risk_alerts=[
            {
                "传感器": ["Pressure"],
                "证据": ["压力未来趋势持续上升"],
            }
        ],
    )


def test_automatic_diagnosis_uses_one_chat_request(tmp_path, monkeypatch) -> None:
    """自动诊断应在分析完成后只请求一次聊天模型。"""

    result = _analysis_result(tmp_path)
    settings = replace(get_settings(), llm_api_key="test-key", knowledge_dir=tmp_path)
    calls: list[list[object]] = []

    class FakeModel:
        def invoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content="## 诊断结论\n高风险，需要现场复核。")

    monkeypatch.setattr("app.diagnosis.service.create_chat_model", lambda _settings: FakeModel())
    monkeypatch.setattr(
        "app.diagnosis.service.search_knowledge",
        lambda *_args, **_kwargs: [
            KnowledgeChunk("manual.md", "压力升高时检查阀门和管路。", 0.9)
        ],
    )

    diagnosis = AutomaticDiagnosisService(settings).diagnose(result)

    assert diagnosis.status == "generated"
    assert len(calls) == 1
    prompt = str(calls[0][-1].content)
    assert "sample.csv" in prompt
    assert "Pressure" in prompt
    assert "10.157894" not in prompt
    assert "manual.md" in prompt


def test_automatic_diagnosis_falls_back_when_model_fails(tmp_path, monkeypatch) -> None:
    """聊天接口限流时应返回确定性诊断，而不是丢失工业分析结果。"""

    result = _analysis_result(tmp_path)
    settings = replace(get_settings(), llm_api_key="test-key", knowledge_dir=tmp_path)

    class FailingModel:
        def invoke(self, _messages):
            raise RuntimeError("sensitive upstream detail")

    monkeypatch.setattr(
        "app.diagnosis.service.create_chat_model", lambda _settings: FailingModel()
    )
    monkeypatch.setattr(
        "app.diagnosis.service.search_knowledge",
        lambda *_args, **_kwargs: [
            KnowledgeChunk("manual.md", "压力升高时检查阀门和管路。", 0.9)
        ],
    )

    diagnosis = AutomaticDiagnosisService(settings).diagnose(result)

    assert diagnosis.status == "fallback"
    assert "## 诊断结论" in diagnosis.diagnosis
    assert "manual.md" in diagnosis.diagnosis
    assert "sensitive upstream detail" not in (diagnosis.error or "")


def test_automatic_diagnosis_without_key_does_not_create_model(tmp_path, monkeypatch) -> None:
    """未配置密钥时直接生成降级摘要，不能尝试访问外部接口。"""

    result = _analysis_result(tmp_path)
    settings = replace(get_settings(), llm_api_key="", knowledge_dir=tmp_path)
    monkeypatch.setattr("app.diagnosis.service.search_knowledge", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "app.diagnosis.service.create_chat_model",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不应创建聊天模型")),
    )

    diagnosis = AutomaticDiagnosisService(settings).diagnose(result)

    assert diagnosis.status == "fallback"
    assert diagnosis.model is None
    assert diagnosis.error is None
