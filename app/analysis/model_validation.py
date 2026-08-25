"""在线异常检测多模型交叉验证。

主分析只需要运行一次完整管线。这个模块复用主检测器结果，再补跑少量互补检测器，
比较异常点、事件数量和标签指标；不会重复执行趋势预测、根因诊断、知识检索或工单生成。
因此它既能为智能体提供多模型证据，也不会把在线响应时间放大到完整分析的数倍。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from app.analysis.detection import (
    DETECTOR_LABELS,
    DETECTOR_RECOMMENDED_THRESHOLDS,
    DetectionOutput,
    detect_anomalies,
    recommended_event_policy,
)
from app.analysis.evaluation import evaluate_predictions
from app.models import AnalysisConfig

DEFAULT_VALIDATION_DETECTORS = (
    "mad",
    "isolation_forest",
    "pca_reconstruction",
    "time_frequency_relation",
)

DETECTOR_FAMILIES = {
    "mad": "稳健统计",
    "isolation_forest": "树模型机器学习",
    "pca_reconstruction": "线性多变量重构",
    "window_autoencoder": "非线性时序重构",
    "time_frequency_relation": "时域-频域-关系多路径",
    "hybrid": "多检测器加权融合",
}


def cross_validate_detectors(
    dataframe: pd.DataFrame,
    sensor_columns: list[str],
    config: AnalysisConfig,
    primary_output: DetectionOutput,
    healthy_reference: pd.DataFrame | None = None,
    detectors: tuple[str, ...] = DEFAULT_VALIDATION_DETECTORS,
) -> dict[str, Any]:
    """比较互补检测器，并给出可审计的一致性结论。

    当前文件中的 ``anomaly`` 标签只用于报告离线指标，绝不据此临时切换线上主模型，
    避免在测试样本上选择模型造成数据泄漏。生产选择仍使用验证集冻结参数和设备配置。
    """

    ordered_detectors = tuple(dict.fromkeys((config.detector, *detectors)))
    outputs: dict[str, DetectionOutput] = {}
    failures: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []

    for detector in ordered_detectors:
        try:
            is_primary = detector == config.detector
            # 主模型已经按本次请求的阈值完成检测，必须保留其真实配置；互补模型才使用
            # 各自的推荐阈值。否则报告里的主模型阈值会与实际计算不一致。
            detector_config = (
                config
                if is_primary
                else replace(
                    config,
                    detector=detector,
                    threshold=DETECTOR_RECOMMENDED_THRESHOLDS[detector],
                    min_event_length=recommended_event_policy(detector)[0],
                    merge_gap=recommended_event_policy(detector)[1],
                )
            )
            output = (
                primary_output
                if is_primary
                else detect_anomalies(
                    dataframe,
                    sensor_columns,
                    detector_config,
                    healthy_reference,
                )
            )
            outputs[detector] = output
            metrics = evaluate_predictions(
                dataframe,
                output.predicted_labels,
                output.combined_score,
                merge_gap=detector_config.merge_gap,
            )
            records.append(
                _model_record(
                    detector,
                    detector_config,
                    output,
                    primary_output.predicted_labels,
                    metrics,
                    is_primary=is_primary,
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            failures.append({"detector": detector, "error": str(exc)[:300]})

    agreement = _agreement_summary(outputs, config.detector)
    conclusion = _validation_conclusion(outputs, config.detector, agreement)
    return {
        "status": "completed" if len(outputs) >= 2 else "degraded",
        "primary_detector": config.detector,
        "primary_detector_name": DETECTOR_LABELS.get(
            config.detector,
            primary_output.detector_name,
        ),
        "selected_detector": config.detector,
        "selection_basis": (
            "主模型及阈值由固定验证集和设备配置预先确定；当前文件标签仅用于离线评估，"
            "不参与在线模型切换。"
        ),
        "model_count": len(outputs),
        "models": records,
        "agreement": agreement,
        "conclusion": conclusion,
        "failed_models": failures,
        "limitations": [
            "模型一致只表示多种统计视角均发现数据偏离，不等于故障根因已经确认。",
            "无标签企业数据只能比较模型一致性，不能计算真实准确率。",
            "现场部署前仍需使用目标设备健康数据重新校准阈值。",
        ],
    }


def _model_record(
    detector: str,
    config: AnalysisConfig,
    output: DetectionOutput,
    primary_labels: pd.Series,
    metrics: Any,
    *,
    is_primary: bool,
) -> dict[str, Any]:
    """把单模型结果压缩为适合万悟和前端读取的证据记录。"""

    record: dict[str, Any] = {
        "detector": detector,
        "detector_name": output.detector_name,
        "model_family": DETECTOR_FAMILIES.get(detector, "其他"),
        "is_primary": is_primary,
        "threshold": config.threshold,
        "anomaly_point_count": int(output.predicted_labels.sum()),
        "event_count": len(output.events),
        "peak_score": round(float(output.combined_score.max()), 4),
        "agreement_with_primary": _label_agreement(
            output.predicted_labels,
            primary_labels,
        ),
    }
    if metrics is None:
        record["evaluation"] = None
    else:
        record["evaluation"] = {
            "point_precision": round(metrics.precision, 4),
            "point_recall": round(metrics.recall, 4),
            "point_f1": round(metrics.f1_score, 4),
            "pr_auc": round(metrics.pr_auc, 4),
            "event_precision": round(metrics.event_precision, 4),
            "event_recall": round(metrics.event_recall, 4),
            "event_f1": round(metrics.event_f1_score, 4),
            "mean_detection_delay": metrics.mean_detection_delay,
            "false_positive_events": metrics.false_positive_event_count,
        }
    return record


def _label_agreement(labels: pd.Series, primary_labels: pd.Series) -> dict[str, float]:
    """计算整体一致率与异常集合 Jaccard，避免只看大量正常点造成虚高。"""

    current = labels.astype(bool).to_numpy()
    primary = primary_labels.astype(bool).to_numpy()
    intersection = int(np.logical_and(current, primary).sum())
    union = int(np.logical_or(current, primary).sum())
    return {
        "point_accuracy": round(float(np.mean(current == primary)), 4),
        "anomaly_jaccard": round(intersection / union, 4) if union else 1.0,
    }


def _agreement_summary(
    outputs: dict[str, DetectionOutput],
    primary_detector: str,
) -> dict[str, Any]:
    """汇总多模型投票与主模型异常集合的一致程度。"""

    if not outputs:
        return {
            "level": "不可用",
            "mean_primary_jaccard": 0.0,
            "consensus_anomaly_points": 0,
            "models_with_events": 0,
            "models_supporting_primary": 0,
        }
    label_matrix = np.vstack(
        [output.predicted_labels.astype(int).to_numpy() for output in outputs.values()]
    )
    votes = label_matrix.sum(axis=0)
    # “多数模型支持”必须严格过半；四模型场景至少需要三票，不能把二比二写成多数。
    required_votes = max(2, len(outputs) // 2 + 1)
    consensus_points = int((votes >= required_votes).sum())
    primary = outputs.get(primary_detector)
    primary_jaccards = []
    models_supporting_primary = 1 if primary is not None and primary.events else 0
    if primary is not None:
        for detector, output in outputs.items():
            if detector == primary_detector:
                continue
            jaccard = _label_agreement(
                output.predicted_labels,
                primary.predicted_labels,
            )["anomaly_jaccard"]
            primary_jaccards.append(jaccard)
            if jaccard > 0:
                models_supporting_primary += 1
    mean_jaccard = float(np.mean(primary_jaccards)) if primary_jaccards else 1.0
    level = "高" if mean_jaccard >= 0.65 else "中" if mean_jaccard >= 0.35 else "低"
    return {
        "level": level,
        "mean_primary_jaccard": round(mean_jaccard, 4),
        "required_votes": required_votes,
        "consensus_anomaly_points": consensus_points,
        "models_with_events": sum(bool(output.events) for output in outputs.values()),
        "models_supporting_primary": models_supporting_primary,
        "event_count_range": [
            min(len(output.events) for output in outputs.values()),
            max(len(output.events) for output in outputs.values()),
        ],
    }


def _validation_conclusion(
    outputs: dict[str, DetectionOutput],
    primary_detector: str,
    agreement: dict[str, Any],
) -> str:
    """生成不夸大模型一致性的简短判断。"""

    if not outputs:
        return "交叉验证模型均未成功运行，保留主分析结果并提示人工复核。"
    primary_has_events = bool(outputs.get(primary_detector) and outputs[primary_detector].events)
    supporting_models = int(agreement.get("models_supporting_primary", 0))
    majority = max(2, len(outputs) // 2 + 1)
    has_consensus_points = int(agreement.get("consensus_anomaly_points", 0)) > 0
    if primary_has_events and supporting_models >= majority and has_consensus_points:
        return "主模型告警获得多数互补模型支持，可进入根因排查，但仍需现场确认。"
    if primary_has_events:
        return "主模型发现异常，但跨模型支持不足，应降低结论置信度并优先核对工况。"
    if supporting_models:
        return "主模型未形成持续事件，但其他模型发现偏离，建议保留观察并复核阈值。"
    return "当前配置下各模型均未形成持续异常事件。"
