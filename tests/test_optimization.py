"""结构化优化建议和受控成效实验测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.optimization import generate_optimization_recommendations
from app.analysis.profiling import build_profile
from app.experiments.optimization_effectiveness import (
    apply_bounded_recommendation,
    build_optimization_report,
    evaluate_optimization_effectiveness,
)


def test_optimization_does_not_invent_unknown_safe_range() -> None:
    """没有企业设备范围时，建议必须明确要求人工确认数值边界。"""

    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=100, freq="s"),
            "Pressure": np.linspace(1.0, 2.0, 100),
            "Current": np.linspace(3.0, 3.5, 100),
        }
    )
    profile = build_profile(dataframe, "sample.csv")
    recommendations = generate_optimization_recommendations(
        profile,
        {"raw_missing_count": 0, "time_alignment_applied": False},
        {
            "Pressure": {
                "风险": "需关注",
                "方向": "持续上升",
                "当前值": 2.0,
                "预测末值": 2.4,
                "不确定度": {"预测可信度": "中"},
            }
        },
        [],
        {"sensor_metadata": {"Pressure": {"safe_range": None}}},
    )

    parameter = next(item for item in recommendations if item.category == "参数稳定")
    assert "待企业设备手册" in parameter.suggested_range
    assert "不直接下发控制指令" in parameter.constraints[-1]
    assert any(item.category == "能耗优化" for item in recommendations)


def test_bounded_recommendation_does_not_act_on_stable_control() -> None:
    """稳定信号不应触发参数调整，且所有调整必须遵守硬上限。"""

    stable = np.zeros(200)
    controlled, audit = apply_bounded_recommendation(stable)

    assert np.array_equal(controlled, stable)
    assert audit["intervention_count"] == 0
    assert audit["saturation_count"] == 0
    assert audit["constraint_violations"] == 0


def test_bounded_recommendation_limits_accumulated_adjustment() -> None:
    """持续退化时累计建议也必须受限，不能无限抵消外部风险。"""

    disturbance = np.linspace(0.0, 2.0, 240)
    controlled, audit = apply_bounded_recommendation(disturbance)

    adjustment = controlled - disturbance
    assert float(np.max(np.abs(adjustment))) <= 0.45 + 1e-12
    assert audit["saturation_count"] > 0
    assert audit["constraint_violations"] == 0


def test_optimization_experiment_exports_boundary_report(tmp_path) -> None:
    """实验报告必须披露受控模拟边界，不能表述为企业收益。"""

    evaluation = evaluate_optimization_effectiveness(tmp_path, seed=5)
    report = build_optimization_report(evaluation.records)

    assert evaluation.csv_path.exists()
    assert "不代表企业设备控制效果" in report
    assert "稳定对照干预次数：0" in report
    assert all(item.constraint_violations == 0 for item in evaluation.records)
