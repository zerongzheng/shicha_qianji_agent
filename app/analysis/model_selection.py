"""面向工业任务的可审计异常检测模型选择。

模型选择不能读取当前待分析文件的 ``anomaly`` 标签，否则会把测试答案泄漏给线上流程。
本模块只使用用户任务目标、设备配置、数据规模、传感器数量和健康基线可用性，按照冻结规则
选择主模型；完整候选排序会随分析结果返回，方便评委和运维人员复核。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

import pandas as pd

from app.analysis.detection import (
    DETECTOR_LABELS,
    DETECTOR_RECOMMENDED_THRESHOLDS,
    recommended_event_policy,
)
from app.models import AnalysisConfig

ANALYSIS_GOAL_LABELS = {
    "balanced": "综合平衡",
    "high_recall": "优先发现异常事件",
    "low_false_alarm": "优先控制误报",
    "relationship_fault": "优先识别多传感器关系破坏",
    "nonlinear_pattern": "优先识别复杂非线性模式",
    "fast_screening": "优先快速筛查",
}

# 每个目标的顺序来自固定实验结论和模型机理，不读取本次文件标签。设备专属配置可以覆盖
# balanced 的首选模型；其他目标仍按这里的能力顺序选择，避免所有场景都固定使用同一模型。
GOAL_PREFERENCES = {
    "balanced": (
        "time_frequency_relation",
        "mad",
        "window_autoencoder",
        "pca_reconstruction",
        "hybrid",
        "isolation_forest",
    ),
    "high_recall": (
        "time_frequency_relation",
        "window_autoencoder",
        "hybrid",
        "pca_reconstruction",
        "mad",
        "isolation_forest",
    ),
    "low_false_alarm": (
        "mad",
        "time_frequency_relation",
        "window_autoencoder",
        "pca_reconstruction",
        "hybrid",
        "isolation_forest",
    ),
    "relationship_fault": (
        "time_frequency_relation",
        "pca_reconstruction",
        "hybrid",
        "window_autoencoder",
        "isolation_forest",
        "mad",
    ),
    "nonlinear_pattern": (
        "window_autoencoder",
        "time_frequency_relation",
        "hybrid",
        "pca_reconstruction",
        "isolation_forest",
        "mad",
    ),
    "fast_screening": (
        "mad",
        "window_autoencoder",
        "pca_reconstruction",
        "time_frequency_relation",
        "isolation_forest",
        "hybrid",
    ),
}


@dataclass(frozen=True)
class DetectorEligibility:
    """一个模型对当前数据的最低适用条件。"""

    min_rows: int
    min_sensors: int
    healthy_baseline_required: bool = False


MODEL_ELIGIBILITY = {
    "mad": DetectorEligibility(min_rows=5, min_sensors=1),
    "isolation_forest": DetectorEligibility(min_rows=64, min_sensors=2),
    "pca_reconstruction": DetectorEligibility(min_rows=32, min_sensors=2),
    "window_autoencoder": DetectorEligibility(
        min_rows=80,
        min_sensors=2,
        healthy_baseline_required=True,
    ),
    "time_frequency_relation": DetectorEligibility(min_rows=64, min_sensors=2),
    "hybrid": DetectorEligibility(min_rows=64, min_sensors=2),
}


def select_detection_model(
    dataframe: pd.DataFrame,
    sensor_columns: list[str],
    config: AnalysisConfig,
    device_context: dict[str, Any],
    *,
    healthy_baseline_available: bool,
) -> tuple[AnalysisConfig, dict[str, Any]]:
    """返回实际生效配置和不依赖当前标签的模型选择证据。"""

    goal = _normalize_goal(config.analysis_goal)
    if config.detector_selection_mode == "manual":
        decision = _manual_decision(
            config,
            goal,
            len(dataframe),
            len(sensor_columns),
            healthy_baseline_available,
        )
        return config, decision

    preferred = list(GOAL_PREFERENCES[goal])
    recommended = device_context.get("recommended_analysis") or {}
    goal_policy = recommended.get("goal_policy") or {}
    profile_detector = str(
        goal_policy.get(goal) or recommended.get("detector") or ""
    ).strip()
    selection_source = "task_goal"
    if profile_detector in preferred:
        preferred.remove(profile_detector)
        preferred.insert(0, profile_detector)
        selection_source = "device_profile_goal_policy"

    candidates = _rank_candidates(
        preferred,
        row_count=len(dataframe),
        sensor_count=len(sensor_columns),
        healthy_baseline_available=healthy_baseline_available,
    )
    selected = next((item for item in candidates if item["eligible"]), None)
    if selected is None:
        # 正常加载成功的数据至少满足 MAD 的五行约束；这里仍保留显式保护，避免未来
        # 调整加载规则后静默选择一个无法运行的模型。
        raise ValueError("当前数据规模不满足任何已注册异常检测模型的最低条件")

    selected_detector = str(selected["detector"])
    threshold = DETECTOR_RECOMMENDED_THRESHOLDS[selected_detector]
    min_event_length, merge_gap = recommended_event_policy(selected_detector)
    default_profile_detector = str(recommended.get("detector") or "").strip()
    if (
        default_profile_detector == selected_detector
        and recommended.get("threshold") is not None
    ):
        threshold = float(recommended["threshold"])
        min_event_length = int(recommended.get("min_event_length", min_event_length))
        merge_gap = int(recommended.get("merge_gap", merge_gap))

    effective = replace(
        config,
        detector=selected_detector,
        threshold=threshold,
        min_event_length=min_event_length,
        merge_gap=merge_gap,
    )
    decision = {
        "mode": "automatic",
        "analysis_goal": goal,
        "analysis_goal_name": ANALYSIS_GOAL_LABELS[goal],
        "selected_detector": selected_detector,
        "selected_detector_name": DETECTOR_LABELS[selected_detector],
        "selected_threshold": threshold,
        "selected_event_policy": {
            "min_event_length": min_event_length,
            "merge_gap": merge_gap,
        },
        "selection_source": selection_source,
        "reason": _selection_reason(
            selected_detector,
            goal,
            selection_source,
            device_context,
        ),
        "data_evidence": {
            "row_count": len(dataframe),
            "sensor_count": len(sensor_columns),
            "healthy_baseline_available": healthy_baseline_available,
            "device_profile_id": device_context.get("profile_id"),
            "device_profile_match_mode": device_context.get("match_mode", "generic"),
        },
        "candidate_ranking": candidates,
        "label_leakage_control": (
            "模型选择未读取当前文件 anomaly/changepoint 标签；标签仅在检测完成后用于离线评价。"
        ),
        "limitations": [
            "自动选择基于冻结规则和设备配置，不等于在目标企业现场已经达到最优。",
            "企业数据接入后需要重新验证模型排序、阈值和任务目标策略。",
        ],
    }
    return effective, decision


def _manual_decision(
    config: AnalysisConfig,
    goal: str,
    row_count: int,
    sensor_count: int,
    healthy_baseline_available: bool,
) -> dict[str, Any]:
    """显式模型由用户或实验协议负责，系统只记录而不偷偷替换。"""

    return {
        "mode": "manual",
        "analysis_goal": goal,
        "analysis_goal_name": ANALYSIS_GOAL_LABELS[goal],
        "selected_detector": config.detector,
        "selected_detector_name": DETECTOR_LABELS.get(config.detector, config.detector),
        "selected_threshold": config.threshold,
        "selection_source": "explicit_configuration",
        "reason": "用户或固定实验协议显式指定模型，系统保持该配置以保证结果可复现。",
        "data_evidence": {
            "row_count": row_count,
            "sensor_count": sensor_count,
            "healthy_baseline_available": healthy_baseline_available,
        },
        "candidate_ranking": [],
        "label_leakage_control": "显式配置未根据当前文件标签进行修改。",
        "limitations": ["手动模式下模型适用性由配置者负责确认。"],
    }


def _rank_candidates(
    preferred: list[str],
    *,
    row_count: int,
    sensor_count: int,
    healthy_baseline_available: bool,
) -> list[dict[str, Any]]:
    """按目标优先级排序，同时明确记录模型为什么可用或不可用。"""

    candidates: list[dict[str, Any]] = []
    total = len(preferred)
    for rank, detector in enumerate(preferred, start=1):
        rule = MODEL_ELIGIBILITY[detector]
        blockers: list[str] = []
        if row_count < rule.min_rows:
            blockers.append(f"至少需要 {rule.min_rows} 行数据")
        if sensor_count < rule.min_sensors:
            blockers.append(f"至少需要 {rule.min_sensors} 个传感器")
        if rule.healthy_baseline_required and not healthy_baseline_available:
            blockers.append("需要可用健康基线")
        candidates.append(
            {
                "detector": detector,
                "detector_name": DETECTOR_LABELS[detector],
                "preference_rank": rank,
                "score": total - rank + 1,
                "eligible": not blockers,
                "eligibility": asdict(rule),
                "blockers": blockers,
            }
        )
    return candidates


def _normalize_goal(goal: str) -> str:
    normalized = str(goal).strip().lower()
    if normalized not in GOAL_PREFERENCES:
        available = "、".join(GOAL_PREFERENCES)
        raise ValueError(f"不支持的分析目标 {goal!r}，可选值：{available}")
    return normalized


def _selection_reason(
    detector: str,
    goal: str,
    source: str,
    device_context: dict[str, Any],
) -> str:
    if source == "device_profile_goal_policy":
        device_name = device_context.get("display_name") or "当前设备"
        return (
            f"{device_name} 的冻结设备配置将 {DETECTOR_LABELS[detector]} 设为综合平衡主模型；"
            "其他模型用于交叉核验。"
        )
    return (
        f"任务目标为“{ANALYSIS_GOAL_LABELS[goal]}”，当前数据满足 "
        f"{DETECTOR_LABELS[detector]} 的最低输入条件，因此按冻结能力顺序选为主模型。"
    )
