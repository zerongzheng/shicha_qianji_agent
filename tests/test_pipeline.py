"""核心流水线回归测试。

测试使用临时 CSV，确保项目上传 GitHub 后，即使没有 SKAB 数据也能验证基本功能。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analysis import analyze_file
from app.analysis.evaluation import evaluate_predictions
from app.models import AnalysisConfig


@pytest.mark.parametrize("detector", ["mad", "isolation_forest", "hybrid"])
def test_pipeline_can_detect_injected_event(tmp_path, detector: str) -> None:
    """人工注入连续异常后，流程应产生事件、指标和报告。"""

    row_count = 240
    random = np.random.default_rng(7)
    pressure = random.normal(10.0, 0.15, row_count)
    current = random.normal(2.0, 0.04, row_count)
    anomaly = np.zeros(row_count)

    pressure[120:140] += 4.0
    current[120:140] += 1.2
    anomaly[120:140] = 1

    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "Pressure": pressure,
            "Current": current,
            "anomaly": anomaly,
            "changepoint": np.zeros(row_count),
        }
    )
    csv_path = tmp_path / "industrial_sample.csv"
    dataframe.to_csv(csv_path, sep=";", index=False)

    result = analyze_file(
        csv_path,
        config=AnalysisConfig(
            detector=detector,
            threshold=3.0,
            rolling_window=31,
            min_event_length=2,
            merge_gap=2,
        ),
        write_report=False,
    )

    assert result.profile.row_count == row_count
    assert len(result.events) >= 1
    assert result.metrics is not None
    assert result.metrics.recall > 0
    assert result.metrics.event_recall > 0
    assert result.detector_name
    assert isinstance(result.to_summary()["评估指标"], dict)
    assert "工业时序诊断报告" in result.report_text


def test_anomaly_free_file_receives_perfect_event_score() -> None:
    """没有真实事件且没有告警时，事件级评价应视为正确。"""

    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=50, freq="s"),
            "Pressure": np.ones(50),
            "anomaly": np.zeros(50),
            "changepoint": np.zeros(50),
        }
    )
    labels = pd.Series(np.zeros(50), dtype=int)
    scores = pd.Series(np.zeros(50), dtype=float)

    metrics = evaluate_predictions(dataframe, labels, scores)

    assert metrics is not None
    assert metrics.event_f1_score == 1.0
    assert metrics.false_positive_event_count == 0


def test_changepoint_related_false_event_is_counted() -> None:
    """工况切换附近的误报应进入变点误报统计。"""

    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=80, freq="s"),
            "Pressure": np.ones(80),
            "anomaly": np.zeros(80),
            "changepoint": [0] * 39 + [1] + [0] * 40,
        }
    )
    labels = pd.Series([0] * 37 + [1, 1, 1, 1, 1] + [0] * 38)
    scores = labels.astype(float)

    metrics = evaluate_predictions(dataframe, labels, scores)

    assert metrics is not None
    assert metrics.false_positive_event_count == 1
    assert metrics.changepoint_related_false_events == 1
    assert metrics.changepoint_false_event_rate == 1.0


def test_anomaly_free_skab_directory_is_evaluated_as_normal(tmp_path) -> None:
    """SKAB anomaly-free 目录缺少标签时应自动按全正常数据评估。"""

    scenario_dir = tmp_path / "anomaly-free"
    scenario_dir.mkdir()
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=80, freq="s"),
            "Pressure": np.linspace(10.0, 10.2, 80),
            "Current": np.linspace(2.0, 2.02, 80),
        }
    )
    csv_path = scenario_dir / "anomaly-free.csv"
    dataframe.to_csv(csv_path, sep=";", index=False)

    result = analyze_file(
        csv_path,
        config=AnalysisConfig(detector="mad", threshold=8.0),
        write_report=False,
    )

    assert result.metrics is not None
    assert result.metrics.actual_event_count == 0
