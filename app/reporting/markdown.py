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
        "## 2. 智能体执行链",
        "",
        "系统按以下步骤自动编排确定性分析模块；记录内容为工具调用事实，不包含大模型隐式推理。",
        "",
        "| 步骤 | 执行模块 | 状态 | 核心输出 | 耗时 |",
        "| --- | --- | --- | --- | ---: |",
        *[
            (
                f"| {index}. {step.title} | `{step.module}` | {_trace_status(step.status)} | "
                f"{_format_trace_output(step.output_summary)} | "
                f"{_format_trace_duration(step.duration_seconds)} |"
            )
            for index, step in enumerate(result.execution_trace, start=1)
        ],
        "",
        "> 每一步的输出均保留适用边界；候选根因和工单草案仍需运维人员现场确认。",
        "",
        "## 3. 数据画像",
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

    lines.extend(["", "## 4. 异常诊断", ""])
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

    lines.extend(["", "## 5. 标签评估", ""])
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

    lines.extend(["", "## 6. 趋势与漂移", ""])
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

    lines.extend(["", "## 7. 工况识别与切换证据", ""])
    regimes = result.operating_regimes
    if regimes is None:
        lines.append("本次未执行无监督工况识别。")
    else:
        lines.extend(
            [
                f"- 识别稳定工况数量：{regimes.state_count}",
                f"- 工况过渡点数量：{int(regimes.transition_mask.sum())}",
                f"- 是否启用过渡期弱告警抑制：{'是' if regimes.suppression_applied else '否'}",
                f"- 被抑制事件数量：{regimes.suppressed_event_count}",
                "",
                "| 事件 | 主要工况 | 过渡期重合率 | 峰值切换分数 | 工况判断 |",
                "| ---: | --- | ---: | ---: | --- |",
            ]
        )
        for context in regimes.event_contexts:
            lines.append(
                f"| {context['事件编号']} | {context['主要工况']} | "
                f"{context['过渡期重合率']:.2%} | {context['峰值切换分数']} | "
                f"{context['工况判断']} |"
            )
        lines.extend(
            [
                "",
                "> 工况切换重合只用于提示负载或控制动作干扰，不能直接把异常事件判为误报。",
            ]
        )

    lines.extend(["", "## 8. 多传感器关系证据", ""])
    if not result.relationship_diagnostics:
        lines.append("当前异常事件不足以形成稳定的相关性或时滞判断。")
    else:
        for diagnostic in result.relationship_diagnostics:
            lines.extend(
                [
                    f"### 事件 {diagnostic['事件编号']}",
                    "",
                    f"- 关系结论：{diagnostic['关系结论']}",
                    f"- 主导传感器：{', '.join(diagnostic['主导传感器'])}",
                    f"- 使用边界：{diagnostic['使用边界']}",
                    "",
                    "| 传感器 A | 传感器 B | 事件前相关 | 事件期相关 | 相关性变化 | 最佳时滞 | 时滞解释 |",
                    "| --- | --- | ---: | ---: | ---: | ---: | --- |",
                ]
            )
            for relation in diagnostic["重点关系"]:
                lines.append(
                    f"| {relation['传感器A']} | {relation['传感器B']} | "
                    f"{relation['事件前相关系数']} | {relation['事件期相关系数']} | "
                    f"{relation['相关性变化']} | {relation['最佳时滞']} | "
                    f"{relation['时滞解释']} |"
                )
            lines.append("")

    lines.extend(["", "## 9. 候选根因与证据链", ""])
    if not result.event_diagnoses:
        lines.append("当前没有异常事件需要生成候选根因。")
    else:
        for diagnosis in result.event_diagnoses:
            primary = diagnosis.primary_candidate
            lines.extend(
                [
                    f"### 事件 {diagnosis.event_number}",
                    "",
                    f"- 诊断状态：{diagnosis.diagnosis_status}",
                    f"- 工况上下文：{diagnosis.regime_context}",
                ]
            )
            if primary:
                lines.extend(
                    [
                        f"- 首要候选：**{primary.name}**",
                        f"- 类别：{primary.category}",
                        f"- 置信度：{primary.confidence:.0%}（{primary.confidence_level}）",
                        f"- 规则来源：{primary.source}",
                        "",
                        "支持证据：",
                        *[f"- {item}" for item in primary.supporting_evidence],
                        "",
                        "证据缺口：",
                        *[f"- {item}" for item in primary.missing_evidence],
                    ]
                )
            lines.extend(
                [
                    "",
                    "> 当前候选仅用于安排现场排查顺序，不能替代设备故障确认。",
                    "",
                ]
            )
            case_matches = result.historical_case_matches.get(diagnosis.event_number, [])
            if case_matches:
                lines.extend(
                    [
                        "历史闭环案例：",
                        *[
                            (
                                f"- {item.confirmed_cause}：相似度 {item.similarity:.0%}，"
                                f"来源工单 {item.source_record_id}"
                            )
                            for item in case_matches[:3]
                        ],
                        "",
                    ]
                )

    lines.extend(["", "## 10. 待确认工单草案", ""])
    if not result.work_order_drafts:
        lines.append("当前没有待生成的处置工单。")
    else:
        for work_order in result.work_order_drafts:
            lines.extend(
                [
                    f"### {work_order.work_order_id}｜{work_order.title}",
                    "",
                    f"- 优先级：{work_order.priority}",
                    f"- 状态：{work_order.status}",
                    f"- 建议角色：{work_order.assigned_role}",
                    "- 处置步骤：",
                    *[
                        f"  {index}. {action}"
                        for index, action in enumerate(work_order.actions, start=1)
                    ],
                    "- 必须回写：",
                    *[f"  - {item}" for item in work_order.required_feedback],
                    "",
                ]
            )

    lines.extend(["", "## 11. 运维处置建议", ""])
    for index, recommendation in enumerate(result.recommendations, start=1):
        lines.append(f"{index}. {recommendation}")

    lines.extend(["", "## 12. 趋势预测与风险预警", ""])
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
            "## 13. 方法说明",
            "",
            (
                "系统先由确定性算法完成数据校验、稳健异常检测、无监督工况识别、标签评估和趋势计算，"
                "并比较异常事件前后的传感器相关性与差分时滞。确定性根因引擎再把事件前后"
                "变化方向、关系证据、工况上下文和预测趋势与通用故障模式匹配，输出候选根因、"
                "证据缺口和工单草案。"
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


def _trace_status(status: str) -> str:
    """把内部英文状态转换为报告中的稳定中文表达。"""

    return {"completed": "已完成", "skipped": "已跳过", "failed": "失败"}.get(
        status,
        status,
    )


def _format_trace_duration(duration_seconds: float | None) -> str:
    """未执行步骤不伪造耗时，已执行步骤统一使用秒。"""

    return f"{duration_seconds:.4f} 秒" if duration_seconds is not None else "-"


def _format_trace_output(output_summary: dict[str, object]) -> str:
    """将轨迹核心输出压缩成单行，避免 Markdown 表格承载原始数据。"""

    labels = {
        "row_count": "数据点",
        "column_count": "字段",
        "time_column": "时间列",
        "profile_id": "配置",
        "display_name": "设备",
        "match_mode": "匹配方式",
        "match_score": "匹配度",
        "sensor_count": "传感器",
        "missing_total": "缺失值",
        "sampling_seconds": "采样间隔(秒)",
        "detector": "检测器",
        "anomaly_point_count": "异常点",
        "event_count": "异常事件",
        "state_count": "工况",
        "transition_point_count": "切换点",
        "suppressed_event_count": "抑制事件",
        "event_evidence_count": "证据事件",
        "forecast_sensor_count": "预测测点",
        "diagnosis_count": "诊断",
        "candidate_count": "候选根因",
        "historical_case_match_count": "历史案例命中",
        "work_order_draft_count": "工单草案",
        "reason": "原因",
    }
    return "；".join(
        f"{labels.get(str(key), key)}：{value}" for key, value in output_summary.items()
    ) or "无新增结果"
