"""任务场景模型选择器回归测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.model_selection import select_detection_model
from app.models import AnalysisConfig


def _dataframe(*, sensors: int = 3, with_labels: bool = False) -> pd.DataFrame:
    data: dict[str, object] = {
        "datetime": pd.date_range("2026-01-01", periods=160, freq="s")
    }
    for index in range(sensors):
        data[f"Sensor{index + 1}"] = np.sin(np.arange(160) / (8 + index))
    if with_labels:
        data["anomaly"] = [0] * 100 + [1] * 20 + [0] * 40
    return pd.DataFrame(data)


def _skab_context() -> dict:
    return {
        "profile_id": "skab_valve",
        "display_name": "SKAB 水循环阀门测试台",
        "match_mode": "automatic",
        "recommended_analysis": {
            "detector": "time_frequency_relation",
            "threshold": 3.5,
            "min_event_length": 12,
            "merge_gap": 30,
            "goal_policy": {
                "balanced": "time_frequency_relation",
                "high_recall": "time_frequency_relation",
                "low_false_alarm": "mad",
                "relationship_fault": "time_frequency_relation",
                "nonlinear_pattern": "window_autoencoder",
                "fast_screening": "mad",
            },
        },
    }


def test_manual_selection_preserves_explicit_detector_and_threshold() -> None:
    """实验和人工指定模型必须保持可复现，不能被自动路由覆盖。"""

    config = AnalysisConfig(
        detector_selection_mode="manual",
        detector="pca_reconstruction",
        threshold=7.25,
    )
    effective, decision = select_detection_model(
        _dataframe(),
        ["Sensor1", "Sensor2", "Sensor3"],
        config,
        {},
        healthy_baseline_available=False,
    )

    assert effective == config
    assert decision["mode"] == "manual"
    assert decision["selected_detector"] == "pca_reconstruction"
    assert decision["selected_threshold"] == 7.25


def test_device_goal_policy_selects_different_models_without_labels() -> None:
    """同一设备应能按任务目标切换模型，而不是所有场景固定使用一个模型。"""

    dataframe = _dataframe()
    sensors = ["Sensor1", "Sensor2", "Sensor3"]
    balanced, balanced_decision = select_detection_model(
        dataframe,
        sensors,
        AnalysisConfig(detector_selection_mode="auto", analysis_goal="balanced"),
        _skab_context(),
        healthy_baseline_available=True,
    )
    low_alarm, low_alarm_decision = select_detection_model(
        dataframe,
        sensors,
        AnalysisConfig(detector_selection_mode="auto", analysis_goal="low_false_alarm"),
        _skab_context(),
        healthy_baseline_available=True,
    )

    assert balanced.detector == "time_frequency_relation"
    assert balanced.threshold == 3.5
    assert balanced.min_event_length == 12
    assert balanced.merge_gap == 30
    assert low_alarm.detector == "mad"
    assert low_alarm.threshold == 5.5
    assert low_alarm.min_event_length == 3
    assert low_alarm.merge_gap == 5
    assert balanced_decision["selected_event_policy"] == {
        "min_event_length": 12,
        "merge_gap": 30,
    }
    assert balanced_decision["selection_source"] == "device_profile_goal_policy"
    assert low_alarm_decision["selection_source"] == "device_profile_goal_policy"


def test_ineligible_model_is_skipped_and_reason_is_recorded() -> None:
    """没有健康基线时不能选择 AutoEncoder，应降级到下一种适用模型并说明原因。"""

    effective, decision = select_detection_model(
        _dataframe(),
        ["Sensor1", "Sensor2", "Sensor3"],
        AnalysisConfig(
            detector_selection_mode="auto",
            analysis_goal="nonlinear_pattern",
        ),
        _skab_context(),
        healthy_baseline_available=False,
    )

    autoencoder = next(
        item for item in decision["candidate_ranking"] if item["detector"] == "window_autoencoder"
    )
    assert effective.detector == "time_frequency_relation"
    assert autoencoder["eligible"] is False
    assert "需要可用健康基线" in autoencoder["blockers"]


def test_single_sensor_data_falls_back_to_mad() -> None:
    """单传感器数据不满足关系模型条件时，应选择可解释的稳健统计模型。"""

    effective, decision = select_detection_model(
        _dataframe(sensors=1),
        ["Sensor1"],
        AnalysisConfig(detector_selection_mode="auto", analysis_goal="balanced"),
        {},
        healthy_baseline_available=False,
    )

    assert effective.detector == "mad"
    assert decision["selection_source"] == "task_goal"


def test_current_file_labels_do_not_change_model_selection() -> None:
    """增加 anomaly 标签前后选择必须一致，证明路由没有查看测试答案。"""

    config = AnalysisConfig(detector_selection_mode="auto", analysis_goal="balanced")
    without_labels, decision_without = select_detection_model(
        _dataframe(with_labels=False),
        ["Sensor1", "Sensor2", "Sensor3"],
        config,
        _skab_context(),
        healthy_baseline_available=True,
    )
    with_labels, decision_with = select_detection_model(
        _dataframe(with_labels=True),
        ["Sensor1", "Sensor2", "Sensor3"],
        config,
        _skab_context(),
        healthy_baseline_available=True,
    )

    assert with_labels.detector == without_labels.detector
    assert decision_with["candidate_ranking"] == decision_without["candidate_ranking"]
