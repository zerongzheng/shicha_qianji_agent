"""项目内部的数据结构定义。

这些数据类相当于各模块之间的“合同”。例如检测算法可以更换，但只要仍然输出
`AnomalyEvent`，报告、页面和 Agent 就不需要跟着修改。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

TFR_RECOMMENDED_TIME_WEIGHT = 0.67
TFR_RECOMMENDED_FREQUENCY_WEIGHT = 0.00
TFR_RECOMMENDED_RELATION_WEIGHT = 0.33


@dataclass(frozen=True)
class AnalysisConfig:
    """一次分析任务使用的算法参数。"""

    # 为空时根据 CSV 表头自动匹配；显式指定时严格执行对应字段契约。
    device_profile_id: str | None = None
    # manual 保持调用方指定模型，auto 根据任务目标、设备配置和数据条件选择。
    detector_selection_mode: str = "manual"
    analysis_goal: str = "balanced"
    detector: str = "time_frequency_relation"
    threshold: float = 3.5
    rolling_window: int = 61
    min_event_length: int = 12
    merge_gap: int = 30
    contamination: float = 0.01
    random_state: int = 42
    use_healthy_baseline: bool = True
    hybrid_mad_weight: float = 0.50
    hybrid_forest_weight: float = 0.30
    hybrid_pca_weight: float = 0.20
    # AutoEncoder 以连续窗口为学习单元，既观察同一时刻的多传感器关系，也观察前后动态。
    # 这些参数进入统一配置，便于 API、实验脚本和后续万悟工作流使用同一套模型设置。
    autoencoder_window: int = 16
    autoencoder_hidden: int = 24
    autoencoder_bottleneck: int = 6
    autoencoder_max_iter: int = 250
    autoencoder_max_training_windows: int = 3000
    # 时频关系多路径模型：时域窗口重构、频谱形态重构和传感器关系重构独立校准后融合。
    tfr_time_weight: float = TFR_RECOMMENDED_TIME_WEIGHT
    tfr_frequency_weight: float = TFR_RECOMMENDED_FREQUENCY_WEIGHT
    tfr_relation_weight: float = TFR_RECOMMENDED_RELATION_WEIGHT
    tfr_frequency_components: int = 8
    tfr_relation_components: int = 4
    # 工况层默认只输出解释证据，不改变告警。必须经过固定验证集实验后才开启过渡期弱告警抑制。
    suppress_transition_events: bool = False
    regime_window: int = 31
    regime_max_states: int = 4
    regime_transition_quantile: float = 0.98
    regime_suppression_overlap: float = 0.75
    regime_suppression_peak_ratio: float = 1.35


@dataclass(frozen=True)
class SensorProfile:
    """单个传感器的质量和统计画像。"""

    name: str
    missing_count: int
    missing_rate: float
    min_value: float
    max_value: float
    mean_value: float
    std_value: float


@dataclass(frozen=True)
class DataProfile:
    """整份时序数据的基础画像。"""

    source_name: str
    row_count: int
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    sampling_seconds: float | None
    sensor_columns: list[str]
    label_columns: list[str]
    sensors: list[SensorProfile]
    missing_total: int


@dataclass(frozen=True)
class AnomalyEvent:
    """由连续异常点合并得到的工业异常事件。"""

    start_index: int
    end_index: int
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    duration_points: int
    peak_score: float
    severity: str
    dominant_sensors: list[str]
    sensor_scores: dict[str, float]


@dataclass(frozen=True)
class EvaluationMetrics:
    """点级、事件级和工况变点相关的完整评估指标。"""

    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int
    precision: float
    recall: float
    f1_score: float
    pr_auc: float
    actual_event_count: int
    predicted_event_count: int
    matched_event_count: int
    event_precision: float
    event_recall: float
    event_f1_score: float
    mean_detection_delay: float | None
    false_positive_event_count: int
    changepoint_related_false_events: int
    changepoint_false_event_rate: float


@dataclass(frozen=True)
class OperatingRegimeResult:
    """无监督工况识别和异常事件工况归因结果。"""

    regime_labels: pd.Series
    transition_score: pd.Series
    transition_mask: pd.Series
    state_count: int
    segments: list[dict[str, Any]]
    event_contexts: list[dict[str, Any]]
    suppression_applied: bool = False
    suppressed_event_count: int = 0


@dataclass(frozen=True)
class RootCauseCandidate:
    """一个由通用故障模式与时序证据共同形成的候选根因。"""

    pattern_id: str
    name: str
    category: str
    confidence: float
    confidence_level: str
    supporting_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    verification_steps: tuple[str, ...]
    source: str = "内置通用故障模式库"


@dataclass(frozen=True)
class EventDiagnosis:
    """单个异常事件的确定性根因排序和现场验证结论。"""

    event_number: int
    event_start: pd.Timestamp
    event_end: pd.Timestamp
    risk_level: str
    diagnosis_status: str
    primary_candidate: RootCauseCandidate | None
    candidates: tuple[RootCauseCandidate, ...]
    sensor_changes: tuple[dict[str, Any], ...]
    regime_context: str
    work_order_actions: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class WorkOrderDraft:
    """可由万悟或后续数据库直接接收的结构化处置任务草案。"""

    work_order_id: str
    event_number: int
    priority: str
    title: str
    status: str
    assigned_role: str
    actions: tuple[str, ...]
    evidence_summary: tuple[str, ...]
    required_feedback: tuple[str, ...]


@dataclass(frozen=True)
class OptimizationRecommendation:
    """带证据、约束和回退条件的参数或能耗优化建议。"""

    recommendation_id: str
    category: str
    target: str
    action: str
    adjustment_direction: str
    suggested_range: str
    confidence: str
    evidence: tuple[str, ...]
    constraints: tuple[str, ...]
    validation_metrics: tuple[str, ...]
    observation_window: str
    rollback_condition: str
    status: str = "待人工确认"


@dataclass(frozen=True)
class HistoricalCaseMatch:
    """从已闭环工单中检索到的相似故障案例。"""

    case_id: str
    confirmed_cause: str
    similarity: float
    source_run_id: str
    source_record_id: str
    matched_sensor_groups: tuple[str, ...]
    matched_directions: tuple[str, ...]
    evidence_summary: tuple[str, ...]
    feedback_note: str | None
    handled_by: str | None
    closed_at: str


@dataclass(frozen=True)
class ExecutionTraceStep:
    """智能体一次确定性分析步骤的可审计记录。

    这里只保存模块调用事实、输入输出摘要和使用边界，不记录大模型思维过程，也不复制
    原始工业数据。这样既能向前端和报告说明系统自动完成了什么，也便于后续定位问题。
    """

    step_id: str
    title: str
    module: str
    status: str
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    automatic: bool = True
    duration_seconds: float | None = None
    limitation: str = ""


@dataclass
class AnalysisResult:
    """完整分析任务的输出，也是页面、报告和 Agent 的共同数据源。"""

    source_path: Path
    detector_name: str
    dataframe: pd.DataFrame
    profile: DataProfile
    anomaly_scores: pd.DataFrame
    combined_score: pd.Series
    predicted_labels: pd.Series
    events: list[AnomalyEvent]
    metrics: EvaluationMetrics | None
    trend_summary: dict[str, dict[str, Any]]
    recommendations: list[str]
    raw_profile: DataProfile | None = None
    preprocessing: dict[str, Any] = field(default_factory=dict)
    optimization_recommendations: list[OptimizationRecommendation] = field(
        default_factory=list
    )
    device_context: dict[str, Any] = field(default_factory=dict)
    model_selection: dict[str, Any] = field(default_factory=dict)
    detector_validation: dict[str, Any] = field(default_factory=dict)
    operating_regimes: OperatingRegimeResult | None = None
    relationship_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    forecast_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    risk_alerts: list[dict[str, Any]] = field(default_factory=list)
    event_diagnoses: list[EventDiagnosis] = field(default_factory=list)
    work_order_drafts: list[WorkOrderDraft] = field(default_factory=list)
    historical_case_matches: dict[int, list[HistoricalCaseMatch]] = field(
        default_factory=dict
    )
    execution_trace: list[ExecutionTraceStep] = field(default_factory=list)
    report_text: str = ""

    def to_summary(self) -> dict[str, Any]:
        """转换为适合大模型读取的紧凑摘要，避免把原始 CSV 发给模型。"""

        return {
            "数据文件": self.profile.source_name,
            "设备配置": self.device_context,
            "自适应预处理": self.preprocessing,
            "模型选择": self.model_selection,
            "检测器": self.detector_name,
            "数据点数": self.profile.row_count,
            "传感器数量": len(self.profile.sensor_columns),
            "时间范围": f"{self.profile.start_time} 至 {self.profile.end_time}",
            "异常事件数": len(self.events),
            "候选根因诊断数": len(self.event_diagnoses),
            "处置工单草案数": len(self.work_order_drafts),
            "智能体执行摘要": _execution_trace_summary(self.execution_trace),
            "最高风险等级": self.events[0].severity if self.events else "未发现明显异常",
            "重点异常传感器": _top_sensors(self.events),
            "评估指标": (
                {
                    "precision": round(self.metrics.precision, 4),
                    "recall": round(self.metrics.recall, 4),
                    "f1": round(self.metrics.f1_score, 4),
                    "pr_auc": round(self.metrics.pr_auc, 4),
                    "event_precision": round(self.metrics.event_precision, 4),
                    "event_recall": round(self.metrics.event_recall, 4),
                    "event_f1": round(self.metrics.event_f1_score, 4),
                    "mean_detection_delay": self.metrics.mean_detection_delay,
                    "changepoint_false_event_rate": round(
                        self.metrics.changepoint_false_event_rate, 4
                    ),
                }
                if self.metrics
                else "当前数据没有 anomaly 标签，无法计算监督评估指标"
            ),
            "趋势判断": self.trend_summary,
            "异常检测交叉验证": self.detector_validation,
            "工况识别": _regime_overview(self.operating_regimes),
            "多传感器关系证据": self.relationship_diagnostics,
            "候选根因诊断": [_event_diagnosis_summary(item) for item in self.event_diagnoses],
            "历史案例复用": {
                str(event_number): [asdict(item) for item in matches[:3]]
                for event_number, matches in self.historical_case_matches.items()
            },
            # 工单完整结果仍由 API 和数据库保存；摘要只取前 8 条，避免大模型提示词过长。
            "处置工单草案": [asdict(item) for item in self.work_order_drafts[:8]],
            "摘要截取说明": (
                f"处置工单草案展示前 {min(8, len(self.work_order_drafts))} 条，"
                f"完整总数为 {len(self.work_order_drafts)} 条。"
            ),
            "预测结果": _forecast_overview(self.forecast_results),
            "风险预警": self.risk_alerts,
            "运维建议": self.recommendations,
            "优化建议": [asdict(item) for item in self.optimization_recommendations],
        }


def _execution_trace_summary(
    execution_trace: list[ExecutionTraceStep],
) -> dict[str, Any]:
    """只向大模型提供步骤状态和核心产出，避免扩大提示词或泄露原始输入。"""

    completed = [step for step in execution_trace if step.status == "completed"]
    skipped = [step.title for step in execution_trace if step.status == "skipped"]
    return {
        "步骤总数": len(execution_trace),
        "自动完成数": len(completed),
        "跳过步骤": skipped,
        "执行结果": [
            {
                "步骤": step.title,
                "状态": step.status,
                "核心输出": step.output_summary,
            }
            for step in execution_trace
        ],
    }


def _event_diagnosis_summary(diagnosis: EventDiagnosis) -> dict[str, Any]:
    """压缩确定性诊断结果，避免把重复验证步骤全部发送给大模型。"""

    return {
        "事件编号": diagnosis.event_number,
        "诊断状态": diagnosis.diagnosis_status,
        "风险等级": diagnosis.risk_level,
        "工况上下文": diagnosis.regime_context,
        "首要候选根因": (
            asdict(diagnosis.primary_candidate) if diagnosis.primary_candidate else None
        ),
        "其他候选": [
            {
                "名称": item.name,
                "类别": item.category,
                "置信度": item.confidence,
                "置信等级": item.confidence_level,
            }
            for item in diagnosis.candidates[1:3]
        ],
        "传感器变化": list(diagnosis.sensor_changes[:5]),
        "现场动作": list(diagnosis.work_order_actions[:5]),
        "使用边界": list(diagnosis.limitations),
    }


def _regime_overview(result: OperatingRegimeResult | None) -> dict[str, Any] | str:
    """只暴露工况段和事件归因，不向大模型发送逐点标签序列。"""

    if result is None:
        return "未执行工况识别"
    return {
        "稳定工况数量": result.state_count,
        "过渡点数量": int(result.transition_mask.sum()),
        "工况分段": result.segments[:12],
        "异常事件工况上下文": result.event_contexts[:10],
        "是否启用过渡期抑制": result.suppression_applied,
        "被抑制事件数": result.suppressed_event_count,
    }


def _top_sensors(events: list[AnomalyEvent]) -> list[str]:
    """统计多个异常事件中最常出现的主导传感器。"""

    counts: dict[str, int] = {}
    for event in events:
        for sensor in event.dominant_sensors:
            counts[sensor] = counts.get(sensor, 0) + 1
    return [name for name, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:5]]


def _forecast_overview(forecast_results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """只向 Agent 摘要暴露预测结论和误差，不重复塞入完整预测数组。"""

    return {
        sensor: {
            key: detail.get(key)
            for key in (
                "模型",
                "模型名称",
                "选择依据",
                "方向",
                "风险",
                "当前值",
                "预测末值",
                "预测末值偏移标准差",
                "回测",
                "频域特征",
                "不确定度",
            )
            if key in detail
        }
        for sensor, detail in forecast_results.items()
    }
