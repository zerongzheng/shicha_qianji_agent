"""时频关系多路径检测器的权重消融实验。

实验严格按完整文件划分数据：验证集用于选择路径组合、融合权重和告警阈值，独立测试集
只在配置冻结后运行一次。这样可以证明频域与关系路径是否带来真实增益，而不是只展示一张
看起来复杂的模型结构图。
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.analysis.detection import apply_detection_threshold, recommended_event_policy
from app.analysis.evaluation import evaluate_predictions
from app.analysis.pipeline import analyze_file
from app.config import get_settings
from app.experiments.benchmark import BenchmarkResult, run_skab_benchmark
from app.experiments.protocol import PROTOCOL_VERSION
from app.experiments.split import ExperimentSplit, build_skab_split
from app.experiments.tuning import DEFAULT_THRESHOLDS, ThresholdTrial, select_best_trial
from app.models import AnalysisConfig

DEFAULT_TFR_CANDIDATES = (
    ("time_only", 1.00, 0.00, 0.00),
    ("time_frequency", 0.67, 0.33, 0.00),
    ("time_relation", 0.67, 0.00, 0.33),
    ("full_equal_aux", 0.50, 0.25, 0.25),
    ("full_frequency", 0.50, 0.35, 0.15),
    ("full_relation", 0.50, 0.15, 0.35),
)


@dataclass(frozen=True)
class TfrAblationRecord:
    """一组路径权重在最佳验证阈值上的表现。"""

    candidate_id: str
    time_weight: float
    frequency_weight: float
    relation_weight: float
    threshold: float
    objective: float
    point_f1: float
    event_f1: float
    event_recall: float
    average_false_events: float
    healthy_false_event_rate: float


@dataclass(frozen=True)
class TfrAblationResult:
    """消融候选、胜出配置和独立测试产物。"""

    split: ExperimentSplit
    records: tuple[TfrAblationRecord, ...]
    selected: TfrAblationRecord
    test_benchmark: BenchmarkResult
    csv_path: Path
    report_path: Path


def run_tfr_weight_ablation(
    data_root: str | Path,
    candidates: tuple[tuple[str, float, float, float], ...] = DEFAULT_TFR_CANDIDATES,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
    output_dir: str | Path | None = None,
) -> TfrAblationResult:
    """在验证集选择路径权重和阈值，并与 AutoEncoder 基线做独立测试对比。"""

    root = Path(data_root).expanduser().resolve()
    split = build_skab_split(root)
    settings = get_settings()
    target_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "experiments"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    records = [
        _evaluate_candidate(candidate, thresholds, split)
        for candidate in candidates
    ]
    selected = select_tfr_candidate(records)
    test_benchmark = _run_selected_test(root, split, selected, target_dir)

    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    csv_path = target_dir / f"tfr_weight_ablation_{timestamp}.csv"
    report_path = target_dir / f"tfr_weight_ablation_{timestamp}.md"
    _write_csv(records, csv_path)
    report_path.write_text(
        _build_report(root, split, records, selected, test_benchmark),
        encoding="utf-8",
    )
    return TfrAblationResult(
        split=split,
        records=tuple(records),
        selected=selected,
        test_benchmark=test_benchmark,
        csv_path=csv_path,
        report_path=report_path,
    )


def select_tfr_candidate(records: list[TfrAblationRecord]) -> TfrAblationRecord:
    """优先从验证事件召回不低于 50% 的候选中选择综合表现最佳者。"""

    if not records:
        raise ValueError("时频关系消融至少需要一组候选权重。")
    reliable = [record for record in records if record.event_recall >= 0.50]
    pool = reliable or records
    return max(
        pool,
        key=lambda item: (
            item.objective,
            item.event_recall,
            item.event_f1,
            item.point_f1,
            -item.average_false_events,
        ),
    )


def _evaluate_candidate(
    candidate: tuple[str, float, float, float],
    thresholds: tuple[float, ...],
    split: ExperimentSplit,
) -> TfrAblationRecord:
    """一组权重只完成一次模型推理，再复用连续分数比较全部阈值。"""

    candidate_id, time_weight, frequency_weight, relation_weight = candidate
    settings = get_settings()
    min_event_length, merge_gap = recommended_event_policy("time_frequency_relation")
    base_config = AnalysisConfig(
        detector="time_frequency_relation",
        threshold=settings.anomaly_threshold,
        rolling_window=settings.rolling_window,
        min_event_length=min_event_length,
        merge_gap=merge_gap,
        contamination=settings.contamination,
        tfr_time_weight=time_weight,
        tfr_frequency_weight=frequency_weight,
        tfr_relation_weight=relation_weight,
    )
    cached_results = [
        analyze_file(
            file_path,
            config=base_config,
            write_report=False,
            run_forecast=False,
            run_regime=False,
        )
        for file_path in (*split.validation_files, *split.healthy_files)
    ]

    trials: list[ThresholdTrial] = []
    for threshold in thresholds:
        validation_metrics = []
        healthy_false_events = 0
        healthy_file_count = 0
        decision_config = AnalysisConfig(
            detector="time_frequency_relation",
            threshold=threshold,
            rolling_window=base_config.rolling_window,
            min_event_length=base_config.min_event_length,
            merge_gap=base_config.merge_gap,
            contamination=base_config.contamination,
            tfr_time_weight=time_weight,
            tfr_frequency_weight=frequency_weight,
            tfr_relation_weight=relation_weight,
        )
        for result in cached_results:
            predicted, _ = apply_detection_threshold(
                result.dataframe,
                result.anomaly_scores,
                result.combined_score,
                decision_config,
            )
            metrics = evaluate_predictions(
                result.dataframe,
                predicted,
                result.combined_score,
                merge_gap=decision_config.merge_gap,
            )
            if metrics is None:
                continue
            if result.source_path.parent.name.lower() == "anomaly-free":
                healthy_file_count += 1
                healthy_false_events += metrics.false_positive_event_count
            else:
                validation_metrics.append(metrics)

        point_f1 = _average(item.f1_score for item in validation_metrics)
        event_f1 = _average(item.event_f1_score for item in validation_metrics)
        event_recall = _average(item.event_recall for item in validation_metrics)
        false_events = _average(
            float(item.false_positive_event_count) for item in validation_metrics
        )
        healthy_false_rate = healthy_false_events / max(healthy_file_count, 1)
        objective = (
            0.45 * event_f1
            + 0.25 * event_recall
            + 0.30 * point_f1
            - 0.015 * min(false_events, 20.0)
            - 0.04 * min(healthy_false_rate, 10.0)
        )
        trials.append(
            ThresholdTrial(
                detector=candidate_id,
                threshold=threshold,
                objective=objective,
                file_count=len(validation_metrics),
                point_f1=point_f1,
                event_f1=event_f1,
                event_recall=event_recall,
                average_false_events=false_events,
                healthy_false_event_rate=healthy_false_rate,
                failed_files=0,
            )
        )

    best = select_best_trial(trials)
    return TfrAblationRecord(
        candidate_id=candidate_id,
        time_weight=time_weight,
        frequency_weight=frequency_weight,
        relation_weight=relation_weight,
        threshold=best.threshold,
        objective=best.objective,
        point_f1=best.point_f1,
        event_f1=best.event_f1,
        event_recall=best.event_recall,
        average_false_events=best.average_false_events,
        healthy_false_event_rate=best.healthy_false_event_rate,
    )


def _run_selected_test(
    root: Path,
    split: ExperimentSplit,
    selected: TfrAblationRecord,
    target_dir: Path,
) -> BenchmarkResult:
    """使用冻结配置对比新模型与原有 AutoEncoder 基线。"""

    return run_skab_benchmark(
        root,
        detectors=("time_frequency_relation", "window_autoencoder"),
        files=split.test_files,
        thresholds={
            "time_frequency_relation": selected.threshold,
        },
        config_overrides={
            "time_frequency_relation": {
                "tfr_time_weight": selected.time_weight,
                "tfr_frequency_weight": selected.frequency_weight,
                "tfr_relation_weight": selected.relation_weight,
            }
        },
        output_dir=target_dir,
        report_prefix="tfr_ablation_test",
    )


def _write_csv(records: list[TfrAblationRecord], target_path: Path) -> None:
    """保存所有验证集候选，便于后续绘制消融图表。"""

    with target_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(TfrAblationRecord.__dataclass_fields__))
        writer.writeheader()
        for record in records:
            writer.writerow(record.__dict__)


def _build_report(
    root: Path,
    split: ExperimentSplit,
    records: list[TfrAblationRecord],
    selected: TfrAblationRecord,
    test_benchmark: BenchmarkResult,
) -> str:
    """生成可直接用于技术文档和竞赛材料的消融报告。"""

    lines = [
        "# 时频关系多路径模型消融报告",
        "",
        f"> 实验协议：`{PROTOCOL_VERSION}`。",
        f"> 数据目录：`{root}`",
        f"> 验证文件：{len(split.validation_files)}；独立测试文件：{len(split.test_files)}。",
        "> 健康基线只用于无监督模型训练和分数校准，异常标签只用于验证与测试评价。",
        "",
        "| 候选 | 时域 | 频域 | 关系 | 阈值 | 目标函数 | 点级 F1 | 事件级 F1 | 事件召回 | 平均误报事件 | 健康误报 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for record in sorted(records, key=lambda item: item.objective, reverse=True):
        lines.append(
            f"| {record.candidate_id} | {record.time_weight:.2f} | "
            f"{record.frequency_weight:.2f} | {record.relation_weight:.2f} | "
            f"{record.threshold:.2f} | {record.objective:.4f} | {record.point_f1:.4f} | "
            f"{record.event_f1:.4f} | {record.event_recall:.4f} | "
            f"{record.average_false_events:.2f} | {record.healthy_false_event_rate:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 冻结配置",
            "",
            (
                f"选择 `{selected.candidate_id}`：时域={selected.time_weight:.2f}、"
                f"频域={selected.frequency_weight:.2f}、关系={selected.relation_weight:.2f}、"
                f"阈值={selected.threshold:.2f}。"
            ),
            "",
            f"独立测试对比报告：`{test_benchmark.report_path.name}`",
            "",
            "最终是否替换默认模型，以独立测试结果为准，不以验证集排名或模型复杂度判断。",
            "",
        ]
    )
    return "\n".join(lines)


def _average(values: Iterable[float]) -> float:
    """计算可迭代数值的平均值。"""

    collected = [float(value) for value in values]
    return sum(collected) / len(collected) if collected else 0.0
