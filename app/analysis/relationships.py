"""异常事件中的多传感器关联、相关性变化和时滞证据分析。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.models import AnomalyEvent


def analyze_event_relationships(
    dataframe: pd.DataFrame,
    sensor_columns: list[str],
    events: list[AnomalyEvent],
    context_points: int = 80,
    max_lag: int = 12,
) -> list[dict[str, Any]]:
    """为高风险事件生成多测点关系证据。

    每个事件选择主导传感器，比较事件前基线与事件期间的相关性，并在差分序列上搜索
    最大互相关时滞。输出用于提供排查线索，不直接宣称统计相关就是物理因果。
    """

    diagnostics: list[dict[str, Any]] = []
    # 关系证据是本地分析结果的一部分，不能因为页面展示数量限制而丢掉后续事件。
    # 前端和 HTTP 可视化层仍会按需截取，避免一次响应过大。
    for event_index, event in enumerate(events, start=1):
        selected = [name for name in event.dominant_sensors if name in sensor_columns]
        if len(selected) < 2:
            selected = _expand_event_sensors(dataframe, sensor_columns, event, selected)
        if len(selected) < 2:
            continue

        event_frame = _numeric_window(dataframe, selected, event.start_index, event.end_index)
        baseline_start = max(0, event.start_index - max(context_points, len(event_frame)))
        baseline_end = event.start_index - 1
        if baseline_end <= baseline_start:
            continue
        baseline_frame = _numeric_window(dataframe, selected, baseline_start, baseline_end)

        pair_records: list[dict[str, Any]] = []
        for left_index, left in enumerate(selected):
            for right in selected[left_index + 1 :]:
                baseline_corr = _safe_correlation(baseline_frame[left], baseline_frame[right])
                event_corr = _safe_correlation(event_frame[left], event_frame[right])
                lag, lag_corr = _best_lag(
                    event_frame[left].diff().dropna().to_numpy(dtype=float),
                    event_frame[right].diff().dropna().to_numpy(dtype=float),
                    max_lag=max_lag,
                )
                pair_records.append(
                    {
                        "传感器A": left,
                        "传感器B": right,
                        "事件前相关系数": round(baseline_corr, 4),
                        "事件期相关系数": round(event_corr, 4),
                        "相关性变化": round(event_corr - baseline_corr, 4),
                        "最佳时滞": lag,
                        "时滞相关系数": round(lag_corr, 4),
                        "时滞解释": _lag_explanation(left, right, lag),
                    }
                )

        pair_records.sort(
            key=lambda item: (
                abs(float(item["相关性变化"])),
                abs(float(item["时滞相关系数"])),
            ),
            reverse=True,
        )
        diagnostics.append(
            {
                "事件编号": event_index,
                "开始时间": event.start_time.isoformat(),
                "结束时间": event.end_time.isoformat(),
                "主导传感器": selected,
                "关系结论": _relationship_summary(pair_records),
                "重点关系": pair_records[:5],
                "使用边界": "相关性与时滞用于缩小排查范围，不能单独证明物理因果。",
            }
        )
    return diagnostics


def _expand_event_sensors(
    dataframe: pd.DataFrame,
    sensor_columns: list[str],
    event: AnomalyEvent,
    selected: list[str],
) -> list[str]:
    """主导测点不足两个时，按事件期间标准化波动补充候选传感器。"""

    event_values = dataframe.loc[event.start_index : event.end_index, sensor_columns]
    numeric = event_values.apply(pd.to_numeric, errors="coerce")
    variation = numeric.std(ddof=0).sort_values(ascending=False)
    result = list(selected)
    for sensor in variation.index:
        if sensor not in result:
            result.append(sensor)
        if len(result) >= 3:
            break
    return result


def _numeric_window(
    dataframe: pd.DataFrame,
    sensors: list[str],
    start_index: int,
    end_index: int,
) -> pd.DataFrame:
    """截取并补齐事件窗口中的数值数据。"""

    frame = dataframe.loc[start_index:end_index, sensors].apply(pd.to_numeric, errors="coerce")
    return frame.interpolate(limit_direction="both").fillna(0.0)


def _safe_correlation(left: pd.Series, right: pd.Series) -> float:
    """对短序列和常量序列返回稳定的 Pearson 相关系数。"""

    if len(left) < 3 or float(left.std(ddof=0)) < 1e-12 or float(right.std(ddof=0)) < 1e-12:
        return 0.0
    correlation = float(left.corr(right))
    return correlation if np.isfinite(correlation) else 0.0


def _best_lag(left: np.ndarray, right: np.ndarray, max_lag: int) -> tuple[int, float]:
    """搜索差分序列绝对相关性最高的领先或滞后采样点。"""

    if len(left) < 5 or len(right) < 5:
        return 0, 0.0
    limit = min(max_lag, len(left) // 3, len(right) // 3)
    best_lag = 0
    best_correlation = 0.0
    for lag in range(-limit, limit + 1):
        if lag < 0:
            aligned_left, aligned_right = left[-lag:], right[:lag]
        elif lag > 0:
            aligned_left, aligned_right = left[:-lag], right[lag:]
        else:
            aligned_left, aligned_right = left, right
        if len(aligned_left) < 4:
            continue
        left_std = float(np.std(aligned_left))
        right_std = float(np.std(aligned_right))
        if left_std < 1e-12 or right_std < 1e-12:
            continue
        correlation = float(np.corrcoef(aligned_left, aligned_right)[0, 1])
        if np.isfinite(correlation) and abs(correlation) > abs(best_correlation):
            best_lag, best_correlation = lag, correlation
    return best_lag, best_correlation


def _lag_explanation(left: str, right: str, lag: int) -> str:
    """把时滞符号转成工程人员可读描述。"""

    if lag > 0:
        return f"{left} 的变化领先 {right} 约 {lag} 个采样点"
    if lag < 0:
        return f"{right} 的变化领先 {left} 约 {-lag} 个采样点"
    return f"{left} 与 {right} 近似同步变化"


def _relationship_summary(records: list[dict[str, Any]]) -> str:
    """提炼事件内最显著的相关性和时滞变化。"""

    if not records:
        return "事件窗口不足以形成稳定的多传感器关系判断"
    strongest = records[0]
    left = strongest["传感器A"]
    right = strongest["传感器B"]
    change = float(strongest["相关性变化"])
    if abs(change) >= 0.5:
        return f"{left} 与 {right} 的相关性发生显著变化，并呈现{strongest['时滞解释']}"
    if abs(float(strongest["时滞相关系数"])) >= 0.7:
        return f"{left} 与 {right} 存在较强动态联动，{strongest['时滞解释']}"
    return "事件内多传感器存在一定联动，但尚不足以形成稳定传播方向判断"
