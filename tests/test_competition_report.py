"""校赛实验汇总模块的回归测试。"""

from __future__ import annotations

from pathlib import Path

from app.experiments.competition_report import (
    _build_effectiveness_rows,
    _build_report,
    _has_current_protocol,
    _read_records,
    _summarize,
    _summarize_by_model,
)
from app.experiments.protocol import PROTOCOL_VERSION


def test_competition_summary_aggregates_by_scenario_and_model(tmp_path: Path) -> None:
    """逐文件记录应先按场景汇总，再按文件数量加权合并为模型总表。"""

    source = tmp_path / "benchmark.csv"
    source.write_text(
        "detector,detector_name,scenario,event_f1,point_f1,pr_auc,event_recall,false_positive_events,inference_seconds\n"
        "mad,稳健 MAD,valve1,0.4,0.2,0.5,0.8,2,0.1\n"
        "mad,稳健 MAD,valve1,0.6,0.4,0.7,1.0,4,0.3\n"
        "mad,稳健 MAD,other,0.2,0.1,0.3,0.5,1,0.2\n"
        "pca_reconstruction,PCA,other,0.8,0.7,0.9,1.0,1,0.1\n",
        encoding="utf-8",
    )

    records = _read_records(source)
    scenario_rows = _summarize(records)
    model_rows = _summarize_by_model(scenario_rows)

    valve_row = next(row for row in scenario_rows if row["scenario"] == "valve1")
    mad_row = next(row for row in model_rows if row["detector"] == "mad")

    assert valve_row["file_count"] == 2
    assert valve_row["event_f1"] == 0.5
    assert mad_row["file_count"] == 3
    assert mad_row["event_f1"] == 0.4


def test_competition_summary_rejects_missing_metric_columns(tmp_path: Path) -> None:
    """缺少关键实验字段时应尽早报错，而不是生成不完整的竞赛数据表。"""

    source = tmp_path / "invalid.csv"
    source.write_text("detector,scenario\nmad,valve1\n", encoding="utf-8")

    try:
        _read_records(source)
    except ValueError as exc:
        assert "event_f1" in str(exc)
    else:
        raise AssertionError("缺少关键字段的实验 CSV 未被拒绝")


def test_effectiveness_table_uses_mad_as_explicit_baseline() -> None:
    """竞赛成效表应计算相对 MAD 的变化，而不是写死某个提升比例。"""

    rows = [
        {
            "detector": "mad",
            "detector_name": "稳健 MAD",
            "scenario": "valve1",
            "file_count": 2,
            "event_f1": 0.40,
            "event_recall": 0.80,
            "point_f1": 0.20,
            "pr_auc": 0.50,
            "false_positive_events": 2.0,
            "inference_seconds": 0.2,
        },
        {
            "detector": "hybrid",
            "detector_name": "多模型融合",
            "scenario": "valve1",
            "file_count": 2,
            "event_f1": 0.55,
            "event_recall": 0.90,
            "point_f1": 0.30,
            "pr_auc": 0.60,
            "false_positive_events": 1.0,
            "inference_seconds": 0.4,
        },
    ]

    effectiveness = _build_effectiveness_rows(rows)
    hybrid = next(item for item in effectiveness if item["detector"] == "hybrid")

    assert hybrid["event_f1_delta_vs_mad"] == 0.15


def test_competition_report_mentions_dynamic_main_model_metrics(tmp_path: Path) -> None:
    """汇总报告应从实验记录读取主模型指标，不能依赖硬编码数值。"""

    rows = [
        {
            "detector": "mad",
            "detector_name": "稳健 MAD",
            "scenario": "valve1",
            "file_count": 1,
            "point_f1": 0.2,
            "pr_auc": 0.4,
            "event_f1": 0.5,
            "event_recall": 0.8,
            "false_positive_events": 1.0,
            "inference_seconds": 0.1,
        },
        {
            "detector": "time_frequency_relation",
            "detector_name": "时频关系多路径检测器",
            "scenario": "valve1",
            "file_count": 1,
            "point_f1": 0.3,
            "pr_auc": 0.5,
            "event_f1": 0.4,
            "event_recall": 0.73,
            "false_positive_events": 2.0,
            "inference_seconds": 0.2,
        },
    ]
    report = _build_report(
        tmp_path,
        type("Split", (), {"healthy_files": [], "validation_files": [], "test_files": []})(),
        [],
        rows,
        tmp_path / "benchmark.md",
        tmp_path / "split.csv",
        _build_effectiveness_rows(rows),
        tmp_path / "protocol.json",
        tmp_path / "protocol.md",
    )

    assert "事件召回为 0.7300" in report
    assert "0.9412" not in report


def test_competition_report_rejects_stale_protocol(tmp_path: Path) -> None:
    """流水线版本变化后不得继续复用旧实验数字。"""

    protocol = tmp_path / "SKAB_EXPERIMENT_PROTOCOL.json"
    protocol.write_text('{"protocol_version":"old"}', encoding="utf-8")
    assert not _has_current_protocol(tmp_path)

    protocol.write_text(
        f'{{"protocol_version":"{PROTOCOL_VERSION}"}}',
        encoding="utf-8",
    )
    assert _has_current_protocol(tmp_path)
