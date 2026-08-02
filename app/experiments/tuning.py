"""无数据泄漏的阈值调优与独立测试。

检测器只在验证文件上选择告警阈值。阈值确定后立即冻结，并在从未参与调参的测试文件上
运行最终评估。所有候选结果、数据划分和最终测试结果都会保存，便于竞赛答辩复现。
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

from app.analysis.detection import apply_detection_threshold
from app.analysis.evaluation import evaluate_predictions
from app.analysis.pipeline import analyze_file
from app.config import get_settings
from app.experiments.benchmark import (
    DEFAULT_DETECTORS,
    BenchmarkResult,
    run_skab_benchmark,
)
from app.experiments.split import ExperimentSplit, build_skab_split, describe_split
from app.models import AnalysisConfig

DEFAULT_THRESHOLDS = (2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0, 9.0, 10.0)
MIN_VALIDATION_EVENT_RECALL = 0.50


@dataclass(frozen=True)
class ThresholdTrial:
    """一个检测器在一个候选阈值上的验证集汇总。"""

    detector: str
    threshold: float
    objective: float
    file_count: int
    point_f1: float
    event_f1: float
    event_recall: float
    average_false_events: float
    healthy_false_event_rate: float
    failed_files: int


@dataclass(frozen=True)
class TuningResult:
    """阈值调优、数据划分和独立测试的完整产物。"""

    split: ExperimentSplit
    trials: tuple[ThresholdTrial, ...]
    selected_thresholds: dict[str, float]
    test_benchmark: BenchmarkResult
    trials_csv_path: Path
    split_csv_path: Path
    report_path: Path


def tune_and_evaluate(
    data_root: str | Path,
    detectors: tuple[str, ...] = DEFAULT_DETECTORS,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
    output_dir: str | Path | None = None,
) -> TuningResult:
    """在验证集选择阈值，并在独立测试集上生成最终基准报告。"""

    if not thresholds or any(threshold <= 0 for threshold in thresholds):
        raise ValueError("候选阈值必须是一组大于 0 的数值。")

    root = Path(data_root).expanduser().resolve()
    split = build_skab_split(root)
    settings = get_settings()
    target_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "experiments"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    trials: list[ThresholdTrial] = []
    selected_thresholds: dict[str, float] = {}
    for detector in detectors:
        detector_trials = _tune_detector(
            detector=detector,
            validation_files=split.validation_files,
            healthy_files=split.healthy_files,
            thresholds=thresholds,
        )
        trials.extend(detector_trials)
        best = select_best_trial(detector_trials)
        selected_thresholds[detector] = best.threshold

    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    trials_csv_path = target_dir / f"threshold_trials_{timestamp}.csv"
    split_csv_path = target_dir / f"data_split_{timestamp}.csv"
    report_path = target_dir / f"tuning_report_{timestamp}.md"
    _write_rows(trials_csv_path, [trial.__dict__ for trial in trials])
    _write_rows(split_csv_path, describe_split(split, root))

    test_benchmark = run_skab_benchmark(
        data_root=root,
        detectors=detectors,
        files=split.test_files,
        thresholds=selected_thresholds,
        output_dir=target_dir,
        report_prefix="independent_test",
    )
    report_path.write_text(
        _build_tuning_report(
            root=root,
            split=split,
            trials=trials,
            selected_thresholds=selected_thresholds,
            test_benchmark=test_benchmark,
        ),
        encoding="utf-8",
    )
    return TuningResult(
        split=split,
        trials=tuple(trials),
        selected_thresholds=selected_thresholds,
        test_benchmark=test_benchmark,
        trials_csv_path=trials_csv_path,
        split_csv_path=split_csv_path,
        report_path=report_path,
    )


def select_best_trial(trials: list[ThresholdTrial]) -> ThresholdTrial:
    """在满足事件召回底线的候选中选择综合表现最好的阈值。

    工业异常检测不能通过“几乎不告警”换取低误报。优先保留验证集事件召回不低于 50%
    的候选；若某个新模型所有候选都未达到底线，才回退到全部候选并保留真实失败结果。
    """

    if not trials:
        raise ValueError("没有可用于选择阈值的验证结果。")
    eligible = [
        trial for trial in trials if trial.event_recall >= MIN_VALIDATION_EVENT_RECALL
    ]
    candidates = eligible or trials
    return max(
        candidates,
        key=lambda trial: (
            trial.objective,
            trial.event_recall,
            trial.point_f1,
            -trial.average_false_events,
            -trial.threshold,
        ),
    )


def _tune_detector(
    detector: str,
    validation_files: tuple[Path, ...],
    healthy_files: tuple[Path, ...],
    thresholds: tuple[float, ...],
) -> list[ThresholdTrial]:
    """对一个检测器只推理一次，再复用分数完成多个阈值评估。"""

    settings = get_settings()
    base_config = AnalysisConfig(
        detector=detector,
        threshold=settings.anomaly_threshold,
        rolling_window=settings.rolling_window,
        min_event_length=settings.min_event_length,
        merge_gap=settings.merge_gap,
        contamination=settings.contamination,
    )
    cached_results = []
    failed_files = 0
    for file_path in (*validation_files, *healthy_files):
        try:
            cached_results.append(analyze_file(file_path, config=base_config, write_report=False))
        # 调优是批量实验任务，单文件损坏不能终止其余候选参数；失败数量会进入目标惩罚。
        except Exception:  # noqa: BLE001
            failed_files += 1

    trials: list[ThresholdTrial] = []
    for threshold in thresholds:
        validation_metrics = []
        healthy_false_events = 0
        healthy_file_count = 0
        threshold_config = AnalysisConfig(
            detector=detector,
            threshold=threshold,
            rolling_window=base_config.rolling_window,
            min_event_length=base_config.min_event_length,
            merge_gap=base_config.merge_gap,
            contamination=base_config.contamination,
        )
        for result in cached_results:
            predicted, _ = apply_detection_threshold(
                dataframe=result.dataframe,
                sensor_scores=result.anomaly_scores,
                combined_score=result.combined_score,
                config=threshold_config,
            )
            metrics = evaluate_predictions(
                result.dataframe,
                predicted,
                result.combined_score,
                merge_gap=threshold_config.merge_gap,
            )
            if metrics is None:
                continue
            if result.source_path.parent.name.lower() == "anomaly-free":
                healthy_file_count += 1
                healthy_false_events += metrics.false_positive_event_count
            else:
                validation_metrics.append(metrics)

        point_f1 = _average(metric.f1_score for metric in validation_metrics)
        event_f1 = _average(metric.event_f1_score for metric in validation_metrics)
        event_recall = _average(metric.event_recall for metric in validation_metrics)
        false_events = _average(
            float(metric.false_positive_event_count) for metric in validation_metrics
        )
        healthy_false_event_rate = healthy_false_events / max(healthy_file_count, 1)
        objective = _industrial_objective(
            point_f1=point_f1,
            event_f1=event_f1,
            event_recall=event_recall,
            average_false_events=false_events,
            healthy_false_event_rate=healthy_false_event_rate,
            failed_files=failed_files,
        )
        trials.append(
            ThresholdTrial(
                detector=detector,
                threshold=threshold,
                objective=objective,
                file_count=len(validation_metrics),
                point_f1=point_f1,
                event_f1=event_f1,
                event_recall=event_recall,
                average_false_events=false_events,
                healthy_false_event_rate=healthy_false_event_rate,
                failed_files=failed_files,
            )
        )
    return trials


def _industrial_objective(
    point_f1: float,
    event_f1: float,
    event_recall: float,
    average_false_events: float,
    healthy_false_event_rate: float,
    failed_files: int,
) -> float:
    """计算面向工业告警的验证目标。

    事件 F1 与事件召回合计占 70%，体现“故障能发现且告警不碎片化”；点级 F1 占 30%。
    每个验证文件的误报事件及健康文件误报会受到温和惩罚，避免靠大量告警换取高召回。
    """

    quality = 0.45 * event_f1 + 0.25 * event_recall + 0.30 * point_f1
    penalty = (
        0.015 * min(average_false_events, 20.0)
        + 0.04 * min(healthy_false_event_rate, 10.0)
        + 0.10 * failed_files
    )
    return quality - penalty


def _build_tuning_report(
    root: Path,
    split: ExperimentSplit,
    trials: list[ThresholdTrial],
    selected_thresholds: dict[str, float],
    test_benchmark: BenchmarkResult,
) -> str:
    """生成竞赛实验方法和参数选择报告。"""

    lines = [
        "# 时察千机阈值调优与独立测试报告",
        "",
        f"> 数据目录：`{root}`",
        (
            f"> 健康基线 {len(split.healthy_files)} 个；验证集 {len(split.validation_files)} 个；"
            f"独立测试集 {len(split.test_files)} 个。"
        ),
        "",
        "## 1. 实验原则",
        "",
        "- 健康文件仅用于无监督标定和健康误报约束，不参与最终模型排名。",
        "- 阈值只使用验证集标签选择，独立测试集在参数冻结后运行。",
        "- 按完整文件划分，避免同一段连续时序同时进入验证集和测试集。",
        "- 目标函数优先事件级检出，并惩罚误报事件和健康数据告警。",
        f"- 参数选择要求验证集事件召回不低于 {MIN_VALIDATION_EVENT_RECALL:.0%}，防止以漏报换低误报。",
        "",
        "## 2. 阈值选择",
        "",
        "| 检测器 | 最佳阈值 | 验证目标 | 点级 F1 | 事件级 F1 | 事件召回 | 平均误报事件 | 健康误报事件 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for detector, threshold in selected_thresholds.items():
        best = next(
            trial
            for trial in trials
            if trial.detector == detector and trial.threshold == threshold
        )
        lines.append(
            f"| {detector} | {threshold:.2f} | {best.objective:.4f} | {best.point_f1:.4f} | "
            f"{best.event_f1:.4f} | {best.event_recall:.4f} | "
            f"{best.average_false_events:.2f} | {best.healthy_false_event_rate:.2f} |"
        )

    lines.extend(
        [
            "",
            "## 3. 独立测试产物",
            "",
            f"- 逐文件结果：`{test_benchmark.csv_path.name}`",
            f"- 测试集排名：`{test_benchmark.report_path.name}`",
            f"- 测试失败任务：{len(test_benchmark.failed_tasks)}",
            "",
            (
                "最终竞赛指标应引用独立测试报告，而不是验证集最佳结果。后续增加新模型时，"
                "继续沿用相同文件划分和评价口径，才能形成可信的横向对比与消融实验。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    """把结构化实验记录写成带 BOM 的 CSV，便于 Excel 直接打开。"""

    if not rows:
        raise ValueError("没有可写入的实验记录。")
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _average(values: Iterable[float]) -> float:
    """计算迭代数据的平均值，空集合返回 0。"""

    materialized = list(values)
    return mean(materialized) if materialized else 0.0
