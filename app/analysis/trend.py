"""传感器短期趋势与漂移判断。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def analyze_recent_trends(
    dataframe: pd.DataFrame,
    sensor_columns: list[str],
    lookback_ratio: float = 0.20,
) -> dict[str, dict[str, Any]]:
    """比较末段工况和历史基线，识别持续上升、下降与均值漂移。"""

    row_count = len(dataframe)
    recent_size = max(20, int(row_count * lookback_ratio))
    recent_size = min(recent_size, row_count)
    result: dict[str, dict[str, Any]] = {}

    for column in sensor_columns:
        full_series = dataframe[column].interpolate(limit_direction="both").fillna(0.0)
        recent = full_series.iloc[-recent_size:]
        historical = full_series.iloc[:-recent_size]
        if historical.empty:
            historical = full_series

        x_axis = np.arange(len(recent), dtype=float)
        slope = float(np.polyfit(x_axis, recent.to_numpy(dtype=float), 1)[0])
        historical_std = float(historical.std(ddof=0))
        mean_shift_z = (
            float((recent.mean() - historical.mean()) / historical_std)
            if historical_std > 1e-9
            else 0.0
        )

        # 将斜率除以历史标准差，使不同量纲传感器可以用统一规则判断方向。
        normalized_slope = slope / historical_std if historical_std > 1e-9 else 0.0
        direction = "平稳"
        if normalized_slope > 0.015:
            direction = "持续上升"
        elif normalized_slope < -0.015:
            direction = "持续下降"

        risk = "正常"
        if abs(mean_shift_z) >= 3 or abs(normalized_slope) >= 0.04:
            risk = "需关注"
        if abs(mean_shift_z) >= 5 or abs(normalized_slope) >= 0.08:
            risk = "高风险"

        if risk != "正常" or direction != "平稳":
            result[column] = {
                "方向": direction,
                "风险": risk,
                "近期均值": round(float(recent.mean()), 4),
                "历史均值": round(float(historical.mean()), 4),
                "均值偏移标准差": round(mean_shift_z, 3),
            }

    # 只保留风险最明显的若干项，防止报告和大模型上下文变成统计量堆砌。
    return dict(
        sorted(
            result.items(),
            key=lambda item: abs(float(item[1]["均值偏移标准差"])),
            reverse=True,
        )[:6]
    )
