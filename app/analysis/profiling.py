"""工业时序数据质量检查与统计画像。"""

from __future__ import annotations

import pandas as pd

from app.data.loader import get_label_columns, get_sensor_columns
from app.models import DataProfile, SensorProfile


def build_profile(dataframe: pd.DataFrame, source_name: str) -> DataProfile:
    """在检测异常前建立数据画像，为算法和报告提供可信输入。"""

    sensor_columns = get_sensor_columns(dataframe)
    sensor_profiles: list[SensorProfile] = []

    for column in sensor_columns:
        series = dataframe[column]
        missing_count = int(series.isna().sum())
        valid_values = series.dropna()
        sensor_profiles.append(
            SensorProfile(
                name=column,
                missing_count=missing_count,
                missing_rate=missing_count / len(dataframe),
                min_value=float(valid_values.min()),
                max_value=float(valid_values.max()),
                mean_value=float(valid_values.mean()),
                std_value=float(valid_values.std(ddof=0)),
            )
        )

    # 使用时间差中位数，可以减少少量丢点对采样周期估计的影响。
    deltas = dataframe["datetime"].diff().dropna().dt.total_seconds()
    sampling_seconds = float(deltas.median()) if not deltas.empty else None

    return DataProfile(
        source_name=source_name,
        row_count=len(dataframe),
        start_time=dataframe["datetime"].iloc[0],
        end_time=dataframe["datetime"].iloc[-1],
        sampling_seconds=sampling_seconds,
        sensor_columns=sensor_columns,
        label_columns=get_label_columns(dataframe),
        sensors=sensor_profiles,
        missing_total=sum(profile.missing_count for profile in sensor_profiles),
    )
