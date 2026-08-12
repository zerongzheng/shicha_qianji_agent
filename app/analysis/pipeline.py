"""工业时序分析流程编排。

这个模块只负责规定步骤顺序，不承载具体算法细节。页面、CLI 和 Agent 都调用这里，
从而保证不同入口得到完全一致的分析结果。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from app.analysis.advice import generate_recommendations
from app.analysis.detection import detect_anomalies
from app.analysis.evaluation import evaluate_predictions
from app.analysis.forecast import forecast_sensors
from app.analysis.model_selection import select_detection_model
from app.analysis.model_validation import cross_validate_detectors
from app.analysis.optimization import generate_optimization_recommendations
from app.analysis.preprocessing import adaptive_preprocess
from app.analysis.profiling import build_profile
from app.analysis.regime import analyze_operating_regimes, suppress_transition_only_events
from app.analysis.relationships import analyze_event_relationships
from app.analysis.trend import analyze_recent_trends
from app.analysis.warning import build_risk_alerts
from app.config import get_settings
from app.data.loader import load_time_series, load_time_series_with_context
from app.diagnosis import diagnose_root_causes
from app.models import (
    AnalysisConfig,
    AnalysisResult,
    ExecutionTraceStep,
    HistoricalCaseMatch,
)
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
    run_detector_validation: bool = False,
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
        detector_selection_mode="auto",
        analysis_goal="balanced",
        detector=settings.anomaly_detector,
        threshold=settings.anomaly_threshold,
        rolling_window=settings.rolling_window,
        min_event_length=settings.min_event_length,
        merge_gap=settings.merge_gap,
        contamination=settings.contamination,
    )
    source_path = Path(file_path).expanduser().resolve()
    execution_trace: list[ExecutionTraceStep] = []

    # 数据接入层负责格式识别、时间排序、字段标准化和设备配置匹配，是后续算法的统一入口。
    started_at = perf_counter()
    loaded = load_time_series_with_context(source_path, config.device_profile_id)
    dataframe = loaded.dataframe
    # SKAB 的 anomaly-free 文件没有显式标签，但目录语义明确表示全程正常。
    # 仅在该标准场景中补全 0 标签，企业无标签数据仍保持“不可监督评估”。
    if source_path.parent.name.lower() == "anomaly-free" and "anomaly" not in dataframe:
        dataframe["anomaly"] = 0
        dataframe["changepoint"] = 0

    execution_trace.append(
        ExecutionTraceStep(
            step_id="data_ingestion",
            title="文件接入与预检",
            module="app.data.loader.load_time_series_with_context",
            status="completed",
            input_summary={"file_name": source_path.name, "file_type": source_path.suffix.lower()},
            output_summary={
                "row_count": len(dataframe),
                "column_count": len(dataframe.columns),
                "time_column": "datetime",
            },
            duration_seconds=_elapsed_seconds(started_at),
            limitation="当前接入层支持 CSV；企业数据库、消息队列和实时流需要通过适配器接入。",
        )
    )
    execution_trace.append(
        ExecutionTraceStep(
            step_id="device_profile_match",
            title="设备配置匹配",
            module="app.data.device_profiles.select_device_profile",
            status="completed",
            input_summary={"requested_profile_id": config.device_profile_id or "automatic"},
            output_summary={
                "profile_id": loaded.context.get("profile_id") or "generic",
                "display_name": loaded.context.get("display_name") or "通用工业时序数据",
                "match_mode": loaded.context.get("match_mode", "generic"),
                "match_score": loaded.context.get("match_score", 0.0),
            },
            limitation="设备配置匹配只确认字段契约和适用范围，不等同于设备身份认证。",
        )
    )

    started_at = perf_counter()
    raw_profile = build_profile(dataframe, source_path.name)
    execution_trace.append(
        ExecutionTraceStep(
            step_id="data_profile",
            title="原始数据画像与质量检查",
            module="app.analysis.profiling.build_profile",
            status="completed",
            input_summary={"row_count": len(dataframe)},
            output_summary={
                "sensor_count": len(raw_profile.sensor_columns),
                "missing_total": raw_profile.missing_total,
                "sampling_seconds": raw_profile.sampling_seconds,
            },
            duration_seconds=_elapsed_seconds(started_at),
            limitation="数据画像反映当前文件质量，不代表设备长期健康状态。",
        )
    )

    # 预处理先根据时间轴和缺失模式决定动作，再把处理后的统一数据交给所有下游模型。
    # 标签只随时间对齐聚合，不参与插值、噪声判断或模型缩放。
    started_at = perf_counter()
    preprocessing_result = adaptive_preprocess(
        dataframe,
        expected_sampling_seconds=loaded.context.get("expected_sampling_seconds"),
        analysis_goal="industrial_anomaly_forecast",
    )
    dataframe = preprocessing_result.dataframe
    preprocessing = preprocessing_result.summary
    profile = build_profile(dataframe, source_path.name)
    execution_trace.append(
        ExecutionTraceStep(
            step_id="adaptive_preprocessing",
            title="自适应对齐、填补与模型适配",
            module="app.analysis.preprocessing.adaptive_preprocess",
            status="completed",
            input_summary={
                "raw_row_count": raw_profile.row_count,
                "raw_missing_count": raw_profile.missing_total,
                "expected_sampling_seconds": loaded.context.get(
                    "expected_sampling_seconds"
                ),
            },
            output_summary={
                "processed_row_count": profile.row_count,
                "inserted_row_count": preprocessing["inserted_row_count"],
                "filled_count": preprocessing["filled_count"],
                "time_alignment_applied": preprocessing["time_alignment_applied"],
                "quality_gate": preprocessing["quality_gate"],
            },
            duration_seconds=_elapsed_seconds(started_at),
            limitation="自动填补恢复的是算法输入连续性，不代表缺失期间的真实设备状态。",
        )
    )

    healthy_reference = None
    baseline_path = settings.healthy_baseline_file
    if loaded.profile is not None:
        profile_baseline = loaded.profile.resolve_healthy_baseline(settings.project_root)
        if profile_baseline is not None:
            baseline_path = profile_baseline
    if config.use_healthy_baseline and baseline_path.exists():
        # 基线沿用同一设备配置，确保企业字段别名与当前分析数据保持一致。
        candidate = load_time_series(baseline_path, config.device_profile_id)
        candidate = adaptive_preprocess(
            candidate,
            expected_sampling_seconds=loaded.context.get("expected_sampling_seconds"),
            analysis_goal="healthy_baseline",
        ).dataframe
        if set(profile.sensor_columns).issubset(candidate.columns):
            healthy_reference = candidate[profile.sensor_columns]

    # 模型路由只读取任务目标、设备配置和数据条件，不使用当前文件标签。返回的新配置
    # 是本次真正生效的模型与阈值，后续检测、交叉验证、报告和 API 必须统一使用它。
    started_at = perf_counter()
    config, model_selection = select_detection_model(
        dataframe,
        profile.sensor_columns,
        config,
        loaded.context,
        healthy_baseline_available=healthy_reference is not None,
    )
    execution_trace.append(
        ExecutionTraceStep(
            step_id="model_selection",
            title="任务场景模型选择",
            module="app.analysis.model_selection.select_detection_model",
            status="completed",
            input_summary={
                "selection_mode": config.detector_selection_mode,
                "analysis_goal": config.analysis_goal,
                "sensor_count": len(profile.sensor_columns),
                "healthy_baseline_available": healthy_reference is not None,
            },
            output_summary={
                "selected_detector": model_selection["selected_detector_name"],
                "selected_threshold": model_selection["selected_threshold"],
                "selection_source": model_selection["selection_source"],
            },
            duration_seconds=_elapsed_seconds(started_at),
            limitation="选择规则来自冻结实验和设备配置，企业现场数据到位后仍需重新校准。",
        )
    )

    started_at = perf_counter()
    detection = detect_anomalies(
        dataframe=dataframe,
        sensor_columns=profile.sensor_columns,
        config=config,
        healthy_reference=healthy_reference,
    )
    detection_duration = _elapsed_seconds(started_at)
    if run_detector_validation:
        started_at = perf_counter()
        detector_validation = cross_validate_detectors(
            dataframe,
            profile.sensor_columns,
            config,
            detection,
            healthy_reference,
        )
        validation_duration = _elapsed_seconds(started_at)
    else:
        detector_validation = {}
        validation_duration = None
    if run_regime:
        started_at = perf_counter()
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
        regime_duration = _elapsed_seconds(started_at)
    else:
        operating_regimes = None
        predicted_labels = detection.predicted_labels
        events = detection.events
        regime_duration = None
    execution_trace.append(
        ExecutionTraceStep(
            step_id="anomaly_detection",
            title="主模型异常检测",
            module="app.analysis.detection.detect_anomalies",
            status="completed",
            input_summary={
                "sensor_count": len(profile.sensor_columns),
                "detector": config.detector,
                "healthy_baseline_used": healthy_reference is not None,
            },
            output_summary={
                "detector": detection.detector_name,
                "anomaly_point_count": int(predicted_labels.sum()),
                "event_count": len(events),
            },
            duration_seconds=detection_duration,
            limitation="异常分数用于发现偏离模式，不能单独证明设备已经发生物理故障。",
        )
    )
    execution_trace.append(
        ExecutionTraceStep(
            step_id="model_cross_validation",
            title="异常检测多模型交叉验证",
            module="app.analysis.model_validation.cross_validate_detectors",
            status="completed" if run_detector_validation else "skipped",
            input_summary={"primary_detector": config.detector},
            output_summary=(
                {
                    "model_count": detector_validation.get("model_count", 0),
                    "agreement_level": detector_validation.get("agreement", {}).get(
                        "level",
                        "不可用",
                    ),
                    "selected_detector": detector_validation.get("selected_detector"),
                }
                if run_detector_validation
                else {"reason": "run_detector_validation=False"}
            ),
            duration_seconds=validation_duration,
            limitation="跨模型一致性用于增强告警可信度，不能替代设备机理和现场复测。",
        )
    )
    execution_trace.append(
        ExecutionTraceStep(
            step_id="operating_regime",
            title="工况识别与切换分析",
            module="app.analysis.regime.analyze_operating_regimes",
            status="completed" if run_regime else "skipped",
            input_summary={"sensor_count": len(profile.sensor_columns)},
            output_summary=(
                {
                    "state_count": operating_regimes.state_count,
                    "transition_point_count": int(operating_regimes.transition_mask.sum()),
                    "suppressed_event_count": operating_regimes.suppressed_event_count,
                }
                if operating_regimes
                else {"reason": "run_regime=False"}
            ),
            duration_seconds=regime_duration,
            limitation="无监督工况编号只表示数据模式分组，需要结合控制指令赋予现场语义。",
        )
    )
    metrics = evaluate_predictions(
        dataframe,
        predicted_labels,
        detection.combined_score,
        merge_gap=config.merge_gap,
    )
    trends = analyze_recent_trends(dataframe, profile.sensor_columns)
    started_at = perf_counter()
    relationship_diagnostics = analyze_event_relationships(
        dataframe,
        profile.sensor_columns,
        events,
    )
    execution_trace.append(
        ExecutionTraceStep(
            step_id="relationship_evidence",
            title="多传感器证据提取",
            module="app.analysis.relationships.analyze_event_relationships",
            status="completed",
            input_summary={"event_count": len(events), "sensor_count": len(profile.sensor_columns)},
            output_summary={"event_evidence_count": len(relationship_diagnostics)},
            duration_seconds=_elapsed_seconds(started_at),
            limitation="相关性和时滞用于缩小排查范围，不能替代设备机理和现场复测。",
        )
    )
    if run_forecast:
        started_at = perf_counter()
        forecasts = forecast_sensors(
            dataframe,
            profile.sensor_columns,
            horizon=settings.forecast_horizon,
            lookback=settings.forecast_lookback,
            holdout=settings.forecast_holdout,
        )
        forecast_duration = _elapsed_seconds(started_at)
    else:
        forecasts = {}
        forecast_duration = None
    execution_trace.append(
        ExecutionTraceStep(
            step_id="forecast_analysis",
            title="趋势预测与风险外推",
            module="app.analysis.forecast.forecast_sensors",
            status="completed" if run_forecast else "skipped",
            input_summary={"sensor_count": len(profile.sensor_columns)},
            output_summary=(
                {"forecast_sensor_count": len(forecasts)}
                if run_forecast
                else {"reason": "run_forecast=False"}
            ),
            duration_seconds=forecast_duration,
            limitation="预测反映短期统计趋势，不承诺故障发生时间或剩余寿命。",
        )
    )
    risk_alerts = build_risk_alerts(
        forecasts,
        events,
        relationship_diagnostics,
        operating_regimes,
    )
    historical_case_matches: dict[int, list[HistoricalCaseMatch]] = {}
    started_at = perf_counter()
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
    diagnosis_duration = _elapsed_seconds(started_at)
    execution_trace.append(
        ExecutionTraceStep(
            step_id="root_cause_diagnosis",
            title="候选根因诊断",
            module="app.diagnosis.root_cause.diagnose_root_causes",
            status="completed",
            input_summary={
                "event_count": len(events),
                "relationship_evidence_count": len(relationship_diagnostics),
                "historical_case_matching": case_matcher is not None,
            },
            output_summary={
                "diagnosis_count": len(event_diagnoses),
                "candidate_count": sum(len(item.candidates) for item in event_diagnoses),
                "historical_case_match_count": sum(
                    len(items) for items in historical_case_matches.values()
                ),
            },
            duration_seconds=diagnosis_duration,
            limitation="根因结果是待验证候选排序，必须结合设备拓扑、控制记录和现场复测确认。",
        )
    )
    execution_trace.append(
        ExecutionTraceStep(
            step_id="work_order_generation",
            title="运维工单草案生成",
            module="app.diagnosis.root_cause.diagnose_root_causes",
            status="completed",
            input_summary={"diagnosis_count": len(event_diagnoses)},
            output_summary={"work_order_draft_count": len(work_order_drafts)},
            limitation="系统只生成待确认草案，派单、执行、验收和根因回写仍由运维人员负责。",
        )
    )
    recommendations = generate_recommendations(profile, events, trends, metrics)
    optimization_recommendations = generate_optimization_recommendations(
        profile,
        preprocessing,
        forecasts,
        event_diagnoses,
        loaded.context,
        historical_case_matches,
    )
    execution_trace.append(
        ExecutionTraceStep(
            step_id="optimization_recommendation",
            title="受约束参数与能耗优化建议",
            module="app.analysis.optimization.generate_optimization_recommendations",
            status="completed",
            input_summary={
                "forecast_sensor_count": len(forecasts),
                "diagnosis_count": len(event_diagnoses),
                "device_safe_ranges_available": sum(
                    bool(item.get("safe_range"))
                    for item in loaded.context.get("sensor_metadata", {}).values()
                ),
            },
            output_summary={
                "recommendation_count": len(optimization_recommendations),
                "categories": sorted(
                    {item.category for item in optimization_recommendations}
                ),
            },
            limitation="建议为待确认草案；没有设备安全范围时不输出控制设定值，也不直接下发设备。",
        )
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
        raw_profile=raw_profile,
        preprocessing=preprocessing,
        optimization_recommendations=optimization_recommendations,
        device_context=loaded.context,
        model_selection=model_selection,
        detector_validation=detector_validation,
        operating_regimes=operating_regimes,
        relationship_diagnostics=relationship_diagnostics,
        forecast_results=forecasts,
        risk_alerts=risk_alerts,
        event_diagnoses=event_diagnoses,
        work_order_drafts=work_order_drafts,
        historical_case_matches=historical_case_matches,
        execution_trace=execution_trace,
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


def _elapsed_seconds(started_at: float) -> float:
    """统一保留四位小数，兼顾短步骤展示和实验可复现性。"""

    return round(perf_counter() - started_at, 4)
