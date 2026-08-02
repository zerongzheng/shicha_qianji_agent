"""工业时序分析流程编排。

这个模块只负责规定步骤顺序，不承载具体算法细节。页面、CLI 和 Agent 都调用这里，
从而保证不同入口得到完全一致的分析结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.analysis.advice import generate_recommendations
from app.analysis.detection import detect_anomalies
from app.analysis.evaluation import evaluate_predictions
from app.analysis.forecast import forecast_sensors
from app.analysis.profiling import build_profile
from app.analysis.trend import analyze_recent_trends
from app.analysis.warning import build_risk_alerts
from app.config import get_settings
from app.data.loader import load_time_series
from app.models import AnalysisConfig, AnalysisResult
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
) -> AnalysisResult:
    """执行一次完整的单文件工业时序分析。"""

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
    metrics = evaluate_predictions(
        dataframe,
        detection.predicted_labels,
        detection.combined_score,
        merge_gap=config.merge_gap,
    )
    trends = analyze_recent_trends(dataframe, profile.sensor_columns)
    recommendations = generate_recommendations(profile, detection.events, trends, metrics)
    forecasts = forecast_sensors(
        dataframe,
        profile.sensor_columns,
        horizon=settings.forecast_horizon,
        lookback=settings.forecast_lookback,
        holdout=settings.forecast_holdout,
    )
    risk_alerts = build_risk_alerts(forecasts, detection.events)

    result = AnalysisResult(
        source_path=source_path,
        detector_name=detection.detector_name,
        dataframe=dataframe,
        profile=profile,
        anomaly_scores=detection.sensor_scores,
        combined_score=detection.combined_score,
        predicted_labels=detection.predicted_labels,
        events=detection.events,
        metrics=metrics,
        trend_summary=trends,
        recommendations=recommendations,
        forecast_results=forecasts,
        risk_alerts=risk_alerts,
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
