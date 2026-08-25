"""多模型严格多数共识实验测试。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.experiments.consensus_evaluation import (
    CONSENSUS_DETECTORS,
    ConsensusRecord,
    build_consensus_report,
    strict_majority_consensus,
)


def test_strict_majority_requires_three_of_four_votes() -> None:
    """四模型二比二不能被包装成多数，三票才形成共识告警。"""

    labels = {
        CONSENSUS_DETECTORS[0]: pd.Series([1, 1, 0]),
        CONSENSUS_DETECTORS[1]: pd.Series([1, 1, 0]),
        CONSENSUS_DETECTORS[2]: pd.Series([0, 1, 0]),
        CONSENSUS_DETECTORS[3]: pd.Series([0, 0, 1]),
    }
    scores = {
        detector: series.astype(float) * (index + 1)
        for index, (detector, series) in enumerate(labels.items())
    }

    consensus, confidence = strict_majority_consensus(labels, scores)

    assert consensus.tolist() == [0, 1, 0]
    assert confidence.tolist() == pytest.approx([0.75, 1.5, 1.0])


def test_consensus_requires_complete_and_aligned_outputs() -> None:
    """缺模型或长度不一致必须报错，不能静默降低投票门槛。"""

    incomplete_labels = {name: pd.Series([0, 1]) for name in CONSENSUS_DETECTORS[:-1]}
    incomplete_scores = {name: pd.Series([0.0, 1.0]) for name in CONSENSUS_DETECTORS[:-1]}
    with pytest.raises(ValueError, match="完整提供四个"):
        strict_majority_consensus(incomplete_labels, incomplete_scores)

    labels = {name: pd.Series([0, 1]) for name in CONSENSUS_DETECTORS}
    scores = {name: pd.Series([0.0, 1.0]) for name in CONSENSUS_DETECTORS}
    scores[CONSENSUS_DETECTORS[-1]] = pd.Series([0.0])
    with pytest.raises(ValueError, match="长度不一致"):
        strict_majority_consensus(labels, scores)


def test_report_states_test_protocol_and_result_boundary() -> None:
    """报告必须说明冻结策略、严格多数及公开数据边界。"""

    records = [
        ConsensusRecord(
            strategy="mad",
            strategy_name="稳健 MAD",
            scenario="valve1",
            file_name="1.csv",
            row_count=100,
            point_precision=0.8,
            point_recall=0.7,
            point_f1=0.75,
            pr_auc=0.8,
            event_precision=1.0,
            event_recall=1.0,
            event_f1=1.0,
            false_positive_events=0,
            detection_delay=2.0,
            inference_seconds=0.2,
        )
    ]

    report = build_consensus_report(Path("SKAB/data"), 1, records, {})

    assert "冻结阈值和固定独立测试集" in report
    assert "至少三个" in report
    assert "不代表联通企业现场收益" in report
