"""校验 SKAB 实验产物与协议的一致性，并生成可追溯运行清单。

竞赛报告不能只引用某一份 Markdown。独立测试的逐文件 CSV、冻结参数和数据划分
必须属于同一次协议，否则可能把旧阈值、旧事件策略或不同文件划分混在一起。
本模块不运行模型，只负责在生成材料前阻止这种不一致。
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.experiments.protocol import PROTOCOL_VERSION


@dataclass(frozen=True)
class ArtifactValidation:
    """一次独立测试产物校验的结果。"""

    passed: bool
    record_count: int
    detector_count: int
    expected_test_file_count: int
    benchmark_sha256: str
    errors: tuple[str, ...]


def validate_independent_test_artifacts(
    records: list[dict[str, str]],
    protocol: dict[str, Any],
    benchmark_csv: str | Path,
) -> ArtifactValidation:
    """验证独立测试明细与当前实验协议是否一一对应。"""

    source = Path(benchmark_csv).expanduser().resolve()
    errors: list[str] = []
    if not source.is_file():
        return ArtifactValidation(False, 0, 0, 0, "", (f"实验明细不存在：{source}",))

    expected_files = {
        (str(item["scenario"]), Path(str(item["relative_path"])).name)
        for item in protocol.get("files", [])
        if item.get("split") == "test"
    }
    if not expected_files:
        errors.append("实验协议未包含独立测试文件清单。")

    grouped: dict[str, list[dict[str, str]]] = {}
    for record in records:
        detector = record.get("detector", "")
        if not detector:
            errors.append("实验明细存在缺少 detector 的记录。")
            continue
        grouped.setdefault(detector, []).append(record)

    frozen_thresholds = protocol.get("frozen_thresholds", {})
    frozen_policies = protocol.get("frozen_event_policies", {})
    expected_detectors = set(protocol.get("detectors", []))
    if expected_detectors != set(grouped):
        errors.append(
            "检测器集合不一致：协议为 "
            f"{sorted(expected_detectors)}，明细为 {sorted(grouped)}。"
        )

    for detector, detector_records in grouped.items():
        file_pairs = {(row.get("scenario", ""), row.get("file_name", "")) for row in detector_records}
        if file_pairs != expected_files:
            missing = sorted(expected_files - file_pairs)
            unexpected = sorted(file_pairs - expected_files)
            errors.append(
                f"{detector} 的独立测试文件覆盖不一致；缺少 {missing}，额外 {unexpected}。"
            )
        if len(file_pairs) != len(detector_records):
            errors.append(f"{detector} 的逐文件测试记录存在重复。")

        _validate_single_float(
            detector,
            detector_records,
            "threshold",
            frozen_thresholds.get(detector),
            errors,
        )
        policy = frozen_policies.get(detector, {})
        _validate_single_int(
            detector,
            detector_records,
            "min_event_length",
            policy.get("min_event_length"),
            errors,
        )
        _validate_single_int(
            detector,
            detector_records,
            "merge_gap",
            policy.get("merge_gap"),
            errors,
        )

    return ArtifactValidation(
        passed=not errors,
        record_count=len(records),
        detector_count=len(grouped),
        expected_test_file_count=len(expected_files),
        benchmark_sha256=_sha256(source),
        errors=tuple(errors),
    )


def write_run_manifest(
    validation: ArtifactValidation,
    output_dir: str | Path,
    *,
    benchmark_csv: str | Path,
    benchmark_report: str | Path,
    split_csv: str | Path,
    protocol_json: str | Path,
) -> Path:
    """写入本次竞赛材料的机器可读运行清单。"""

    if not validation.passed:
        raise ValueError("不能为未通过校验的实验产物生成运行清单。")

    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "manifest_version": "skab-run-manifest-v1",
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "validation": asdict(validation),
        "artifacts": {
            "benchmark_csv": _describe_file(benchmark_csv),
            "benchmark_report": _describe_file(benchmark_report),
            "split_csv": _describe_file(split_csv),
            "protocol_json": _describe_file(protocol_json),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "disclosure": "运行耗时仅用于同一环境下相对比较；当前数据为公开 SKAB 验证数据。",
    }
    path = target / "SKAB_EXPERIMENT_RUN_MANIFEST.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _validate_single_float(
    detector: str,
    records: list[dict[str, str]],
    field: str,
    expected: object,
    errors: list[str],
) -> None:
    """检查浮点参数在明细中唯一且与协议冻结值相同。"""

    try:
        actual = {round(float(item[field]), 10) for item in records}
        target = round(float(expected), 10)
    except (KeyError, TypeError, ValueError):
        errors.append(f"{detector} 缺少或无法解析 {field}。")
        return
    if actual != {target}:
        errors.append(f"{detector} 的 {field} 为 {sorted(actual)}，协议冻结值为 {target}。")


def _validate_single_int(
    detector: str,
    records: list[dict[str, str]],
    field: str,
    expected: object,
    errors: list[str],
) -> None:
    """检查事件策略参数在明细中唯一且与协议冻结值相同。"""

    try:
        actual = {int(float(item[field])) for item in records}
        target = int(expected)
    except (KeyError, TypeError, ValueError):
        errors.append(f"{detector} 缺少或无法解析 {field}。")
        return
    if actual != {target}:
        errors.append(f"{detector} 的 {field} 为 {sorted(actual)}，协议冻结值为 {target}。")


def _describe_file(path: str | Path) -> dict[str, str | int]:
    """记录文件路径、大小和哈希，方便答辩时核验输入是否发生变化。"""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"实验产物不存在：{source}")
    return {
        "path": str(source),
        "size_bytes": source.stat().st_size,
        "sha256": _sha256(source),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
