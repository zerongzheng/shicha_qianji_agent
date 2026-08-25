"""多模型交叉验证的独立回归测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd

import app.analysis.model_validation as validation_module
from app.analysis.detection import detect_anomalies
from app.analysis.model_validation import cross_validate_detectors
from app.models import AnalysisConfig


def _sample_dataframe(*, with_labels: bool = True) -> pd.DataFrame:
    """构造包含一段联合偏移的小型工业时序，避免测试依赖外部 SKAB 路径。"""

    row_count = 220
    random = np.random.default_rng(23)
    pressure = random.normal(10.0, 0.08, row_count)
    current = random.normal(2.0, 0.03, row_count)
    pressure[120:145] += 2.8
    current[120:145] += 0.8
    data: dict[str, object] = {
        "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
        "Pressure": pressure,
        "Current": current,
    }
    if with_labels:
        data["anomaly"] = [0] * 120 + [1] * 25 + [0] * 75
        data["changepoint"] = np.zeros(row_count)
    return pd.DataFrame(data)


def _run_validation(dataframe: pd.DataFrame) -> dict:
    config = AnalysisConfig(
        detector="mad",
        threshold=3.25,
        rolling_window=31,
        min_event_length=2,
        merge_gap=2,
        use_healthy_baseline=False,
    )
    primary = detect_anomalies(dataframe, ["Pressure", "Current"], config)
    return cross_validate_detectors(
        dataframe,
        ["Pressure", "Current"],
        config,
        primary,
    )


def test_cross_validation_preserves_primary_configuration_and_metrics() -> None:
    """主模型身份和实际阈值必须可信，带标签数据应输出离线指标。"""

    result = _run_validation(_sample_dataframe())

    assert result["status"] == "completed"
    assert result["model_count"] == 4
    primary_records = [item for item in result["models"] if item["is_primary"]]
    assert len(primary_records) == 1
    assert primary_records[0]["detector"] == "mad"
    assert primary_records[0]["threshold"] == 3.25
    assert all(item["evaluation"] is not None for item in result["models"])
    assert result["agreement"]["required_votes"] == 3
    assert result["selection_basis"]


def test_cross_validation_without_labels_only_reports_model_agreement() -> None:
    """企业无标签数据不能伪造准确率，但仍可报告跨模型一致性。"""

    result = _run_validation(_sample_dataframe(with_labels=False))

    assert result["model_count"] == 4
    assert all(item["evaluation"] is None for item in result["models"])
    assert result["agreement"]["level"] in {"高", "中", "低"}


def test_one_complementary_model_failure_does_not_abort_validation(monkeypatch) -> None:
    """互补模型故障应被隔离，主分析和其他验证证据仍然可用。"""

    dataframe = _sample_dataframe()
    config = AnalysisConfig(
        detector="mad",
        threshold=3.25,
        rolling_window=31,
        min_event_length=2,
        merge_gap=2,
        use_healthy_baseline=False,
    )
    primary = detect_anomalies(dataframe, ["Pressure", "Current"], config)
    original_detect = validation_module.detect_anomalies

    def fail_one_detector(dataframe, sensor_columns, candidate_config, healthy_reference=None):
        if candidate_config.detector == "pca_reconstruction":
            raise RuntimeError("模拟互补模型加载失败")
        return original_detect(
            dataframe,
            sensor_columns,
            candidate_config,
            healthy_reference,
        )

    monkeypatch.setattr(validation_module, "detect_anomalies", fail_one_detector)
    result = cross_validate_detectors(
        dataframe,
        ["Pressure", "Current"],
        config,
        primary,
    )

    assert result["status"] == "completed"
    assert result["model_count"] == 3
    assert result["failed_models"] == [
        {"detector": "pca_reconstruction", "error": "模拟互补模型加载失败"}
    ]
    assert sum(item["is_primary"] for item in result["models"]) == 1
