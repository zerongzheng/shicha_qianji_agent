"""趋势预测与提前预警成效实验测试。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.experiments.forecast_effectiveness import (
    AUTOMATIC_STRATEGY,
    RealForecastRecord,
    WarningScenarioRecord,
    build_controlled_scenarios,
    build_forecast_effectiveness_report,
)


def test_controlled_scenarios_are_deterministic_and_include_stable_control() -> None:
    """受控场景必须可复现，并包含真正不越界的稳定对照。"""

    first = build_controlled_scenarios(seed=7)
    second = build_controlled_scenarios(seed=7)

    assert [item.scenario for item in first] == [item.scenario for item in second]
    assert all(np.array_equal(left.values, right.values) for left, right in zip(first, second))
    stable = next(item for item in first if item.scenario == "stable_control")
    assert stable.degradation_start is None
    assert stable.risk_threshold == 1.0
    assert np.max(np.abs(stable.values)) < stable.risk_threshold


def test_controlled_scenarios_cover_required_degradation_patterns() -> None:
    """风险场景应覆盖上升、下降、加速和周期漂移四种机制。"""

    scenarios = {item.scenario: item for item in build_controlled_scenarios()}

    assert set(scenarios) == {
        "gradual_up",
        "gradual_down",
        "accelerated",
        "cyclic_drift",
        "stable_control",
    }
    assert scenarios["gradual_up"].values[-1] > scenarios["gradual_up"].risk_threshold
    assert scenarios["gradual_down"].values[-1] < scenarios["gradual_down"].risk_threshold


def test_forecast_report_separates_real_data_and_controlled_simulation() -> None:
    """报告必须清楚披露真实公开数据和受控模拟的证据边界。"""

    real_record = RealForecastRecord(
        strategy=AUTOMATIC_STRATEGY,
        strategy_name="滚动回测自动选模",
        selected_model="linear_trend",
        scenario="valve1",
        file_name="1.csv",
        sensor="Pressure",
        history_points=200,
        holdout_points=30,
        rmse=0.1,
        mae=0.08,
        mape=0.05,
        normalized_rmse=0.4,
        normalized_mae=0.3,
        persistence_improvement=0.2,
        interval_coverage=0.93,
        direction_correct=True,
        inference_seconds=0.1,
    )
    warning_record = WarningScenarioRecord(
        scenario="gradual_up",
        scenario_name="渐进上升退化",
        risk_direction="up",
        threshold=1.0,
        crossing_index=260,
        forecast_opportunities=15,
        event_opportunities=3,
        warning_count=2,
        true_warning_count=2,
        false_warning_count=0,
        event_detected=True,
        lead_time_points=20,
        direction_accuracy=0.8,
        interval_coverage=0.95,
        selected_models='{"linear_trend": 15}',
    )

    report = build_forecast_effectiveness_report(
        Path("SKAB/data"),
        1,
        [real_record],
        [warning_record],
        {},
        holdout=30,
    )

    assert "SKAB 真实时序预测" in report
    assert "受控退化提前预警" in report
    assert "不是企业设备工程限值" in report
    assert "不能替代企业现场验证" in report
    assert "预测起点之后的真实值只用于最终评价" in report
