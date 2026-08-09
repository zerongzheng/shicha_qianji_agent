"""无监督工况识别与过渡期告警策略测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.regime import analyze_operating_regimes, suppress_transition_only_events
from app.models import AnalysisConfig, AnomalyEvent, OperatingRegimeResult


def _two_regime_dataframe() -> pd.DataFrame:
    """构造两个稳定工况和一次明显切换，不提供 changepoint 标签。"""

    random = np.random.default_rng(42)
    row_count = 240
    pressure = np.concatenate(
        [random.normal(10.0, 0.05, 120), random.normal(14.0, 0.05, 120)]
    )
    current = np.concatenate(
        [random.normal(2.0, 0.02, 120), random.normal(3.2, 0.02, 120)]
    )
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "Pressure": pressure,
            "Current": current,
        }
    )


def test_regime_detector_finds_stable_states_and_transition() -> None:
    """两个水平明显不同的平台应形成多工况，并在跃迁附近给出过渡证据。"""

    dataframe = _two_regime_dataframe()
    event = AnomalyEvent(
        start_index=116,
        end_index=126,
        start_time=dataframe.at[116, "datetime"],
        end_time=dataframe.at[126, "datetime"],
        duration_points=11,
        peak_score=6.0,
        severity="低风险",
        dominant_sensors=["Pressure", "Current"],
        sensor_scores={"Pressure": 6.0, "Current": 5.8},
    )

    result = analyze_operating_regimes(
        dataframe,
        ["Pressure", "Current"],
        [event],
        AnalysisConfig(regime_window=31, regime_transition_quantile=0.95),
    )

    assert result.state_count >= 2
    assert (
        result.regime_labels.iloc[:80].mode().iloc[0]
        != result.regime_labels.iloc[-80:].mode().iloc[0]
    )
    assert bool(result.transition_mask.loc[105:140].any())
    assert result.event_contexts[0]["过渡期重合率"] > 0


def test_default_regime_mode_never_changes_alerts() -> None:
    """默认配置仅增加工况解释，必须保持原有标签和事件不变。"""

    dataframe = _two_regime_dataframe()
    event = AnomalyEvent(
        start_index=116,
        end_index=126,
        start_time=dataframe.at[116, "datetime"],
        end_time=dataframe.at[126, "datetime"],
        duration_points=11,
        peak_score=6.0,
        severity="低风险",
        dominant_sensors=["Pressure"],
        sensor_scores={"Pressure": 6.0},
    )
    config = AnalysisConfig(suppress_transition_events=False)
    regimes = analyze_operating_regimes(dataframe, ["Pressure", "Current"], [event], config)
    labels = pd.Series(0, index=dataframe.index, dtype=int)
    labels.loc[116:126] = 1

    updated_labels, updated_events, updated_regimes = suppress_transition_only_events(
        regimes,
        [event],
        labels,
        config,
    )

    assert updated_labels.equals(labels)
    assert updated_events == [event]
    assert not updated_regimes.suppression_applied


def test_suppression_only_removes_weak_transition_event() -> None:
    """开启策略后只移除高度重合的低风险事件，高风险事件必须保留。"""

    dataframe = _two_regime_dataframe()
    weak = AnomalyEvent(
        50,
        55,
        dataframe.at[50, "datetime"],
        dataframe.at[55, "datetime"],
        6,
        6.0,
        "低风险",
        ["Pressure"],
        {"Pressure": 6.0},
    )
    severe = AnomalyEvent(
        150,
        156,
        dataframe.at[150, "datetime"],
        dataframe.at[156, "datetime"],
        7,
        15.0,
        "高风险",
        ["Current"],
        {"Current": 15.0},
    )
    index = dataframe.index
    regimes = OperatingRegimeResult(
        regime_labels=pd.Series(0, index=index),
        transition_score=pd.Series(0.0, index=index),
        transition_mask=pd.Series(False, index=index),
        state_count=1,
        segments=[],
        event_contexts=[
            {"事件编号": 1, "过渡期重合率": 1.0},
            {"事件编号": 2, "过渡期重合率": 1.0},
        ],
    )
    labels = pd.Series(0, index=index, dtype=int)
    labels.loc[50:55] = 1
    labels.loc[150:156] = 1
    config = AnalysisConfig(
        threshold=5.5,
        suppress_transition_events=True,
        regime_suppression_overlap=0.75,
        regime_suppression_peak_ratio=1.35,
    )

    updated_labels, events, updated_regimes = suppress_transition_only_events(
        regimes,
        [weak, severe],
        labels,
        config,
    )

    assert int(updated_labels.loc[50:55].sum()) == 0
    assert int(updated_labels.loc[150:156].sum()) == 7
    assert events == [severe]
    assert updated_regimes.suppressed_event_count == 1
