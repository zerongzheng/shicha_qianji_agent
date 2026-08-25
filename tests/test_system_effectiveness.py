"""系统工程成效统计的回归测试。"""

from __future__ import annotations

from pathlib import Path

from app.experiments.system_effectiveness import (
    _aggregate_rows,
    _build_report,
    _coverage,
)


def test_coverage_is_defined_for_empty_event_set() -> None:
    """没有异常事件时覆盖率应为 100%，因为不存在待覆盖事件。"""

    assert _coverage(0, 0) == 1.0
    assert _coverage(2, 4) == 0.5


def test_aggregate_rows_uses_event_weighted_coverage() -> None:
    """汇总覆盖率按事件数量计算，不能简单平均文件覆盖率。"""

    rows = [
        {
            "row_count": 100,
            "event_count": 1,
            "evidence_event_count": 1,
            "diagnosis_event_count": 1,
            "work_order_event_count": 1,
            "inference_seconds": 0.2,
        },
        {
            "row_count": 200,
            "event_count": 3,
            "evidence_event_count": 2,
            "diagnosis_event_count": 1,
            "work_order_event_count": 0,
            "inference_seconds": 0.4,
        },
    ]

    aggregate = _aggregate_rows(rows)

    assert aggregate["total_rows"] == 300
    assert aggregate["total_events"] == 4
    assert aggregate["evidence_event_count"] == 3
    assert aggregate["evidence_coverage"] == 0.75
    assert aggregate["diagnosis_coverage"] == 0.5
    assert aggregate["work_order_coverage"] == 0.25
    assert aggregate["average_inference_seconds"] == 0.3


def test_effectiveness_report_explains_boundary(tmp_path: Path) -> None:
    """报告必须写清公开数据和覆盖率的含义，防止被误读成企业收益。"""

    report = _build_report(
        tmp_path / "SKAB" / "data",
        "time_frequency_relation",
        4.5,
        2,
        2,
        {},
        {
            "total_rows": 300,
            "total_events": 4,
            "evidence_event_count": 3,
            "diagnosis_event_count": 2,
            "work_order_event_count": 1,
            "evidence_coverage": 0.75,
            "diagnosis_coverage": 0.5,
            "work_order_coverage": 0.25,
            "average_inference_seconds": 0.3,
        },
    )

    assert "不代表企业现场收益" in report
    assert "75.00%" in report
    assert "工单草案覆盖率表示系统能够形成可执行任务" in report
