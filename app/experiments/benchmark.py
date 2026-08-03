"""SKAB 全场景模型基准测试。

该模块把“算法能运行”升级为“算法可以公平比较”。每个检测器在同一批文件上运行，
最终输出逐文件指标、分场景汇总和模型排名，直接服务于竞赛对比实验和消融实验。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from zoneinfo import ZoneInfo

from app.analysis.detection import DETECTOR_RECOMMENDED_THRESHOLDS
from app.analysis.pipeline import analyze_file
from app.config import get_settings
from app.models import (
    TFR_RECOMMENDED_FREQUENCY_WEIGHT,
    TFR_RECOMMENDED_RELATION_WEIGHT,
    TFR_RECOMMENDED_TIME_WEIGHT,
    AnalysisConfig,
    AnalysisResult,
)

DEFAULT_DETECTORS = (
    "mad",
    "isolation_forest",
    "pca_reconstruction",
    "window_autoencoder",
    "time_frequency_relation",
    "hybrid",
)


@dataclass(frozen=True)
class BenchmarkRecord:
    """一个检测器在一个 SKAB 文件上的评估记录。"""

    detector: str
    detector_name: str
    threshold: float
    scenario: str
    file_name: str
    row_count: int
    anomaly_ratio: float
    point_precision: float
    point_recall: float
    point_f1: float
    pr_auc: float
    event_precision: float
    event_recall: float
    event_f1: float
    detection_delay: float | None
    false_positive_events: int
    changepoint_false_event_rate: float
    inference_seconds: float


@dataclass(frozen=True)
class BenchmarkResult:
    """一次完整基准实验的输出路径和记录。"""

    records: list[BenchmarkRecord]
    failed_tasks: dict[str, str]
    csv_path: Path
    report_path: Path


def run_skab_benchmark(
    data_root: str | Path,
    detectors: tuple[str, ...] = DEFAULT_DETECTORS,
    max_files: int | None = None,
    output_dir: str | Path | None = None,
    files: tuple[Path, ...] | None = None,
    thresholds: dict[str, float] | None = None,
    config_overrides: dict[str, dict[str, float]] | None = None,
    report_prefix: str = "skab_benchmark",
) -> BenchmarkResult:
    """递归运行 SKAB 全场景对比实验。"""

    root = Path(data_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"找不到 SKAB 数据目录：{root}")

    selected_files = list(files) if files is not None else sorted(
        root.rglob("*.csv"),
        key=lambda path: (path.parent.name, _natural_key(path)),
    )
    if max_files is not None and max_files > 0:
        selected_files = selected_files[:max_files]
    if not selected_files:
        raise FileNotFoundError(f"目录中没有 CSV：{root}")

    settings = get_settings()
    target_dir = Path(output_dir).resolve() if output_dir else settings.output_dir / "benchmarks"
    target_dir.mkdir(parents=True, exist_ok=True)

    records: list[BenchmarkRecord] = []
    failed_tasks: dict[str, str] = {}
    for detector in detectors:
        overrides = (config_overrides or {}).get(detector, {})
        config = AnalysisConfig(
            detector=detector,
            threshold=(thresholds or {}).get(
                detector,
                DETECTOR_RECOMMENDED_THRESHOLDS.get(detector, settings.anomaly_threshold),
            ),
            rolling_window=settings.rolling_window,
            min_event_length=settings.min_event_length,
            merge_gap=settings.merge_gap,
            contamination=settings.contamination,
            hybrid_mad_weight=float(overrides.get("hybrid_mad_weight", 0.50)),
            hybrid_forest_weight=float(overrides.get("hybrid_forest_weight", 0.30)),
            hybrid_pca_weight=float(overrides.get("hybrid_pca_weight", 0.20)),
            autoencoder_window=int(overrides.get("autoencoder_window", 16)),
            autoencoder_hidden=int(overrides.get("autoencoder_hidden", 24)),
            autoencoder_bottleneck=int(overrides.get("autoencoder_bottleneck", 6)),
            autoencoder_max_iter=int(overrides.get("autoencoder_max_iter", 250)),
            autoencoder_max_training_windows=int(
                overrides.get("autoencoder_max_training_windows", 3000)
            ),
            tfr_time_weight=float(
                overrides.get("tfr_time_weight", TFR_RECOMMENDED_TIME_WEIGHT)
            ),
            tfr_frequency_weight=float(
                overrides.get("tfr_frequency_weight", TFR_RECOMMENDED_FREQUENCY_WEIGHT)
            ),
            tfr_relation_weight=float(
                overrides.get("tfr_relation_weight", TFR_RECOMMENDED_RELATION_WEIGHT)
            ),
            tfr_frequency_components=int(overrides.get("tfr_frequency_components", 8)),
            tfr_relation_components=int(overrides.get("tfr_relation_components", 4)),
        )
        for file_path in selected_files:
            task_name = f"{detector}:{file_path.parent.name}/{file_path.name}"
            try:
                started_at = perf_counter()
                result = analyze_file(
                    file_path,
                    config=config,
                    write_report=False,
                    run_forecast=False,
                    run_regime=False,
                )
                inference_seconds = perf_counter() - started_at
                records.append(
                    to_benchmark_record(
                        detector,
                        file_path.parent.name,
                        config.threshold,
                        result,
                        inference_seconds,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                failed_tasks[task_name] = str(exc)

    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    csv_path = target_dir / f"{report_prefix}_{timestamp}.csv"
    report_path = target_dir / f"{report_prefix}_{timestamp}.md"
    _write_csv(records, csv_path)
    report_path.write_text(
        build_benchmark_report(records, failed_tasks, root),
        encoding="utf-8",
    )
    return BenchmarkResult(records, failed_tasks, csv_path, report_path)


def build_benchmark_report(
    records: list[BenchmarkRecord],
    failed_tasks: dict[str, str],
    data_root: Path,
) -> str:
    """生成带模型排名和场景拆分的 Markdown 实验报告。"""

    detector_groups = _group_records(records, "detector")
    ranking_rows = []
    for detector_records in detector_groups.values():
        ranking_rows.append(
            {
                "detector": detector_records[0].detector_name,
                "threshold": detector_records[0].threshold,
                "files": len(detector_records),
                "point_f1": _average(detector_records, "point_f1"),
                "pr_auc": _average(detector_records, "pr_auc"),
                "event_f1": _average(detector_records, "event_f1"),
                "delay": _average(detector_records, "detection_delay"),
                "false_events": _average(detector_records, "false_positive_events"),
                "changepoint_rate": _average(
                    detector_records,
                    "changepoint_false_event_rate",
                ),
                "inference_seconds": _average(detector_records, "inference_seconds"),
            }
        )
    ranking_rows.sort(
        key=lambda row: (row["event_f1"], row["pr_auc"], row["point_f1"]),
        reverse=True,
    )

    lines = [
        "# 时察千机 SKAB 模型基准报告",
        "",
        f"> 数据目录：`{data_root}`",
        f"> 成功任务数：{len(records)}；失败任务数：{len(failed_tasks)}",
        "",
        "## 1. 模型总排名",
        "",
        "| 排名 | 检测器 | 阈值 | 文件数 | 点级 F1 | PR-AUC | 事件级 F1 | 平均延迟 | 平均误报事件 | 变点误报占比 | 单文件耗时/秒 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, row in enumerate(ranking_rows, start=1):
        lines.append(
            f"| {rank} | {row['detector']} | {row['threshold']:.2f} | {row['files']} | "
            f"{row['point_f1']:.4f} | "
            f"{row['pr_auc']:.4f} | {row['event_f1']:.4f} | {row['delay']:.2f} | "
            f"{row['false_events']:.2f} | "
            f"{row['changepoint_rate']:.2%} | {row['inference_seconds']:.4f} |"
        )

    lines.extend(["", "## 2. 分场景结果", ""])
    scenario_groups = _group_records(records, "scenario")
    for scenario, scenario_records in sorted(scenario_groups.items()):
        lines.extend(
            [
                f"### {scenario}",
                "",
                "| 检测器 | 阈值 | 文件数 | 点级 F1 | PR-AUC | 事件级 F1 | 平均延迟 | 平均误报事件 |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for detector_records in _group_records(scenario_records, "detector").values():
            lines.append(
                f"| {detector_records[0].detector_name} | "
                f"{detector_records[0].threshold:.2f} | {len(detector_records)} | "
                f"{_average(detector_records, 'point_f1'):.4f} | "
                f"{_average(detector_records, 'pr_auc'):.4f} | "
                f"{_average(detector_records, 'event_f1'):.4f} | "
                f"{_average(detector_records, 'detection_delay'):.2f} | "
                f"{_average(detector_records, 'false_positive_events'):.2f} |"
            )
        lines.append("")

    lines.extend(
        [
            "## 3. 指标说明",
            "",
            "- 点级 F1：评价每一个采样点的分类结果。",
            "- PR-AUC：在类别不平衡条件下评价异常排序能力。",
            "- 事件级 F1：评价一次完整故障是否被告警，以及告警事件是否误报。",
            "- 检测延迟：从真实异常开始到首次有效告警之间的采样点数。",
            "- 变点误报占比：误报事件中与 changepoint 标签邻近的比例，用于衡量工况切换干扰。",
            "",
            (
                "本报告用于比较可解释基线和后续自研模型。模型结论应结合逐场景结果、"
                "误报数量和检测延迟综合判断，不能只选择单一文件或单一指标。"
            ),
            "",
        ]
    )

    if failed_tasks:
        lines.extend(["## 4. 失败任务", ""])
        for task, reason in failed_tasks.items():
            lines.append(f"- `{task}`：{reason}")
        lines.append("")
    return "\n".join(lines)


def to_benchmark_record(
    detector: str,
    scenario: str,
    threshold: float,
    result: AnalysisResult,
    inference_seconds: float,
) -> BenchmarkRecord:
    """把单文件分析结果压缩为实验记录。"""

    metrics = result.metrics
    if metrics is None:
        raise ValueError("基准测试文件缺少 anomaly 标签。")
    anomaly_ratio = float(result.dataframe["anomaly"].fillna(0).mean())
    return BenchmarkRecord(
        detector=detector,
        detector_name=result.detector_name,
        threshold=threshold,
        scenario=scenario,
        file_name=result.profile.source_name,
        row_count=result.profile.row_count,
        anomaly_ratio=anomaly_ratio,
        point_precision=metrics.precision,
        point_recall=metrics.recall,
        point_f1=metrics.f1_score,
        pr_auc=metrics.pr_auc,
        event_precision=metrics.event_precision,
        event_recall=metrics.event_recall,
        event_f1=metrics.event_f1_score,
        detection_delay=metrics.mean_detection_delay,
        false_positive_events=metrics.false_positive_event_count,
        changepoint_false_event_rate=metrics.changepoint_false_event_rate,
        inference_seconds=inference_seconds,
    )


def _write_csv(records: list[BenchmarkRecord], target_path: Path) -> None:
    """输出可供 Excel、Pandas 和绘图脚本继续处理的逐文件结果。"""

    fieldnames = list(BenchmarkRecord.__dataclass_fields__)
    with target_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(record.__dict__)


def _group_records(
    records: list[BenchmarkRecord],
    attribute: str,
) -> dict[str, list[BenchmarkRecord]]:
    """按字段分组，避免依赖额外数据分析框架。"""

    groups: dict[str, list[BenchmarkRecord]] = {}
    for record in records:
        key = str(getattr(record, attribute))
        groups.setdefault(key, []).append(record)
    return groups


def _average(records: list[BenchmarkRecord], attribute: str) -> float:
    """计算字段平均值，忽略空检测延迟。"""

    values = [float(value) for record in records if (value := getattr(record, attribute)) is not None]
    return mean(values) if values else 0.0


def _natural_key(path: Path) -> tuple[int, str]:
    """让数字文件名按自然顺序排列。"""

    return (int(path.stem), path.name) if path.stem.isdigit() else (10**9, path.name)
