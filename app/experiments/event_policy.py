"""只使用验证集选择异常事件后处理参数。

异常检测模型先输出逐点风险分数，随后还需要把零散告警点整理成运维人员能够处理的
连续事件。最短持续时间过小会保留噪声，合并间隔过小会把同一故障切成多张工单；但参数
过大又可能漏掉短时故障。本模块固定模型和阈值，只在验证集选择这两个事件参数，再把
冻结策略与当前基线一起放到独立测试集比较，避免使用测试答案调参。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

from app.analysis.detection import DETECTOR_RECOMMENDED_THRESHOLDS, apply_detection_threshold
from app.analysis.evaluation import evaluate_predictions, extract_binary_events
from app.analysis.pipeline import analyze_file
from app.config import get_settings
from app.experiments.split import ExperimentSplit, build_skab_split
from app.models import AnalysisConfig, AnalysisResult

DEFAULT_MIN_EVENT_LENGTHS = (3, 5, 8, 12)
DEFAULT_MERGE_GAPS = (5, 10, 20, 30)
BASELINE_MIN_EVENT_LENGTH = 3
BASELINE_MERGE_GAP = 5


@dataclass(frozen=True)
class EventPolicyTrial:
    """一种事件后处理策略在一组文件上的汇总指标。"""

    split: str
    min_event_length: int
    merge_gap: int
    file_count: int
    point_f1: float
    event_f1: float
    event_recall: float
    average_false_events: float
    healthy_false_events: float


@dataclass(frozen=True)
class EventPolicyEvaluation:
    """事件策略验证、冻结和独立测试的完整产物。"""

    split: ExperimentSplit
    baseline_validation: EventPolicyTrial
    selected_validation: EventPolicyTrial
    baseline_test: EventPolicyTrial
    selected_test: EventPolicyTrial
    recommended: bool
    trials: tuple[EventPolicyTrial, ...]
    csv_path: Path
    report_path: Path


def evaluate_event_policy(
    data_root: str | Path,
    output_dir: str | Path | None = None,
    *,
    detector: str = "time_frequency_relation",
    threshold: float | None = None,
    min_event_lengths: tuple[int, ...] = DEFAULT_MIN_EVENT_LENGTHS,
    merge_gaps: tuple[int, ...] = DEFAULT_MERGE_GAPS,
) -> EventPolicyEvaluation:
    """调优事件后处理策略，并在固定独立测试集进行一次最终比较。"""

    if not min_event_lengths or any(value < 1 for value in min_event_lengths):
        raise ValueError("最短事件长度候选必须是大于等于 1 的整数。")
    if not merge_gaps or any(value < 0 for value in merge_gaps):
        raise ValueError("事件合并间隔候选必须是大于等于 0 的整数。")

    settings = get_settings()
    root = Path(data_root).expanduser().resolve()
    split = build_skab_split(root)
    target = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "experiments"
    )
    target.mkdir(parents=True, exist_ok=True)
    frozen_threshold = float(
        threshold
        if threshold is not None
        else DETECTOR_RECOMMENDED_THRESHOLDS[detector]
    )

    # 每份文件只运行一次检测器。候选策略复用同一风险分数，保证比较公平并缩短实验时间。
    validation_results = _analyze_files(
        (*split.validation_files, *split.healthy_files),
        detector,
        frozen_threshold,
    )
    trials = tuple(
        _evaluate_results(
            validation_results,
            split_name="validation",
            detector=detector,
            threshold=frozen_threshold,
            min_event_length=min_event_length,
            merge_gap=merge_gap,
        )
        for min_event_length in sorted(set(min_event_lengths))
        for merge_gap in sorted(set(merge_gaps))
    )
    baseline_validation = _find_trial(
        trials,
        BASELINE_MIN_EVENT_LENGTH,
        BASELINE_MERGE_GAP,
    )
    selected_validation = select_event_policy(trials, baseline_validation)

    # 参数到这里已经冻结。测试集只比较当前基线和验证集选出的策略，不再参与选择。
    test_results = _analyze_files(split.test_files, detector, frozen_threshold)
    baseline_test = _evaluate_results(
        test_results,
        split_name="independent_test",
        detector=detector,
        threshold=frozen_threshold,
        min_event_length=BASELINE_MIN_EVENT_LENGTH,
        merge_gap=BASELINE_MERGE_GAP,
    )
    selected_test = _evaluate_results(
        test_results,
        split_name="independent_test",
        detector=detector,
        threshold=frozen_threshold,
        min_event_length=selected_validation.min_event_length,
        merge_gap=selected_validation.merge_gap,
    )
    recommended = _is_recommended(baseline_test, selected_test)

    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    csv_path = target / f"event_policy_trials_{timestamp}.csv"
    report_path = target / f"event_policy_evaluation_{timestamp}.md"
    _write_csv(csv_path, [*trials, baseline_test, selected_test])
    report_path.write_text(
        _build_report(
            root,
            detector,
            frozen_threshold,
            baseline_validation,
            selected_validation,
            baseline_test,
            selected_test,
            recommended,
        ),
        encoding="utf-8",
    )
    return EventPolicyEvaluation(
        split=split,
        baseline_validation=baseline_validation,
        selected_validation=selected_validation,
        baseline_test=baseline_test,
        selected_test=selected_test,
        recommended=recommended,
        trials=trials,
        csv_path=csv_path,
        report_path=report_path,
    )


def select_event_policy(
    trials: tuple[EventPolicyTrial, ...],
    baseline: EventPolicyTrial,
) -> EventPolicyTrial:
    """在召回不低于基线的候选中优先提高事件 F1 并减少误报。"""

    if not trials:
        raise ValueError("没有可用于选择事件策略的验证结果。")
    eligible = [
        trial
        for trial in trials
        if trial.event_recall + 1e-9 >= baseline.event_recall
    ]
    candidates = eligible or list(trials)
    return max(
        candidates,
        key=lambda trial: (
            trial.event_f1,
            -trial.average_false_events,
            trial.point_f1,
            -trial.min_event_length,
            -trial.merge_gap,
        ),
    )


def _analyze_files(
    files: tuple[Path, ...],
    detector: str,
    threshold: float,
) -> tuple[AnalysisResult, ...]:
    """使用宽松基准事件参数缓存风险分数，标签不参与模型推理。"""

    settings = get_settings()
    config = AnalysisConfig(
        detector=detector,
        threshold=threshold,
        rolling_window=settings.rolling_window,
        min_event_length=BASELINE_MIN_EVENT_LENGTH,
        merge_gap=BASELINE_MERGE_GAP,
        contamination=settings.contamination,
    )
    return tuple(
        analyze_file(
            path,
            config=config,
            write_report=False,
            run_forecast=False,
            run_regime=False,
        )
        for path in files
    )


def _evaluate_results(
    results: tuple[AnalysisResult, ...],
    *,
    split_name: str,
    detector: str,
    threshold: float,
    min_event_length: int,
    merge_gap: int,
) -> EventPolicyTrial:
    """对缓存分数应用一种事件策略，并汇总带标签文件与健康文件。"""

    metrics = []
    healthy_false_events = 0
    healthy_file_count = 0
    for result in results:
        config = AnalysisConfig(
            detector=detector,
            threshold=threshold,
            min_event_length=min_event_length,
            merge_gap=merge_gap,
        )
        predicted, _ = apply_detection_threshold(
            result.dataframe,
            result.anomaly_scores,
            result.combined_score,
            config,
        )
        evaluation = evaluate_predictions(
            result.dataframe,
            predicted,
            result.combined_score,
            merge_gap=merge_gap,
        )
        if evaluation is None:
            healthy_file_count += 1
            healthy_false_events += len(extract_binary_events(predicted, merge_gap=merge_gap))
        elif result.source_path.parent.name.casefold() == "anomaly-free":
            healthy_file_count += 1
            healthy_false_events += evaluation.false_positive_event_count
        else:
            metrics.append(evaluation)

    return EventPolicyTrial(
        split=split_name,
        min_event_length=min_event_length,
        merge_gap=merge_gap,
        file_count=len(metrics),
        point_f1=_average(item.f1_score for item in metrics),
        event_f1=_average(item.event_f1_score for item in metrics),
        event_recall=_average(item.event_recall for item in metrics),
        average_false_events=_average(
            float(item.false_positive_event_count) for item in metrics
        ),
        healthy_false_events=healthy_false_events / max(healthy_file_count, 1),
    )


def _find_trial(
    trials: tuple[EventPolicyTrial, ...],
    min_event_length: int,
    merge_gap: int,
) -> EventPolicyTrial:
    """读取基线候选；调用方漏传基线参数时给出明确错误。"""

    try:
        return next(
            trial
            for trial in trials
            if trial.min_event_length == min_event_length and trial.merge_gap == merge_gap
        )
    except StopIteration as exc:
        raise ValueError("候选网格必须包含当前基线事件策略 3/5。") from exc


def _is_recommended(baseline: EventPolicyTrial, candidate: EventPolicyTrial) -> bool:
    """独立测试同时保持召回、提高 F1 且减少误报时才建议替换默认值。"""

    return (
        candidate.event_recall + 1e-9 >= baseline.event_recall
        and candidate.event_f1 > baseline.event_f1 + 1e-9
        and candidate.average_false_events < baseline.average_false_events - 1e-9
    )


def _build_report(
    root: Path,
    detector: str,
    threshold: float,
    baseline_validation: EventPolicyTrial,
    selected_validation: EventPolicyTrial,
    baseline_test: EventPolicyTrial,
    selected_test: EventPolicyTrial,
    recommended: bool,
) -> str:
    """生成可直接用于竞赛实验附件的参数选择说明。"""

    decision = (
        "独立测试保持事件召回并同时提高事件级 F1、减少误报，建议将验证集策略设为产品默认。"
        if recommended
        else "独立测试未同时满足召回、事件级 F1 和误报约束，产品继续使用当前基线策略。"
    )
    lines = [
        "# SKAB 异常事件后处理策略评测",
        "",
        f"> 数据目录：`{root}`",
        f"> 检测器：`{detector}`；冻结阈值：`{threshold:.2f}`。",
        "> 参数只由验证集选择；独立测试集不参与调参。",
        "",
        "## 参数比较",
        "",
        "| 集合 | 策略 | 最短事件长度 | 合并间隔 | 点级 F1 | 事件级 F1 | 事件召回 | 平均误报事件 | 健康误报事件 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        _report_row("验证集", "当前基线", baseline_validation),
        _report_row("验证集", "验证集选择", selected_validation),
        _report_row("独立测试集", "当前基线", baseline_test),
        _report_row("独立测试集", "冻结候选", selected_test),
        "",
        "## 决策",
        "",
        decision,
        "",
        "## 结果边界",
        "",
        "- 该实验只优化告警事件整理方式，不改变模型风险分数。",
        "- 参数单位是 SKAB 当前一秒采样网格上的采样点，企业接入后必须按真实采样周期重标定。",
        "- 未同时改善独立测试指标时，不因验证集结果好看而修改产品默认值。",
        "",
    ]
    return "\n".join(lines)


def _report_row(split_name: str, strategy: str, trial: EventPolicyTrial) -> str:
    """把一条汇总指标格式化为 Markdown 表格行。"""

    return (
        f"| {split_name} | {strategy} | {trial.min_event_length} | {trial.merge_gap} | "
        f"{trial.point_f1:.4f} | {trial.event_f1:.4f} | {trial.event_recall:.4f} | "
        f"{trial.average_false_events:.2f} | {trial.healthy_false_events:.2f} |"
    )


def _write_csv(path: Path, trials: list[EventPolicyTrial]) -> None:
    """保存完整候选和独立测试对照，便于答辩复核。"""

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(EventPolicyTrial.__dataclass_fields__))
        writer.writeheader()
        for trial in trials:
            writer.writerow(trial.__dict__)


def _average(values) -> float:
    """计算平均值并统一实验输出精度。"""

    items = list(values)
    return round(mean(items), 6) if items else 0.0
