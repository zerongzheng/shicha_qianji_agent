"""从异常证据生成可执行的运维建议。"""

from __future__ import annotations

from app.models import AnomalyEvent, DataProfile, EvaluationMetrics

SENSOR_ACTIONS = {
    "pressure": "核查阀门开度、管路堵塞、泄漏和压力传感器零点漂移",
    "temperature": "检查散热、润滑、负载变化及温度传感器安装状态",
    "thermocouple": "复核热电偶接触、补偿导线和局部热源变化",
    "current": "检查电机负载、机械卡阻、供电质量和电流采样回路",
    "voltage": "核查供电波动、变频器输出和电气连接可靠性",
    "accelerometer": "检查轴承、联轴器、紧固状态及旋转部件不平衡",
    "flow": "核查阀门、泵工况、管路阻力及流量计状态",
}


def generate_recommendations(
    profile: DataProfile,
    events: list[AnomalyEvent],
    trends: dict[str, dict],
    metrics: EvaluationMetrics | None,
) -> list[str]:
    """把算法发现转为分优先级的现场处置建议。"""

    recommendations: list[str] = []

    if profile.missing_total:
        recommendations.append(
            f"先处理数据质量：当前共发现 {profile.missing_total} 个传感器缺失值，"
            "应排查采集链路、时间同步和断点补传机制。"
        )

    if not events:
        recommendations.append(
            "当前阈值下未形成连续异常事件，建议保持在线监测，并结合设备工况定期复核阈值。"
        )
    else:
        highest = events[0]
        recommendations.append(
            f"优先复核 {highest.start_time} 至 {highest.end_time} 的{highest.severity}事件，"
            f"重点关注 {', '.join(highest.dominant_sensors)}。"
        )

        handled_actions: set[str] = set()
        for sensor in _collect_top_sensors(events):
            action = _match_sensor_action(sensor)
            if action and action not in handled_actions:
                recommendations.append(f"针对 {sensor}：{action}。")
                handled_actions.add(action)

    if trends:
        risky = [name for name, detail in trends.items() if detail["风险"] != "正常"]
        if risky:
            recommendations.append(
                f"建立趋势预警观察清单：{', '.join(risky)} 已出现持续漂移，"
                "建议与负载、阀门动作和维护记录做时间对齐分析。"
            )

    if metrics and metrics.recall < 0.6:
        recommendations.append(
            "当前基线模型召回率偏低，下一阶段应引入多尺度窗口和时序深度模型，"
            "并在训练集上标定阈值，避免把基线结果作为最终工程指标。"
        )

    if metrics and metrics.changepoint_false_event_rate >= 0.3:
        recommendations.append(
            "当前较多误报集中在工况变点附近，建议启用工况切换识别并对切换窗口采用独立阈值，"
            "避免把阀门动作、启停或负载调整直接判定为设备故障。"
        )

    recommendations.append(
        "现场闭环建议：将告警确认、故障原因和检修结果回写为事件标签，形成可持续迭代的数据资产。"
    )
    return recommendations[:7]


def _collect_top_sensors(events: list[AnomalyEvent]) -> list[str]:
    """按事件出现次数汇总重点传感器。"""

    counts: dict[str, int] = {}
    for event in events[:10]:
        for sensor in event.dominant_sensors:
            counts[sensor] = counts.get(sensor, 0) + 1
    return [name for name, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:5]]


def _match_sensor_action(sensor_name: str) -> str | None:
    """用通用字段关键词匹配运维动作，方便兼容不同企业列名。"""

    normalized = sensor_name.lower().replace(" ", "")
    for keyword, action in SENSOR_ACTIONS.items():
        if keyword in normalized:
            return action
    return "检查该测点的校准状态，并与相邻传感器和设备工况交叉验证"
