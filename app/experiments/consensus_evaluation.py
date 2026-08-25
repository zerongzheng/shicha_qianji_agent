"""SKAB 独立测试集上的多模型严格多数共识实验。

该实验只使用固定测试文件评价冻结策略，不在测试集上选择阈值。四个互补检测器分别完成
预测后，至少三票重合才形成共识告警，用于检验多模型核验是否真正改善误报与事件发现。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.analysis.detection import (
    DETECTOR_LABELS,
    DETECTOR_RECOMMENDED_THRESHOLDS,
    recommended_event_policy,
)
from app.analysis.evaluation import evaluate_predictions
from app.analysis.pipeline import analyze_file
from app.config import Settings, get_settings
from app.experiments.protocol import read_frozen_thresholds
from app.experiments.split import build_skab_split
from app.models import AnalysisConfig, EvaluationMetrics

CONSENSUS_DETECTORS = (
    "mad",
    "isolation_forest",
    "pca_reconstruction",
    "time_frequency_relation",
)
CONSENSUS_STRATEGY = "strict_majority_3_of_4"


@dataclass(frozen=True)
class ConsensusRecord:
    """一个模型策略在一个独立测试文件上的评价。"""

    strategy: str
    strategy_name: str
    scenario: str
    file_name: str
    row_count: int
    point_precision: float
    point_recall: float
    point_f1: float
    pr_auc: float
    event_precision: float
    event_recall: float
    event_f1: float
    false_positive_events: int
    detection_delay: float | None
    inference_seconds: float


@dataclass(frozen=True)
class ConsensusEvaluation:
    """共识实验产物和执行状态。"""

    records: list[ConsensusRecord]
    failed_files: dict[str, str]
    csv_path: Path
    report_path: Path


def evaluate_detector_consensus(
    data_root: str | Path,
    output_dir: str | Path | None = None,
    *,
    max_files: int | None = None,
) -> ConsensusEvaluation:
    """在固定独立测试集比较四个单模型与严格多数共识。"""

    root = Path(data_root).expanduser().resolve()
    split = build_skab_split(root)
    test_files = list(split.test_files)
    if max_files is not None and max_files > 0:
        test_files = test_files[:max_files]

    settings = get_settings()
    target_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "competition"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    frozen_thresholds = read_frozen_thresholds(target_dir)
    records: list[ConsensusRecord] = []
    failed_files: dict[str, str] = {}
    for file_path in test_files:
        try:
            labels: dict[str, pd.Series] = {}
            scores: dict[str, pd.Series] = {}
            durations: dict[str, float] = {}
            for detector in CONSENSUS_DETECTORS:
                config = _detector_config(detector, settings, frozen_thresholds)
                started_at = perf_counter()
                result = analyze_file(
                    file_path,
                    config,
                    write_report=False,
                    run_forecast=False,
                    run_regime=False,
                )
                durations[detector] = perf_counter() - started_at
                labels[detector] = result.predicted_labels
                scores[detector] = result.combined_score
                metrics = result.metrics
                if metrics is None:
                    raise ValueError("独立测试文件缺少 anomaly 标签")
                records.append(
                    _record_from_metrics(
                        detector,
                        DETECTOR_LABELS[detector],
                        file_path,
                        result.profile.row_count,
                        metrics,
                        durations[detector],
                    )
                )

            consensus_labels, consensus_score = strict_majority_consensus(labels, scores)
            metrics = evaluate_predictions(
                result.dataframe,
                consensus_labels,
                consensus_score,
                merge_gap=settings.merge_gap,
            )
            if metrics is None:
                raise ValueError("独立测试文件缺少 anomaly 标签")
            records.append(
                _record_from_metrics(
                    CONSENSUS_STRATEGY,
                    "四模型严格多数共识（3/4）",
                    file_path,
                    result.profile.row_count,
                    metrics,
                    sum(durations.values()),
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            failed_files[f"{file_path.parent.name}/{file_path.name}"] = str(exc)

    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    csv_path = target_dir / f"consensus_evaluation_{timestamp}.csv"
    report_path = target_dir / f"consensus_evaluation_{timestamp}.md"
    _write_csv(csv_path, records)
    report_path.write_text(
        build_consensus_report(root, len(test_files), records, failed_files),
        encoding="utf-8",
    )
    return ConsensusEvaluation(records, failed_files, csv_path, report_path)


def strict_majority_consensus(
    labels: dict[str, pd.Series],
    scores: dict[str, pd.Series],
) -> tuple[pd.Series, pd.Series]:
    """四模型至少三票形成告警，平均风险分数只用于连续排序评价。"""

    if set(labels) != set(CONSENSUS_DETECTORS) or set(scores) != set(CONSENSUS_DETECTORS):
        raise ValueError("共识实验必须完整提供四个冻结检测器的结果")
    lengths = {len(value) for value in labels.values()} | {len(value) for value in scores.values()}
    if len(lengths) != 1:
        raise ValueError("参与共识的模型输出长度不一致")
    label_matrix = np.vstack(
        [labels[name].astype(int).to_numpy() for name in CONSENSUS_DETECTORS]
    )
    score_matrix = np.vstack(
        [scores[name].astype(float).to_numpy() for name in CONSENSUS_DETECTORS]
    )
    index = labels[CONSENSUS_DETECTORS[0]].index
    return (
        pd.Series((label_matrix.sum(axis=0) >= 3).astype(int), index=index),
        pd.Series(score_matrix.mean(axis=0), index=index),
    )


def build_consensus_report(
    data_root: Path,
    planned_file_count: int,
    records: list[ConsensusRecord],
    failed_files: dict[str, str],
) -> str:
    """生成可以直接用于技术报告的模型共识成效表。"""

    summaries = _summarize(records)
    lines = [
        "# SKAB 多模型严格多数共识实验",
        "",
        "> 本实验使用冻结阈值和固定独立测试集；测试标签仅用于最终评价，不参与模型选择或调参。",
        "",
        "## 实验口径",
        "",
        f"- 数据目录：`{data_root}`",
        f"- 计划测试文件：{planned_file_count} 个",
        f"- 成功文件：{len(records) // 5} 个",
        f"- 失败文件：{len(failed_files)} 个",
        "- 参与模型：MAD、Isolation Forest、PCA 多变量重构、时频关系多路径",
        "- 共识规则：四个模型中至少三个在同一采样点告警，才形成共识告警",
        "- 单模型均使用统一自适应预处理，阈值优先读取当前实验协议冻结值",
        "",
        "## 独立测试结果",
        "",
        "| 策略 | 文件数 | 点级 F1 | PR-AUC | 事件级 F1 | 事件召回 | 平均误报事件 | 平均检测延迟 | 单文件耗时/秒 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summaries:
        lines.append(
            f"| {item['strategy_name']} | {item['file_count']} | {item['point_f1']:.4f} | "
            f"{item['pr_auc']:.4f} | {item['event_f1']:.4f} | "
            f"{item['event_recall']:.4f} | {item['false_positive_events']:.2f} | "
            f"{item['detection_delay']:.2f} | {item['inference_seconds']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "- 共识策略只有在独立测试结果优于主模型时，才能作为告警抑制方案；否则仅作为可信度证据展示。",
            "- 严格多数通常降低误报，也可能损失事件召回，不能未经验证直接替代主模型。",
            "- 结果来自 SKAB 公开数据，不代表联通企业现场收益。",
        ]
    )
    if failed_files:
        lines.extend(["", "## 失败任务", ""])
        lines.extend(f"- `{name}`：{error}" for name, error in sorted(failed_files.items()))
    return "\n".join(lines) + "\n"


def _detector_config(
    detector: str,
    settings: Settings,
    frozen_thresholds: dict[str, float],
) -> AnalysisConfig:
    min_event_length, merge_gap = recommended_event_policy(detector)
    return AnalysisConfig(
        detector_selection_mode="manual",
        detector=detector,
        threshold=frozen_thresholds.get(
            detector,
            DETECTOR_RECOMMENDED_THRESHOLDS[detector],
        ),
        rolling_window=settings.rolling_window,
        min_event_length=min_event_length,
        merge_gap=merge_gap,
        contamination=settings.contamination,
    )


def _record_from_metrics(
    strategy: str,
    strategy_name: str,
    file_path: Path,
    row_count: int,
    metrics: EvaluationMetrics,
    inference_seconds: float,
) -> ConsensusRecord:
    return ConsensusRecord(
        strategy=strategy,
        strategy_name=strategy_name,
        scenario=file_path.parent.name,
        file_name=file_path.name,
        row_count=row_count,
        point_precision=metrics.precision,
        point_recall=metrics.recall,
        point_f1=metrics.f1_score,
        pr_auc=metrics.pr_auc,
        event_precision=metrics.event_precision,
        event_recall=metrics.event_recall,
        event_f1=metrics.event_f1_score,
        false_positive_events=metrics.false_positive_event_count,
        detection_delay=metrics.mean_detection_delay,
        inference_seconds=inference_seconds,
    )


def _summarize(records: list[ConsensusRecord]) -> list[dict[str, float | int | str]]:
    groups: dict[str, list[ConsensusRecord]] = {}
    for record in records:
        groups.setdefault(record.strategy, []).append(record)
    order = (*CONSENSUS_DETECTORS, CONSENSUS_STRATEGY)
    summaries = []
    for strategy in order:
        items = groups.get(strategy, [])
        if not items:
            continue
        summaries.append(
            {
                "strategy": strategy,
                "strategy_name": items[0].strategy_name,
                "file_count": len(items),
                "point_f1": mean(item.point_f1 for item in items),
                "pr_auc": mean(item.pr_auc for item in items),
                "event_f1": mean(item.event_f1 for item in items),
                "event_recall": mean(item.event_recall for item in items),
                "false_positive_events": mean(item.false_positive_events for item in items),
                "detection_delay": _mean_optional(item.detection_delay for item in items),
                "inference_seconds": mean(item.inference_seconds for item in items),
            }
        )
    return summaries


def _mean_optional(values) -> float:
    available = [float(value) for value in values if value is not None]
    return mean(available) if available else 0.0


def _write_csv(path: Path, records: list[ConsensusRecord]) -> None:
    fieldnames = list(ConsensusRecord.__dataclass_fields__)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(record.__dict__)
