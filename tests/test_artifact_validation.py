"""实验产物一致性校验的回归测试。"""

from __future__ import annotations

import json
from pathlib import Path

from app.experiments.artifact_validation import (
    validate_independent_test_artifacts,
    write_run_manifest,
)


def _protocol() -> dict[str, object]:
    return {
        "detectors": ["mad"],
        "files": [
            {
                "split": "test",
                "scenario": "valve1",
                "relative_path": "valve1/1.csv",
            }
        ],
        "frozen_thresholds": {"mad": 5.5},
        "frozen_event_policies": {"mad": {"min_event_length": 3, "merge_gap": 5}},
    }


def _record(*, threshold: str = "5.5") -> dict[str, str]:
    return {
        "detector": "mad",
        "scenario": "valve1",
        "file_name": "1.csv",
        "threshold": threshold,
        "min_event_length": "3",
        "merge_gap": "5",
    }


def test_artifact_validation_accepts_matching_protocol(tmp_path: Path) -> None:
    """同一测试文件和冻结参数应通过校验并可生成运行清单。"""

    benchmark_csv = tmp_path / "independent_test.csv"
    benchmark_csv.write_text("detector\nmad\n", encoding="utf-8")
    validation = validate_independent_test_artifacts([_record()], _protocol(), benchmark_csv)

    assert validation.passed
    protocol_json = tmp_path / "protocol.json"
    protocol_json.write_text("{}", encoding="utf-8")
    benchmark_report = tmp_path / "independent_test.md"
    benchmark_report.write_text("report", encoding="utf-8")
    split_csv = tmp_path / "split.csv"
    split_csv.write_text("split", encoding="utf-8")
    manifest_path = write_run_manifest(
        validation,
        tmp_path,
        benchmark_csv=benchmark_csv,
        benchmark_report=benchmark_report,
        split_csv=split_csv,
        protocol_json=protocol_json,
    )

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["validation"]["passed"] is True
    assert payload["artifacts"]["benchmark_csv"]["sha256"] == validation.benchmark_sha256


def test_artifact_validation_rejects_stale_threshold(tmp_path: Path) -> None:
    """旧阈值明细不能被当前协议重新包装成竞赛结论。"""

    benchmark_csv = tmp_path / "independent_test.csv"
    benchmark_csv.write_text("detector\nmad\n", encoding="utf-8")
    validation = validate_independent_test_artifacts(
        [_record(threshold="4.5")],
        _protocol(),
        benchmark_csv,
    )

    assert not validation.passed
    assert any("threshold" in error for error in validation.errors)
