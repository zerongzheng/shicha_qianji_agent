"""实验划分和阈值决策的回归测试。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app.analysis.detection import apply_detection_threshold
from app.experiments.split import build_skab_split
from app.experiments.tuning import ThresholdTrial, select_best_trial
from app.models import AnalysisConfig


def test_skab_split_is_deterministic_and_disjoint(tmp_path: Path) -> None:
    """完整文件只能属于一个集合，重复调用应得到相同划分。"""

    for scenario, names in {
        "anomaly-free": ["anomaly-free.csv"],
        "valve1": ["0.csv", "1.csv", "2.csv", "3.csv"],
        "valve2": ["0.csv", "1.csv"],
    }.items():
        scenario_dir = tmp_path / scenario
        scenario_dir.mkdir()
        for name in names:
            (scenario_dir / name).touch()

    first = build_skab_split(tmp_path)
    second = build_skab_split(tmp_path)

    assert first == second
    assert {path.name for path in first.healthy_files} == {"anomaly-free.csv"}
    assert set(first.validation_files).isdisjoint(first.test_files)
    assert len(first.validation_files) == 3
    assert len(first.test_files) == 3


def test_threshold_changes_decision_without_changing_scores() -> None:
    """提高阈值应减少告警，而风险分数本身保持不变。"""

    row_count = 12
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "Pressure": np.ones(row_count),
        }
    )
    sensor_scores = pd.DataFrame({"Pressure": np.linspace(0.0, 6.0, row_count)})
    combined_score = sensor_scores["Pressure"].rename("risk_score")

    low_labels, _ = apply_detection_threshold(
        dataframe,
        sensor_scores,
        combined_score,
        AnalysisConfig(threshold=2.0, min_event_length=1, merge_gap=0),
    )
    high_labels, _ = apply_detection_threshold(
        dataframe,
        sensor_scores,
        combined_score,
        AnalysisConfig(threshold=5.0, min_event_length=1, merge_gap=0),
    )

    assert int(low_labels.sum()) > int(high_labels.sum())
    assert combined_score.equals(sensor_scores["Pressure"].rename("risk_score"))


def test_threshold_selection_rejects_low_recall_shortcut() -> None:
    """低召回候选即使综合分更高，也不能靠少告警成为工业最优阈值。"""

    reliable = ThresholdTrial(
        detector="isolation_forest",
        threshold=5.0,
        objective=0.02,
        file_count=10,
        point_f1=0.20,
        event_f1=0.23,
        event_recall=0.65,
        average_false_events=6.0,
        healthy_false_event_rate=5.0,
        failed_files=0,
    )
    silent = ThresholdTrial(
        detector="isolation_forest",
        threshold=10.0,
        objective=0.10,
        file_count=10,
        point_f1=0.04,
        event_f1=0.09,
        event_recall=0.24,
        average_false_events=1.0,
        healthy_false_event_rate=0.0,
        failed_files=0,
    )

    assert select_best_trial([reliable, silent]) == reliable
