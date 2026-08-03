"""融合当前异常、预测风险和多传感器联动的结构化预警。"""

from __future__ import annotations

from typing import Any

from app.models import AnomalyEvent, OperatingRegimeResult


def build_risk_alerts(
    forecast_results: dict[str, dict[str, Any]],
    events: list[AnomalyEvent],
    relationship_diagnostics: list[dict[str, Any]] | None = None,
    operating_regimes: OperatingRegimeResult | None = None,
) -> list[dict[str, Any]]:
    """生成风险等级、可信度、触发原因和人工核查动作。

    预警不是简单转述“模型说有风险”，而是把当前异常事件、滚动回测误差、预测区间、
    模型分歧和多测点同步变化整理为可追溯证据。
    """

    alerts: list[dict[str, Any]] = []
    relationship_map = {
        int(item.get("事件编号", 0)): item for item in relationship_diagnostics or []
    }
    regime_map = {
        int(item.get("事件编号", 0)): item
        for item in (operating_regimes.event_contexts if operating_regimes else [])
    }
    for index, event in enumerate(events[:5], start=1):
        evidence = [
            f"异常事件持续 {event.duration_points} 个采样点",
            f"峰值风险分数为 {event.peak_score:.2f}",
            f"主导测点为{'、'.join(event.dominant_sensors)}",
        ]
        diagnostic = relationship_map.get(index)
        if diagnostic:
            evidence.append(str(diagnostic.get("关系结论", "")))
        regime_context = regime_map.get(index)
        if regime_context:
            evidence.append(
                f"{regime_context['工况判断']}，过渡期重合率为"
                f"{float(regime_context['过渡期重合率']):.1%}"
            )
        checks = ["核查主导传感器的量程、接线和采集状态", "核对设备负载与近期操作记录"]
        if diagnostic and diagnostic.get("重点关系"):
            strongest = diagnostic["重点关系"][0]
            checks.append(
                f"优先核查 {strongest['传感器A']} 与 {strongest['传感器B']} 的共同工艺链路："
                f"{strongest['时滞解释']}"
            )
        if regime_context and regime_context.get("工况判断") == "工况切换期事件":
            checks.insert(0, "优先核对阀门动作、负载变化和控制指令是否与事件时间重合")
        alerts.append(
            {
                "alert_id": f"current-event-{index:03d}",
                "类型": "当前异常事件",
                "等级": event.severity,
                "可信度": "高" if event.duration_points >= 5 else "中",
                "状态": "需要确认",
                "传感器": event.dominant_sensors,
                "触发原因": evidence,
                "证据": evidence,
                "人工确认": checks,
                "建议动作": "先核查主导传感器、设备负载和近期操作记录，再确认是否派发检修任务",
            }
        )

    risky_forecasts: dict[str, dict[str, Any]] = {}
    for index, (sensor, forecast) in enumerate(forecast_results.items(), start=1):
        risk = str(forecast.get("风险", "正常"))
        if risk == "正常":
            continue
        risky_forecasts[sensor] = forecast
        backtest = forecast.get("回测", {})
        uncertainty = forecast.get("不确定度", {})
        confidence = str(uncertainty.get("预测可信度", "低"))
        evidence = [
            f"{forecast.get('模型名称', forecast.get('模型', '预测模型'))}判断未来{forecast.get('方向', '变化')}",
            f"预测末值偏移历史波动 {forecast.get('预测末值偏移标准差', 0)} 个标准差",
            f"滚动回测 RMSE 为 {backtest.get('RMSE', '未知')}，MAPE 为 {backtest.get('MAPE', '未知')}",
            f"95% 区间末值宽度为 {uncertainty.get('末值区间宽度', '未知')}，模型平均分歧为 {uncertainty.get('平均模型分歧', '未知')}",
        ]
        checks = ["核查该测点关联的负载和工艺设定", "对照维护记录确认是否存在已知退化过程"]
        alerts.append(
            {
                "alert_id": f"forecast-risk-{index:03d}",
                "类型": "趋势预测预警",
                "等级": risk,
                "可信度": confidence,
                "状态": "提前关注" if confidence != "低" else "建议人工复核",
                "传感器": [sensor],
                "触发原因": evidence,
                "证据": evidence,
                "人工确认": checks,
                "建议动作": "核查该测点关联的负载、工艺参数和维护计划，必要时提前安排人工复核",
            }
        )

    linked_alert = _build_linked_sensor_alert(risky_forecasts)
    if linked_alert:
        alerts.append(linked_alert)
    # 统一按等级、可信度和告警类型排序，避免多传感器联动证据被数量上限挤掉。
    level_order = {"高风险": 0, "需关注": 1, "中风险": 1, "低风险": 2}
    confidence_order = {"高": 0, "中": 1, "低": 2}
    type_order = {"多传感器联动预警": 0, "当前异常事件": 1, "趋势预测预警": 2}
    alerts.sort(
        key=lambda item: (
            level_order.get(str(item.get("等级")), 3),
            confidence_order.get(str(item.get("可信度")), 3),
            type_order.get(str(item.get("类型")), 3),
        )
    )
    return alerts[:10]


def _build_linked_sensor_alert(
    risky_forecasts: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """当多个传感器出现同向风险时形成设备级联动预警。"""

    direction_groups: dict[str, list[str]] = {}
    for sensor, forecast in risky_forecasts.items():
        direction = str(forecast.get("方向", "基本平稳"))
        if direction == "基本平稳":
            continue
        direction_groups.setdefault(direction, []).append(sensor)
    if not direction_groups:
        return None

    direction, sensors = max(direction_groups.items(), key=lambda item: len(item[1]))
    if len(sensors) < 2:
        return None

    confidences = [
        risky_forecasts[sensor].get("不确定度", {}).get("预测可信度", "低")
        for sensor in sensors
    ]
    confidence = "高" if all(item == "高" for item in confidences) else "中"
    evidence = [
        f"{'、'.join(sensors)}共 {len(sensors)} 个测点预测{direction}",
        "多个测点同步变化比单点越界更可能对应设备工况或退化过程",
    ]
    checks = ["核查这些测点是否属于同一设备或工艺链路", "对照阀门动作、负载变化和控制指令时间"]
    return {
        "alert_id": "linked-sensor-risk-001",
        "类型": "多传感器联动预警",
        "等级": "高风险" if confidence == "高" else "需关注",
        "可信度": confidence,
        "状态": "优先核查",
        "传感器": sensors,
        "触发原因": evidence,
        "证据": evidence,
        "人工确认": checks,
        "建议动作": "按设备拓扑核查多测点共同上游因素，优先排除负载突变、控制异常和部件退化",
    }
