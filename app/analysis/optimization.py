"""从预测、诊断和设备约束生成结构化优化建议。

建议层只负责生成待人工确认的决策草案，不直接控制设备。没有企业安全范围、控制拓扑或
联锁规则时，系统明确保留数值空缺，不用公开数据或通用经验编造参数设定值。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.models import (
    DataProfile,
    EventDiagnosis,
    HistoricalCaseMatch,
    OptimizationRecommendation,
)

ENERGY_KEYWORDS = ("current", "voltage", "power", "energy", "电流", "电压", "功率", "能耗")


def generate_optimization_recommendations(
    profile: DataProfile,
    preprocessing: dict[str, Any],
    forecast_results: dict[str, dict[str, Any]],
    diagnoses: list[EventDiagnosis],
    device_context: dict[str, Any],
    historical_case_matches: dict[int, list[HistoricalCaseMatch]] | None = None,
) -> list[OptimizationRecommendation]:
    """生成参数稳定、能耗和数据质量三类受约束建议。"""

    recommendations: list[OptimizationRecommendation] = []
    sensor_metadata = device_context.get("sensor_metadata", {})
    case_matches = historical_case_matches or {}

    risky_forecasts = sorted(
        (
            (sensor, detail)
            for sensor, detail in forecast_results.items()
            if detail.get("风险") not in {None, "正常"}
        ),
        key=lambda item: (_risk_order(str(item[1].get("风险"))), item[0]),
    )
    for index, (sensor, forecast) in enumerate(risky_forecasts[:5], start=1):
        metadata = sensor_metadata.get(sensor, {})
        safe_range = metadata.get("safe_range")
        direction = str(forecast.get("方向", "变化方向待复核"))
        evidence = [
            f"{sensor} 预测{direction}，风险等级为 {forecast.get('风险', '未知')}",
            f"当前值 {forecast.get('当前值', '未知')}，预测末值 {forecast.get('预测末值', '未知')}",
            f"预测可信度 {forecast.get('不确定度', {}).get('预测可信度', '未知')}",
        ]
        evidence.extend(_diagnosis_evidence(sensor, diagnoses))
        evidence.extend(_historical_case_evidence(sensor, diagnoses, case_matches))
        recommendations.append(
            OptimizationRecommendation(
                recommendation_id=f"OPT-PARAM-{index:03d}",
                category="参数稳定",
                target=sensor,
                action=(
                    "先核对工况指令、测点校准和关联设备状态；确认趋势真实后，"
                    "由工艺人员分级调整相关控制参数，使该指标回到已确认健康区间"
                ),
                adjustment_direction=_adjustment_direction(direction),
                suggested_range=_safe_range_text(safe_range),
                confidence=_forecast_confidence(forecast),
                evidence=tuple(evidence[:6]),
                constraints=(
                    "调整前必须核对设备手册、工艺上下限、报警阈值和联锁条件",
                    "一次只调整一个可控参数，并保留调整前基线",
                    "建议由工艺与设备运维共同确认，系统不直接下发控制指令",
                ),
                validation_metrics=(
                    sensor,
                    "异常风险分数",
                    "关联测点同步变化",
                    "产品质量或设备负载指标",
                ),
                observation_window=_observation_window(profile.sampling_seconds),
                rollback_condition=(
                    "风险分数继续上升、关联测点出现反向恶化、触发报警/联锁，或产品质量下降时立即回退"
                ),
            )
        )

    energy_sensors = [
        sensor
        for sensor in profile.sensor_columns
        if any(keyword in sensor.casefold() for keyword in ENERGY_KEYWORDS)
    ]
    if energy_sensors:
        energy_evidence = _energy_evidence(energy_sensors, forecast_results, diagnoses)
        recommendations.append(
            OptimizationRecommendation(
                recommendation_id="OPT-ENERGY-001",
                category="能耗优化",
                target="、".join(energy_sensors[:4]),
                action=(
                    "在异常事件已排除且生产任务允许时，比较稳定工况下的电气负载基线，"
                    "检查空载运行、频繁启停和机械阻力，并分阶段优化负载或启停策略"
                ),
                adjustment_direction="优先减少无效空载、异常机械阻力和非必要启停，不追求单点最低电流",
                suggested_range="缺少功率、产量和企业能耗基准，暂不输出节能百分比或控制设定值",
                confidence="中" if energy_evidence else "低",
                evidence=tuple(
                    energy_evidence
                    or ["已识别电气测点，但当前数据不足以计算单位产量能耗"]
                ),
                constraints=(
                    "不得以降低能耗为由绕过安全联锁或降低必要工艺负载",
                    "需要同步记录产量、质量、负载和环境条件，避免只比较瞬时电流",
                    "企业能耗基准建立前不宣称节能率",
                ),
                validation_metrics=(
                    "单位产量能耗",
                    "Current/Voltage/Power 稳定性",
                    "异常事件数",
                    "产品质量与节拍",
                ),
                observation_window="至少覆盖一个完整生产周期，并与相同工况历史基线比较",
                rollback_condition="设备风险、产品质量或生产节拍任一项恶化时恢复原策略",
            )
        )

    if preprocessing.get("raw_missing_count", 0) or preprocessing.get(
        "time_alignment_applied"
    ):
        recommendations.append(
            OptimizationRecommendation(
                recommendation_id="OPT-DATA-001",
                category="采集质量",
                target="数据采集链路",
                action="核查时间同步、断点补传和传感器通信，减少依赖算法填补的数据区间",
                adjustment_direction="降低缺失率和不规则采样比例",
                suggested_range=(
                    "目标应由企业数据 SLA 确认；当前仅要求处理后无残余缺失，不能替代采集修复"
                ),
                confidence="高",
                evidence=(
                    f"原始缺失 {preprocessing.get('raw_missing_count', 0)} 个点",
                    f"时间对齐新增 {preprocessing.get('inserted_row_count', 0)} 个采样点",
                    f"不规则采样比例 {float(preprocessing.get('irregular_sampling_ratio', 0)):.2%}",
                ),
                constraints=(
                    "保留原始文件和处理日志，禁止用插值数据覆盖源数据",
                    "长缺口期间的设备状态必须结合控制日志和现场记录确认",
                ),
                validation_metrics=("原始缺失率", "采样周期偏差", "断点补传成功率"),
                observation_window="连续观察至少一个完整采集班次",
                rollback_condition="修复导致时间戳重复、乱序或单位变化时撤回配置并重新校验",
            )
        )

    if not recommendations:
        recommendations.append(
            OptimizationRecommendation(
                recommendation_id="OPT-MONITOR-001",
                category="稳定运行",
                target="当前监测对象",
                action="保持现有参数，继续积累同工况健康基线和能耗基准，不进行无证据调参",
                adjustment_direction="保持",
                suggested_range="等待企业确认设备安全范围和控制参数后再生成数值建议",
                confidence="中",
                evidence=("当前未发现需要提前干预的预测风险",),
                constraints=("持续监测不等于永久健康，仍需按维护计划巡检",),
                validation_metrics=("异常事件数", "趋势风险", "数据质量"),
                observation_window="按企业维护周期持续观察",
                rollback_condition="出现持续趋势偏移或异常事件时重新分析并升级处置",
            )
        )
    return recommendations[:8]


def _diagnosis_evidence(sensor: str, diagnoses: list[EventDiagnosis]) -> list[str]:
    evidence: list[str] = []
    for diagnosis in diagnoses[:5]:
        if not any(item.get("传感器") == sensor for item in diagnosis.sensor_changes):
            continue
        if diagnosis.primary_candidate:
            evidence.append(
                f"事件 {diagnosis.event_number} 首要候选为"
                f"{diagnosis.primary_candidate.name}（{diagnosis.primary_candidate.confidence:.0%}）"
            )
    return evidence


def _historical_case_evidence(
    sensor: str,
    diagnoses: list[EventDiagnosis],
    matches: dict[int, list[HistoricalCaseMatch]],
) -> list[str]:
    evidence: list[str] = []
    for diagnosis in diagnoses:
        if not any(item.get("传感器") == sensor for item in diagnosis.sensor_changes):
            continue
        for match in matches.get(diagnosis.event_number, [])[:1]:
            evidence.append(
                f"历史闭环案例 {match.case_id} 确认原因为 {match.confirmed_cause}，"
                f"相似度 {match.similarity:.0%}"
            )
    return evidence


def _energy_evidence(
    sensors: list[str],
    forecasts: dict[str, dict[str, Any]],
    diagnoses: list[EventDiagnosis],
) -> list[str]:
    evidence: list[str] = []
    for sensor in sensors:
        forecast = forecasts.get(sensor)
        if forecast:
            evidence.append(
                f"{sensor} 预测{forecast.get('方向', '未知')}，风险 {forecast.get('风险', '未知')}"
            )
    causes = Counter(
        diagnosis.primary_candidate.name
        for diagnosis in diagnoses
        if diagnosis.primary_candidate is not None
    )
    if causes:
        cause, count = causes.most_common(1)[0]
        evidence.append(f"{count} 个事件的首要候选根因集中于 {cause}")
    return evidence[:5]


def _adjustment_direction(direction: str) -> str:
    if "上升" in direction:
        return "优先抑制继续上升并回归经企业确认的健康区间"
    if "下降" in direction:
        return "优先阻止继续下降并回归经企业确认的健康区间"
    return "先稳定波动，再由工艺人员确认是否需要改变设定"


def _safe_range_text(safe_range: Any) -> str:
    if not isinstance(safe_range, list) or len(safe_range) != 2:
        return "设备安全范围未知，数值区间待企业设备手册和工艺负责人确认"
    lower, upper = safe_range
    if lower is None and upper is None:
        return "设备安全范围未知，数值区间待企业设备手册和工艺负责人确认"
    return f"仅允许在设备配置确认范围 [{lower if lower is not None else '-∞'}, {upper if upper is not None else '+∞'}] 内分级调整"


def _forecast_confidence(forecast: dict[str, Any]) -> str:
    confidence = str(forecast.get("不确定度", {}).get("预测可信度", "低"))
    return confidence if confidence in {"高", "中", "低"} else "低"


def _observation_window(sampling_seconds: float | None) -> str:
    if sampling_seconds is None:
        return "至少观察一个完整工况周期，并与调整前同工况基线比较"
    seconds = max(30, round(sampling_seconds * 30))
    return f"每次调整后至少观察 30 个采样点（约 {seconds} 秒）并与调整前基线比较"


def _risk_order(risk: str) -> int:
    return {"高风险": 0, "需关注": 1, "中风险": 1, "低风险": 2}.get(risk, 3)
