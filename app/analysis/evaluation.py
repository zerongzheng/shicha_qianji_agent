"""工业异常检测的点级、事件级和工况变点评估。"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from app.models import EvaluationMetrics


def evaluate_predictions(
    dataframe: pd.DataFrame,
    predicted_labels: pd.Series,
    anomaly_score: pd.Series,
    event_tolerance: int = 5,
    merge_gap: int = 5,
) -> EvaluationMetrics | None:
    """使用 SKAB 标签评估检测质量。

    点级指标衡量每个采样点是否判断正确；事件级指标衡量一次真实故障是否至少触发过一次
    有效告警；变点指标则评估误报是否集中在正常工况切换附近。
    """

    if "anomaly" not in dataframe.columns:
        return None

    actual = dataframe["anomaly"].fillna(0).astype(int).clip(0, 1)
    predicted = predicted_labels.astype(int).clip(0, 1)

    true_positive = int(((actual == 1) & (predicted == 1)).sum())
    false_positive = int(((actual == 0) & (predicted == 1)).sum())
    false_negative = int(((actual == 1) & (predicted == 0)).sum())
    true_negative = int(((actual == 0) & (predicted == 0)).sum())
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)
    f1_score = _safe_divide(2 * precision * recall, precision + recall)

    pr_auc = (
        float(average_precision_score(actual, anomaly_score))
        if actual.nunique() > 1
        else 0.0
    )

    actual_events = extract_binary_events(actual, merge_gap=0)
    predicted_events = extract_binary_events(predicted, merge_gap=merge_gap)
    matched_actual, matched_predicted, delays = _match_events(
        actual_events,
        predicted_events,
        tolerance=event_tolerance,
    )

    matched_event_count = len(matched_actual)
    if not actual_events and not predicted_events:
        event_precision = event_recall = event_f1 = 1.0
    else:
        event_precision = _safe_divide(matched_event_count, len(predicted_events))
        event_recall = _safe_divide(matched_event_count, len(actual_events))
        event_f1 = _safe_divide(
            2 * event_precision * event_recall,
            event_precision + event_recall,
        )

    false_event_indexes = [
        index for index in range(len(predicted_events)) if index not in matched_predicted
    ]
    changepoints = _changepoint_indexes(dataframe)
    changepoint_related = sum(
        _event_near_indexes(predicted_events[index], changepoints, event_tolerance)
        for index in false_event_indexes
    )

    return EvaluationMetrics(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        true_negative=true_negative,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
        pr_auc=pr_auc,
        actual_event_count=len(actual_events),
        predicted_event_count=len(predicted_events),
        matched_event_count=matched_event_count,
        event_precision=event_precision,
        event_recall=event_recall,
        event_f1_score=event_f1,
        mean_detection_delay=float(np.mean(delays)) if delays else None,
        false_positive_event_count=len(false_event_indexes),
        changepoint_related_false_events=changepoint_related,
        changepoint_false_event_rate=_safe_divide(
            changepoint_related,
            len(false_event_indexes),
        ),
    )


def extract_binary_events(labels: pd.Series, merge_gap: int = 0) -> list[tuple[int, int]]:
    """把 0/1 点标签转换为闭区间事件，并按告警规则合并短间隔。"""

    binary = labels.fillna(0).astype(int).clip(0, 1)
    indexes = list(binary.index[binary.astype(bool)])
    if not indexes:
        return []

    events: list[tuple[int, int]] = []
    start = previous = int(indexes[0])
    for raw_index in indexes[1:]:
        current = int(raw_index)
        if current > previous + merge_gap + 1:
            events.append((start, previous))
            start = current
        previous = current
    events.append((start, previous))
    return events


def _match_events(
    actual_events: list[tuple[int, int]],
    predicted_events: list[tuple[int, int]],
    tolerance: int,
) -> tuple[set[int], set[int], list[int]]:
    """按时间重叠一对一匹配事件，并计算首次检测延迟。"""

    matched_actual: set[int] = set()
    matched_predicted: set[int] = set()
    delays: list[int] = []

    for actual_index, actual_event in enumerate(actual_events):
        candidates: list[tuple[int, int]] = []
        for predicted_index, predicted_event in enumerate(predicted_events):
            if predicted_index in matched_predicted:
                continue
            if _events_overlap(actual_event, predicted_event, tolerance):
                delay = max(0, predicted_event[0] - actual_event[0])
                candidates.append((delay, predicted_index))

        if not candidates:
            continue
        delay, predicted_index = min(candidates, key=lambda item: item[0])
        matched_actual.add(actual_index)
        matched_predicted.add(predicted_index)
        delays.append(delay)

    return matched_actual, matched_predicted, delays


def _events_overlap(
    first: tuple[int, int],
    second: tuple[int, int],
    tolerance: int,
) -> bool:
    """允许少量边界误差判断两个事件是否代表同一次异常。"""

    return first[0] - tolerance <= second[1] and second[0] <= first[1] + tolerance


def _changepoint_indexes(dataframe: pd.DataFrame) -> list[int]:
    """读取 SKAB changepoint 标签位置。"""

    if "changepoint" not in dataframe.columns:
        return []
    mask = dataframe["changepoint"].fillna(0).astype(float) > 0
    return [int(index) for index in dataframe.index[mask]]


def _event_near_indexes(
    event: tuple[int, int],
    indexes: list[int],
    tolerance: int,
) -> bool:
    """判断误报事件是否靠近工况变点。"""

    return any(event[0] - tolerance <= index <= event[1] + tolerance for index in indexes)


def _safe_divide(numerator: float, denominator: float) -> float:
    """分母为零时返回 0，保证无事件文件也能稳定评估。"""

    return numerator / denominator if denominator else 0.0
