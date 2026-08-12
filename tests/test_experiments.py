"""实验划分和阈值决策的回归测试。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app.analysis.detection import DETECTOR_RECOMMENDED_THRESHOLDS, apply_detection_threshold
from app.api.server import _parse_config
from app.experiments.protocol import (
    build_protocol_manifest,
    read_frozen_thresholds,
    write_protocol_artifacts,
)
from app.experiments.split import build_skab_split
from app.experiments.tfr_ablation import TfrAblationRecord, select_tfr_candidate
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


def test_threshold_selection_freezes_the_complete_parameter_tuple() -> None:
    """联合调参必须选择完整组合，不能只记阈值而丢失事件后处理参数。"""

    baseline = ThresholdTrial(
        detector="time_frequency_relation",
        threshold=4.0,
        objective=0.20,
        file_count=17,
        point_f1=0.30,
        event_f1=0.45,
        event_recall=0.90,
        average_false_events=3.0,
        healthy_false_event_rate=0.0,
        failed_files=0,
        min_event_length=3,
        merge_gap=5,
    )
    concise = ThresholdTrial(
        detector="time_frequency_relation",
        threshold=4.0,
        objective=0.28,
        file_count=17,
        point_f1=0.30,
        event_f1=0.56,
        event_recall=0.90,
        average_false_events=1.5,
        healthy_false_event_rate=0.0,
        failed_files=0,
        min_event_length=5,
        merge_gap=30,
    )

    selected = select_best_trial([baseline, concise])

    assert (selected.threshold, selected.min_event_length, selected.merge_gap) == (4.0, 5, 30)


def test_api_uses_detector_specific_frozen_threshold() -> None:
    """万悟未显式传阈值时，应使用对应检测器的验证集冻结值。"""

    pca_config = _parse_config({"detector": "pca_reconstruction"})
    autoencoder_config = _parse_config({"detector": "window_autoencoder"})
    overridden = _parse_config({"detector": "pca_reconstruction", "threshold": 7.5})

    assert pca_config.threshold == DETECTOR_RECOMMENDED_THRESHOLDS["pca_reconstruction"]
    assert autoencoder_config.threshold == DETECTOR_RECOMMENDED_THRESHOLDS["window_autoencoder"]
    assert overridden.threshold == 7.5


def test_api_uses_frozen_tfr_weights_by_default() -> None:
    """万悟未显式传权重时，应使用固定消融实验选出的路径配置。"""

    config = _parse_config({"detector": "time_frequency_relation"})

    assert config.tfr_time_weight == 0.67
    assert config.tfr_frequency_weight == 0.0
    assert config.tfr_relation_weight == 0.33
    assert config.threshold == 3.5
    assert config.min_event_length == 12
    assert config.merge_gap == 30


def test_api_preserves_explicit_event_policy_overrides() -> None:
    """企业设备已标定参数由调用方显式传入时，API 不能用 SKAB 推荐值覆盖。"""

    config = _parse_config(
        {
            "detector": "time_frequency_relation",
            "min_event_length": 9,
            "merge_gap": 12,
        }
    )

    assert config.min_event_length == 9
    assert config.merge_gap == 12


def test_api_parses_regime_suppression_boolean_strings() -> None:
    """万悟 JSON 或表单传入字符串 false 时不能误开启告警抑制。"""

    disabled = _parse_config({"suppress_transition_events": "false"})
    enabled = _parse_config({"suppress_transition_events": "true"})

    assert not disabled.suppress_transition_events
    assert enabled.suppress_transition_events


def test_tfr_candidate_selection_rejects_low_recall_shortcut() -> None:
    """消融选择不能让低召回候选仅凭较少误报胜出。"""

    reliable = TfrAblationRecord(
        candidate_id="time_relation",
        time_weight=0.67,
        frequency_weight=0.0,
        relation_weight=0.33,
        threshold=5.0,
        objective=0.20,
        point_f1=0.30,
        event_f1=0.40,
        event_recall=0.70,
        average_false_events=3.0,
        healthy_false_event_rate=1.0,
    )
    silent = TfrAblationRecord(
        candidate_id="full_equal_aux",
        time_weight=0.50,
        frequency_weight=0.25,
        relation_weight=0.25,
        threshold=9.0,
        objective=0.25,
        point_f1=0.20,
        event_f1=0.20,
        event_recall=0.30,
        average_false_events=0.5,
        healthy_false_event_rate=0.0,
    )

    assert select_tfr_candidate([silent, reliable]) == reliable


def test_protocol_manifest_records_disjoint_files_and_hashes(tmp_path: Path) -> None:
    """实验协议应记录固定划分和文件指纹，确保后续结果可追溯。"""

    for scenario, names in {
        "anomaly-free": ["anomaly-free.csv"],
        "valve1": ["0.csv", "1.csv"],
    }.items():
        scenario_dir = tmp_path / scenario
        scenario_dir.mkdir()
        for name in names:
            (scenario_dir / name).write_text("datetime;anomaly\n2026-01-01;0\n", encoding="utf-8")

    split = build_skab_split(tmp_path)
    manifest = build_protocol_manifest(
        tmp_path,
        split,
        selected_thresholds={"mad": 5.5},
        selected_event_policies={"mad": {"min_event_length": 3, "merge_gap": 5}},
        detectors=("mad",),
    )
    assert manifest["counts"] == {"healthy": 1, "validation": 1, "test": 1, "total": 3}
    assert len(manifest["files"][0]["sha256"]) == 64
    assert manifest["frozen_thresholds"]["mad"] == 5.5

    json_path, markdown_path = write_protocol_artifacts(manifest, tmp_path / "out")
    assert json_path.exists()
    assert markdown_path.exists()
    assert "文件校验清单" in markdown_path.read_text(encoding="utf-8")
    assert "持续状态标签" in manifest["preprocessing_policy"]["label_aggregation"]
    assert "瞬时标签新增点填 0" in manifest["preprocessing_policy"]["label_aggregation"]
    assert "验证集联合选择" in manifest["event_policy"]["selection"]
    assert manifest["frozen_event_policies"]["mad"] == {
        "min_event_length": 3,
        "merge_gap": 5,
    }
    assert "最短事件 `3`" in markdown_path.read_text(encoding="utf-8")
    assert read_frozen_thresholds(tmp_path / "out") == {"mad": 5.5}
