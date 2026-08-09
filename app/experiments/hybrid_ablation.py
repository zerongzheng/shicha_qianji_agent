"""Hybrid 检测器融合权重消融实验。

候选权重只在验证集上比较，测试集仅用于冻结配置后的最终评价。该模块把代码中的经验常数
转化为可复现的实验结论，为竞赛答辩提供权重选择依据。
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.analysis.detection import apply_detection_threshold
from app.analysis.evaluation import evaluate_predictions
from app.analysis.pipeline import analyze_file
from app.config import get_settings
from app.experiments.benchmark import BenchmarkResult, run_skab_benchmark
from app.experiments.split import ExperimentSplit, build_skab_split
from app.experiments.tuning import DEFAULT_THRESHOLDS, ThresholdTrial, select_best_trial
from app.models import AnalysisConfig

DEFAULT_WEIGHT_CANDIDATES = (
    (0.50, 0.30, 0.20),
    (0.55, 0.20, 0.25),
    (0.55, 0.15, 0.30),
    (0.60, 0.10, 0.30),
    (0.60, 0.00, 0.40),
    (0.50, 0.10, 0.40),
)


@dataclass(frozen=True)
class HybridAblationRecord:
    """一组融合权重在最佳验证阈值上的表现。"""

    candidate_id: str
    mad_weight: float
    forest_weight: float
    pca_weight: float
    threshold: float
    objective: float
    point_f1: float
    event_f1: float
    event_recall: float
    average_false_events: float
    healthy_false_event_rate: float


@dataclass(frozen=True)
class HybridAblationResult:
    """权重消融、胜出配置和独立测试产物。"""

    split: ExperimentSplit
    records: tuple[HybridAblationRecord, ...]
    selected: HybridAblationRecord
    test_benchmark: BenchmarkResult
    csv_path: Path
    report_path: Path


def run_hybrid_weight_ablation(
    data_root: str | Path,
    candidates: tuple[tuple[float, float, float], ...] = DEFAULT_WEIGHT_CANDIDATES,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
    output_dir: str | Path | None = None,
) -> HybridAblationResult:
    """在验证集选择 Hybrid 权重与阈值，并在独立测试集完成最终评价。"""

    root = Path(data_root).expanduser().resolve()
    split = build_skab_split(root)
    settings = get_settings()
    target_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "experiments"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    records: list[HybridAblationRecord] = []
    for candidate_index, weights in enumerate(candidates, start=1):
        candidate_id = f"hybrid_w{candidate_index:02d}"
        records.append(
            _evaluate_candidate(
                candidate_id,
                weights,
                thresholds,
                split,
            )
        )

    selected = max(
        records,
        key=lambda item: (
            item.objective,
            item.event_recall,
            item.event_f1,
            item.point_f1,
            -item.average_false_events,
        ),
    )
    test_benchmark = _run_selected_test(root, split, selected, target_dir)

    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    csv_path = target_dir / f"hybrid_weight_ablation_{timestamp}.csv"
    report_path = target_dir / f"hybrid_weight_ablation_{timestamp}.md"
    _write_csv(records, csv_path)
    report_path.write_text(
        _build_report(root, split, records, selected, test_benchmark),
        encoding="utf-8",
    )
    return HybridAblationResult(
        split=split,
        records=tuple(records),
        selected=selected,
        test_benchmark=test_benchmark,
        csv_path=csv_path,
        report_path=report_path,
    )


def _evaluate_candidate(
    candidate_id: str,
    weights: tuple[float, float, float],
    thresholds: tuple[float, ...],
    split: ExperimentSplit,
) -> HybridAblationRecord:
    """对一组权重只推理一次，再复用分数比较多个阈值。"""

    settings = get_settings()
    base_config = AnalysisConfig(
        detector="hybrid",
        threshold=settings.anomaly_threshold,
        rolling_window=settings.rolling_window,
        min_event_length=settings.min_event_length,
        merge_gap=settings.merge_gap,
        contamination=settings.contamination,
        hybrid_mad_weight=weights[0],
        hybrid_forest_weight=weights[1],
        hybrid_pca_weight=weights[2],
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
            detector="hybrid",
            threshold=threshold,
            rolling_window=base_config.rolling_window,
            min_event_length=base_config.min_event_length,
            merge_gap=base_config.merge_gap,
            contamination=base_config.contamination,
            hybrid_mad_weight=weights[0],
            hybrid_forest_weight=weights[1],
            hybrid_pca_weight=weights[2],
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
    return HybridAblationRecord(
        candidate_id=candidate_id,
        mad_weight=weights[0],
        forest_weight=weights[1],
        pca_weight=weights[2],
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
    selected: HybridAblationRecord,
    target_dir: Path,
) -> BenchmarkResult:
    """使用冻结的最佳权重和阈值运行独立测试集。"""

    return run_skab_benchmark(
        root,
        detectors=("hybrid",),
        files=split.test_files,
        thresholds={"hybrid": selected.threshold},
        config_overrides={
            "hybrid": {
                "hybrid_mad_weight": selected.mad_weight,
                "hybrid_forest_weight": selected.forest_weight,
                "hybrid_pca_weight": selected.pca_weight,
            }
        },
        output_dir=target_dir,
        report_prefix="hybrid_ablation_test",
    )


def _write_csv(records: list[HybridAblationRecord], target_path: Path) -> None:
    """保存验证集消融结果。"""

    with target_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(HybridAblationRecord.__dataclass_fields__))
        writer.writeheader()
        for record in records:
            writer.writerow(record.__dict__)


def _build_report(
    root: Path,
    split: ExperimentSplit,
    records: list[HybridAblationRecord],
    selected: HybridAblationRecord,
    test_benchmark: BenchmarkResult,
) -> str:
    """生成权重选择依据和独立测试产物说明。"""

    lines = [
        "# Hybrid 融合权重消融报告",
        "",
        f"> 数据目录：`{root}`",
        f"> 验证文件：{len(split.validation_files)}；独立测试文件：{len(split.test_files)}。",
        "",
        "| 候选 | MAD | IF | PCA | 阈值 | 目标函数 | 点级 F1 | 事件级 F1 | 事件召回 | 平均误报事件 | 健康误报 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for record in sorted(records, key=lambda item: item.objective, reverse=True):
        lines.append(
            f"| {record.candidate_id} | {record.mad_weight:.2f} | {record.forest_weight:.2f} | "
            f"{record.pca_weight:.2f} | {record.threshold:.2f} | {record.objective:.4f} | "
            f"{record.point_f1:.4f} | {record.event_f1:.4f} | {record.event_recall:.4f} | "
            f"{record.average_false_events:.2f} | {record.healthy_false_event_rate:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 冻结配置",
            "",
            (
                f"选择 `{selected.candidate_id}`：MAD={selected.mad_weight:.2f}、"
                f"IF={selected.forest_weight:.2f}、PCA={selected.pca_weight:.2f}、"
                f"阈值={selected.threshold:.2f}。"
            ),
            "",
            f"独立测试报告：`{test_benchmark.report_path.name}`",
            "",
            "权重与阈值均由验证集确定，独立测试集未参与参数选择。",
            "",
        ]
    )
    return "\n".join(lines)


def _average(values: Iterable[float]) -> float:
    """计算可迭代数值的平均值。"""

    collected = [float(value) for value in values]
    return sum(collected) / len(collected) if collected else 0.0
