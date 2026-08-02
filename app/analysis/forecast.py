"""工业时序多模型预测、滚动回测和不确定度评估。

本模块不依赖大模型。它为每个传感器训练多种可解释候选模型，使用严格按时间顺序
开展的滚动回测选择最优模型，再生成未来预测、预测区间和频域画像。这样既避免未来
数据泄漏，也能向评委和工程人员说明“为什么选择当前模型”。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ForecastFunction = Callable[[np.ndarray, int, int], np.ndarray]

MODEL_LABELS = {
    "persistence": "最近值持续模型",
    "moving_average": "指数平滑模型",
    "linear_trend": "局部线性趋势模型",
    "lag_ridge": "滞后特征岭回归模型",
    "time_frequency_ridge": "时频特征增强岭回归模型",
}
DEFAULT_MODELS = tuple(MODEL_LABELS)


def forecast_sensors(
    dataframe: pd.DataFrame,
    sensor_columns: list[str],
    horizon: int = 30,
    lookback: int = 120,
    holdout: int = 30,
    models: list[str] | tuple[str, ...] | None = None,
) -> dict[str, dict[str, Any]]:
    """为每个传感器自动选择预测模型，并返回未来曲线和量化证据。

    模型选择只使用历史滚动回测结果。数据较短或复杂模型训练失败时，系统会保留
    可运行的简单基线，不会因为单个候选模型失败而中断整份工业数据分析。
    """

    horizon = max(1, int(horizon))
    lookback = max(30, int(lookback))
    holdout = max(5, int(holdout))
    selected_models = _validate_models(models)
    timestamps = pd.to_datetime(dataframe["datetime"])
    results: dict[str, dict[str, Any]] = {}

    for sensor in sensor_columns:
        series = pd.to_numeric(dataframe[sensor], errors="coerce")
        series = series.interpolate(limit_direction="both").fillna(0.0)
        if len(series) < 20:
            continue

        values = series.to_numpy(dtype=float)
        model_scores = rolling_backtest(values, selected_models, lookback, holdout)
        valid_scores = {
            name: metrics
            for name, metrics in model_scores.items()
            if metrics.get("RMSE") is not None
        }
        if not valid_scores:
            continue

        # RMSE 对较大误差更敏感，适合工业风险预测；相同时再用 MAE 保持稳定排序。
        best_model = min(
            valid_scores,
            key=lambda name: (
                float(valid_scores[name]["RMSE"]),
                float(valid_scores[name]["MAE"]),
            ),
        )
        history = values[-min(lookback, len(values)) :]
        candidate_predictions: dict[str, np.ndarray] = {}
        for model_name in valid_scores:
            try:
                candidate = _forecast_with_model(
                    model_name, history, horizon, lookback
                )
                # 递归模型可能在某些近乎常量或强趋势窗口上数值发散。失稳曲线不应
                # 参与模型分歧和预测区间，否则即便它未被选中也会污染最终结论。
                if _is_stable_forecast(history, candidate):
                    candidate_predictions[model_name] = candidate
            except (ValueError, np.linalg.LinAlgError):
                # 最终拟合失败的候选不会影响已成功模型，选择时改用剩余候选。
                continue
        if not candidate_predictions:
            continue
        if best_model not in candidate_predictions:
            best_model = min(
                candidate_predictions,
                key=lambda name: float(valid_scores[name]["RMSE"]),
            )

        predictions = candidate_predictions[best_model]
        uncertainty = _prediction_interval(
            predictions=predictions,
            candidate_predictions=candidate_predictions,
            metrics=valid_scores[best_model],
            history=history,
        )
        lower = uncertainty.pop("lower")
        upper = uncertainty.pop("upper")
        baseline_mean = float(np.mean(history))
        baseline_std = max(float(np.std(history)), 1e-9)
        end_z = float((predictions[-1] - baseline_mean) / baseline_std)

        results[sensor] = {
            "传感器": sensor,
            "模型": best_model,
            "模型名称": MODEL_LABELS[best_model],
            "模型候选": list(valid_scores),
            "选择依据": "滚动回测 RMSE 最低，RMSE 相同时比较 MAE",
            "候选模型回测": valid_scores,
            "预测步数": horizon,
            "方向": _direction(float(predictions[-1] - values[-1]), baseline_std),
            "风险": _forecast_risk(
                predictions, lower, upper, baseline_mean, baseline_std, uncertainty
            ),
            "当前值": round(float(values[-1]), 6),
            "预测末值": round(float(predictions[-1]), 6),
            "预测末值偏移标准差": round(end_z, 4),
            "预测值": _rounded_list(predictions),
            "下界": _rounded_list(lower),
            "上界": _rounded_list(upper),
            "预测时间": [item.isoformat() for item in _future_timestamps(timestamps, horizon)],
            "回测": valid_scores[best_model],
            "频域特征": build_time_frequency_features(history),
            "不确定度": uncertainty,
        }
    return results


def rolling_backtest(
    values: np.ndarray,
    models: list[str] | tuple[str, ...] | None = None,
    lookback: int = 120,
    holdout: int = 30,
    max_folds: int = 5,
) -> dict[str, dict[str, float | int | None]]:
    """按时间顺序执行多折滚动回测，返回每个候选模型的误差。

    每一折只能看到预测起点之前的数据。验证窗口按连续块向前滚动，因此不会把未来
    真实值作为特征，也不会随机打乱工业时序。
    """

    values = np.asarray(values, dtype=float)
    selected_models = _validate_models(models)
    validation_size = min(max(5, int(holdout)), max(5, len(values) // 3))
    if len(values) <= validation_size + 20:
        return {name: _empty_metrics() for name in selected_models}

    validation_start = len(values) - validation_size
    fold_count = min(max_folds, max(2, validation_size // 5))
    boundaries = np.linspace(validation_start, len(values), fold_count + 1, dtype=int)
    predictions: dict[str, list[float]] = {name: [] for name in selected_models}
    actuals: dict[str, list[float]] = {name: [] for name in selected_models}
    failures: dict[str, int] = {name: 0 for name in selected_models}

    for fold_index in range(fold_count):
        origin = int(boundaries[fold_index])
        end = int(boundaries[fold_index + 1])
        if end <= origin:
            continue
        history = values[max(0, origin - lookback) : origin]
        actual = values[origin:end]
        for model_name in selected_models:
            try:
                predicted = _forecast_with_model(model_name, history, len(actual), lookback)
            except (ValueError, np.linalg.LinAlgError):
                failures[model_name] += 1
                continue
            predictions[model_name].extend(predicted.tolist())
            actuals[model_name].extend(actual.tolist())

    scores: dict[str, dict[str, float | int | None]] = {}
    for model_name in selected_models:
        if not predictions[model_name]:
            scores[model_name] = _empty_metrics(failures[model_name])
            continue
        predicted = np.asarray(predictions[model_name], dtype=float)
        actual = np.asarray(actuals[model_name], dtype=float)
        errors = predicted - actual
        denominator = np.maximum(np.abs(actual), max(float(np.std(actual)) * 0.1, 1e-6))
        scores[model_name] = {
            "样本数": len(actual),
            "折数": int(fold_count - failures[model_name]),
            "MAE": round(float(np.mean(np.abs(errors))), 6),
            "RMSE": round(float(np.sqrt(np.mean(errors**2))), 6),
            "MAPE": round(float(np.mean(np.abs(errors) / denominator)), 6),
            "残差标准差": round(float(np.std(errors)), 6),
            "残差绝对值95分位": round(float(np.quantile(np.abs(errors), 0.95)), 6),
        }
    return scores


def build_time_frequency_features(values: np.ndarray) -> dict[str, float]:
    """提取时域趋势和频域能量特征，用于模型输入与结果解释。"""

    values = np.asarray(values, dtype=float)
    if len(values) < 4:
        return {
            "主频": 0.0,
            "低频能量占比": 0.0,
            "高频能量占比": 0.0,
            "谱熵": 0.0,
            "频域峰值": 0.0,
        }

    centered = values - np.mean(values)
    # 先标准化再计算 FFT，避免量纲很大的工业信号在平方能量时溢出。
    scale = max(float(np.std(centered)), float(np.max(np.abs(centered))) * 1e-6, 1e-12)
    centered = centered / scale
    spectrum = np.abs(np.fft.rfft(centered)) ** 2
    frequencies = np.fft.rfftfreq(len(centered), d=1.0)
    if len(spectrum) > 1:
        spectrum[0] = 0.0
    total_energy = max(float(np.sum(spectrum)), 1e-12)
    dominant_index = int(np.argmax(spectrum))
    split_index = max(1, len(spectrum) // 3)
    probabilities = spectrum / total_energy
    nonzero = probabilities[probabilities > 0]
    entropy_scale = np.log(max(len(probabilities), 2))
    spectral_entropy = -float(np.sum(nonzero * np.log(nonzero))) / entropy_scale

    return {
        "主频": round(float(frequencies[dominant_index]), 6),
        "低频能量占比": round(float(np.sum(spectrum[1:split_index]) / total_energy), 6),
        "高频能量占比": round(float(np.sum(spectrum[split_index:]) / total_energy), 6),
        "谱熵": round(float(spectral_entropy), 6),
        "频域峰值": round(float(np.sqrt(spectrum[dominant_index])), 6),
    }


def _forecast_with_model(
    model_name: str,
    history: np.ndarray,
    horizon: int,
    lookback: int,
) -> np.ndarray:
    """统一调度候选模型，保证回测和未来预测走完全相同的代码路径。"""

    functions: dict[str, ForecastFunction] = {
        "persistence": _persistence_forecast,
        "moving_average": _moving_average_forecast,
        "linear_trend": _linear_trend_forecast,
        "lag_ridge": _lag_ridge_forecast,
        "time_frequency_ridge": _time_frequency_ridge_forecast,
    }
    return functions[model_name](np.asarray(history, dtype=float), horizon, lookback)


def _persistence_forecast(history: np.ndarray, horizon: int, _: int) -> np.ndarray:
    """最近值持续模型，是所有复杂模型必须超过的朴素基线。"""

    return np.repeat(float(history[-1]), horizon)


def _moving_average_forecast(history: np.ndarray, horizon: int, _: int) -> np.ndarray:
    """用指数平滑估计当前水平，适合平稳或缓慢变化的工业信号。"""

    alpha = 0.3
    level = float(history[0])
    for value in history[1:]:
        level = alpha * float(value) + (1 - alpha) * level
    return np.repeat(level, horizon)


def _linear_trend_forecast(history: np.ndarray, horizon: int, _: int) -> np.ndarray:
    """拟合最近窗口的局部线性趋势，适合单调漂移和退化过程。"""

    window = history[-min(60, len(history)) :]
    x_axis = np.arange(len(window), dtype=float)
    slope, intercept = np.polyfit(x_axis, window, 1)
    future_x = np.arange(len(window), len(window) + horizon, dtype=float)
    return np.asarray(intercept + slope * future_x, dtype=float)


def _lag_ridge_forecast(history: np.ndarray, horizon: int, lookback: int) -> np.ndarray:
    """使用滞后值、差分和滚动统计量训练岭回归，并递归完成多步预测。"""

    model, feature_window = _fit_ridge(history, lookback, include_frequency=False)
    return _recursive_ridge_forecast(model, history, horizon, feature_window, False)


def _time_frequency_ridge_forecast(
    history: np.ndarray, horizon: int, lookback: int
) -> np.ndarray:
    """在滞后特征上加入频带能量和谱熵，形成时域与频域融合模型。"""

    model, feature_window = _fit_ridge(history, lookback, include_frequency=True)
    return _recursive_ridge_forecast(model, history, horizon, feature_window, True)


def _fit_ridge(
    history: np.ndarray,
    lookback: int,
    include_frequency: bool,
) -> tuple[Any, int]:
    """构造严格的一步预测训练样本并拟合标准化岭回归。"""

    training = history[-min(lookback, len(history)) :]
    feature_window = min(24, max(12, len(training) // 5))
    if len(training) < feature_window + 12:
        raise ValueError("历史数据不足以训练滞后特征模型")

    features: list[list[float]] = []
    targets: list[float] = []
    for target_index in range(feature_window, len(training)):
        # 目标点本身绝不能进入特征窗口，这是避免预测泄漏的关键边界。
        window = training[target_index - feature_window : target_index]
        features.append(_feature_vector(window, include_frequency))
        targets.append(float(training[target_index]))

    model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    model.fit(np.asarray(features), np.asarray(targets))
    return model, feature_window


def _recursive_ridge_forecast(
    model: Any,
    history: np.ndarray,
    horizon: int,
    feature_window: int,
    include_frequency: bool,
) -> np.ndarray:
    """逐点预测并把预测值放回历史窗口，实现无未来信息的多步预测。"""

    working = history.astype(float).tolist()
    predictions: list[float] = []
    center = float(np.median(history))
    scale = max(float(np.std(history)), float(np.ptp(history)) * 0.1, 1e-6)
    for _ in range(horizon):
        window = np.asarray(working[-feature_window:], dtype=float)
        prediction = float(model.predict([_feature_vector(window, include_frequency)])[0])
        # 在预测值回填窗口前立即阻断发散，避免极端中间值进入下一步时频特征。
        if not np.isfinite(prediction) or abs(prediction - center) > 20 * scale:
            raise ValueError("递归预测脱离历史尺度，已触发稳定性保护")
        predictions.append(prediction)
        working.append(prediction)
    return np.asarray(predictions, dtype=float)


def _feature_vector(window: np.ndarray, include_frequency: bool) -> list[float]:
    """把一个历史窗口转换为可学习、可解释的时序特征。"""

    lags = [1, 2, 3, 5, 8, min(13, len(window))]
    features = [float(window[-lag]) for lag in lags]
    differences = np.diff(window)
    x_axis = np.arange(len(window), dtype=float)
    slope = float(np.polyfit(x_axis, window, 1)[0])
    features.extend(
        [
            float(window[-1] - window[-2]),
            float(np.mean(differences[-5:])),
            float(np.mean(window[-5:])),
            float(np.std(window[-5:])),
            float(np.mean(window[-10:])),
            float(np.std(window[-10:])),
            slope,
        ]
    )
    if include_frequency:
        frequency = build_time_frequency_features(window)
        features.extend(
            [
                frequency["主频"],
                frequency["低频能量占比"],
                frequency["高频能量占比"],
                frequency["谱熵"],
                frequency["频域峰值"],
            ]
        )
    return features


def _prediction_interval(
    predictions: np.ndarray,
    candidate_predictions: dict[str, np.ndarray],
    metrics: dict[str, float | int | None],
    history: np.ndarray,
) -> dict[str, Any]:
    """融合回测残差与候选模型分歧，生成随预测步长扩张的 95% 区间。"""

    residual_quantile = float(metrics.get("残差绝对值95分位") or 0.0)
    history_scale = max(float(np.std(history)), 1e-9)
    residual_quantile = max(residual_quantile, history_scale * 0.05)
    matrix = np.vstack(list(candidate_predictions.values()))
    disagreement = np.std(matrix, axis=0) if len(matrix) > 1 else np.zeros(len(predictions))
    step_scale = np.sqrt(1.0 + 0.5 * np.arange(1, len(predictions) + 1) / len(predictions))
    half_width = residual_quantile * step_scale + disagreement
    lower = predictions - half_width
    upper = predictions + half_width
    average_width = float(np.mean(upper - lower))
    normalized_rmse = float(metrics.get("RMSE") or 0.0) / history_scale
    normalized_width = average_width / history_scale
    if normalized_rmse <= 0.35 and normalized_width <= 1.5:
        confidence = "高"
    elif normalized_rmse <= 0.8 and normalized_width <= 3.0:
        confidence = "中"
    else:
        confidence = "低"
    return {
        "lower": lower,
        "upper": upper,
        "平均区间宽度": round(average_width, 6),
        "末值区间宽度": round(float(upper[-1] - lower[-1]), 6),
        "平均模型分歧": round(float(np.mean(disagreement)), 6),
        "置信水平": 0.95,
        "预测可信度": confidence,
    }


def _direction(delta: float, scale: float) -> str:
    """根据预测末值相对当前值的标准化变化判断未来方向。"""

    normalized = delta / max(scale, 1e-9)
    if normalized > 0.25:
        return "持续上升"
    if normalized < -0.25:
        return "持续下降"
    return "基本平稳"


def _forecast_risk(
    predictions: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    baseline_mean: float,
    baseline_std: float,
    uncertainty: dict[str, Any],
) -> str:
    """同时考虑预测越界程度和可信度，避免宽区间直接触发高风险。"""

    upper_limit = baseline_mean + 3 * baseline_std
    lower_limit = baseline_mean - 3 * baseline_std
    point_breach = bool(np.max(predictions) >= upper_limit or np.min(predictions) <= lower_limit)
    severe_breach = bool(
        np.max(predictions) >= upper_limit + baseline_std
        or np.min(predictions) <= lower_limit - baseline_std
    )
    interval_breach = bool(np.max(upper) >= upper_limit or np.min(lower) <= lower_limit)
    confidence = uncertainty.get("预测可信度", "低")
    if severe_breach and confidence in {"高", "中"}:
        return "高风险"
    if point_breach or interval_breach:
        return "需关注"
    return "正常"


def _is_stable_forecast(history: np.ndarray, predictions: np.ndarray) -> bool:
    """过滤数值发散的递归预测，防止单个失败模型污染融合区间。"""

    if not np.all(np.isfinite(predictions)):
        return False
    center = float(np.median(history))
    scale = max(float(np.std(history)), float(np.ptp(history)) * 0.1, 1e-6)
    maximum_departure = float(np.max(np.abs(predictions - center)))
    return maximum_departure <= 20 * scale


def _future_timestamps(timestamps: pd.Series, horizon: int) -> list[pd.Timestamp]:
    """根据历史采样间隔生成未来时间轴。"""

    if len(timestamps) >= 2:
        intervals = timestamps.diff().dropna()
        positive = intervals[intervals > pd.Timedelta(0)]
        interval = positive.median() if not positive.empty else pd.Timedelta(seconds=1)
    else:
        interval = pd.Timedelta(seconds=1)
    return [timestamps.iloc[-1] + interval * index for index in range(1, horizon + 1)]


def _validate_models(models: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """校验模型名称并去重，防止 API 传入未实现算法。"""

    selected = tuple(dict.fromkeys(models or DEFAULT_MODELS))
    unknown = set(selected) - set(MODEL_LABELS)
    if not selected or unknown:
        raise ValueError(f"不支持的预测模型：{', '.join(sorted(unknown))}")
    return selected


def _empty_metrics(failures: int = 0) -> dict[str, float | int | None]:
    """返回统一的空回测结构，便于 API 和页面稳定解析。"""

    return {
        "样本数": 0,
        "折数": 0,
        "MAE": None,
        "RMSE": None,
        "MAPE": None,
        "残差标准差": None,
        "残差绝对值95分位": None,
        "失败折数": failures,
    }


def _rounded_list(values: np.ndarray) -> list[float]:
    """统一限制 JSON 中浮点数位数，降低万悟工作流传输体积。"""

    return [round(float(value), 6) for value in values]
