"""工业时序分析流程编排。

这个模块只负责规定步骤顺序，不承载具体算法细节。页面、CLI 和 Agent 都调用这里，
从而保证不同入口得到完全一致的分析结果。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.analysis.advice import generate_recommendations
from app.analysis.detection import detect_anomalies
from app.analysis.evaluation import evaluate_predictions
from app.analysis.forecast import forecast_sensors
from app.analysis.profiling import build_profile
from app.analysis.regime import analyze_operating_regimes, suppress_transition_only_events
from app.analysis.relationships import analyze_event_relationships
from app.analysis.trend import analyze_recent_trends
from app.analysis.warning import build_risk_alerts
from app.config import get_settings
from app.data.loader import load_time_series
from app.diagnosis import diagnose_root_causes
from app.models import AnalysisConfig, AnalysisResult, HistoricalCaseMatch
from app.reporting import build_markdown_report, save_report


@dataclass
class BatchAnalysisResult:
    """文件夹批量分析的汇总结果。"""

    source_dir: Path
    results: list[AnalysisResult]
    failed_files: dict[str, str]

    @property
    def total_rows(self) -> int:
        return sum(result.profile.row_count for result in self.results)

    @property
    def total_events(self) -> int:
        return sum(len(result.events) for result in self.results)

    @property
    def average_f1(self) -> float | None:
        values = [result.metrics.f1_score for result in self.results if result.metrics]
        return sum(values) / len(values) if values else None

    @property
    def average_event_f1(self) -> float | None:
        """批量文件的平均事件级 F1。"""

        values = [result.metrics.event_f1_score for result in self.results if result.metrics]
        return sum(values) / len(values) if values else None


def analyze_file(
    file_path: str | Path,
    config: AnalysisConfig | None = None,
    write_report: bool = True,
    run_forecast: bool = True,
    run_regime: bool = True,
    case_matcher: Callable[
        [list[dict[str, Any]], list[str], str],
        list[HistoricalCaseMatch],
    ]
    | None = None,
) -> AnalysisResult:
    """执行单文件工业时序分析。

    `run_forecast=False` 仅供异常检测基准和阈值调优使用，避免在比较检测器时重复运行
    与实验目标无关的五模型预测；产品分析和页面入口保持默认完整流程。
    """

    settings = get_settings()
    config = config or AnalysisConfig(
        detector=settings.anomaly_detector,
        threshold=settings.anomaly_threshold,
        rolling_window=settings.rolling_window,
        min_event_length=settings.min_event_length,
        merge_gap=settings.merge_gap,
        contamination=settings.contamination,
    )
    source_path = Path(file_path).expanduser().resolve()
    dataframe = load_time_series(source_path)
    # SKAB 的 anomaly-free 文件没有显式标签，但目录语义明确表示全程正常。
    # 仅在该标准场景中补全 0 标签，企业无标签数据仍保持“不可监督评估”。
    if source_path.parent.name.lower() == "anomaly-free" and "anomaly" not in dataframe:
        dataframe["anomaly"] = 0
        dataframe["changepoint"] = 0
    profile = build_profile(dataframe, source_path.name)

    healthy_reference = None
    if config.use_healthy_baseline and settings.healthy_baseline_file.exists():
        candidate = load_time_series(settings.healthy_baseline_file)
        if set(profile.sensor_columns).issubset(candidate.columns):
            healthy_reference = candidate[profile.sensor_columns]

    detection = detect_anomalies(
        dataframe=dataframe,
        sensor_columns=profile.sensor_columns,
        config=config,
        healthy_reference=healthy_reference,
    )
    if run_regime:
        operating_regimes = analyze_operating_regimes(
            dataframe,
            profile.sensor_columns,
            detection.events,
            config,
        )
        predicted_labels, events, operating_regimes = suppress_transition_only_events(
            operating_regimes,
            detection.events,
            detection.predicted_labels,
            config,
        )
    else:
        operating_regimes = None
        predicted_labels = detection.predicted_labels
        events = detection.events
    metrics = evaluate_predictions(
        dataframe,
        predicted_labels,
        detection.combined_score,
        merge_gap=config.merge_gap,
    )
    trends = analyze_recent_trends(dataframe, profile.sensor_columns)
    relationship_diagnostics = analyze_event_relationships(
        dataframe,
        profile.sensor_columns,
        events,
    )
    recommendations = generate_recommendations(profile, events, trends, metrics)
    forecasts = (
        forecast_sensors(
            dataframe,
            profile.sensor_columns,
            horizon=settings.forecast_horizon,
            lookback=settings.forecast_lookback,
            holdout=settings.forecast_holdout,
        )
        if run_forecast
        else {}
    )
    risk_alerts = build_risk_alerts(
        forecasts,
        events,
        relationship_diagnostics,
        operating_regimes,
    )
    historical_case_matches: dict[int, list[HistoricalCaseMatch]] = {}
    event_diagnoses, work_order_drafts = diagnose_root_causes(
        dataframe=dataframe,
        sensor_columns=profile.sensor_columns,
        events=events,
        relationship_diagnostics=relationship_diagnostics,
        operating_regimes=operating_regimes,
        trend_summary=trends,
        forecast_results=forecasts,
        case_matcher=case_matcher,
        historical_matches_output=historical_case_matches,
    )

    result = AnalysisResult(
        source_path=source_path,
        detector_name=detection.detector_name,
        dataframe=dataframe,
        profile=profile,
        anomaly_scores=detection.sensor_scores,
        combined_score=detection.combined_score,
        predicted_labels=predicted_labels,
        events=events,
        metrics=metrics,
        trend_summary=trends,
        recommendations=recommendations,
        operating_regimes=operating_regimes,
        relationship_diagnostics=relationship_diagnostics,
        forecast_results=forecasts,
        risk_alerts=risk_alerts,
        event_diagnoses=event_diagnoses,
        work_order_drafts=work_order_drafts,
        historical_case_matches=historical_case_matches,
    )
    result.report_text = build_markdown_report(result, config)

    if write_report:
        report_name = f"{source_path.stem}_analysis.md"
        save_report(result.report_text, settings.output_dir / report_name)
    return result


def analyze_folder(
    directory: str | Path,
    config: AnalysisConfig | None = None,
    max_files: int | None = None,
) -> BatchAnalysisResult:
    """按文件名顺序批量分析目录下的 CSV，并隔离单文件错误。"""

    source_dir = Path(directory).expanduser().resolve()
    if not source_dir.is_dir():
        raise NotADirectoryError(f"找不到数据目录：{source_dir}")

    files = sorted(source_dir.glob("*.csv"), key=_natural_sort_key)
    if max_files is not None and max_files > 0:
        files = files[:max_files]
    if not files:
        raise FileNotFoundError(f"目录中没有 CSV 文件：{source_dir}")

    results: list[AnalysisResult] = []
    failed_files: dict[str, str] = {}
    for file_path in files:
        try:
            results.append(analyze_file(file_path, config=config, write_report=False))
        # 批处理入口必须隔离单文件的解析、计算和文件系统错误，不能因一个坏文件
        # 放弃整个设备场景，因此这里有意在任务边界统一捕获并记录。
        except Exception as exc:  # noqa: BLE001
            failed_files[file_path.name] = str(exc)

    return BatchAnalysisResult(
        source_dir=source_dir,
        results=results,
        failed_files=failed_files,
    )


def _natural_sort_key(path: Path) -> tuple[int, str]:
    """让 2.csv 排在 10.csv 前面，同时兼容非数字文件名。"""

    return (int(path.stem), path.name) if path.stem.isdigit() else (10**9, path.name)
