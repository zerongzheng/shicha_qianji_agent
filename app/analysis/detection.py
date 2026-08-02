"""可插拔的多变量工业时序异常检测器。

当前提供三种检测方式：

1. `mad`：滚动中位数与 MAD，擅长解释单传感器局部偏离；
2. `isolation_forest`：使用多传感器值、变化率和局部波动，识别联合工况异常；
3. `hybrid`：融合前两种证据，作为当前默认竞赛基线。

所有检测器使用相同输入输出协议。未来接入 AutoEncoder、LSTM-AE 或 TranAD 时，页面、
报告和 Agent 无需重写。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from app.models import AnalysisConfig, AnomalyEvent

DETECTOR_LABELS = {
    "mad": "稳健 MAD",
    "isolation_forest": "Isolation Forest",
    "hybrid": "时序-工况混合检测器",
}

# 健康基线的高分位统一映射到固定风险标尺。用户调整 threshold 时只改变告警决策，
# 不会反过来改变分数本身，保证不同阈值实验可以公平比较。
BASELINE_ALERT_SCORE = 4.5


@dataclass(frozen=True)
class DetectionOutput:
    """检测器的统一输出。"""

    detector_name: str
    sensor_scores: pd.DataFrame
    combined_score: pd.Series
    predicted_labels: pd.Series
    events: list[AnomalyEvent]


def detect_anomalies(
    dataframe: pd.DataFrame,
    sensor_columns: list[str],
    config: AnalysisConfig,
    healthy_reference: pd.DataFrame | None = None,
) -> DetectionOutput:
    """根据配置选择检测器并形成连续异常事件。"""

    clean_data = _prepare_sensor_data(dataframe[sensor_columns])
    clean_reference = (
        _prepare_sensor_data(healthy_reference[sensor_columns])
        if healthy_reference is not None
        else None
    )
    detector = config.detector.lower().strip()

    if detector == "mad":
        sensor_scores, combined_score = _mad_scores(clean_data, config, clean_reference)
    elif detector == "isolation_forest":
        sensor_scores, combined_score = _isolation_forest_scores(
            clean_data,
            config,
            clean_reference,
        )
    elif detector == "hybrid":
        sensor_scores, combined_score = _hybrid_scores(clean_data, config, clean_reference)
    else:
        choices = ", ".join(DETECTOR_LABELS)
        raise ValueError(f"不支持的检测器：{config.detector}，可选值为：{choices}")

    predicted_labels, events = apply_detection_threshold(
        dataframe=dataframe,
        sensor_scores=sensor_scores,
        combined_score=combined_score,
        config=config,
    )
    return DetectionOutput(
        detector_name=DETECTOR_LABELS[detector],
        sensor_scores=sensor_scores,
        combined_score=combined_score,
        predicted_labels=predicted_labels,
        events=events,
    )


def apply_detection_threshold(
    dataframe: pd.DataFrame,
    sensor_scores: pd.DataFrame,
    combined_score: pd.Series,
    config: AnalysisConfig,
) -> tuple[pd.Series, list[AnomalyEvent]]:
    """把连续风险分数转换为告警标签和异常事件。

    模型负责生成不随阈值变化的风险分数，本函数只负责告警决策。阈值调优时可以复用一次
    模型推理的结果，避免每尝试一个阈值就重新训练 Isolation Forest。
    """

    raw_labels = _hysteresis_labels(
        combined_score,
        high_threshold=config.threshold,
        low_threshold=config.threshold * 0.85,
        release_points=2,
    )
    predicted_labels = _remove_short_runs(raw_labels, config.min_event_length)
    events = _build_events(
        dataframe=dataframe,
        scores=sensor_scores,
        combined_score=combined_score,
        labels=predicted_labels,
        config=config,
    )
    return predicted_labels.astype(int), events


def _mad_scores(
    clean_data: pd.DataFrame,
    config: AnalysisConfig,
    healthy_reference: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """计算每个传感器相对于历史局部基线的稳健异常分数。"""

    reference = healthy_reference if healthy_reference is not None else clean_data
    scores = _raw_mad_sensor_scores(clean_data, config, reference)
    combined = scores.quantile(0.90, axis=1)

    if healthy_reference is not None:
        reference_scores = _raw_mad_sensor_scores(reference, config, reference)
        reference_combined = reference_scores.quantile(0.90, axis=1)
        combined = _calibrate_score(combined, reference_combined, config)
        calibration_ratio = combined / scores.quantile(0.90, axis=1).clip(lower=1e-9)
        scores = scores.mul(calibration_ratio, axis=0)
    return scores, combined.rename("mad_score")


def _raw_mad_sensor_scores(
    clean_data: pd.DataFrame,
    config: AnalysisConfig,
    scale_reference: pd.DataFrame,
) -> pd.DataFrame:
    """计算未校准的 MAD 分数，并用健康数据估计每个测点的噪声下限。"""

    scores = pd.DataFrame(index=clean_data.index)
    minimum_periods = max(5, config.rolling_window // 4)

    for column in clean_data.columns:
        series = clean_data[column]
        # 只使用当前点之前的历史窗口，避免持续异常被吸收进局部正常基线。
        rolling_median = series.shift(1).rolling(
            window=config.rolling_window,
            min_periods=minimum_periods,
        ).median()
        residual = (series - rolling_median).abs()
        rolling_mad = residual.shift(1).rolling(
            window=config.rolling_window,
            min_periods=minimum_periods,
        ).median()

        noise_floor = _sensor_noise_floor(scale_reference[column])
        local_scale = (1.4826 * rolling_mad).clip(lower=noise_floor)
        robust_score = residual / local_scale
        scores[column] = robust_score.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return scores


def _isolation_forest_scores(
    clean_data: pd.DataFrame,
    config: AnalysisConfig,
    healthy_reference: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """使用多变量工况特征训练 Isolation Forest 并生成可比较分数。"""

    features, sensor_feature_groups = _build_multivariate_features(clean_data, config)
    if healthy_reference is not None:
        training_features, _ = _build_multivariate_features(healthy_reference, config)
    else:
        training_end = _choose_training_end(len(features))
        training_features = features.iloc[:training_end]
    training_end = len(training_features)

    scaler = RobustScaler()
    scaled_training = scaler.fit_transform(training_features)
    scaled_all = scaler.transform(features)

    model = IsolationForest(
        n_estimators=240,
        max_samples=min(256, training_end),
        contamination="auto",
        random_state=config.random_state,
        n_jobs=-1,
    )
    model.fit(scaled_training)

    # decision_function 越小越异常。先用训练段分布转成稳健 z 分数，
    # 再统一映射到与 MAD 相近的“越大越异常”量纲。
    raw_anomaly = -model.decision_function(scaled_all)
    training_raw = -model.decision_function(scaled_training)
    center = float(np.median(training_raw))
    mad = float(np.median(np.abs(training_raw - center)))
    scale = max(1.4826 * mad, float(np.std(training_raw)), 1e-9)
    normalized_score = np.maximum((raw_anomaly - center) / scale, 0.0)
    training_normalized = np.maximum((training_raw - center) / scale, 0.0)
    calibration_quantile = max(0.95, min(0.999, 1.0 - config.contamination))
    calibration_value = float(np.quantile(training_normalized, calibration_quantile))
    score_multiplier = BASELINE_ALERT_SCORE / max(calibration_value, 1e-9)
    combined_score = pd.Series(
        normalized_score * score_multiplier,
        index=clean_data.index,
        name="isolation_forest_score",
    )

    # Isolation Forest 本身只给设备级分数。这里计算各传感器的稳健特征偏离，
    # 作为异常事件的归因证据，而不是伪造树模型内部特征重要度。
    sensor_scores = pd.DataFrame(index=clean_data.index)
    for sensor, columns in sensor_feature_groups.items():
        sensor_scores[sensor] = _feature_deviation_score(
            features[columns],
            baseline_features=training_features[columns],
        )
    return sensor_scores, combined_score


def _hybrid_scores(
    clean_data: pd.DataFrame,
    config: AnalysisConfig,
    healthy_reference: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """融合局部突变检测和多变量联合工况检测。"""

    mad_sensor_scores, mad_combined = _mad_scores(clean_data, config, healthy_reference)
    forest_sensor_scores, forest_combined = _isolation_forest_scores(
        clean_data,
        config,
        healthy_reference,
    )

    # 采用略偏向 MAD 的加权融合，保证突发故障有较快响应；
    # Isolation Forest 补充多传感器组合异常和工况偏离证据。
    combined_score = 0.60 * mad_combined + 0.40 * forest_combined
    sensor_scores = 0.60 * mad_sensor_scores + 0.40 * forest_sensor_scores
    return sensor_scores, combined_score.rename("hybrid_score")


def _build_multivariate_features(
    clean_data: pd.DataFrame,
    config: AnalysisConfig,
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """构造局部残差、变化率和波动三类动态特征。

    不直接使用绝对传感器值，因为不同设备、阀门和负载的正常工作点可能完全不同。
    动态特征更适合跨工况迁移，也能避免把正常工况差异误判成故障。
    """

    feature_columns: dict[str, pd.Series] = {}
    groups: dict[str, list[str]] = {}
    volatility_window = max(5, config.rolling_window // 5)
    minimum_periods = max(5, config.rolling_window // 4)

    for sensor in clean_data.columns:
        residual_name = f"{sensor}__residual"
        diff_name = f"{sensor}__diff"
        volatility_name = f"{sensor}__volatility"
        series = clean_data[sensor]
        local_baseline = series.shift(1).rolling(
            config.rolling_window,
            min_periods=minimum_periods,
        ).median()
        residual = (series - local_baseline).fillna(0.0)
        difference = series.diff().fillna(0.0)

        feature_columns[residual_name] = residual
        feature_columns[diff_name] = difference
        feature_columns[volatility_name] = (
            difference
            .rolling(volatility_window, min_periods=2)
            .std(ddof=0)
            .fillna(0.0)
        )
        groups[sensor] = [residual_name, diff_name, volatility_name]

    return pd.DataFrame(feature_columns, index=clean_data.index), groups


def _feature_deviation_score(
    features: pd.DataFrame,
    baseline_features: pd.DataFrame,
) -> pd.Series:
    """计算一组传感器特征相对训练基线的稳健偏离，用于事件归因。"""

    median = baseline_features.median()
    mad = (baseline_features - median).abs().median()
    floor = baseline_features.std(ddof=0).mul(0.02).clip(lower=1e-9)
    scale = (1.4826 * mad).clip(lower=floor)
    normalized = (features - median).abs() / scale
    return normalized.quantile(0.80, axis=1).replace([np.inf, -np.inf], 0.0).fillna(0.0)


def _sensor_noise_floor(series: pd.Series) -> float:
    """估计传感器可分辨的最小有效波动，避免量化平台造成分母接近零。"""

    values = series.dropna()
    if values.empty:
        return 1e-6
    differences = values.diff().abs()
    nonzero_differences = differences[differences > 0]
    diff_floor = float(nonzero_differences.quantile(0.25)) if not nonzero_differences.empty else 0.0
    iqr = float(values.quantile(0.75) - values.quantile(0.25))
    std_floor = float(values.std(ddof=0)) * 0.01
    return max(diff_floor * 0.5, iqr * 0.005, std_floor, 1e-6)


def _calibrate_score(
    target_score: pd.Series,
    healthy_score: pd.Series,
    config: AnalysisConfig,
) -> pd.Series:
    """让健康基线高分位对应统一阈值，获得跨文件可比较风险分数。"""

    quantile = max(0.95, min(0.999, 1.0 - config.contamination))
    reference_value = float(healthy_score.quantile(quantile))
    multiplier = BASELINE_ALERT_SCORE / max(reference_value, 1e-9)
    return target_score * multiplier


def _choose_training_end(row_count: int) -> int:
    """选择无监督模型的历史基线段，兼顾稳定性和短序列可用性。"""

    if row_count < 40:
        raise ValueError("Isolation Forest 至少需要 40 个时序点。")
    return min(max(40, int(row_count * 0.25)), row_count - 1)


def _prepare_sensor_data(sensor_data: pd.DataFrame) -> pd.DataFrame:
    """插值补齐少量缺失值，整列缺失时使用零作为最终兜底。"""

    prepared = sensor_data.interpolate(method="linear", limit_direction="both")
    return prepared.fillna(0.0)


def _remove_short_runs(labels: pd.Series, minimum_length: int) -> pd.Series:
    """删除过短的孤立异常点，降低工业噪声造成的误报。"""

    if minimum_length <= 1:
        return labels.astype(bool)

    result = labels.astype(bool).copy()
    group_ids = result.ne(result.shift()).cumsum()
    for indexes in result[result].groupby(group_ids[result]).groups.values():
        if len(indexes) < minimum_length:
            result.loc[indexes] = False
    return result


def _hysteresis_labels(
    scores: pd.Series,
    high_threshold: float,
    low_threshold: float,
    release_points: int,
) -> pd.Series:
    """使用双阈值迟滞减少同一异常过程中的告警反复开关。

    高阈值负责触发，触发后只有风险分数连续若干点低于低阈值才结束事件。
    这符合工业告警的“进入难、退出稳”逻辑，可以减少事件碎片化。
    """

    labels = pd.Series(False, index=scores.index)
    active = False
    below_count = 0
    for index, score in scores.items():
        if not active:
            if score >= high_threshold:
                active = True
                labels.at[index] = True
            continue

        if score >= low_threshold:
            labels.at[index] = True
            below_count = 0
            continue

        below_count += 1
        if below_count < release_points:
            labels.at[index] = True
        else:
            active = False
            below_count = 0
    return labels


def _build_events(
    dataframe: pd.DataFrame,
    scores: pd.DataFrame,
    combined_score: pd.Series,
    labels: pd.Series,
    config: AnalysisConfig,
) -> list[AnomalyEvent]:
    """把相距较近的异常点段合并成完整工业事件。"""

    true_indexes = list(dataframe.index[labels.astype(bool)])
    if not true_indexes:
        return []

    ranges: list[tuple[int, int]] = []
    start = previous = true_indexes[0]
    for current in true_indexes[1:]:
        if current - previous > config.merge_gap + 1:
            ranges.append((start, previous))
            start = current
        previous = current
    ranges.append((start, previous))

    events: list[AnomalyEvent] = []
    for start_index, end_index in ranges:
        event_scores = scores.loc[start_index:end_index]
        peak_score = float(combined_score.loc[start_index:end_index].max())
        max_sensor_scores = event_scores.max().sort_values(ascending=False)
        dominant_sensors = list(max_sensor_scores.head(3).index)
        events.append(
            AnomalyEvent(
                start_index=int(start_index),
                end_index=int(end_index),
                start_time=dataframe.at[start_index, "datetime"],
                end_time=dataframe.at[end_index, "datetime"],
                duration_points=int(end_index - start_index + 1),
                peak_score=peak_score,
                severity=_severity_from_score(peak_score, config.threshold),
                dominant_sensors=dominant_sensors,
                sensor_scores={
                    name: float(value) for name, value in max_sensor_scores.head(5).items()
                },
            )
        )
    return sorted(events, key=lambda event: event.peak_score, reverse=True)


def _severity_from_score(score: float, threshold: float) -> str:
    """依据异常分数相对阈值划分风险等级。"""

    ratio = score / max(threshold, 1e-9)
    if ratio >= 2.5:
        return "高风险"
    if ratio >= 1.5:
        return "中风险"
    return "低风险"
