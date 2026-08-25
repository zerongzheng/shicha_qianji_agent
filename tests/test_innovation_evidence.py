"""创新算法证据矩阵的纯计算测试。

测试不启动数据库，也不重新运行 SKAB 模型，只验证：总体均值按逐文件计算、分场景差值
以同场景 MAD 为基线、目标路由不依赖异常标签，以及历史消融产物会被正确标记。
"""

from __future__ import annotations

import csv

from app.experiments.innovation_evidence import (
    _build_ablation_rows,
    _build_overall_rows,
    _build_routing_rows,
    _build_scenario_rows,
)


def _records() -> list[dict[str, str]]:
    return [
        {
            "detector": "mad",
            "detector_name": "稳健 MAD",
            "scenario": "other",
            "point_f1": "0.10",
            "pr_auc": "0.40",
            "event_f1": "0.40",
            "event_recall": "0.80",
            "false_positive_events": "1",
            "inference_seconds": "0.10",
        },
        {
            "detector": "mad",
            "detector_name": "稳健 MAD",
            "scenario": "valve1",
            "point_f1": "0.20",
            "pr_auc": "0.50",
            "event_f1": "0.60",
            "event_recall": "0.90",
            "false_positive_events": "2",
            "inference_seconds": "0.20",
        },
        {
            "detector": "time_frequency_relation",
            "detector_name": "时频关系多路径检测器",
            "scenario": "other",
            "point_f1": "0.30",
            "pr_auc": "0.60",
            "event_f1": "0.70",
            "event_recall": "0.95",
            "false_positive_events": "2",
            "inference_seconds": "0.12",
        },
        {
            "detector": "time_frequency_relation",
            "detector_name": "时频关系多路径检测器",
            "scenario": "valve1",
            "point_f1": "0.25",
            "pr_auc": "0.55",
            "event_f1": "0.50",
            "event_recall": "0.85",
            "false_positive_events": "1",
            "inference_seconds": "0.22",
        },
        {
            "detector": "window_autoencoder",
            "detector_name": "滑动窗口 AutoEncoder 检测器",
            "scenario": "other",
            "point_f1": "0.35",
            "pr_auc": "0.58",
            "event_f1": "0.45",
            "event_recall": "0.75",
            "false_positive_events": "3",
            "inference_seconds": "0.08",
        },
        {
            "detector": "window_autoencoder",
            "detector_name": "滑动窗口 AutoEncoder 检测器",
            "scenario": "valve1",
            "point_f1": "0.30",
            "pr_auc": "0.57",
            "event_f1": "0.55",
            "event_recall": "0.80",
            "false_positive_events": "3",
            "inference_seconds": "0.09",
        },
    ]


def test_overall_uses_file_level_mean_and_baseline_delta() -> None:
    rows = _build_overall_rows(_records())
    tfr = next(row for row in rows if row["detector"] == "time_frequency_relation")
    assert tfr["file_count"] == 2
    assert tfr["event_f1"] == 0.6
    assert tfr["event_f1_delta_vs_mad"] == 0.1


def test_scenario_delta_uses_same_scenario_mad() -> None:
    rows = _build_scenario_rows(_records())
    other_tfr = next(
        row
        for row in rows
        if row["detector"] == "time_frequency_relation" and row["scenario"] == "other"
    )
    assert other_tfr["event_f1_delta_vs_mad"] == 0.3
    assert other_tfr["false_events_delta_vs_mad"] == 1.0


def test_routing_is_frozen_goal_policy_not_per_file_oracle() -> None:
    rows = _build_routing_rows(_build_overall_rows(_records()))
    by_goal = {row["analysis_goal"]: row for row in rows}
    assert by_goal["balanced"]["selected_detector"] == "time_frequency_relation"
    assert by_goal["low_false_alarm"]["selected_detector"] == "mad"
    assert all("不读取" in row["label_leakage_control"] for row in rows)


def test_historical_ablation_is_marked_and_normalized(tmp_path) -> None:
    path = tmp_path / "tfr_weight_ablation_old.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "candidate_id",
                "time_weight",
                "frequency_weight",
                "relation_weight",
                "threshold",
                "objective",
                "point_f1",
                "event_f1",
                "event_recall",
                "average_false_events",
                "healthy_false_event_rate",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "candidate_id": "full_equal_aux",
                "time_weight": "0.5",
                "frequency_weight": "0.25",
                "relation_weight": "0.25",
                "threshold": "4.5",
                "objective": "0.2",
                "point_f1": "0.3",
                "event_f1": "0.4",
                "event_recall": "0.8",
                "average_false_events": "2",
                "healthy_false_event_rate": "1",
            }
        )
    rows, status = _build_ablation_rows(path)
    assert status == "historical_unversioned"
    assert rows[0]["candidate_id"] == "full_equal_aux"
    assert rows[0]["validation_event_f1"] == 0.4
