"""自适应预处理测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.preprocessing import adaptive_preprocess


def test_preprocessing_aligns_time_and_preserves_label_semantics() -> None:
    """不规则时间轴应补齐，持续状态和瞬时变点必须按不同语义处理。"""

    dataframe = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                ["2026-01-01 00:00:00", "2026-01-01 00:00:01", "2026-01-01 00:00:03"]
            ),
            "Pressure": [1.0, np.nan, 4.0],
            "anomaly": [0, 1, 0],
            "changepoint": [0, 1, 0],
        }
    )

    result = adaptive_preprocess(dataframe, expected_sampling_seconds=1.0)

    assert len(result.dataframe) == 4
    assert result.summary["time_alignment_applied"] is True
    assert result.summary["inserted_row_count"] == 1
    assert result.summary["remaining_missing_count"] == 0
    # 00:00:02 是异常状态区间中的新增网格点，应继承前一个状态 1。
    assert result.dataframe["anomaly"].fillna(0).tolist() == [0.0, 1.0, 1.0, 0.0]
    # 00:00:02 没有真实变点观测，不能由前一个变点标记传播得到 1。
    assert result.dataframe["changepoint"].fillna(0).tolist() == [0.0, 1.0, 0.0, 0.0]


def test_preprocessing_propagates_state_across_missing_timestamp() -> None:
    """连续异常区间中间缺少采样点时，不应被拆成多个事件。"""

    dataframe = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                ["2026-01-01 00:00:00", "2026-01-01 00:00:02"]
            ),
            "Pressure": [2.0, 2.5],
            "anomaly": [1, 1],
        }
    )

    result = adaptive_preprocess(dataframe, expected_sampling_seconds=1.0)

    assert result.dataframe["anomaly"].tolist() == [1.0, 1.0, 1.0]


def test_preprocessing_keeps_spikes_for_anomaly_detection() -> None:
    """异常敏感任务不能用平滑操作直接抹掉瞬时尖峰。"""

    values = np.zeros(80)
    values[40] = 10.0
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=80, freq="s"),
            "Pressure": values,
        }
    )

    result = adaptive_preprocess(dataframe)

    assert result.dataframe.loc[40, "Pressure"] == 10.0
    noise_action = next(
        item for item in result.summary["actions"] if item["action"] == "noise_handling"
    )
    assert noise_action["status"] == "model_internal"
    assert "保留原始尖峰" in noise_action["method"]


def test_preprocessing_rejects_entirely_missing_sensor() -> None:
    """整列缺失没有可信填补依据，必须阻断分析而不是填零。"""

    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=30, freq="s"),
            "Pressure": [np.nan] * 30,
        }
    )

    try:
        adaptive_preprocess(dataframe)
    except ValueError as exc:
        assert "整列缺失" in str(exc)
    else:
        raise AssertionError("整列缺失传感器应触发质量门错误")
