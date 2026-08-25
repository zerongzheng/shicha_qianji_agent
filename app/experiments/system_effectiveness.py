"""统计 SKAB 独立测试集上的系统工程成效。

算法基准主要回答“检测模型能不能发现异常”，本模块继续回答“发现异常后，系统能不能
形成证据、候选诊断和可执行工单”。所有统计都来自统一分析流水线，不调用大模型，也不
把这些公开数据结果表述成企业现场收益。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from app.analysis.detection import (
    DETECTOR_LABELS,
    DETECTOR_RECOMMENDED_THRESHOLDS,
    recommended_event_policy,
)
from app.analysis.pipeline import analyze_file
from app.config import get_settings
from app.experiments.protocol import read_frozen_thresholds
from app.experiments.split import ExperimentSplit, build_skab_split
from app.models import AnalysisConfig, AnalysisResult


@dataclass(frozen=True)
class SystemEffectiveness:
    """一批独立测试文件的系统能力统计和输出路径。"""

    detector: str
    detector_name: str
    split_name: str
    file_count: int
    analyzed_file_count: int
    failed_files: dict[str, str]
    total_rows: int
    total_events: int
    evidence_event_count: int
    diagnosis_event_count: int
    work_order_event_count: int
    evidence_coverage: float
    diagnosis_coverage: float
    work_order_coverage: float
    average_inference_seconds: float
    csv_path: Path
    report_path: Path


def analyze_skab_system_effectiveness(
    data_root: str | Path,
    output_dir: str | Path | None = None,
    *,
    detector: str = "time_frequency_relation",
    threshold: float | None = None,
    split: ExperimentSplit | None = None,
    files: tuple[Path, ...] | None = None,
) -> SystemEffectiveness:
    """统计固定独立测试文件上的端到端系统能力。

    统计使用独立测试集、固定阈值和统一分析入口，并关闭预测计算与外部大模型调用；
    重点衡量时序检测、证据提取、候选诊断和工单草案的输出完整性。
    """

    settings = get_settings()
    root = Path(data_root).expanduser().resolve()
    target_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "competition"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    experiment_split = split or build_skab_split(root)
    selected_files = list(files) if files is not None else list(experiment_split.test_files)
    if not selected_files:
        raise ValueError("没有可用于系统成效统计的独立测试文件。")
    frozen_thresholds = read_frozen_thresholds(target_dir)
    min_event_length, merge_gap = recommended_event_policy(detector)

    config = AnalysisConfig(
        detector=detector,
        threshold=(
            threshold
            if threshold is not None
            else frozen_thresholds.get(
                detector,
                float(DETECTOR_RECOMMENDED_THRESHOLDS.get(detector, settings.anomaly_threshold)),
            )
        ),
        rolling_window=settings.rolling_window,
        min_event_length=min_event_length,
        merge_gap=merge_gap,
        contamination=settings.contamination,
    )
    rows: list[dict[str, Any]] = []
    failed_files: dict[str, str] = {}
    for file_path in selected_files:
        started = perf_counter()
        try:
            result = analyze_file(
                file_path,
                config=config,
                write_report=False,
                run_forecast=False,
                run_regime=True,
            )
            rows.append(_build_file_row(result, file_path, root, perf_counter() - started))
        except Exception as exc:  # noqa: BLE001
            failed_files[_safe_relative_path(file_path, root)] = str(exc)

    aggregate = _aggregate_rows(rows)
    csv_path = target_dir / f"{detector}_system_effectiveness.csv"
    report_path = target_dir / f"{detector}_system_effectiveness.md"
    _write_rows_csv(csv_path, rows)
    report_path.write_text(
        _build_report(
            root,
            detector,
            config.threshold,
            len(selected_files),
            len(rows),
            failed_files,
            aggregate,
        ),
        encoding="utf-8",
    )
    return SystemEffectiveness(
        detector=detector,
        detector_name=DETECTOR_LABELS.get(detector, detector),
        split_name="independent_test",
        file_count=len(selected_files),
        analyzed_file_count=len(rows),
        failed_files=failed_files,
        total_rows=int(aggregate["total_rows"]),
        total_events=int(aggregate["total_events"]),
        evidence_event_count=int(aggregate["evidence_event_count"]),
        diagnosis_event_count=int(aggregate["diagnosis_event_count"]),
        work_order_event_count=int(aggregate["work_order_event_count"]),
        evidence_coverage=float(aggregate["evidence_coverage"]),
        diagnosis_coverage=float(aggregate["diagnosis_coverage"]),
        work_order_coverage=float(aggregate["work_order_coverage"]),
        average_inference_seconds=float(aggregate["average_inference_seconds"]),
        csv_path=csv_path,
        report_path=report_path,
    )


def _build_file_row(
    result: AnalysisResult,
    file_path: Path,
    root: Path,
    elapsed: float,
) -> dict[str, Any]:
    """把一份分析结果转换为可审计的单文件统计行。"""

    event_count = len(result.events)
    evidence_count = sum(bool(event.dominant_sensors) for event in result.events)
    diagnosis_count = sum(
        bool(item.primary_candidate or item.candidates)
        for item in result.event_diagnoses
    )
    work_order_count = sum(
        bool(item.work_order_id and item.actions)
        for item in result.work_order_drafts
    )
    return {
        "scenario": file_path.parent.name,
        "file_name": result.profile.source_name,
        "relative_path": _safe_relative_path(file_path, root),
        "row_count": result.profile.row_count,
        "sensor_count": len(result.profile.sensor_columns),
        "event_count": event_count,
        "evidence_event_count": evidence_count,
        "diagnosis_event_count": diagnosis_count,
        "work_order_event_count": work_order_count,
        "evidence_coverage": _coverage(evidence_count, event_count),
        "diagnosis_coverage": _coverage(diagnosis_count, event_count),
        "work_order_coverage": _coverage(work_order_count, event_count),
        "inference_seconds": round(elapsed, 6),
    }


def _aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按事件数加权汇总文件级结果，避免短文件和长文件被等权误读。"""

    total_events = sum(int(row["event_count"]) for row in rows)
    evidence_count = sum(int(row["evidence_event_count"]) for row in rows)
    diagnosis_count = sum(int(row["diagnosis_event_count"]) for row in rows)
    work_order_count = sum(int(row["work_order_event_count"]) for row in rows)
    return {
        "total_rows": sum(int(row["row_count"]) for row in rows),
        "total_events": total_events,
        "evidence_event_count": evidence_count,
        "diagnosis_event_count": diagnosis_count,
        "work_order_event_count": work_order_count,
        "evidence_coverage": _coverage(evidence_count, total_events),
        "diagnosis_coverage": _coverage(diagnosis_count, total_events),
        "work_order_coverage": _coverage(work_order_count, total_events),
        "average_inference_seconds": round(
            sum(float(row["inference_seconds"]) for row in rows) / max(len(rows), 1),
            6,
        ),
    }


def _coverage(numerator: int, denominator: int) -> float:
    """计算覆盖率；没有异常事件时返回 1，表示不存在需要覆盖的事件。"""

    return 1.0 if denominator == 0 else round(numerator / denominator, 6)


def _write_rows_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """保存逐文件统计表，使用 BOM 方便 Windows Excel 直接打开。"""

    fields = [
        "scenario",
        "file_name",
        "relative_path",
        "row_count",
        "sensor_count",
        "event_count",
        "evidence_event_count",
        "diagnosis_event_count",
        "work_order_event_count",
        "evidence_coverage",
        "diagnosis_coverage",
        "work_order_coverage",
        "inference_seconds",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _build_report(
    root: Path,
    detector: str,
    threshold: float,
    file_count: int,
    analyzed_count: int,
    failed_files: dict[str, str],
    aggregate: dict[str, Any],
) -> str:
    """生成适合 PPT 和答辩复核的系统成效说明。"""

    lines = [
        "# SKAB 系统工程成效统计",
        "",
        "> 统计对象为 SKAB 独立测试集上的确定性分析流程，不含外部大模型调用，不代表企业现场收益。",
        f"> 检测器：{DETECTOR_LABELS.get(detector, detector)}；阈值：{threshold:.2f}。",
        f"> 数据目录：{root}；文件完成率：{analyzed_count}/{file_count}。",
        "",
        "## 1. 端到端能力覆盖",
        "",
        "| 能力 | 事件数量 | 覆盖率 | 统计定义 |",
        "| --- | ---: | ---: | --- |",
        f"| 异常事件证据 | {aggregate['evidence_event_count']} / {aggregate['total_events']} | {aggregate['evidence_coverage']:.2%} | 事件包含主导传感器证据 |",
        f"| 候选根因诊断 | {aggregate['diagnosis_event_count']} / {aggregate['total_events']} | {aggregate['diagnosis_coverage']:.2%} | 事件形成候选根因或候选列表 |",
        f"| 运维工单草案 | {aggregate['work_order_event_count']} / {aggregate['total_events']} | {aggregate['work_order_coverage']:.2%} | 事件形成结构化工单和处置动作 |",
        "",
        "## 2. 工程运行统计",
        "",
        f"- 独立测试文件：{file_count} 份；成功完成分析：{analyzed_count} 份。",
        f"- 累计处理采样点：{aggregate['total_rows']} 个。",
        f"- 累计异常事件：{aggregate['total_events']} 个。",
        f"- 单文件平均推理耗时：{aggregate['average_inference_seconds']:.4f} 秒。",
        "",
        "## 3. 口径边界",
        "",
        "- 覆盖率只反映当前 SKAB 文件和检测阈值下的系统输出完整性，不代表诊断正确率。",
        "- 候选根因仍需结合设备结构、运行日志和现场复测确认。",
        "- 工单草案覆盖率表示系统能够形成可执行任务，不表示现场已经完成处置。",
        "- 企业数据接入后应使用相同字段重新统计，并增加人工确认准确率、处置时长和复测结果。",
    ]
    if failed_files:
        lines.extend(["", "## 4. 未完成文件", ""])
        lines.extend(f"- {name}：{reason}" for name, reason in failed_files.items())
    return "\n".join(lines) + "\n"


def _safe_relative_path(path: Path, root: Path) -> str:
    """生成稳定相对路径，兼容外部传入的测试文件。"""

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
