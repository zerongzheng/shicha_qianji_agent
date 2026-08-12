"""核心流水线回归测试。

测试使用临时 CSV，确保项目上传 GitHub 后，即使没有 SKAB 数据也能验证基本功能。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analysis import analyze_file
from app.analysis.detection import (
    _AUTOENCODER_CACHE,
    _build_frequency_windows,
    _build_relation_windows,
    _map_window_values_to_endpoints,
    _normalized_tfr_weights,
    clear_autoencoder_cache,
    detect_anomalies,
)
from app.analysis.evaluation import evaluate_predictions
from app.analysis.relationships import analyze_event_relationships
from app.models import AnalysisConfig, AnomalyEvent


@pytest.mark.parametrize(
    "detector",
    [
        "mad",
        "isolation_forest",
        "pca_reconstruction",
        "window_autoencoder",
        "time_frequency_relation",
        "hybrid",
    ],
)
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
    assert result.operating_regimes is not None
    assert result.operating_regimes.state_count >= 1
    assert not result.operating_regimes.suppression_applied
    assert result.detector_name
    assert len(result.event_diagnoses) == len(result.events)
    assert len(result.work_order_drafts) == len(result.event_diagnoses)
    assert "候选根因诊断" in result.to_summary()
    assert isinstance(result.to_summary()["评估指标"], dict)
    assert "工业时序诊断报告" in result.report_text


def test_execution_trace_records_stable_automatic_chain(tmp_path) -> None:
    """执行轨迹应记录真实步骤顺序，并明确标记被关闭的可选模块。"""

    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=100, freq="s"),
            "Pressure": np.ones(100),
            "Current": np.ones(100),
        }
    )
    csv_path = tmp_path / "trace_sample.csv"
    dataframe.to_csv(csv_path, sep=";", index=False)

    result = analyze_file(
        csv_path,
        config=AnalysisConfig(detector="mad", threshold=8.0),
        write_report=False,
        run_forecast=False,
        run_regime=False,
    )

    step_ids = [step.step_id for step in result.execution_trace]
    assert step_ids == [
        "data_ingestion",
        "device_profile_match",
        "data_profile",
        "adaptive_preprocessing",
        "model_selection",
        "anomaly_detection",
        "model_cross_validation",
        "operating_regime",
        "relationship_evidence",
        "forecast_analysis",
        "root_cause_diagnosis",
        "work_order_generation",
        "optimization_recommendation",
    ]
    trace_by_id = {step.step_id: step for step in result.execution_trace}
    assert trace_by_id["model_cross_validation"].status == "skipped"
    assert trace_by_id["operating_regime"].status == "skipped"
    assert trace_by_id["forecast_analysis"].status == "skipped"
    assert trace_by_id["anomaly_detection"].output_summary["event_count"] == 0
    assert "智能体执行摘要" in result.to_summary()
    assert "智能体执行链" in result.report_text


def test_pipeline_can_run_detector_cross_validation(tmp_path) -> None:
    """完整流水线应按显式开关运行多模型验证，并写入可审计执行轨迹。"""

    rows = 220
    pressure = np.ones(rows)
    current = np.ones(rows) * 2
    pressure[120:145] += 3
    current[120:145] += 0.8
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=rows, freq="s"),
            "Pressure": pressure,
            "Current": current,
            "anomaly": [0] * 120 + [1] * 25 + [0] * 75,
        }
    )
    csv_path = tmp_path / "validation_sample.csv"
    dataframe.to_csv(csv_path, sep=";", index=False)

    result = analyze_file(
        csv_path,
        config=AnalysisConfig(
            detector="mad",
            threshold=3.0,
            rolling_window=31,
            min_event_length=2,
            use_healthy_baseline=False,
        ),
        write_report=False,
        run_forecast=False,
        run_regime=False,
        run_detector_validation=True,
    )

    validation = result.detector_validation
    assert validation["status"] == "completed"
    assert validation["model_count"] >= 3
    trace = next(
        step for step in result.execution_trace if step.step_id == "model_cross_validation"
    )
    assert trace.status == "completed"
    assert trace.output_summary["model_count"] >= 3


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


def test_pca_reconstruction_detects_broken_sensor_relationship() -> None:
    """单点幅值不极端但传感器耦合关系被破坏时，PCA 应形成异常事件。"""

    row_count = 300
    random = np.random.default_rng(11)
    base = np.sin(np.arange(row_count) / 18) + random.normal(0, 0.02, row_count)
    sensor_a = base.copy()
    sensor_b = 2 * base + random.normal(0, 0.02, row_count)
    sensor_b[210:250] = -2 * base[210:250] + 3
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "SensorA": sensor_a,
            "SensorB": sensor_b,
        }
    )

    output = detect_anomalies(
        dataframe,
        ["SensorA", "SensorB"],
        AnalysisConfig(
            detector="pca_reconstruction",
            threshold=4.5,
            rolling_window=31,
            min_event_length=3,
            merge_gap=2,
            use_healthy_baseline=False,
        ),
    )

    assert output.events
    assert int(output.predicted_labels.loc[210:249].sum()) > 0
    assert float(output.combined_score.max()) <= 90.0


def test_window_autoencoder_detects_nonlinear_relationship_change() -> None:
    """健康非线性关系被破坏后，窗口 AutoEncoder 应在异常区间产生显著风险。"""

    row_count = 420
    random = np.random.default_rng(23)
    phase = np.arange(row_count) / 13
    source = np.sin(phase) + 0.15 * np.sin(phase * 0.31)
    sensor_a = source + random.normal(0, 0.01, row_count)
    sensor_b = source**2 + 0.2 * source + random.normal(0, 0.01, row_count)
    sensor_c = np.tanh(1.8 * source) + random.normal(0, 0.01, row_count)
    sensor_b[300:355] = 1.2 - source[300:355] ** 2
    sensor_c[300:355] = -np.tanh(1.8 * source[300:355])
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "SensorA": sensor_a,
            "SensorB": sensor_b,
            "SensorC": sensor_c,
        }
    )

    output = detect_anomalies(
        dataframe,
        ["SensorA", "SensorB", "SensorC"],
        AnalysisConfig(
            detector="window_autoencoder",
            threshold=4.0,
            rolling_window=31,
            min_event_length=3,
            merge_gap=2,
            use_healthy_baseline=False,
            autoencoder_window=12,
            autoencoder_hidden=20,
            autoencoder_bottleneck=5,
            autoencoder_max_iter=180,
        ),
    )

    normal_score = float(output.combined_score.loc[150:220].median())
    anomaly_score = float(output.combined_score.loc[305:350].median())
    assert anomaly_score > normal_score * 2
    assert int(output.predicted_labels.loc[300:355].sum()) > 0


def test_frequency_path_detects_frequency_shift() -> None:
    """振幅近似不变但主频改变时，频域路径应在异常区间给出更高风险。"""

    row_count = 520
    phase = np.arange(row_count)
    healthy = np.sin(2 * np.pi * phase / 32)
    shifted = healthy.copy()
    shifted[360:440] = np.sin(2 * np.pi * phase[360:440] / 8)
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "Vibration": shifted,
        }
    )
    reference = pd.DataFrame({"Vibration": healthy[:300]})

    output = detect_anomalies(
        dataframe,
        ["Vibration"],
        AnalysisConfig(
            detector="time_frequency_relation",
            threshold=4.5,
            min_event_length=2,
            merge_gap=1,
            autoencoder_window=32,
            tfr_time_weight=0.0,
            tfr_frequency_weight=1.0,
            tfr_relation_weight=0.0,
            tfr_frequency_components=4,
        ),
        reference,
    )

    normal_score = float(output.combined_score.loc[160:280].median())
    anomaly_score = float(output.combined_score.loc[380:430].median())
    assert anomaly_score > max(normal_score * 3, 4.5)
    assert int(output.predicted_labels.loc[360:440].sum()) > 0


def test_relation_path_detects_sensor_coupling_break() -> None:
    """各传感器幅值仍正常但相关方向反转时，关系路径应识别耦合结构异常。"""

    row_count = 520
    random = np.random.default_rng(91)
    source = np.sin(np.arange(row_count) / 10) + random.normal(0, 0.015, row_count)
    sensor_a = source + random.normal(0, 0.01, row_count)
    sensor_b = source + random.normal(0, 0.01, row_count)
    sensor_b[360:440] = -source[360:440] + random.normal(0, 0.01, 80)
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "SensorA": sensor_a,
            "SensorB": sensor_b,
        }
    )
    reference = pd.DataFrame(
        {
            "SensorA": sensor_a[:300],
            "SensorB": sensor_a[:300] + random.normal(0, 0.01, 300),
        }
    )

    output = detect_anomalies(
        dataframe,
        ["SensorA", "SensorB"],
        AnalysisConfig(
            detector="time_frequency_relation",
            threshold=4.5,
            min_event_length=2,
            merge_gap=1,
            autoencoder_window=24,
            tfr_time_weight=0.0,
            tfr_frequency_weight=0.0,
            tfr_relation_weight=1.0,
        ),
        reference,
    )

    normal_score = float(output.combined_score.loc[160:280].median())
    anomaly_score = float(output.combined_score.loc[380:430].median())
    assert anomaly_score > max(normal_score * 3, 4.5)
    assert int(output.predicted_labels.loc[360:440].sum()) > 0


def test_tfr_weights_support_ablation_and_reject_invalid_values() -> None:
    """多路径权重应可用于消融，并拒绝负数或全部关闭的无效配置。"""

    weights = _normalized_tfr_weights(
        AnalysisConfig(
            tfr_time_weight=2.0,
            tfr_frequency_weight=1.0,
            tfr_relation_weight=1.0,
        )
    )

    assert weights == pytest.approx((0.5, 0.25, 0.25))
    with pytest.raises(ValueError, match="非负数"):
        _normalized_tfr_weights(AnalysisConfig(tfr_time_weight=-1.0))
    with pytest.raises(ValueError, match="总和大于 0"):
        _normalized_tfr_weights(
            AnalysisConfig(
                tfr_time_weight=0.0,
                tfr_frequency_weight=0.0,
                tfr_relation_weight=0.0,
            )
        )


def test_tfr_window_features_are_causal_and_have_expected_shape() -> None:
    """时频关系特征只能由当前及历史窗口构成，并保持可解释的特征维度。"""

    values = np.arange(20, dtype=float).reshape(10, 2)
    frequency = _build_frequency_windows(values, window_size=4)
    relation = _build_relation_windows(values, window_size=4)

    assert frequency.shape == (7, 6)
    assert relation.shape == (7, 1)
    modified = values.copy()
    modified[-1] = 999.0
    modified_frequency = _build_frequency_windows(modified, window_size=4)
    assert np.allclose(frequency[:-1], modified_frequency[:-1])


def test_window_score_is_only_written_at_window_endpoint() -> None:
    """窗口结束前不能提前出现该窗口分数，防止离线评估产生未来信息泄漏。"""

    mapped = _map_window_values_to_endpoints(np.array([2.0, 4.0, 8.0]), 6, 4)

    assert mapped.tolist() == [0.0, 0.0, 0.0, 2.0, 4.0, 8.0]


def test_autoencoder_reuses_identical_healthy_model() -> None:
    """相同健康基线和参数的连续分析应复用已训练模型。"""

    clear_autoencoder_cache()
    row_count = 180
    source = np.sin(np.arange(row_count) / 12)
    healthy = pd.DataFrame({"SensorA": source, "SensorB": source**2})
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "SensorA": source,
            "SensorB": source**2,
        }
    )
    config = AnalysisConfig(
        detector="window_autoencoder",
        autoencoder_window=10,
        autoencoder_max_iter=60,
        min_event_length=1,
    )

    detect_anomalies(dataframe, ["SensorA", "SensorB"], config, healthy)
    first_model = next(iter(_AUTOENCODER_CACHE.values())).model
    detect_anomalies(dataframe, ["SensorA", "SensorB"], config, healthy)
    second_model = next(iter(_AUTOENCODER_CACHE.values())).model

    assert len(_AUTOENCODER_CACHE) == 1
    assert first_model is second_model


def test_autoencoder_restores_model_after_memory_cache_clear(tmp_path, monkeypatch) -> None:
    """清空进程缓存后，相同健康模型应从磁盘仓库恢复而不是重新训练。"""

    import app.model_store.autoencoder as model_store

    monkeypatch.setattr(
        model_store,
        "get_settings",
        lambda: type("TestSettings", (), {"output_dir": tmp_path})(),
    )
    clear_autoencoder_cache()
    row_count = 180
    source = np.cos(np.arange(row_count) / 11)
    healthy = pd.DataFrame({"SensorA": source, "SensorB": source**2})
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "SensorA": source,
            "SensorB": source**2,
        }
    )
    config = AnalysisConfig(
        detector="window_autoencoder",
        autoencoder_window=9,
        autoencoder_max_iter=55,
        min_event_length=1,
    )

    first = detect_anomalies(dataframe, ["SensorA", "SensorB"], config, healthy)
    clear_autoencoder_cache()
    monkeypatch.setattr(
        "app.analysis.detection.MLPRegressor.fit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("磁盘模型命中后不应重新训练")
        ),
    )
    second = detect_anomalies(dataframe, ["SensorA", "SensorB"], config, healthy)

    assert len(_AUTOENCODER_CACHE) == 1
    assert np.allclose(first.combined_score, second.combined_score)


def test_relationship_diagnostics_reports_leading_sensor() -> None:
    """时滞诊断应能从差分序列中找到先变化的测点。"""

    row_count = 160
    source = np.sin(np.arange(row_count) / 5)
    delayed = np.concatenate([np.zeros(3), source[:-3]])
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "Source": source,
            "Delayed": delayed,
        }
    )
    event = AnomalyEvent(
        start_index=80,
        end_index=130,
        start_time=dataframe.at[80, "datetime"],
        end_time=dataframe.at[130, "datetime"],
        duration_points=51,
        peak_score=8.0,
        severity="中风险",
        dominant_sensors=["Source", "Delayed"],
        sensor_scores={"Source": 8.0, "Delayed": 7.0},
    )

    diagnostics = analyze_event_relationships(
        dataframe,
        ["Source", "Delayed"],
        [event],
        max_lag=8,
    )

    relation = diagnostics[0]["重点关系"][0]
    assert relation["最佳时滞"] == 3
    assert "Source 的变化领先 Delayed" in relation["时滞解释"]
