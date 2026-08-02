"""项目内部的数据结构定义。

这些数据类相当于各模块之间的“合同”。例如检测算法可以更换，但只要仍然输出
`AnomalyEvent`，报告、页面和 Agent 就不需要跟着修改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class AnalysisConfig:
    """一次分析任务使用的算法参数。"""

    detector: str = "hybrid"
    threshold: float = 4.5
    rolling_window: int = 61
    min_event_length: int = 3
    merge_gap: int = 5
    contamination: float = 0.01
    random_state: int = 42
    use_healthy_baseline: bool = True


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
    forecast_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    risk_alerts: list[dict[str, Any]] = field(default_factory=list)
    report_text: str = ""

    def to_summary(self) -> dict[str, Any]:
        """转换为适合大模型读取的紧凑摘要，避免把原始 CSV 发给模型。"""

        return {
            "数据文件": self.profile.source_name,
            "检测器": self.detector_name,
            "数据点数": self.profile.row_count,
            "传感器数量": len(self.profile.sensor_columns),
            "时间范围": f"{self.profile.start_time} 至 {self.profile.end_time}",
            "异常事件数": len(self.events),
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
            "预测结果": _forecast_overview(self.forecast_results),
            "风险预警": self.risk_alerts,
            "运维建议": self.recommendations,
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
