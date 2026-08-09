"""工业设备稳定工况与过渡过程识别。

工况识别只使用传感器时序，不读取 anomaly 或 changepoint 标签。稳定状态通过滚动中位水平
聚类得到，过渡过程通过当前窗口相对历史窗口的稳健变化强度得到。默认模式只为异常事件
补充工况上下文，不删除任何告警，便于在固定验证集上独立评价其收益和风险。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import RobustScaler

from app.models import AnalysisConfig, AnomalyEvent, OperatingRegimeResult


def analyze_operating_regimes(
    dataframe: pd.DataFrame,
    sensor_columns: list[str],
    events: list[AnomalyEvent],
    config: AnalysisConfig,
) -> OperatingRegimeResult:
    """识别稳定工况、过渡点，并说明异常事件是否与工况切换重合。"""

    clean = dataframe[sensor_columns].interpolate(limit_direction="both").ffill().bfill()
    window = _odd_window(config.regime_window, len(clean))
    level_features = clean.rolling(window, center=True, min_periods=max(3, window // 3)).median()
    level_features = level_features.ffill().bfill()
    scaled_levels = RobustScaler().fit_transform(level_features)
    labels = _cluster_regimes(scaled_levels, config.regime_max_states, config.random_state)
    labels = _stabilize_short_states(pd.Series(labels, index=dataframe.index), window)

    transition_score = _causal_transition_score(clean, window)
    nonzero = transition_score[transition_score > 0]
    quantile = min(max(config.regime_transition_quantile, 0.90), 0.999)
    threshold = float(nonzero.quantile(quantile)) if not nonzero.empty else float("inf")
    transition_mask = transition_score >= threshold
    transition_mask = _expand_mask(transition_mask, max(1, window // 6))

    segments = _build_segments(dataframe, labels, transition_mask)
    event_contexts = _build_event_contexts(events, labels, transition_mask, transition_score)
    return OperatingRegimeResult(
        regime_labels=labels.astype(int),
        transition_score=transition_score,
        transition_mask=transition_mask.astype(bool),
        state_count=int(labels.nunique()),
        segments=segments,
        event_contexts=event_contexts,
    )


def suppress_transition_only_events(
    result: OperatingRegimeResult,
    events: list[AnomalyEvent],
    labels: pd.Series,
    config: AnalysisConfig,
) -> tuple[pd.Series, list[AnomalyEvent], OperatingRegimeResult]:
    """仅抑制高度重合切换期且峰值较低的事件，高风险事件始终保留。"""

    if not config.suppress_transition_events:
        return labels, events, result
    contexts = {
        int(item["事件编号"]): item for item in result.event_contexts
    }
    kept_events: list[AnomalyEvent] = []
    suppressed = 0
    updated_labels = labels.astype(int).copy()
    for index, event in enumerate(events, start=1):
        context = contexts.get(index, {})
        overlap = float(context.get("过渡期重合率", 0.0))
        weak_peak = event.peak_score < config.threshold * config.regime_suppression_peak_ratio
        should_suppress = (
            event.severity == "低风险"
            and overlap >= config.regime_suppression_overlap
            and weak_peak
        )
        if should_suppress:
            updated_labels.loc[event.start_index : event.end_index] = 0
            suppressed += 1
        else:
            kept_events.append(event)
    updated_result = OperatingRegimeResult(
        regime_labels=result.regime_labels,
        transition_score=result.transition_score,
        transition_mask=result.transition_mask,
        state_count=result.state_count,
        segments=result.segments,
        event_contexts=_build_event_contexts(
            kept_events,
            result.regime_labels,
            result.transition_mask,
            result.transition_score,
        ),
        suppression_applied=True,
        suppressed_event_count=suppressed,
    )
    return updated_labels.astype(int), kept_events, updated_result


def _cluster_regimes(values: np.ndarray, max_states: int, random_state: int) -> np.ndarray:
    """在 1 到 max_states 中按轮廓系数选择稳定工况数量。"""

    sample_count = len(values)
    upper = min(max(1, max_states), 6, max(1, sample_count // 30))
    if upper < 2 or np.allclose(values, values[0]):
        return np.zeros(sample_count, dtype=int)
    sample_indexes = np.linspace(0, sample_count - 1, min(sample_count, 1200)).astype(int)
    best_labels = np.zeros(sample_count, dtype=int)
    best_score = -1.0
    for state_count in range(2, upper + 1):
        model = KMeans(n_clusters=state_count, n_init=10, random_state=random_state)
        labels = model.fit_predict(values)
        sampled_labels = labels[sample_indexes]
        if len(np.unique(sampled_labels)) < 2:
            continue
        score = float(silhouette_score(values[sample_indexes], sampled_labels))
        # 轮廓系数不足说明不存在清晰的多工况结构，保留单工况更可解释。
        if score > best_score and score >= 0.30:
            best_score = score
            best_labels = labels
    return _canonicalize_labels(best_labels, values)


def _canonicalize_labels(labels: np.ndarray, values: np.ndarray) -> np.ndarray:
    """按第一主特征中心排序工况编号，使重复运行后的编号语义稳定。"""

    unique = sorted(np.unique(labels))
    centers = {label: float(np.median(values[labels == label, 0])) for label in unique}
    mapping = {label: index for index, label in enumerate(sorted(unique, key=centers.get))}
    return np.asarray([mapping[int(label)] for label in labels], dtype=int)


def _causal_transition_score(dataframe: pd.DataFrame, window: int) -> pd.Series:
    """比较当前短窗口与此前历史窗口，只使用当前及过去数据识别工况跃迁。"""

    short = max(3, window // 3)
    recent = dataframe.rolling(short, min_periods=short).median()
    historical = dataframe.shift(short).rolling(window, min_periods=max(5, window // 2)).median()
    scale = dataframe.diff().abs().rolling(window, min_periods=max(5, window // 2)).median()
    global_floor = dataframe.diff().abs().median().replace(0, np.nan).fillna(1e-6)
    denominator = (1.4826 * scale).clip(lower=global_floor, axis=1)
    normalized = (recent - historical).abs().div(denominator)
    score = normalized.quantile(0.90, axis=1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return score.rename("regime_transition_score")


def _stabilize_short_states(labels: pd.Series, window: int) -> pd.Series:
    """用中心多数投票清除只持续几个采样点的聚类抖动。"""

    vote_window = max(3, min(window // 3, 15))
    if vote_window % 2 == 0:
        vote_window += 1
    return labels.rolling(vote_window, center=True, min_periods=1).apply(
        lambda values: float(pd.Series(values).mode().iloc[0]),
        raw=False,
    ).astype(int)


def _expand_mask(mask: pd.Series, radius: int) -> pd.Series:
    """将过渡点向前后扩展少量采样点，覆盖切换过程而不是单一尖峰。"""

    return mask.astype(int).rolling(radius * 2 + 1, center=True, min_periods=1).max().astype(bool)


def _build_segments(
    dataframe: pd.DataFrame,
    labels: pd.Series,
    transition_mask: pd.Series,
) -> list[dict[str, Any]]:
    """把逐点工况编号压缩为面向页面和报告的连续分段。"""

    segments: list[dict[str, Any]] = []
    groups = labels.ne(labels.shift()).cumsum()
    for indexes in labels.groupby(groups).groups.values():
        start = int(indexes[0])
        end = int(indexes[-1])
        segments.append(
            {
                "工况编号": int(labels.at[start]) + 1,
                "开始时间": dataframe.at[start, "datetime"],
                "结束时间": dataframe.at[end, "datetime"],
                "持续点数": end - start + 1,
                "过渡点占比": round(float(transition_mask.loc[start:end].mean()), 4),
            }
        )
    return segments


def _build_event_contexts(
    events: list[AnomalyEvent],
    labels: pd.Series,
    transition_mask: pd.Series,
    transition_score: pd.Series,
) -> list[dict[str, Any]]:
    """说明每个异常事件发生在哪个稳定工况以及是否靠近切换过程。"""

    contexts: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        event_labels = labels.loc[event.start_index : event.end_index]
        dominant = int(event_labels.mode().iloc[0]) + 1
        overlap = float(transition_mask.loc[event.start_index : event.end_index].mean())
        contexts.append(
            {
                "事件编号": index,
                "主要工况": f"工况 {dominant}",
                "过渡期重合率": round(overlap, 4),
                "峰值切换分数": round(
                    float(transition_score.loc[event.start_index : event.end_index].max()),
                    4,
                ),
                "工况判断": "工况切换期事件" if overlap >= 0.5 else "稳定工况内事件",
                "使用边界": "工况重合只能提示切换干扰，不能直接判定该事件为误报。",
            }
        )
    return contexts


def _odd_window(requested: int, row_count: int) -> int:
    """将窗口限制在数据长度内并保持奇数。"""

    maximum = max(5, min(row_count - 1 if row_count % 2 == 0 else row_count, 301))
    window = max(5, min(int(requested), maximum))
    return window if window % 2 == 1 else max(5, window - 1)
