"""校赛实验汇总模块的回归测试。"""

from __future__ import annotations

from pathlib import Path

from app.experiments.competition_report import _read_records, _summarize, _summarize_by_model


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
