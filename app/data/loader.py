"""工业时序数据加载与标准化。

当前重点适配 SKAB 的分号分隔 CSV，同时兼容常见逗号 CSV。这里完成的是格式清洗，
后续分析模块只接收统一的 DataFrame，因此未来对接企业数据库、消息队列或接口时，
只需要在本模块增加新的读取函数。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from app.data.device_profiles import (
    LoadedTimeSeries,
    apply_device_profile,
    build_device_context,
    select_device_profile,
)

TIME_COLUMN_CANDIDATES = ("datetime", "timestamp", "time", "date")
LABEL_COLUMNS = {"anomaly", "changepoint", "label", "target"}


def load_time_series(
    file_path: str | Path,
    device_profile_id: str | None = None,
) -> pd.DataFrame:
    """读取并标准化一份工业时序 CSV。

    返回的数据满足三个约定：
    1. 时间列统一命名为 `datetime`，并按时间升序排列；
    2. 传感器和标签列尽量转换为数值；
    3. 重复时间点只保留最后一条，防止评估和画图出现位置错乱。
    """

    return load_time_series_with_context(file_path, device_profile_id).dataframe


def load_time_series_with_context(
    file_path: str | Path,
    device_profile_id: str | None = None,
) -> LoadedTimeSeries:
    """读取工业时序 CSV，并返回设备配置匹配与字段映射上下文。

    显式传入 `device_profile_id` 时严格按该配置校验；未传入时先自动匹配，匹配失败则
    延续通用 CSV 模式。这个接口供分析主流程使用，原有 `load_time_series()` 继续只返回
    DataFrame，避免破坏实验脚本和预测接口。
    """

    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"找不到工业时序数据文件：{path}")
    if path.suffix.lower() != ".csv":
        raise ValueError("当前版本只支持 CSV 文件，后续可在数据接入层扩展其他格式。")

    delimiter = _detect_delimiter(path)
    dataframe = pd.read_csv(path, sep=delimiter, encoding="utf-8")
    if dataframe.empty:
        raise ValueError(f"数据文件为空：{path}")

    # 去掉列名前后的空格，避免企业导出文件中出现难以察觉的字段不一致。
    dataframe.columns = [str(column).strip() for column in dataframe.columns]
    selection = select_device_profile(dataframe.columns, device_profile_id)
    renamed_columns: dict[str, str] = {}
    if selection.profile is not None:
        dataframe, renamed_columns = apply_device_profile(dataframe, selection.profile)

    time_column = _find_time_column(dataframe.columns)
    if time_column != "datetime":
        dataframe = dataframe.rename(columns={time_column: "datetime"})

    dataframe["datetime"] = pd.to_datetime(dataframe["datetime"], errors="coerce")
    if dataframe["datetime"].isna().any():
        bad_rows = int(dataframe["datetime"].isna().sum())
        raise ValueError(f"时间列中有 {bad_rows} 行无法解析，请检查 datetime 格式。")

    for column in dataframe.columns:
        if column != "datetime":
            dataframe[column] = pd.to_numeric(dataframe[column], errors="coerce")

    dataframe = (
        dataframe.sort_values("datetime")
        .drop_duplicates(subset="datetime", keep="last")
        .reset_index(drop=True)
    )
    if not get_sensor_columns(dataframe):
        raise ValueError("没有找到可用于分析的数值传感器列。")
    return LoadedTimeSeries(
        dataframe=dataframe,
        profile=selection.profile,
        context=build_device_context(
            selection.profile,
            selection.match_mode,
            selection.match_score,
            renamed_columns,
        ),
    )


def get_sensor_columns(dataframe: pd.DataFrame) -> list[str]:
    """识别需要参与异常检测的传感器列。"""

    return [
        column
        for column in dataframe.select_dtypes(include="number").columns
        if column.lower() not in LABEL_COLUMNS
    ]


def get_label_columns(dataframe: pd.DataFrame) -> list[str]:
    """识别数据集自带的标签列，SKAB 通常包含 anomaly 和 changepoint。"""

    return [column for column in dataframe.columns if column.lower() in LABEL_COLUMNS]


def save_uploaded_file(uploaded_file: BinaryIO, target_dir: Path) -> Path:
    """保存 Streamlit 上传文件并返回本地路径。

    文件名只保留最后一级名称，防止上传对象携带目录跳转字符。
    """

    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(getattr(uploaded_file, "name", "uploaded.csv")).name
    target_path = target_dir / safe_name
    target_path.write_bytes(uploaded_file.getvalue())
    return target_path


def _detect_delimiter(path: Path) -> str:
    """从文件开头自动判断分号或逗号分隔符。"""

    with path.open("r", encoding="utf-8", newline="") as file:
        sample = file.read(4096)
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,").delimiter
    except csv.Error:
        # SKAB 默认使用分号；无法判断时以分号作为保守回退。
        return ";"


def _find_time_column(columns: pd.Index) -> str:
    """在常见时间字段名中寻找数据的时间列。"""

    normalized = {str(column).lower(): str(column) for column in columns}
    for candidate in TIME_COLUMN_CANDIDATES:
        if candidate in normalized:
            return normalized[candidate]
    raise ValueError("没有找到时间列，请使用 datetime、timestamp、time 或 date 作为列名。")
