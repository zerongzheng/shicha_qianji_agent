"""工业时序自适应预处理与质量证据。

预处理不是固定执行一串滤波器，而是先判断时间轴、缺失模式和信号噪声，再决定实际动作。
异常检测任务对尖峰敏感，因此默认不平滑原始传感器值；系统使用模型内部的稳健缩放和噪声
下限抑制随机扰动，并把这一安全选择记录在结果中。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.data.loader import get_label_columns, get_sensor_columns

MAX_SHORT_GAP = 5
IRREGULAR_RATIO_THRESHOLD = 0.02
# SKAB 的 anomaly/label/target 表示一段时间内持续成立的状态；changepoint
# 表示某个采样点上的瞬时事件。两类标签在重采样时不能使用同一种填补方式。
STATE_LABELS = {"anomaly", "label", "target"}
POINT_LABELS = {"changepoint"}


@dataclass(frozen=True)
class PreprocessingResult:
    """模型输入数据和不包含逐点原始值的处理证据。"""

    dataframe: pd.DataFrame
    summary: dict[str, Any]


def adaptive_preprocess(
    dataframe: pd.DataFrame,
    *,
    expected_sampling_seconds: float | None = None,
    analysis_goal: str = "anomaly_detection",
) -> PreprocessingResult:
    """根据当前文件质量自动选择时间对齐、填补和抗噪策略。

    标签列只在时间重采样时使用最大值聚合，不参与任何插值、缩放或噪声判断。持续状态标签
    对重采样新增的时间点继承前一个观测状态，瞬时变点标签则填 0，避免把一个连续故障区间
    人为切碎。传感器值始终保持物理量纲，真正的 RobustScaler/StandardScaler 由对应模型在
    训练窗口内拟合，从而避免全文件缩放造成未来信息泄漏。
    """

    if dataframe.empty:
        raise ValueError("无法预处理空的工业时序数据。")
    working = dataframe.copy()
    sensors = get_sensor_columns(working)
    labels = get_label_columns(working)
    if not sensors:
        raise ValueError("没有可用于预处理的数值传感器列。")

    raw_rows = len(working)
    raw_missing = int(working[sensors].isna().sum().sum())
    interval, irregular_ratio = _sampling_diagnostics(working["datetime"])
    target_interval = _select_sampling_interval(interval, expected_sampling_seconds)
    alignment_applied = _should_align(
        working["datetime"],
        target_interval,
        irregular_ratio,
        expected_sampling_seconds,
    )
    if alignment_applied:
        working = _align_time_axis(working, sensors, labels, target_interval)

    missing_before_fill = int(working[sensors].isna().sum().sum())
    fill_records: list[dict[str, Any]] = []
    for sensor in sensors:
        filled, record = _fill_sensor(working[sensor], sensor)
        working[sensor] = filled
        fill_records.append(record)
    missing_after_fill = int(working[sensors].isna().sum().sum())

    noise_records = [_noise_strategy(working[sensor], sensor) for sensor in sensors]
    inserted_rows = max(0, len(working) - raw_rows)
    actions = [
        {
            "action": "time_alignment",
            "status": "applied" if alignment_applied else "not_needed",
            "method": (
                f"按 {target_interval:g} 秒时间网格聚合并补齐"
                if alignment_applied
                else "保留原时间轴"
            ),
            "reason": (
                f"不规则采样比例 {irregular_ratio:.2%}，目标周期 {target_interval:g} 秒"
                if alignment_applied
                else f"时间轴规则，不规则采样比例 {irregular_ratio:.2%}"
            ),
        },
        {
            "action": "missing_value_fill",
            "status": "applied" if missing_before_fill else "not_needed",
            "method": "短缺口线性插值；长缺口分段前后填充；最终使用历史中位数兜底",
            "reason": f"对齐后发现 {missing_before_fill} 个缺失点",
        },
        {
            "action": "noise_handling",
            "status": "model_internal",
            "method": "保留原始尖峰，使用滚动中位数、稳健尺度和传感器噪声下限",
            "reason": "异常检测需要保留可能代表故障的瞬态变化，禁止无条件平滑",
        },
        {
            "action": "normalization",
            "status": "model_internal",
            "method": "MAD 使用局部稳健尺度；重构模型使用训练窗口 RobustScaler；预测模型使用训练窗口 StandardScaler",
            "reason": "按模型需要选择缩放方式，并避免使用预测起点后的数据拟合缩放器",
        },
    ]
    summary = {
        "analysis_goal": analysis_goal,
        "raw_row_count": raw_rows,
        "processed_row_count": len(working),
        "inserted_row_count": inserted_rows,
        "raw_missing_count": raw_missing,
        "aligned_missing_count": missing_before_fill,
        "remaining_missing_count": missing_after_fill,
        "filled_count": missing_before_fill - missing_after_fill,
        "observed_sampling_seconds": round(interval, 6) if interval is not None else None,
        "target_sampling_seconds": round(target_interval, 6),
        "irregular_sampling_ratio": round(irregular_ratio, 6),
        "time_alignment_applied": alignment_applied,
        "fill_records": fill_records,
        "noise_records": noise_records,
        "actions": actions,
        "quality_gate": (
            "passed" if missing_after_fill == 0 and len(working) >= 20 else "review_required"
        ),
        "limitations": [
            "自动填补只保证算法输入连续，不代表缺失期间设备真实状态已经恢复。",
            "异常敏感任务不自动删除尖峰；疑似传感器毛刺需结合相邻测点和现场记录复核。",
            "企业接入后应由设备负责人确认采样周期、允许缺口和安全范围。",
        ],
    }
    return PreprocessingResult(working.reset_index(drop=True), summary)


def _sampling_diagnostics(timestamps: pd.Series) -> tuple[float | None, float]:
    """返回中位采样周期和偏离中位周期的差分比例。"""

    deltas = timestamps.diff().dropna().dt.total_seconds()
    positive = deltas[deltas > 0]
    if positive.empty:
        return None, 0.0
    interval = float(positive.median())
    tolerance = max(interval * 0.05, 1e-6)
    irregular_ratio = float(np.mean(np.abs(positive - interval) > tolerance))
    return interval, irregular_ratio


def _select_sampling_interval(
    observed: float | None,
    expected: float | None,
) -> float:
    """设备配置优先；未知时使用文件中位周期。"""

    candidate = expected if expected is not None and expected > 0 else observed
    return float(candidate) if candidate is not None and candidate > 0 else 1.0


def _should_align(
    timestamps: pd.Series,
    target_interval: float,
    irregular_ratio: float,
    expected: float | None,
) -> bool:
    """只有存在明显不规则或设备周期不符时才重建时间网格。"""

    if irregular_ratio > IRREGULAR_RATIO_THRESHOLD:
        return True
    if expected is None or len(timestamps) < 2:
        return False
    observed = timestamps.diff().dropna().dt.total_seconds().median()
    return bool(abs(float(observed) - target_interval) > max(target_interval * 0.05, 1e-6))


def _align_time_axis(
    dataframe: pd.DataFrame,
    sensors: list[str],
    labels: list[str],
    interval_seconds: float,
) -> pd.DataFrame:
    """按固定周期聚合时序，并按标签语义补齐重采样新增点。

    ``anomaly`` 等状态标签描述的是一个持续区间，因此新增点应继承前一个状态；
    ``changepoint`` 只在真实观测点成立，新增点必须保持为 0。未知标签不做前向传播，
    采用更保守的 0 填补，避免把不明确的业务字段误当成持续状态。
    """

    frequency = pd.to_timedelta(interval_seconds, unit="s")
    indexed = dataframe.set_index("datetime")
    sensor_frame = indexed[sensors].resample(frequency).mean()
    pieces = [sensor_frame]
    if labels:
        label_frame = indexed[labels].resample(frequency).max()
        for label in labels:
            normalized = label.casefold()
            if normalized in STATE_LABELS:
                # 只传播重采样产生的空位，不改变原始观测到的 0 状态。
                label_frame[label] = label_frame[label].ffill().fillna(0)
            elif normalized in POINT_LABELS:
                # 变点是瞬时标记，时间网格中没有真实观测的位置不能凭空生成变点。
                label_frame[label] = label_frame[label].fillna(0)
            else:
                # 对未登记语义的标签保持保守处理，并在代码中明确这一边界。
                label_frame[label] = label_frame[label].fillna(0)
        pieces.append(label_frame)
    aligned = pd.concat(pieces, axis=1).reset_index()
    return aligned[["datetime", *sensors, *labels]]


def _fill_sensor(series: pd.Series, sensor: str) -> tuple[pd.Series, dict[str, Any]]:
    """短缺口插值、长缺口保守填补，并记录实际使用方式。"""

    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    missing_before = int(numeric.isna().sum())
    if missing_before == 0:
        return numeric, {
            "sensor": sensor,
            "missing_before": 0,
            "filled": 0,
            "method": "none",
        }
    valid = numeric.dropna()
    if valid.empty:
        raise ValueError(f"传感器 {sensor} 整列缺失，无法建立可信输入。")

    # limit 限制线性插值只穿过短缺口，长缺口不伪造平滑趋势。
    filled = numeric.interpolate(
        method="linear",
        limit=MAX_SHORT_GAP,
        limit_direction="both",
        limit_area="inside",
    )
    short_filled = missing_before - int(filled.isna().sum())
    # 对长缺口仅延续最近可见水平，剩余边界缺失使用历史中位数兜底。
    filled = filled.ffill(limit=MAX_SHORT_GAP).bfill(limit=MAX_SHORT_GAP)
    filled = filled.fillna(float(valid.median()))
    return filled, {
        "sensor": sensor,
        "missing_before": missing_before,
        "filled": missing_before,
        "short_gap_linear_filled": short_filled,
        "fallback_filled": missing_before - short_filled,
        "method": "adaptive_linear_then_local_hold_then_median",
    }


def _noise_strategy(series: pd.Series, sensor: str) -> dict[str, Any]:
    """估计噪声强度，只决定模型抗噪方式，不删除原始尖峰。"""

    values = series.to_numpy(dtype=float)
    differences = np.diff(values)
    if not len(differences):
        ratio = 0.0
    else:
        center = float(np.median(differences))
        robust_noise = 1.4826 * float(np.median(np.abs(differences - center)))
        signal_scale = max(float(np.std(values)), float(np.ptp(values)) * 0.1, 1e-9)
        ratio = robust_noise / signal_scale
    level = "high" if ratio >= 0.8 else "medium" if ratio >= 0.3 else "low"
    return {
        "sensor": sensor,
        "noise_level": level,
        "normalized_difference_noise": round(ratio, 6),
        "strategy": "preserve_spikes_with_robust_model_scaling",
    }
