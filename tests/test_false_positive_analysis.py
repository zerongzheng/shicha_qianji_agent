"""误报审计和成果包案例选择的回归测试。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app.experiments.false_positive_analysis import audit_result
from app.models import (
    AnalysisResult,
    AnomalyEvent,
    DataProfile,
    OperatingRegimeResult,
    SensorProfile,
)
from app.reporting import evidence_pack


def _build_result(
    tmp_path: Path,
    *,
    changepoint_index: int | None = None,
    context_overlap: float = 0.0,
    missing_window: bool = False,
) -> AnalysisResult:
    """构造一份最小分析结果，专门验证误报归因而不运行完整检测器。"""

    row_count = 40
    pressure = np.linspace(1.0, 2.0, row_count)
    if missing_window:
        pressure[10:16] = np.nan
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "Pressure": pressure,
            "anomaly": np.zeros(row_count, dtype=int),
            "changepoint": np.zeros(row_count, dtype=int),
        }
    )
    if changepoint_index is not None:
        dataframe.loc[changepoint_index, "changepoint"] = 1

    scores = pd.DataFrame({"Pressure": np.zeros(row_count)})
    scores.loc[10:15, "Pressure"] = 6.0
    combined_score = scores["Pressure"].rename("risk_score")
    predicted_labels = pd.Series(np.zeros(row_count, dtype=int))
    predicted_labels.loc[10:15] = 1
    event = AnomalyEvent(
        start_index=10,
        end_index=15,
        start_time=dataframe.loc[10, "datetime"],
        end_time=dataframe.loc[15, "datetime"],
        duration_points=6,
        peak_score=6.0,
        severity="中风险",
        dominant_sensors=["Pressure"],
        sensor_scores={"Pressure": 6.0},
    )
    sensor_profile = SensorProfile(
        name="Pressure",
        missing_count=int(dataframe["Pressure"].isna().sum()),
        missing_rate=float(dataframe["Pressure"].isna().mean()),
        min_value=1.0,
        max_value=2.0,
        mean_value=1.5,
        std_value=0.2,
    )
    profile = DataProfile(
        source_name="sample.csv",
        row_count=row_count,
        start_time=dataframe.loc[0, "datetime"],
        end_time=dataframe.loc[row_count - 1, "datetime"],
        sampling_seconds=1.0,
        sensor_columns=["Pressure"],
        label_columns=["anomaly", "changepoint"],
        sensors=[sensor_profile],
        missing_total=sensor_profile.missing_count,
    )
    regimes = OperatingRegimeResult(
        regime_labels=pd.Series(np.zeros(row_count, dtype=int)),
        transition_score=pd.Series(np.zeros(row_count)),
        transition_mask=pd.Series(np.zeros(row_count, dtype=bool)),
        state_count=1,
        segments=[],
        event_contexts=[{"事件编号": 1, "过渡期重合率": context_overlap}],
    )
    return AnalysisResult(
        source_path=tmp_path / "other" / "sample.csv",
        detector_name="测试检测器",
        dataframe=dataframe,
        profile=profile,
        anomaly_scores=scores,
        combined_score=combined_score,
        predicted_labels=predicted_labels,
        events=[event],
        metrics=None,
        trend_summary={},
        recommendations=[],
        operating_regimes=regimes,
    )


def test_audit_matches_context_by_event_interval(tmp_path: Path) -> None:
    """工况上下文应按区间匹配，而不是依赖预测事件下标。"""

    result = _build_result(tmp_path, context_overlap=0.75)
    events = audit_result(result, event_tolerance=1)

    assert len(events) == 1
    assert events[0].category == "工况切换期"
    assert events[0].transition_overlap == 0.75


def test_audit_classifies_changepoint_before_other_evidence(tmp_path: Path) -> None:
    """变点证据优先级最高，避免同一事件被重复归因。"""

    result = _build_result(tmp_path, changepoint_index=12, context_overlap=0.9)
    events = audit_result(result, event_tolerance=2)

    assert events[0].category == "工况变点附近"
    assert events[0].changepoint_nearby


def test_audit_classifies_sensor_quality_risk(tmp_path: Path) -> None:
    """告警区间存在较高缺失率时应进入传感器质量排查。"""

    result = _build_result(tmp_path, missing_window=True)
    events = audit_result(result)

    assert events[0].category == "传感器质量风险"
    assert events[0].missing_rate == 1.0


def test_select_case_files_prioritizes_other_then_valves(tmp_path: Path) -> None:
    """成果包默认案例顺序应覆盖 other、valve1、valve2。"""

    for scenario in ("valve2", "other", "valve1"):
        folder = tmp_path / scenario
        folder.mkdir()
        (folder / "0.csv").write_text("datetime;Pressure;anomaly\n2026-01-01;1;0\n", encoding="utf-8")

    selected = evidence_pack._select_case_files(tmp_path, 3)

    assert [item.parent.name for item in selected] == ["other", "valve1", "valve2"]


def test_select_case_files_falls_back_when_other_is_missing(tmp_path: Path) -> None:
    """缺少指定场景时仍应按现有场景稳定补齐。"""

    for scenario in ("valve2", "valve1"):
        folder = tmp_path / scenario
        folder.mkdir()
        (folder / "0.csv").write_text("datetime;Pressure;anomaly\n2026-01-01;1;0\n", encoding="utf-8")

    selected = evidence_pack._select_case_files(tmp_path, 2)

    assert [item.parent.name for item in selected] == ["valve1", "valve2"]
