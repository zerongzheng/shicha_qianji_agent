"""可插拔的多变量工业时序异常检测器。

当前提供六种检测方式：

1. `mad`：滚动中位数与 MAD，擅长解释单传感器局部偏离；
2. `isolation_forest`：使用多传感器值、变化率和局部波动，识别联合工况异常；
3. `pca_reconstruction`：学习健康工况下多变量动态特征的低维结构，识别关系破坏；
4. `window_autoencoder`：通过滑动窗口非线性重构，识别复杂时序关系破坏；
5. `hybrid`：融合 MAD、Isolation Forest 和 PCA 重构证据，作为当前默认竞赛基线。
6. `time_frequency_relation`：融合时域窗口、频域形态和多传感器关系重构证据。

所有检测器使用相同输入输出协议。未来接入 AutoEncoder、LSTM-AE 或 TranAD 时，页面、
报告和 Agent 无需重写。
"""

from __future__ import annotations

import warnings
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import IsolationForest
from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import RobustScaler

from app.model_store import (
    AutoEncoderModelPackage,
    build_training_timestamp,
    load_autoencoder_package,
    save_autoencoder_package,
)
from app.models import AnalysisConfig, AnomalyEvent

DETECTOR_LABELS = {
    "mad": "稳健 MAD",
    "isolation_forest": "Isolation Forest",
    "pca_reconstruction": "PCA 多变量重构检测器",
    "window_autoencoder": "滑动窗口 AutoEncoder 检测器",
    "time_frequency_relation": "时频关系多路径检测器",
    "hybrid": "时序-工况混合检测器",
}

# 每个阈值都来自相同 17 文件验证集，随后在独立测试集冻结评价。集中维护可以防止
# CLI、API、页面和实验脚本使用不同默认值，导致同一模型在不同入口产生不一致结果。
DETECTOR_RECOMMENDED_THRESHOLDS = {
    "mad": 5.5,
    "isolation_forest": 5.0,
    "pca_reconstruction": 9.0,
    "window_autoencoder": 5.5,
    "time_frequency_relation": 3.5,
    "hybrid": 5.0,
}

# 事件后处理参数与分数阈值承担不同职责：阈值决定哪些点进入告警，最短持续时间与
# 合并间隔决定如何把告警点整理成可执行工单。当前时频模型的 3.5/12/30 来自 v6
# 联合验证集选择，并在独立测试集保持事件召回、事件 F1 与误报之间的平衡；其他模型仍保留原 3/5 基线。
DETECTOR_RECOMMENDED_EVENT_POLICIES = {
    detector: {"min_event_length": 3, "merge_gap": 5}
    for detector in DETECTOR_RECOMMENDED_THRESHOLDS
}
DETECTOR_RECOMMENDED_EVENT_POLICIES["time_frequency_relation"] = {
    "min_event_length": 12,
    "merge_gap": 30,
}


def recommended_event_policy(detector: str) -> tuple[int, int]:
    """返回检测器经验证的最短事件长度和合并间隔。"""

    policy = DETECTOR_RECOMMENDED_EVENT_POLICIES.get(
        detector,
        {"min_event_length": 3, "merge_gap": 5},
    )
    return int(policy["min_event_length"]), int(policy["merge_gap"])


# 健康基线的高分位统一映射到固定风险标尺。用户调整 threshold 时只改变告警决策，
# 不会反过来改变分数本身，保证不同阈值实验可以公平比较。
BASELINE_ALERT_SCORE = 4.5
AUTOENCODER_CACHE_SIZE = 8


@dataclass(frozen=True)
class _AutoEncoderArtifacts:
    """一次健康基线训练产生的可复用模型组件。"""

    scaler: RobustScaler
    model: MLPRegressor
    training_window_raw: np.ndarray
    sensor_training_raw: dict[str, np.ndarray]
    feature_columns: tuple[str, ...]
    window_size: int


# FastAPI 和 Streamlit 会在同一进程中连续分析多份文件。健康基线、传感器字段和模型参数
# 相同时无需重复训练 AutoEncoder；小型 LRU 缓存可以显著降低批量分析耗时，又不会持久化
# 用户原始数据。锁只保护缓存读写，耗时训练在锁外完成，避免阻塞其他请求。
_AUTOENCODER_CACHE: OrderedDict[str, _AutoEncoderArtifacts] = OrderedDict()
_AUTOENCODER_CACHE_LOCK = Lock()


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
    elif detector == "pca_reconstruction":
        sensor_scores, combined_score = _pca_reconstruction_scores(
            clean_data,
            config,
            clean_reference,
        )
    elif detector == "window_autoencoder":
        sensor_scores, combined_score = _window_autoencoder_scores(
            clean_data,
            config,
            clean_reference,
        )
    elif detector == "time_frequency_relation":
        sensor_scores, combined_score = _time_frequency_relation_scores(
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
    pca_sensor_scores, pca_combined = _pca_reconstruction_scores(
        clean_data,
        config,
        healthy_reference,
    )

    mad_weight, forest_weight, pca_weight = _normalized_hybrid_weights(config)
    # MAD 保证突发故障的响应速度，Isolation Forest 识别非线性联合工况，
    # PCA 重构误差补充“单点并不极端，但多传感器关系被破坏”的异常证据。
    combined_score = (
        mad_weight * mad_combined
        + forest_weight * forest_combined
        + pca_weight * pca_combined
    )
    sensor_scores = (
        mad_weight * mad_sensor_scores
        + forest_weight * forest_sensor_scores
        + pca_weight * pca_sensor_scores
    )
    return sensor_scores, combined_score.rename("hybrid_score")


def _normalized_hybrid_weights(config: AnalysisConfig) -> tuple[float, float, float]:
    """校验并归一化 Hybrid 权重，保证消融配置可比较且不会产生负权重。"""

    weights = np.asarray(
        [config.hybrid_mad_weight, config.hybrid_forest_weight, config.hybrid_pca_weight],
        dtype=float,
    )
    if np.any(weights < 0) or float(np.sum(weights)) <= 0:
        raise ValueError("Hybrid 权重必须为非负数，且总和大于 0。")
    normalized = weights / np.sum(weights)
    return tuple(float(value) for value in normalized)


def _pca_reconstruction_scores(
    clean_data: pd.DataFrame,
    config: AnalysisConfig,
    healthy_reference: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """使用健康动态特征的 PCA 重构误差识别多变量关系异常。

    PCA 在健康工况上学习多个传感器共同变化形成的低维子空间。当设备出现耦合关系破坏、
    某个测点偏离其他测点或新的动态模式时，特征无法被健康子空间准确重构，误差会上升。
    """

    features, sensor_feature_groups = _build_multivariate_features(clean_data, config)
    if healthy_reference is not None:
        training_features, _ = _build_multivariate_features(healthy_reference, config)
    else:
        training_end = _choose_training_end(len(features))
        training_features = features.iloc[:training_end]

    scaler = RobustScaler()
    scaled_training = scaler.fit_transform(training_features)
    scaled_all = scaler.transform(features)
    component_count = _choose_pca_components(scaled_training)
    model = PCA(n_components=component_count, random_state=config.random_state)
    model.fit(scaled_training)

    reconstructed_all = model.inverse_transform(model.transform(scaled_all))
    reconstructed_training = model.inverse_transform(model.transform(scaled_training))
    feature_errors = (scaled_all - reconstructed_all) ** 2
    training_errors = (scaled_training - reconstructed_training) ** 2

    raw_score = np.mean(feature_errors, axis=1)
    training_raw = np.mean(training_errors, axis=1)
    combined_score = _calibrate_raw_anomaly_score(
        raw_score,
        training_raw,
        features.index,
        config,
        name="pca_reconstruction_score",
    )

    sensor_scores = pd.DataFrame(index=features.index)
    column_positions = {name: index for index, name in enumerate(features.columns)}
    for sensor, columns in sensor_feature_groups.items():
        positions = [column_positions[column] for column in columns]
        sensor_raw = np.mean(feature_errors[:, positions], axis=1)
        sensor_training = np.mean(training_errors[:, positions], axis=1)
        sensor_scores[sensor] = _calibrate_raw_anomaly_score(
            sensor_raw,
            sensor_training,
            features.index,
            config,
            name=sensor,
        )
    return sensor_scores, combined_score


def _choose_pca_components(scaled_training: np.ndarray) -> int:
    """选择能够解释约 90% 健康变化、且至少保留一个残差方向的主成分数。"""

    maximum = min(scaled_training.shape[0] - 1, scaled_training.shape[1] - 1)
    if maximum < 1:
        raise ValueError("PCA 重构检测至少需要两个有效特征和足够的历史数据。")
    probe = PCA(n_components=maximum).fit(scaled_training)
    cumulative = np.cumsum(probe.explained_variance_ratio_)
    selected = int(np.searchsorted(cumulative, 0.90) + 1)
    return min(max(1, selected), maximum)


def _window_autoencoder_scores(
    clean_data: pd.DataFrame,
    config: AnalysisConfig,
    healthy_reference: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """使用滑动窗口 AutoEncoder 识别非线性时序关系异常。

    每个训练样本包含连续多个时刻的全部动态特征。MLP 的输入和目标相同，网络必须经过
    较窄的瓶颈层重构健康窗口；当传感器耦合方式、变化节奏或局部波形偏离健康规律时，
    重构误差会升高。这里使用 scikit-learn 是为了保持部署轻量，后续有更多企业数据后可
    在不改变检测器协议的情况下替换为 PyTorch LSTM-AE 或 Transformer AutoEncoder。
    """

    features, sensor_feature_groups = _build_multivariate_features(clean_data, config)
    if healthy_reference is not None:
        training_features, _ = _build_multivariate_features(healthy_reference, config)
    else:
        training_end = _choose_training_end(len(features))
        training_features = features.iloc[:training_end]

    window_size = max(4, int(config.autoencoder_window))
    minimum_training_windows = max(20, window_size)
    if len(training_features) - window_size + 1 < minimum_training_windows:
        # 较短文件无法提供足够窗口训练神经网络。回退 PCA 可保证产品链路继续运行，
        # 同时仍然保留多变量关系重构能力，而不是返回无意义的全零告警分数。
        return _pca_reconstruction_scores(clean_data, config, healthy_reference)

    artifacts = _get_or_train_autoencoder(training_features, config, window_size)
    if artifacts.feature_columns != tuple(features.columns):
        # 理论上 profile 已保证字段一致；显式校验可防止未来数据适配器改变列顺序后静默错位。
        raise ValueError("AutoEncoder 健康模型的特征字段与待分析数据不一致。")
    scaled_all = artifacts.scaler.transform(features)
    all_windows = _build_sliding_windows(scaled_all, window_size)
    all_reconstruction = artifacts.model.predict(all_windows)
    all_errors = (all_windows - all_reconstruction) ** 2
    window_raw = np.mean(all_errors, axis=1)
    point_raw = _map_window_values_to_endpoints(window_raw, len(features), window_size)
    training_point_raw = _map_window_values_to_endpoints(
        artifacts.training_window_raw,
        len(training_features),
        window_size,
    )
    combined_score = _calibrate_raw_anomaly_score(
        point_raw,
        training_point_raw,
        features.index,
        config,
        name="window_autoencoder_score",
    )

    # 网络输入的排列是“窗口内时刻 -> 特征”。将属于同一传感器的各动态特征误差聚合，
    # 再回填到采样点，得到能用于事件主导传感器排序的归因证据。
    sensor_scores = pd.DataFrame(index=features.index)
    feature_positions = {name: position for position, name in enumerate(features.columns)}
    feature_count = len(features.columns)
    for sensor, columns in sensor_feature_groups.items():
        base_positions = [feature_positions[column] for column in columns]
        window_positions = [
            offset * feature_count + position
            for offset in range(window_size)
            for position in base_positions
        ]
        sensor_window_raw = np.mean(all_errors[:, window_positions], axis=1)
        sensor_point_raw = _map_window_values_to_endpoints(
            sensor_window_raw,
            len(features),
            window_size,
        )
        sensor_training_points = _map_window_values_to_endpoints(
            artifacts.sensor_training_raw[sensor],
            len(training_features),
            window_size,
        )
        sensor_scores[sensor] = _calibrate_raw_anomaly_score(
            sensor_point_raw,
            sensor_training_points,
            features.index,
            config,
            name=sensor,
        )
    return sensor_scores, combined_score


def _time_frequency_relation_scores(
    clean_data: pd.DataFrame,
    config: AnalysisConfig,
    healthy_reference: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """融合时域窗口、频谱形态和传感器关系三类健康重构证据。

    - 时域路径复用窗口 AutoEncoder，学习非线性动态特征；
    - 频域路径重构每个窗口内的归一化功率谱，识别周期、频带能量和波形节奏变化；
    - 关系路径重构传感器相关矩阵，识别单点幅值不极端但耦合结构被破坏的异常。

    三条路径都只使用健康基线训练，并分别校准到统一风险标尺后再融合。这样消融实验可以
    直接将某一路权重设为零，而不需要改动页面、API 和事件构造逻辑。
    """

    time_weight, frequency_weight, relation_weight = _normalized_tfr_weights(config)
    zero_score = pd.Series(0.0, index=clean_data.index, dtype=float)
    zero_sensor_scores = pd.DataFrame(
        0.0,
        index=clean_data.index,
        columns=clean_data.columns,
    )
    if time_weight > 0:
        time_sensor_scores, time_score = _window_autoencoder_scores(
            clean_data,
            config,
            healthy_reference,
        )
    else:
        time_sensor_scores, time_score = zero_sensor_scores, zero_score
    reference = healthy_reference
    if reference is None:
        training_end = _choose_training_end(len(clean_data))
        reference = clean_data.iloc[:training_end]

    window_size = max(8, int(config.autoencoder_window))
    if len(reference) < window_size + 20 or len(clean_data) < window_size:
        if time_weight <= 0:
            raise ValueError("时频关系模型未启用时域路径时，需要足够数据构造频域或关系窗口。")
        return time_sensor_scores, time_score.rename("time_frequency_relation_score")

    if frequency_weight > 0:
        frequency_sensor, frequency_score = _frequency_reconstruction_scores(
            clean_data,
            reference,
            config,
            window_size,
        )
    else:
        frequency_sensor, frequency_score = zero_sensor_scores, zero_score
    if relation_weight > 0:
        relation_sensor, relation_score = _relation_reconstruction_scores(
            clean_data,
            reference,
            config,
            window_size,
        )
    else:
        relation_sensor, relation_score = zero_sensor_scores, zero_score
    combined = (
        time_weight * time_score
        + frequency_weight * frequency_score
        + relation_weight * relation_score
    ).rename("time_frequency_relation_score")
    sensors = list(clean_data.columns)
    sensor_scores = pd.DataFrame(index=clean_data.index)
    for sensor in sensors:
        sensor_scores[sensor] = (
            time_weight * time_sensor_scores[sensor]
            + frequency_weight * frequency_sensor[sensor]
            + relation_weight * relation_sensor[sensor]
        )
    return sensor_scores, combined


def _frequency_reconstruction_scores(
    clean_data: pd.DataFrame,
    healthy_reference: pd.DataFrame,
    config: AnalysisConfig,
    window_size: int,
) -> tuple[pd.DataFrame, pd.Series]:
    """用健康频谱 PCA 重构误差识别窗口内波形节奏变化。"""

    all_features = _build_frequency_windows(clean_data.to_numpy(dtype=float), window_size)
    training_features = _build_frequency_windows(
        healthy_reference.to_numpy(dtype=float),
        window_size,
    )
    scaler = RobustScaler()
    scaled_training = scaler.fit_transform(training_features)
    scaled_all = scaler.transform(all_features)
    model = _fit_reconstruction_pca(
        scaled_training,
        max_components=max(1, int(config.tfr_frequency_components)),
        random_state=config.random_state,
    )
    training_errors = (scaled_training - model.inverse_transform(model.transform(scaled_training))) ** 2
    all_errors = (scaled_all - model.inverse_transform(model.transform(scaled_all))) ** 2
    training_raw = np.mean(training_errors, axis=1)
    all_raw = np.mean(all_errors, axis=1)
    combined = _calibrate_window_score(
        all_raw,
        training_raw,
        clean_data.index,
        len(clean_data),
        len(healthy_reference),
        window_size,
        config,
        "frequency_reconstruction_score",
    )

    bins_per_sensor = all_features.shape[1] // len(clean_data.columns)
    sensor_scores = pd.DataFrame(index=clean_data.index)
    for sensor_index, sensor in enumerate(clean_data.columns):
        start = sensor_index * bins_per_sensor
        end = start + bins_per_sensor
        sensor_scores[sensor] = _calibrate_window_score(
            np.mean(all_errors[:, start:end], axis=1),
            np.mean(training_errors[:, start:end], axis=1),
            clean_data.index,
            len(clean_data),
            len(healthy_reference),
            window_size,
            config,
            str(sensor),
        )
    return sensor_scores, combined


def _relation_reconstruction_scores(
    clean_data: pd.DataFrame,
    healthy_reference: pd.DataFrame,
    config: AnalysisConfig,
    window_size: int,
) -> tuple[pd.DataFrame, pd.Series]:
    """用健康相关结构 PCA 重构误差识别多传感器耦合关系破坏。"""

    all_features = _build_relation_windows(clean_data.to_numpy(dtype=float), window_size)
    training_features = _build_relation_windows(
        healthy_reference.to_numpy(dtype=float),
        window_size,
    )
    if training_features.shape[1] == 1:
        # 关系特征只有一维时无法保留 PCA 残差方向。这既可能来自单传感器自相关，也可能
        # 来自双传感器的唯一相关系数；两种情况都直接计算其相对健康分布的稳健偏移。
        all_raw, training_raw = _single_feature_deviation(
            all_features[:, 0],
            training_features[:, 0],
        )
        combined = _calibrate_window_score(
            all_raw,
            training_raw,
            clean_data.index,
            len(clean_data),
            len(healthy_reference),
            window_size,
            config,
            "relation_reconstruction_score",
        )
        # 唯一关系由全部可用传感器共同贡献，因此每个传感器都获得同一关系异常证据。
        sensor_scores = pd.DataFrame(
            {sensor: combined for sensor in clean_data.columns},
            index=clean_data.index,
        )
        return sensor_scores, combined

    scaler = RobustScaler()
    scaled_training = scaler.fit_transform(training_features)
    scaled_all = scaler.transform(all_features)
    model = _fit_reconstruction_pca(
        scaled_training,
        max_components=max(1, int(config.tfr_relation_components)),
        random_state=config.random_state,
    )
    training_errors = (scaled_training - model.inverse_transform(model.transform(scaled_training))) ** 2
    all_errors = (scaled_all - model.inverse_transform(model.transform(scaled_all))) ** 2
    combined = _calibrate_window_score(
        np.mean(all_errors, axis=1),
        np.mean(training_errors, axis=1),
        clean_data.index,
        len(clean_data),
        len(healthy_reference),
        window_size,
        config,
        "relation_reconstruction_score",
    )

    sensor_scores = pd.DataFrame(index=clean_data.index)
    pairs = _sensor_pairs(len(clean_data.columns))
    for sensor_index, sensor in enumerate(clean_data.columns):
        positions = [
            position
            for position, (left, right) in enumerate(pairs)
            if sensor_index in {left, right}
        ]
        if positions:
            sensor_all = np.mean(all_errors[:, positions], axis=1)
            sensor_training = np.mean(training_errors[:, positions], axis=1)
        else:
            sensor_all = np.mean(all_errors, axis=1)
            sensor_training = np.mean(training_errors, axis=1)
        sensor_scores[sensor] = _calibrate_window_score(
            sensor_all,
            sensor_training,
            clean_data.index,
            len(clean_data),
            len(healthy_reference),
            window_size,
            config,
            str(sensor),
        )
    return sensor_scores, combined


def _build_frequency_windows(values: np.ndarray, window_size: int) -> np.ndarray:
    """为每个传感器提取窗口归一化功率谱，不保留量纲和直流工作点。"""

    windows = _build_window_tensor(values, window_size)
    centered = windows - np.mean(windows, axis=1, keepdims=True)
    scale = np.std(centered, axis=1, keepdims=True)
    normalized = centered / np.maximum(scale, 1e-8)
    power = np.abs(np.fft.rfft(normalized, axis=1)) ** 2
    if power.shape[1] > 1:
        power[:, 0, :] = 0.0
    power /= np.maximum(np.sum(power, axis=1, keepdims=True), 1e-12)
    # 按“传感器 -> 频率桶”展平，便于传感器级误差归因。
    return np.ascontiguousarray(np.swapaxes(power, 1, 2).reshape(len(power), -1))


def _build_relation_windows(values: np.ndarray, window_size: int) -> np.ndarray:
    """提取每个窗口的传感器相关结构上三角元素。"""

    windows = _build_window_tensor(values, window_size)
    sensor_count = values.shape[1]
    pairs = _sensor_pairs(sensor_count)
    if not pairs:
        # 单传感器数据没有横向关系，使用相邻差分自相关作为退化关系特征。
        signal = windows[:, :, 0]
        left = signal[:, :-1] - np.mean(signal[:, :-1], axis=1, keepdims=True)
        right = signal[:, 1:] - np.mean(signal[:, 1:], axis=1, keepdims=True)
        denominator = np.linalg.norm(left, axis=1) * np.linalg.norm(right, axis=1)
        correlation = np.sum(left * right, axis=1) / np.maximum(denominator, 1e-12)
        return correlation[:, None]

    centered = windows - np.mean(windows, axis=1, keepdims=True)
    norm = np.linalg.norm(centered, axis=1)
    features = []
    for left, right in pairs:
        numerator = np.sum(centered[:, :, left] * centered[:, :, right], axis=1)
        denominator = norm[:, left] * norm[:, right]
        features.append(numerator / np.maximum(denominator, 1e-12))
    return np.column_stack(features)


def _build_window_tensor(values: np.ndarray, window_size: int) -> np.ndarray:
    """构造形状为“窗口数、时间点、传感器数”的因果窗口张量。"""

    if values.ndim != 2 or len(values) < window_size:
        raise ValueError("时频关系窗口长度不能超过可用时序数据长度。")
    windows = np.lib.stride_tricks.sliding_window_view(values, window_size, axis=0)
    return np.ascontiguousarray(np.swapaxes(windows, 1, 2))


def _fit_reconstruction_pca(
    training_features: np.ndarray,
    max_components: int,
    random_state: int,
) -> PCA:
    """保留至少一个残差方向，避免 PCA 将训练特征完全复制。"""

    maximum = max(
        min(training_features.shape[0] - 1, training_features.shape[1] - 1),
        1,
    )
    component_count = min(max(1, max_components), maximum)
    return PCA(n_components=component_count, random_state=random_state).fit(training_features)


def _single_feature_deviation(
    all_values: np.ndarray,
    training_values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """计算单个窗口特征相对健康中心的绝对偏移，供后续统一风险校准。"""

    center = float(np.median(training_values))
    return np.abs(all_values - center), np.abs(training_values - center)


def _calibrate_window_score(
    all_window_raw: np.ndarray,
    training_window_raw: np.ndarray,
    index: pd.Index,
    point_count: int,
    training_point_count: int,
    window_size: int,
    config: AnalysisConfig,
    name: str,
) -> pd.Series:
    """将窗口误差因果回填到结束点，再校准为统一风险分数。"""

    all_points = _map_window_values_to_endpoints(all_window_raw, point_count, window_size)
    training_points = _map_window_values_to_endpoints(
        training_window_raw,
        training_point_count,
        window_size,
    )
    return _calibrate_raw_anomaly_score(
        all_points,
        training_points,
        index,
        config,
        name,
    )


def _sensor_pairs(sensor_count: int) -> list[tuple[int, int]]:
    """返回相关矩阵上三角中的传感器组合。"""

    return [
        (left, right)
        for left in range(sensor_count)
        for right in range(left + 1, sensor_count)
    ]


def _normalized_tfr_weights(config: AnalysisConfig) -> tuple[float, float, float]:
    """校验并归一化时域、频域和关系分支权重。"""

    weights = np.asarray(
        [config.tfr_time_weight, config.tfr_frequency_weight, config.tfr_relation_weight],
        dtype=float,
    )
    if np.any(weights < 0) or float(np.sum(weights)) <= 0:
        raise ValueError("时频关系路径权重必须为非负数，且总和大于 0。")
    normalized = weights / np.sum(weights)
    return tuple(float(value) for value in normalized)


def _get_or_train_autoencoder(
    training_features: pd.DataFrame,
    config: AnalysisConfig,
    window_size: int,
) -> _AutoEncoderArtifacts:
    """复用相同健康基线的 AutoEncoder，未命中时完成一次训练。"""

    cache_key = _autoencoder_cache_key(training_features, config, window_size)
    with _AUTOENCODER_CACHE_LOCK:
        cached = _AUTOENCODER_CACHE.get(cache_key)
        if cached is not None:
            _AUTOENCODER_CACHE.move_to_end(cache_key)
            return cached

    persisted = load_autoencoder_package(cache_key)
    if persisted is not None:
        artifacts = _AutoEncoderArtifacts(
            scaler=persisted.scaler,
            model=persisted.model,
            training_window_raw=persisted.training_window_raw,
            sensor_training_raw=persisted.sensor_training_raw,
            feature_columns=persisted.feature_columns,
            window_size=persisted.window_size,
        )
        _remember_autoencoder(cache_key, artifacts)
        return artifacts

    scaler = RobustScaler()
    scaled_training = scaler.fit_transform(training_features)
    training_windows = _build_sliding_windows(scaled_training, window_size)
    # 企业健康基线可能包含数十万采样点。等距采样训练窗口控制训练开销，同时覆盖完整工况。
    fit_windows = _sample_training_windows(
        training_windows,
        max_windows=max(100, int(config.autoencoder_max_training_windows)),
    )
    input_width = fit_windows.shape[1]
    hidden = min(max(8, int(config.autoencoder_hidden)), max(8, input_width - 1))
    bottleneck = min(max(2, int(config.autoencoder_bottleneck)), hidden - 1)
    model = MLPRegressor(
        hidden_layer_sizes=(hidden, bottleneck, hidden),
        activation="relu",
        solver="adam",
        alpha=1e-4,
        batch_size=min(128, max(1, int(len(fit_windows) * 0.80))),
        learning_rate_init=1e-3,
        max_iter=max(50, int(config.autoencoder_max_iter)),
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=15,
        random_state=config.random_state,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=ConvergenceWarning)
        model.fit(fit_windows, fit_windows)
    training_reconstruction = model.predict(training_windows)
    training_errors = (training_windows - training_reconstruction) ** 2
    artifacts = _AutoEncoderArtifacts(
        scaler=scaler,
        model=model,
        training_window_raw=np.mean(training_errors, axis=1),
        sensor_training_raw=_aggregate_sensor_window_errors(
            training_errors,
            training_features.columns,
            window_size,
        ),
        feature_columns=tuple(training_features.columns),
        window_size=window_size,
    )

    # 模型训练成功后才写入磁盘。磁盘仓库是性能优化，不应因为只读部署目录而中断分析。
    try:
        save_autoencoder_package(
            AutoEncoderModelPackage(
                cache_key=cache_key,
                scaler=artifacts.scaler,
                model=artifacts.model,
                training_window_raw=artifacts.training_window_raw,
                sensor_training_raw=artifacts.sensor_training_raw,
                feature_columns=artifacts.feature_columns,
                window_size=artifacts.window_size,
                trained_at=build_training_timestamp(),
                training_window_count=len(training_windows),
            )
        )
    except OSError:
        pass

    with _AUTOENCODER_CACHE_LOCK:
        # 并发请求可能训练出相同模型；保留先完成写入的对象，确保后续复用稳定。
        existing = _AUTOENCODER_CACHE.get(cache_key)
        if existing is not None:
            _AUTOENCODER_CACHE.move_to_end(cache_key)
            return existing
        _AUTOENCODER_CACHE[cache_key] = artifacts
        while len(_AUTOENCODER_CACHE) > AUTOENCODER_CACHE_SIZE:
            _AUTOENCODER_CACHE.popitem(last=False)
    return artifacts


def _remember_autoencoder(cache_key: str, artifacts: _AutoEncoderArtifacts) -> None:
    """把磁盘恢复的模型放入进程内 LRU 缓存。"""

    with _AUTOENCODER_CACHE_LOCK:
        _AUTOENCODER_CACHE[cache_key] = artifacts
        _AUTOENCODER_CACHE.move_to_end(cache_key)
        while len(_AUTOENCODER_CACHE) > AUTOENCODER_CACHE_SIZE:
            _AUTOENCODER_CACHE.popitem(last=False)


def _aggregate_sensor_window_errors(
    window_errors: np.ndarray,
    feature_columns: pd.Index,
    window_size: int,
) -> dict[str, np.ndarray]:
    """把完整窗口误差压缩为每个传感器的健康校准序列。"""

    feature_names = [str(column) for column in feature_columns]
    sensor_names = list(dict.fromkeys(name.split("__", 1)[0] for name in feature_names))
    feature_count = len(feature_names)
    result: dict[str, np.ndarray] = {}
    for sensor in sensor_names:
        base_positions = [
            position
            for position, feature_name in enumerate(feature_names)
            if feature_name.startswith(f"{sensor}__")
        ]
        window_positions = [
            offset * feature_count + position
            for offset in range(window_size)
            for position in base_positions
        ]
        result[sensor] = np.mean(window_errors[:, window_positions], axis=1)
    return result


def _autoencoder_cache_key(
    training_features: pd.DataFrame,
    config: AnalysisConfig,
    window_size: int,
) -> str:
    """根据健康特征内容、字段和模型参数生成不可逆缓存键。"""

    values = np.ascontiguousarray(training_features.to_numpy(dtype=np.float64))
    digest = sha256()
    digest.update(values.tobytes())
    digest.update("\x1f".join(str(column) for column in training_features.columns).encode("utf-8"))
    parameters: tuple[Any, ...] = (
        window_size,
        config.autoencoder_hidden,
        config.autoencoder_bottleneck,
        config.autoencoder_max_iter,
        config.autoencoder_max_training_windows,
        config.random_state,
    )
    digest.update(repr(parameters).encode("ascii"))
    return digest.hexdigest()


def clear_autoencoder_cache() -> None:
    """清空进程内健康模型缓存，供测试和模型版本切换使用。"""

    with _AUTOENCODER_CACHE_LOCK:
        _AUTOENCODER_CACHE.clear()


def _build_sliding_windows(values: np.ndarray, window_size: int) -> np.ndarray:
    """把二维时序特征转换为按时间连续的展平窗口。"""

    if values.ndim != 2 or len(values) < window_size:
        raise ValueError("AutoEncoder 窗口长度不能超过可用时序数据长度。")
    windows = np.lib.stride_tricks.sliding_window_view(values, window_size, axis=0)
    # NumPy 对 axis=0 的窗口结果形状为 (窗口数, 特征数, 窗口长度)，先调整为
    # (窗口数, 窗口长度, 特征数)，确保后续传感器位置映射与时间顺序一致。
    windows = np.swapaxes(windows, 1, 2)
    return np.ascontiguousarray(windows.reshape(len(windows), -1))


def _sample_training_windows(windows: np.ndarray, max_windows: int) -> np.ndarray:
    """等距抽取训练窗口，在控制开销的同时覆盖完整健康工况。"""

    if len(windows) <= max_windows:
        return windows
    positions = np.linspace(0, len(windows) - 1, max_windows, dtype=int)
    return windows[positions]


def _map_window_values_to_endpoints(
    window_values: np.ndarray,
    point_count: int,
    window_size: int,
) -> np.ndarray:
    """把窗口异常量记录在窗口结束点，保证在线告警不使用未来数据。"""

    if len(window_values) != point_count - window_size + 1:
        raise ValueError("窗口异常量数量与原始时序长度不匹配。")
    point_values = np.zeros(point_count, dtype=float)
    point_values[window_size - 1 :] = np.asarray(window_values, dtype=float)
    return point_values


def _calibrate_raw_anomaly_score(
    raw_score: np.ndarray,
    training_raw: np.ndarray,
    index: pd.Index,
    config: AnalysisConfig,
    name: str,
) -> pd.Series:
    """把任意非负原始异常量校准到项目统一的风险分数标尺。"""

    center = float(np.median(training_raw))
    mad = float(np.median(np.abs(training_raw - center)))
    scale = max(1.4826 * mad, float(np.std(training_raw)), 1e-12)
    normalized = np.maximum((raw_score - center) / scale, 0.0)
    training_normalized = np.maximum((training_raw - center) / scale, 0.0)
    quantile = max(0.95, min(0.999, 1.0 - config.contamination))
    calibration_value = float(np.quantile(training_normalized, quantile))
    multiplier = BASELINE_ALERT_SCORE / max(calibration_value, 1e-12)
    # 风险分数用于阈值和排序，不需要无限增长。限制极端上界可以避免高度相关的
    # 健康基线在关系突然破坏时产生上万分，影响报告可读性和模型融合稳定性。
    calibrated = np.minimum(normalized * multiplier, BASELINE_ALERT_SCORE * 20)
    return pd.Series(calibrated, index=index, name=name)


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
