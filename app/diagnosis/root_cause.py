"""基于多源时序证据的确定性根因排序与工单草案生成。

本模块不调用大模型，也不依赖企业知识库。它把异常强度、事件前后测点变化、相关关系、
时滞、工况切换和趋势预测组合成可审计分数。内置模式只提供通用排查假设，因此最高置信度
受到限制；企业资料接入后可增加设备专属规则并提高证据上限。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from app.diagnosis.patterns import BUILTIN_FAULT_PATTERNS, FaultPattern, classify_sensor
from app.models import (
    AnomalyEvent,
    EventDiagnosis,
    HistoricalCaseMatch,
    OperatingRegimeResult,
    RootCauseCandidate,
    WorkOrderDraft,
)

BUILTIN_CONFIDENCE_CAP = 0.78


def diagnose_root_causes(
    dataframe: pd.DataFrame,
    sensor_columns: list[str],
    events: list[AnomalyEvent],
    relationship_diagnostics: list[dict[str, Any]],
    operating_regimes: OperatingRegimeResult | None,
    trend_summary: dict[str, dict[str, Any]],
    forecast_results: dict[str, dict[str, Any]],
    case_matcher: Callable[
        [list[dict[str, Any]], list[str], str],
        list[HistoricalCaseMatch],
    ]
    | None = None,
    historical_matches_output: dict[int, list[HistoricalCaseMatch]] | None = None,
) -> tuple[list[EventDiagnosis], list[WorkOrderDraft]]:
    """为每个异常事件生成候选根因排序和结构化工单草案。"""

    relationship_map = {
        int(item.get("事件编号", 0)): item for item in relationship_diagnostics
    }
    regime_map = {
        int(item.get("事件编号", 0)): item
        for item in (operating_regimes.event_contexts if operating_regimes else [])
    }
    diagnoses: list[EventDiagnosis] = []
    work_orders: list[WorkOrderDraft] = []
    historical_matches: dict[int, list[HistoricalCaseMatch]] = {}
    # 本地诊断和工单属于确定性业务结果，应覆盖全部异常事件。
    # 面向大模型的摘要会在 AnalysisResult.to_summary() 中单独限量，避免把两个边界混在一起。
    for event_number, event in enumerate(events, start=1):
        sensor_changes = _event_sensor_changes(dataframe, sensor_columns, event)
        regime_context = str(
            regime_map.get(event_number, {}).get("工况判断", "未执行工况归因")
        )
        case_matches = (
            case_matcher(sensor_changes, event.dominant_sensors, regime_context)
            if case_matcher
            else []
        )
        if case_matches:
            historical_matches[event_number] = case_matches
        candidates = _rank_candidates(
            event,
            sensor_changes,
            relationship_map.get(event_number),
            regime_map.get(event_number),
            trend_summary,
            forecast_results,
        )
        candidates.extend(_historical_case_candidates(case_matches))
        candidates.sort(
            key=lambda item: (
                item.confidence,
                len(item.supporting_evidence),
                -len(item.missing_evidence),
            ),
            reverse=True,
        )
        primary = candidates[0] if candidates else None
        actions = _merge_actions(primary, event, regime_context)
        diagnosis = EventDiagnosis(
            event_number=event_number,
            event_start=event.start_time,
            event_end=event.end_time,
            risk_level=event.severity,
            diagnosis_status=(
                "形成待验证候选根因" if primary and primary.confidence >= 0.45 else "证据不足"
            ),
            primary_candidate=primary,
            candidates=tuple(candidates[:3]),
            sensor_changes=tuple(sensor_changes[:6]),
            regime_context=regime_context,
            work_order_actions=actions,
            limitations=(
                "当前候选来自内置通用故障模式，不是企业设备专属知识。",
                "统计相关、时滞和变化方向只能缩小排查范围，不能单独证明物理因果。",
                "需要结合设备拓扑、控制指令、维修记录和现场复测完成故障确认。",
            ),
        )
        diagnoses.append(diagnosis)
        work_orders.append(_build_work_order(diagnosis, event))
    if historical_matches_output is not None:
        historical_matches_output.update(historical_matches)
    return diagnoses, work_orders


def _historical_case_candidates(
    matches: list[HistoricalCaseMatch],
) -> list[RootCauseCandidate]:
    """将相似历史工单聚合为候选根因，保留案例来源和人工复核边界。"""

    grouped: dict[str, list[HistoricalCaseMatch]] = defaultdict(list)
    for match in matches:
        grouped[match.confirmed_cause].append(match)

    candidates: list[RootCauseCandidate] = []
    for confirmed_cause, cases in grouped.items():
        cases.sort(key=lambda item: item.similarity, reverse=True)
        best = cases[0]
        confidence = min(0.42 + 0.32 * best.similarity + 0.04 * (len(cases) - 1), 0.76)
        evidence = [
            (
                f"历史闭环案例 {item.source_record_id} 的现场确认根因为"
                f"“{item.confirmed_cause}”，当前事件相似度 {item.similarity:.0%}"
            )
            for item in cases[:3]
        ]
        for item in cases[:2]:
            if item.matched_sensor_groups:
                evidence.append(
                    "共同异常物理量：" + "、".join(item.matched_sensor_groups)
                )
            if item.feedback_note:
                evidence.append(f"历史处置反馈：{item.feedback_note[:160]}")
        candidates.append(
            RootCauseCandidate(
                pattern_id=f"historical_case:{best.source_record_id}",
                name=confirmed_cause,
                category="历史闭环案例",
                confidence=round(confidence, 4),
                confidence_level=_confidence_level(confidence),
                supporting_evidence=tuple(dict.fromkeys(evidence))[:8],
                missing_evidence=(
                    "历史相似不等于当前故障确诊，仍需核对设备型号、负载和控制指令。",
                    "案例库规模和覆盖工况有限，现场反馈可能存在标注偏差。",
                ),
                verification_steps=(
                    f"优先复核与历史根因“{confirmed_cause}”相关的设备部件和控制记录",
                    "对照历史处置前后数据，确认当前事件是否具有相同恢复特征",
                    "完成现场复测后回写本次真实根因，继续扩充案例库",
                ),
                source=(
                    f"历史已闭环工单：{best.source_record_id}"
                    f"（共命中 {len(cases)} 个同根因案例）"
                ),
            )
        )
    return candidates


def _event_sensor_changes(
    dataframe: pd.DataFrame,
    sensor_columns: list[str],
    event: AnomalyEvent,
    minimum_context: int = 30,
) -> list[dict[str, Any]]:
    """计算事件期相对事件前稳定窗口的变化方向和稳健标准化幅度。"""

    event_length = max(1, event.end_index - event.start_index + 1)
    context = max(minimum_context, event_length * 2)
    baseline_start = max(0, event.start_index - context)
    baseline_end = event.start_index - 1
    if baseline_end < baseline_start:
        baseline_start = 0
        baseline_end = max(0, event.start_index)

    changes: list[dict[str, Any]] = []
    for sensor in sensor_columns:
        series = pd.to_numeric(dataframe[sensor], errors="coerce").interpolate(
            limit_direction="both"
        )
        baseline = series.loc[baseline_start:baseline_end]
        event_values = series.loc[event.start_index : event.end_index]
        if baseline.empty or event_values.empty:
            continue
        baseline_median = float(baseline.median())
        baseline_mad = float(np.median(np.abs(baseline - baseline_median)))
        robust_scale = max(1.4826 * baseline_mad, float(baseline.std(ddof=0)), 1e-9)
        event_median = float(event_values.median())
        delta_z = (event_median - baseline_median) / robust_scale
        direction = "up" if delta_z >= 0.8 else "down" if delta_z <= -0.8 else "flat"
        changes.append(
            {
                "传感器": sensor,
                "类别": classify_sensor(sensor),
                "方向": {"up": "上升", "down": "下降", "flat": "无明显方向"}[direction],
                "direction_code": direction,
                "事件前中位数": round(baseline_median, 6),
                "事件期中位数": round(event_median, 6),
                "变化标准差": round(float(delta_z), 4),
                "异常分数": round(float(event.sensor_scores.get(sensor, 0.0)), 4),
            }
        )
    changes.sort(
        key=lambda item: (
            abs(float(item["变化标准差"])),
            float(item["异常分数"]),
        ),
        reverse=True,
    )
    return changes


def _rank_candidates(
    event: AnomalyEvent,
    sensor_changes: list[dict[str, Any]],
    relationship: dict[str, Any] | None,
    regime_context: dict[str, Any] | None,
    trend_summary: dict[str, dict[str, Any]],
    forecast_results: dict[str, dict[str, Any]],
) -> list[RootCauseCandidate]:
    """按模式覆盖度、方向一致性和独立证据数量排序候选根因。"""

    group_changes = _group_sensor_changes(sensor_changes)
    candidates = [
        _score_pattern(
            pattern,
            event,
            group_changes,
            sensor_changes,
            relationship,
            regime_context,
            trend_summary,
            forecast_results,
        )
        for pattern in BUILTIN_FAULT_PATTERNS
    ]
    candidates.sort(
        key=lambda item: (
            item.confidence,
            len(item.supporting_evidence),
            -len(item.missing_evidence),
        ),
        reverse=True,
    )
    return candidates


def _group_sensor_changes(
    sensor_changes: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """按物理量类别聚合测点，保留同类中变化最显著的证据。"""

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in sensor_changes:
        groups[str(item["类别"])].append(item)
    for values in groups.values():
        values.sort(key=lambda item: abs(float(item["变化标准差"])), reverse=True)
    return dict(groups)


def _score_pattern(
    pattern: FaultPattern,
    event: AnomalyEvent,
    group_changes: dict[str, list[dict[str, Any]]],
    sensor_changes: list[dict[str, Any]],
    relationship: dict[str, Any] | None,
    regime_context: dict[str, Any] | None,
    trend_summary: dict[str, dict[str, Any]],
    forecast_results: dict[str, dict[str, Any]],
) -> RootCauseCandidate:
    """把每一类证据独立计分，并保留可读的支持与缺失说明。"""

    score = 0.08
    evidence: list[str] = []
    missing: list[str] = []
    active_groups = {
        group
        for group, changes in group_changes.items()
        if changes and abs(float(changes[0].get("变化标准差", 0.0))) >= 0.8
    }
    present_groups = set(pattern.sensor_groups).intersection(active_groups)
    if present_groups:
        coverage = len(present_groups) / max(len(pattern.sensor_groups), 1)
        score += 0.16 * coverage
        sensors = [
            group_changes[group][0]["传感器"]
            for group in present_groups
            if group_changes[group]
        ]
        evidence.append(f"参与事件的相关测点：{'、'.join(str(item) for item in sensors)}")

    required_present = [group for group in pattern.required_groups if group in active_groups]
    if pattern.required_groups:
        score += 0.18 * len(required_present) / len(pattern.required_groups)
        absent = [group for group in pattern.required_groups if group not in active_groups]
        if absent:
            score -= 0.10 * len(absent) / len(pattern.required_groups)
            missing.append(f"缺少关键测点类别：{'、'.join(absent)}")

    for group, expected in pattern.directional_rules:
        strongest = group_changes.get(group, [{}])[0]
        actual = strongest.get("direction_code")
        if actual == expected:
            magnitude = min(abs(float(strongest.get("变化标准差", 0.0))) / 5.0, 1.0)
            score += 0.11 + 0.04 * magnitude
            evidence.append(
                f"{strongest['传感器']} 在事件期{strongest['方向']} "
                f"{abs(float(strongest['变化标准差'])):.2f} 个稳健标准差"
            )
        elif actual and actual != "flat":
            score -= 0.07
            missing.append(
                f"{strongest.get('传感器', group)} 的变化方向与该模式不一致"
            )
        else:
            missing.append(f"{group} 未出现可确认的方向性变化")

    relation_score, relation_evidence = _relationship_evidence(
        pattern,
        relationship,
        group_changes,
    )
    score += relation_score
    evidence.extend(relation_evidence)

    dominant_groups = {
        classify_sensor(sensor) for sensor in event.dominant_sensors
    }
    matching_dominant_sensors = [
        sensor
        for sensor in event.dominant_sensors
        if classify_sensor(sensor) in pattern.sensor_groups
    ]
    if dominant_groups.intersection(pattern.sensor_groups):
        score += 0.08
        evidence.append(
            f"模式覆盖事件主导传感器：{'、'.join(matching_dominant_sensors)}"
        )

    trend_evidence = _trend_evidence(pattern, trend_summary, forecast_results)
    if trend_evidence:
        score += min(0.10, 0.04 * len(trend_evidence))
        evidence.extend(trend_evidence)

    overlap = float((regime_context or {}).get("过渡期重合率", 0.0))
    if overlap >= 0.5:
        if pattern.pattern_id == "multivariable_process_change":
            score += 0.16
            evidence.append(f"事件与工况切换重合 {overlap:.0%}")
        else:
            score -= 0.08
            missing.append("事件高度重合工况切换，需先排除正常操作影响")
    elif regime_context:
        score += 0.03
        evidence.append("事件主要发生在稳定工况内")

    if pattern.pattern_id == "sensor_or_acquisition":
        active = [
            item for item in sensor_changes if abs(float(item["变化标准差"])) >= 1.5
        ]
        if len(active) == 1:
            score += 0.25
            evidence.append("仅单一测点显著偏离，测量链路异常需要优先排除")
        elif len(active) >= 3:
            score -= 0.08
            missing.append("多个物理量同步变化，不支持单点测量异常作为首要解释")

    if pattern.pattern_id == "multivariable_process_change":
        active_groups = {
            str(item["类别"])
            for item in sensor_changes
            if abs(float(item["变化标准差"])) >= 1.5
        }
        if len(active_groups) >= 3:
            score += 0.18
            evidence.append(f"{len(active_groups)} 类物理量同步偏离健康状态")

    confidence = round(min(max(score, 0.05), BUILTIN_CONFIDENCE_CAP), 4)
    missing.extend(item for item in pattern.missing_information if item not in missing)
    return RootCauseCandidate(
        pattern_id=pattern.pattern_id,
        name=pattern.name,
        category=pattern.category,
        confidence=confidence,
        confidence_level=_confidence_level(confidence),
        supporting_evidence=tuple(dict.fromkeys(evidence))[:8],
        missing_evidence=tuple(dict.fromkeys(missing))[:6],
        verification_steps=pattern.verification_steps,
    )


def _relationship_evidence(
    pattern: FaultPattern,
    relationship: dict[str, Any] | None,
    group_changes: dict[str, list[dict[str, Any]]],
) -> tuple[float, list[str]]:
    """从相关变化和时滞中提取与模式传感器组合一致的证据。"""

    if not relationship or not pattern.relationship_groups:
        return 0.0, []
    score = 0.0
    evidence: list[str] = []
    for relation in relationship.get("重点关系", []):
        left = str(relation.get("传感器A", ""))
        right = str(relation.get("传感器B", ""))
        pair = {classify_sensor(left), classify_sensor(right)}
        if not any(pair == set(expected) for expected in pattern.relationship_groups):
            continue
        change = abs(float(relation.get("相关性变化", 0.0)))
        lag_corr = abs(float(relation.get("时滞相关系数", 0.0)))
        relation_strength = min(0.12, 0.08 * change + 0.05 * lag_corr)
        score += relation_strength
        evidence.append(
            f"{left} 与 {right} 的关系发生变化：{relation.get('时滞解释', '动态联动')}"
        )
    # 只保留模式涉及的物理量类别，参数用于让调用意图更明确。
    _ = group_changes
    return min(score, 0.16), evidence[:2]


def _trend_evidence(
    pattern: FaultPattern,
    trend_summary: dict[str, dict[str, Any]],
    forecast_results: dict[str, dict[str, Any]],
) -> list[str]:
    """补充事件外的持续趋势和未来风险证据。"""

    evidence: list[str] = []
    for sensor, detail in trend_summary.items():
        if classify_sensor(sensor) not in pattern.sensor_groups:
            continue
        if detail.get("风险") in {"需关注", "高风险"}:
            evidence.append(f"{sensor} 近期趋势为{detail.get('方向')}，风险为{detail.get('风险')}")
    for sensor, detail in forecast_results.items():
        if classify_sensor(sensor) not in pattern.sensor_groups:
            continue
        if detail.get("风险") not in {None, "正常"}:
            evidence.append(f"{sensor} 未来预测为{detail.get('风险')}，方向{detail.get('方向')}")
    return list(dict.fromkeys(evidence))[:3]


def _merge_actions(
    primary: RootCauseCandidate | None,
    event: AnomalyEvent,
    regime_context: str,
) -> tuple[str, ...]:
    """按“先确认操作与测量，再检查设备，最后回写”形成现场顺序。"""

    actions = []
    if regime_context == "工况切换期事件":
        actions.append("先核对事件时刻的阀门动作、负载变化、启停操作和控制指令")
    actions.append(f"冻结事件 {event.start_time} 至 {event.end_time} 的原始数据和操作日志")
    actions.append(f"复核 {'、'.join(event.dominant_sensors)} 的量程、接线、时间同步和采样状态")
    if primary:
        actions.extend(primary.verification_steps)
    actions.append("记录现场确认根因、处置动作、恢复时间和复测结果，并回写事件标签")
    return tuple(dict.fromkeys(actions))[:7]


def _build_work_order(
    diagnosis: EventDiagnosis,
    event: AnomalyEvent,
) -> WorkOrderDraft:
    """把诊断结果转换为不依赖数据库的可序列化工单草案。"""

    primary = diagnosis.primary_candidate
    evidence = primary.supporting_evidence[:4] if primary else ()
    title = (
        f"核查：{primary.name}" if primary else "异常事件现场复核"
    )
    priority_map = {"高风险": "P1", "中风险": "P2", "低风险": "P3"}
    return WorkOrderDraft(
        work_order_id=f"WO-E{diagnosis.event_number:03d}-{event.start_index:06d}",
        event_number=diagnosis.event_number,
        priority=priority_map.get(event.severity, "P3"),
        title=title,
        status="待确认",
        assigned_role="设备运维与工艺联合复核",
        actions=diagnosis.work_order_actions,
        evidence_summary=evidence,
        required_feedback=(
            "确认根因或标记为正常工况",
            "记录采取的处置动作和执行时间",
            "记录复测数据、设备恢复状态和是否再次告警",
        ),
    )


def _confidence_level(confidence: float) -> str:
    """把连续置信度映射为现场易理解等级。"""

    if confidence >= 0.65:
        return "较高"
    if confidence >= 0.45:
        return "中等"
    return "较低"


def diagnosis_to_dict(diagnosis: EventDiagnosis) -> dict[str, Any]:
    """提供稳定序列化入口，供 API 和后续数据库层复用。"""

    return asdict(diagnosis)
