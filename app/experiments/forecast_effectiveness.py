"""趋势预测与提前预警成效实验。

本模块把两类证据严格分开：

1. SKAB 真实公开数据：在固定独立测试文件中保留时间尾段，只用此前历史完成滚动
   选模和预测，再用保留段评价误差、方向和区间覆盖率；
2. 受控退化场景：用固定随机种子构造已知风险阈值与真实越界时刻的信号，评价预警
   是否命中、能提前多少采样点，以及稳定对照是否误报。

第二类结果只验证方法机制，不属于企业现场数据，也不能表述为企业收益。
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.analysis.forecast import DEFAULT_MODELS, MODEL_LABELS, forecast_sensors
from app.config import get_settings
from app.data.loader import get_sensor_columns, load_time_series
from app.experiments.split import ExperimentSplit, build_skab_split

AUTOMATIC_STRATEGY = "automatic_selection"
AUTOMATIC_LABEL = "滚动回测自动选模"
DEFAULT_HOLDOUT = 30
DEFAULT_LOOKBACK = 120


@dataclass(frozen=True)
class RealForecastRecord:
    """一个模型策略在一份 SKAB 文件的一个传感器上的时间尾段结果。"""

    strategy: str
    strategy_name: str
    selected_model: str
    scenario: str
    file_name: str
    sensor: str
    history_points: int
    holdout_points: int
    rmse: float
    mae: float
    mape: float
    normalized_rmse: float
    normalized_mae: float
    persistence_improvement: float
    interval_coverage: float
    direction_correct: bool
    inference_seconds: float


@dataclass(frozen=True)
class ControlledScenario:
    """带有已知风险边界的确定性受控退化时序。"""

    scenario: str
    scenario_name: str
    values: np.ndarray
    risk_direction: str
    risk_threshold: float | None
    degradation_start: int | None


@dataclass(frozen=True)
class WarningScenarioRecord:
    """一个受控场景上的滚动预警汇总。"""

    scenario: str
    scenario_name: str
    risk_direction: str
    threshold: float | None
    crossing_index: int | None
    forecast_opportunities: int
    event_opportunities: int
    warning_count: int
    true_warning_count: int
    false_warning_count: int
    event_detected: bool | None
    lead_time_points: int | None
    direction_accuracy: float
    interval_coverage: float
    selected_models: str


@dataclass(frozen=True)
class ForecastEffectiveness:
    """一次预测与预警实验的记录、失败任务和文件产物。"""

    real_records: list[RealForecastRecord]
    warning_records: list[WarningScenarioRecord]
    failed_tasks: dict[str, str]
    real_csv_path: Path
    warning_csv_path: Path
    report_path: Path


def evaluate_forecast_effectiveness(
    data_root: str | Path,
    output_dir: str | Path | None = None,
    *,
    split: ExperimentSplit | None = None,
    max_files: int | None = None,
    holdout: int = DEFAULT_HOLDOUT,
    lookback: int = DEFAULT_LOOKBACK,
) -> ForecastEffectiveness:
    """运行 SKAB 时间尾段评估和受控退化预警实验。"""

    root = Path(data_root).expanduser().resolve()
    experiment_split = split or build_skab_split(root)
    test_files = list(experiment_split.test_files)
    if max_files is not None and max_files > 0:
        test_files = test_files[:max_files]
    if not test_files:
        raise ValueError("没有可用于趋势预测评估的独立测试文件。")

    settings = get_settings()
    target_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "competition"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    real_records, failed_tasks = _evaluate_real_skab(
        root,
        test_files,
        holdout=max(5, int(holdout)),
        lookback=max(30, int(lookback)),
    )
    warning_records = evaluate_controlled_warning_scenarios(
        lookback=max(60, int(lookback)),
        horizon=max(20, int(holdout)),
    )

    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    real_csv_path = target_dir / f"forecast_backtest_{timestamp}.csv"
    warning_csv_path = target_dir / f"controlled_warning_{timestamp}.csv"
    report_path = target_dir / f"forecast_effectiveness_{timestamp}.md"
    _write_dataclass_csv(real_csv_path, real_records, RealForecastRecord)
    _write_dataclass_csv(warning_csv_path, warning_records, WarningScenarioRecord)
    report_path.write_text(
        build_forecast_effectiveness_report(
            root,
            len(test_files),
            real_records,
            warning_records,
            failed_tasks,
            holdout=max(5, int(holdout)),
        ),
        encoding="utf-8",
    )
    return ForecastEffectiveness(
        real_records=real_records,
        warning_records=warning_records,
        failed_tasks=failed_tasks,
        real_csv_path=real_csv_path,
        warning_csv_path=warning_csv_path,
        report_path=report_path,
    )


def build_controlled_scenarios(
    *,
    seed: int = 20260811,
    length: int = 300,
) -> tuple[ControlledScenario, ...]:
    """构造固定、可复现且包含稳定对照的退化场景。

    所有风险场景都在后半段才出现退化，前半段为模型提供健康历史。风险阈值是实验
    定义的标准化边界，不冒充任何真实设备的工程限值。
    """

    if length < 220:
        raise ValueError("受控退化场景至少需要 220 个采样点。")
    rng = np.random.default_rng(seed)
    time_axis = np.arange(length, dtype=float)
    start = int(length * 0.5)
    progress = np.clip((time_axis - start) / max(length - start - 1, 1), 0.0, 1.0)

    # 各场景使用独立噪声，避免因为共享同一条噪声轨迹而高估方法稳定性。
    noise = [rng.normal(0.0, 0.035, length) for _ in range(5)]
    gradual_up = noise[0] + 1.45 * progress
    gradual_down = noise[1] - 1.45 * progress
    accelerated = noise[2] + 1.65 * progress**2
    cyclic_drift = noise[3] + 0.08 * np.sin(2 * np.pi * time_axis / 24) + 1.35 * progress
    stable = noise[4] + 0.08 * np.sin(2 * np.pi * time_axis / 30)

    return (
        ControlledScenario("gradual_up", "渐进上升退化", gradual_up, "up", 1.0, start),
        ControlledScenario(
            "gradual_down", "渐进下降退化", gradual_down, "down", -1.0, start
        ),
        ControlledScenario(
            "accelerated", "加速退化", accelerated, "up", 1.0, start
        ),
        ControlledScenario(
            "cyclic_drift", "周期扰动叠加漂移", cyclic_drift, "up", 1.0, start
        ),
        ControlledScenario(
            "stable_control", "稳定无风险对照", stable, "two_sided", 1.0, None
        ),
    )


def evaluate_controlled_warning_scenarios(
    *,
    lookback: int = DEFAULT_LOOKBACK,
    horizon: int = DEFAULT_HOLDOUT,
    stride: int = 10,
    seed: int = 20260811,
) -> list[WarningScenarioRecord]:
    """按滚动预测起点评价已知阈值越界和稳定对照误报。"""

    records: list[WarningScenarioRecord] = []
    for scenario in build_controlled_scenarios(seed=seed):
        crossing_index = _first_threshold_crossing(scenario)
        trace: list[dict[str, int | bool]] = []
        direction_results: list[bool] = []
        covered_points = 0
        evaluated_points = 0
        selected_models: Counter[str] = Counter()
        event_opportunities = 0

        start_origin = max(80, min(lookback, len(scenario.values) - horizon - 1))
        stop_origin = len(scenario.values) - horizon + 1
        if crossing_index is not None:
            # 越界后属于当前异常处置，不再计入“提前预警”或误报统计。
            stop_origin = min(stop_origin, crossing_index + 1)
        for origin in range(start_origin, stop_origin, stride):
            history_values = scenario.values[:origin]
            dataframe = pd.DataFrame(
                {
                    "datetime": pd.date_range("2026-01-01", periods=origin, freq="s"),
                    "controlled_sensor": history_values,
                }
            )
            result = forecast_sensors(
                dataframe,
                ["controlled_sensor"],
                horizon=horizon,
                lookback=lookback,
                holdout=min(horizon, max(5, origin // 4)),
            ).get("controlled_sensor")
            if result is None:
                continue

            prediction = np.asarray(result["预测值"], dtype=float)
            lower = np.asarray(result["下界"], dtype=float)
            upper = np.asarray(result["上界"], dtype=float)
            actual = scenario.values[origin : origin + horizon]
            selected_models[str(result["模型"])] += 1
            warning = _crosses_threshold(prediction, scenario)
            crossing_in_horizon = (
                crossing_index is not None and origin <= crossing_index < origin + horizon
            )
            event_opportunities += int(crossing_in_horizon)
            trace.append(
                {
                    "origin": origin,
                    "warning": warning,
                    "true": bool(warning and crossing_in_horizon),
                }
            )

            scale = _history_scale(history_values, lookback)
            predicted_direction = _numeric_direction(prediction[-1] - history_values[-1], scale)
            if crossing_in_horizon:
                # 方向指标只评价真实越界已进入预测窗口的临界阶段；更早的健康/缓慢
                # 漂移阶段由误报率评价，避免把两种统计口径混在一起。
                direction_results.append(predicted_direction == scenario.risk_direction)
            covered_points += int(np.sum((actual >= lower) & (actual <= upper)))
            evaluated_points += len(actual)

        true_origins = [item["origin"] for item in trace if item["warning"] and item["true"]]
        warning_count = sum(bool(item["warning"]) for item in trace)
        true_warning_count = len(true_origins)
        false_warning_count = warning_count - true_warning_count
        event_detected = None if crossing_index is None else bool(true_origins)
        lead_time = (
            int(crossing_index - min(true_origins))
            if crossing_index is not None and true_origins
            else None
        )
        records.append(
            WarningScenarioRecord(
                scenario=scenario.scenario,
                scenario_name=scenario.scenario_name,
                risk_direction=scenario.risk_direction,
                threshold=scenario.risk_threshold,
                crossing_index=crossing_index,
                forecast_opportunities=len(trace),
                event_opportunities=event_opportunities,
                warning_count=warning_count,
                true_warning_count=true_warning_count,
                false_warning_count=false_warning_count,
                event_detected=event_detected,
                lead_time_points=lead_time,
                direction_accuracy=_safe_rate(sum(direction_results), len(direction_results)),
                interval_coverage=_safe_rate(covered_points, evaluated_points),
                selected_models=json.dumps(selected_models, ensure_ascii=False, sort_keys=True),
            )
        )
    return records


def build_forecast_effectiveness_report(
    data_root: Path,
    planned_file_count: int,
    real_records: list[RealForecastRecord],
    warning_records: list[WarningScenarioRecord],
    failed_tasks: dict[str, str],
    *,
    holdout: int,
) -> str:
    """生成可用于技术报告和答辩复核的预测成效说明。"""

    real_summary = _summarize_real_records(real_records)
    automatic_records = [item for item in real_records if item.strategy == AUTOMATIC_STRATEGY]
    model_frequency = Counter(item.selected_model for item in automatic_records)
    risk_records = [item for item in warning_records if item.event_detected is not None]
    stable_records = [item for item in warning_records if item.event_detected is None]
    detected = sum(item.event_detected is True for item in risk_records)
    leads = [item.lead_time_points for item in risk_records if item.lead_time_points is not None]
    false_warnings = sum(item.false_warning_count for item in warning_records)
    non_event_opportunities = sum(
        item.forecast_opportunities - item.event_opportunities for item in warning_records
    )

    lines = [
        "# 趋势预测与提前预警成效实验",
        "",
        "> 本报告包含 SKAB 公开数据时间尾段评估和受控退化模拟，两类证据不可混为企业现场收益。",
        "",
        "## 1. SKAB 真实时序预测",
        "",
        f"- 数据目录：`{data_root}`",
        f"- 固定独立测试文件：{planned_file_count} 份",
        f"- 每个传感器保留最后 {holdout} 个采样点作为时间尾段，只用此前历史选模和预测。",
        "- `anomaly`、`changepoint` 标签不参与预测模型选择、拟合或输入特征。",
        "- 不同传感器量纲不同，因此模型横向比较以标准化 RMSE/MAE 为主。",
        "",
        "| 策略 | 成功序列 | 标准化 RMSE | 标准化 MAE | MAPE | 相对持续模型改善 | 95% 区间覆盖 | 方向准确率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in real_summary:
        lines.append(
            f"| {item['strategy_name']} | {item['count']} | {item['normalized_rmse']:.4f} | "
            f"{item['normalized_mae']:.4f} | {item['mape']:.4f} | "
            f"{item['persistence_improvement']:.2%} | {item['interval_coverage']:.2%} | "
            f"{item['direction_accuracy']:.2%} |"
        )
    lines.extend(
        [
            "",
            "### 自动选模分布",
            "",
            *(f"- {MODEL_LABELS.get(name, name)}：{count} 条序列" for name, count in model_frequency.most_common()),
            "",
            "## 2. 受控退化提前预警",
            "",
            "> 这些场景由固定随机种子生成，阈值是标准化实验边界，不是企业设备工程限值。",
            "",
            "| 场景 | 实际越界点 | 是否提前命中 | 提前量/点 | 告警次数 | 误报次数 | 临界窗口方向准确率 | 区间覆盖率 |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in warning_records:
        lines.append(
            f"| {item.scenario_name} | {item.crossing_index if item.crossing_index is not None else '-'} | "
            f"{_event_label(item.event_detected)} | "
            f"{item.lead_time_points if item.lead_time_points is not None else '-'} | "
            f"{item.warning_count} | {item.false_warning_count} | "
            f"{f'{item.direction_accuracy:.2%}' if item.event_detected is not None else '-'} | "
            f"{item.interval_coverage:.2%} |"
        )
    lines.extend(
        [
            "",
            "### 模拟汇总结论",
            "",
            f"- 风险场景提前命中：{detected}/{len(risk_records)}。",
            f"- 已命中场景平均提前量：{mean(leads):.2f} 个采样点。" if leads else "- 已命中场景平均提前量：无。",
            f"- 非事件机会中的误报率：{_safe_rate(false_warnings, non_event_opportunities):.2%}。",
            f"- 稳定对照告警次数：{sum(item.warning_count for item in stable_records)}。",
            "",
            "## 3. 结果边界",
            "",
            "- SKAB 本身主要用于异常检测评价，时间尾段结果只能证明公开信号上的短期预测能力。",
            "- 受控模拟用于验证预警机制是否能在已知退化轨迹上提前触发，不能替代企业现场验证。",
            "- 企业数据接入后必须按设备工艺阈值重新评估提前量、漏报率、误报率和区间覆盖率。",
            "- 所有模型均按时间顺序训练与回测，预测起点之后的真实值只用于最终评价。",
        ]
    )
    if failed_tasks:
        lines.extend(["", "## 4. 失败任务", ""])
        lines.extend(f"- `{name}`：{error}" for name, error in sorted(failed_tasks.items()))
    return "\n".join(lines) + "\n"


def _evaluate_real_skab(
    root: Path,
    test_files: list[Path],
    *,
    holdout: int,
    lookback: int,
) -> tuple[list[RealForecastRecord], dict[str, str]]:
    """逐文件、逐传感器运行固定模型和自动选模的时间尾段评估。"""

    records: list[RealForecastRecord] = []
    failed_tasks: dict[str, str] = {}
    strategies = (*DEFAULT_MODELS, AUTOMATIC_STRATEGY)
    for file_path in test_files:
        try:
            dataframe = load_time_series(file_path)
        except (OSError, TypeError, ValueError) as exc:
            failed_tasks[f"{file_path.parent.name}/{file_path.name}"] = str(exc)
            continue
        effective_holdout = min(holdout, max(5, len(dataframe) // 5))
        if len(dataframe) <= effective_holdout + 30:
            failed_tasks[f"{file_path.parent.name}/{file_path.name}"] = "数据不足以切分历史段和尾段"
            continue
        training = dataframe.iloc[:-effective_holdout].reset_index(drop=True)
        actual_frame = dataframe.iloc[-effective_holdout:].reset_index(drop=True)
        for sensor in get_sensor_columns(dataframe):
            actual = pd.to_numeric(actual_frame[sensor], errors="coerce").interpolate(
                limit_direction="both"
            )
            if actual.isna().any():
                failed_tasks[f"{file_path.parent.name}/{file_path.name}:{sensor}"] = "尾段包含无法修复的空值"
                continue
            history_values = pd.to_numeric(training[sensor], errors="coerce").interpolate(
                limit_direction="both"
            ).fillna(0.0).to_numpy(dtype=float)
            baseline = np.repeat(history_values[-1], effective_holdout)
            baseline_rmse = _rmse(actual.to_numpy(dtype=float), baseline)
            scale = _history_scale(history_values, lookback)
            for strategy in strategies:
                started = perf_counter()
                try:
                    forecast = forecast_sensors(
                        training,
                        [sensor],
                        horizon=effective_holdout,
                        lookback=lookback,
                        holdout=effective_holdout,
                        models=None if strategy == AUTOMATIC_STRATEGY else [strategy],
                    ).get(sensor)
                    if forecast is None:
                        raise ValueError("预测模型未返回稳定结果")
                    records.append(
                        _build_real_record(
                            strategy,
                            file_path,
                            sensor,
                            history_values,
                            actual.to_numpy(dtype=float),
                            forecast,
                            baseline_rmse,
                            scale,
                            perf_counter() - started,
                        )
                    )
                except (FloatingPointError, np.linalg.LinAlgError, TypeError, ValueError) as exc:
                    task = f"{file_path.parent.name}/{file_path.name}:{sensor}:{strategy}"
                    failed_tasks[task] = str(exc)
    return records, failed_tasks


def _build_real_record(
    strategy: str,
    file_path: Path,
    sensor: str,
    history: np.ndarray,
    actual: np.ndarray,
    forecast: dict[str, Any],
    baseline_rmse: float,
    scale: float,
    elapsed: float,
) -> RealForecastRecord:
    """把一条预测转换为量纲一致、可横向比较的实验记录。"""

    prediction = np.asarray(forecast["预测值"], dtype=float)
    lower = np.asarray(forecast["下界"], dtype=float)
    upper = np.asarray(forecast["上界"], dtype=float)
    rmse = _rmse(actual, prediction)
    mae = float(np.mean(np.abs(actual - prediction)))
    denominator = np.maximum(np.abs(actual), max(float(np.std(actual)) * 0.1, 1e-6))
    actual_direction = _numeric_direction(actual[-1] - history[-1], scale)
    predicted_direction = _numeric_direction(prediction[-1] - history[-1], scale)
    return RealForecastRecord(
        strategy=strategy,
        strategy_name=(AUTOMATIC_LABEL if strategy == AUTOMATIC_STRATEGY else MODEL_LABELS[strategy]),
        selected_model=str(forecast["模型"]),
        scenario=file_path.parent.name,
        file_name=file_path.name,
        sensor=sensor,
        history_points=len(history),
        holdout_points=len(actual),
        rmse=round(rmse, 6),
        mae=round(mae, 6),
        mape=round(float(np.mean(np.abs(actual - prediction) / denominator)), 6),
        normalized_rmse=round(rmse / scale, 6),
        normalized_mae=round(mae / scale, 6),
        persistence_improvement=round(
            (baseline_rmse - rmse) / max(baseline_rmse, scale * 0.01, 1e-9),
            6,
        ),
        interval_coverage=round(float(np.mean((actual >= lower) & (actual <= upper))), 6),
        direction_correct=predicted_direction == actual_direction,
        inference_seconds=round(elapsed, 6),
    )


def _first_threshold_crossing(scenario: ControlledScenario) -> int | None:
    """返回退化开始后的首次真实越界位置。"""

    if scenario.risk_threshold is None or scenario.degradation_start is None:
        return None
    tail = scenario.values[scenario.degradation_start :]
    if scenario.risk_direction == "up":
        positions = np.flatnonzero(tail >= scenario.risk_threshold)
    else:
        positions = np.flatnonzero(tail <= scenario.risk_threshold)
    return (
        int(scenario.degradation_start + positions[0])
        if len(positions)
        else None
    )


def _crosses_threshold(prediction: np.ndarray, scenario: ControlledScenario) -> bool:
    """判断点预测是否越过受控场景的已知风险阈值。"""

    if scenario.risk_threshold is None:
        return False
    if scenario.risk_direction == "up":
        return bool(np.max(prediction) >= scenario.risk_threshold)
    if scenario.risk_direction == "down":
        return bool(np.min(prediction) <= scenario.risk_threshold)
    return bool(np.max(np.abs(prediction)) >= abs(scenario.risk_threshold))


def _history_scale(values: np.ndarray, lookback: int) -> float:
    """构造稳健尺度，防止近常量信号让标准化误差无限放大。"""

    window = np.asarray(values[-min(lookback, len(values)) :], dtype=float)
    return max(
        float(np.std(window)),
        float(np.ptp(window)) * 0.1,
        abs(float(np.mean(window))) * 0.01,
        1e-6,
    )


def _numeric_direction(delta: float, scale: float) -> str:
    """使用与在线预测相同的标准化方向阈值。"""

    if delta / max(scale, 1e-9) > 0.25:
        return "up"
    if delta / max(scale, 1e-9) < -0.25:
        return "down"
    return "stable"


def _rmse(actual: np.ndarray, prediction: np.ndarray) -> float:
    return float(np.sqrt(np.mean((np.asarray(actual) - np.asarray(prediction)) ** 2)))


def _safe_rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _event_label(value: bool | None) -> str:
    if value is None:
        return "稳定对照"
    return "是" if value else "否"


def _summarize_real_records(records: list[RealForecastRecord]) -> list[dict[str, Any]]:
    groups: dict[str, list[RealForecastRecord]] = {}
    for record in records:
        groups.setdefault(record.strategy, []).append(record)
    summaries: list[dict[str, Any]] = []
    for strategy in (*DEFAULT_MODELS, AUTOMATIC_STRATEGY):
        items = groups.get(strategy, [])
        if not items:
            continue
        summaries.append(
            {
                "strategy": strategy,
                "strategy_name": items[0].strategy_name,
                "count": len(items),
                "normalized_rmse": mean(item.normalized_rmse for item in items),
                "normalized_mae": mean(item.normalized_mae for item in items),
                "mape": mean(item.mape for item in items),
                "persistence_improvement": mean(item.persistence_improvement for item in items),
                "interval_coverage": mean(item.interval_coverage for item in items),
                "direction_accuracy": mean(item.direction_correct for item in items),
            }
        )
    return summaries


def _write_dataclass_csv(path: Path, records: list[Any], record_type: type[Any]) -> None:
    """写入 UTF-8 BOM CSV，方便在 Windows Excel 中直接打开。"""

    fieldnames = list(record_type.__dataclass_fields__)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(record.__dict__)
