"""将分析结果整理为可下载、可追溯的 Markdown 报告。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.models import AnalysisConfig, AnalysisResult


def build_markdown_report(result: AnalysisResult, config: AnalysisConfig) -> str:
    """生成一份面向工程人员的结构化诊断报告。"""

    profile = result.profile
    lines = [
        "# 时察千机工业时序诊断报告",
        "",
        f"> 生成时间：{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 数据来源：`{result.source_path}`",
        "",
        "## 1. 任务概览",
        "",
        f"- 数据点数：{profile.row_count}",
        f"- 时间范围：{profile.start_time} 至 {profile.end_time}",
        f"- 采样间隔：{_format_number(profile.sampling_seconds)} 秒",
        f"- 传感器数量：{len(profile.sensor_columns)}",
        f"- 缺失值总数：{profile.missing_total}",
        f"- 检测器：{result.detector_name}",
        f"- 检测阈值：{config.threshold}",
        f"- 滚动窗口：{config.rolling_window} 个采样点",
        "",
        "## 2. 数据画像",
        "",
        "| 传感器 | 缺失率 | 最小值 | 最大值 | 均值 | 标准差 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]

    for sensor in profile.sensors:
        lines.append(
            f"| {sensor.name} | {sensor.missing_rate:.2%} | "
            f"{sensor.min_value:.4f} | {sensor.max_value:.4f} | "
            f"{sensor.mean_value:.4f} | {sensor.std_value:.4f} |"
        )

    lines.extend(["", "## 3. 异常诊断", ""])
    if not result.events:
        lines.append("当前参数下未识别到满足持续时长要求的异常事件。")
    else:
        lines.extend(
            [
                f"共识别到 **{len(result.events)}** 个异常事件，以下列出风险最高的前 10 个：",
                "",
                "| 风险 | 开始时间 | 结束时间 | 持续点数 | 峰值分数 | 主导传感器 |",
                "| --- | --- | --- | ---: | ---: | --- |",
            ]
        )
        for event in result.events[:10]:
            lines.append(
                f"| {event.severity} | {event.start_time} | {event.end_time} | "
                f"{event.duration_points} | {event.peak_score:.2f} | "
                f"{', '.join(event.dominant_sensors)} |"
            )

    lines.extend(["", "## 4. 标签评估", ""])
    if result.metrics is None:
        lines.append("数据中没有 `anomaly` 标签，本次不计算监督评估指标。")
    else:
        metrics = result.metrics
        lines.extend(
            [
                f"- Precision：{metrics.precision:.4f}",
                f"- Recall：{metrics.recall:.4f}",
                f"- F1：{metrics.f1_score:.4f}",
                f"- PR-AUC：{metrics.pr_auc:.4f}",
                (
                    f"- 事件级 Precision / Recall / F1：{metrics.event_precision:.4f} / "
                    f"{metrics.event_recall:.4f} / {metrics.event_f1_score:.4f}"
                ),
                (
                    f"- 真实事件 / 预测事件 / 匹配事件：{metrics.actual_event_count} / "
                    f"{metrics.predicted_event_count} / {metrics.matched_event_count}"
                ),
                f"- 平均检测延迟：{_format_number(metrics.mean_detection_delay)} 个采样点",
                (
                    f"- 误报事件：{metrics.false_positive_event_count}，其中变点相关 "
                    f"{metrics.changepoint_related_false_events} 个"
                    f"（{metrics.changepoint_false_event_rate:.2%}）"
                ),
                (
                    f"- TP / FP / FN / TN：{metrics.true_positive} / {metrics.false_positive} / "
                    f"{metrics.false_negative} / {metrics.true_negative}"
                ),
                "",
                "> 以上指标用于评价当前可解释基线，不代表项目最终模型上限。",
            ]
        )

    lines.extend(["", "## 5. 趋势与漂移", ""])
    if not result.trend_summary:
        lines.append("末段数据未出现明显趋势漂移。")
    else:
        lines.extend(
            [
                "| 传感器 | 方向 | 风险 | 近期均值 | 历史均值 | 均值偏移标准差 |",
                "| --- | --- | --- | ---: | ---: | ---: |",
            ]
        )
        for sensor, detail in result.trend_summary.items():
            lines.append(
                f"| {sensor} | {detail['方向']} | {detail['风险']} | "
                f"{detail['近期均值']} | {detail['历史均值']} | "
                f"{detail['均值偏移标准差']} |"
            )

    lines.extend(["", "## 6. 运维处置建议", ""])
    for index, recommendation in enumerate(result.recommendations, start=1):
        lines.append(f"{index}. {recommendation}")

    lines.extend(["", "## 7. 趋势预测与风险预警", ""])
    if not result.forecast_results:
        lines.append("当前数据长度不足，未生成预测结果。")
    else:
        lines.extend(
            [
                "| 传感器 | 最优模型 | 方向 | 风险 | 可信度 | 当前值 | 预测末值 | RMSE | MAPE |",
                "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for sensor, forecast in result.forecast_results.items():
            backtest = forecast.get("回测", {})
            uncertainty = forecast.get("不确定度", {})
            lines.append(
                f"| {sensor} | {forecast.get('模型名称', forecast.get('模型', '未知'))} | "
                f"{forecast.get('方向', '未知')} | {forecast.get('风险', '未知')} | "
                f"{uncertainty.get('预测可信度', '未知')} | {forecast.get('当前值', '未知')} | "
                f"{forecast.get('预测末值', '未知')} | {backtest.get('RMSE', '未知')} | "
                f"{backtest.get('MAPE', '未知')} |"
            )
    if result.risk_alerts:
        lines.extend(["", "### 预警清单", ""])
        for alert in result.risk_alerts:
            lines.append(
                f"- **{alert['等级']}**｜{alert['类型']}｜传感器：{', '.join(alert['传感器'])}｜"
                f"{alert['建议动作']}"
            )

    lines.extend(
        [
            "",
            "## 8. 方法说明",
            "",
            (
                "系统先由确定性算法完成数据校验、稳健异常检测、标签评估和趋势计算。"
                "预测模块在五类候选模型上开展时间顺序滚动回测，以 RMSE 为主选择最优模型，"
                "并融合历史残差和模型分歧构造 95% 预测区间。之后将结构化结果交给智能体解释。"
                "原始工业数据不会直接交由大模型判断，"
                "从而保证分析过程可复现、证据可追踪、模型可替换。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def save_report(report_text: str, output_path: Path) -> Path:
    """将报告写入 outputs 目录，并返回最终路径。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    return output_path


def _format_number(value: float | None) -> str:
    """友好显示可能为空的采样间隔。"""

    return f"{value:.2f}" if value is not None else "未知"
