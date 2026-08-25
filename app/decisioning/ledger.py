"""把分析结果整理为可审计的智能体决策记录。

执行轨迹回答“系统调用了哪些模块”，决策记录回答“系统基于哪些可核验证据采取了什么
业务动作”。这里不保存大模型思维过程，也不复制原始工业数据，只记录冻结规则、结构化
证据、动作、责任对象、人工闸门和回退条件，便于运维复核与竞赛展示。
"""

from __future__ import annotations

from typing import Any

from app.models import AgentDecisionRecord


def build_agent_decision_ledger(
    *,
    preprocessing: dict[str, Any],
    model_selection: dict[str, Any],
    detector_validation: dict[str, Any],
    events: list[Any],
    risk_alerts: list[dict[str, Any]],
    event_diagnoses: list[Any],
    work_order_drafts: list[Any],
    optimization_recommendations: list[Any],
) -> list[AgentDecisionRecord]:
    """按业务决策顺序生成一组稳定记录。"""

    records = [
        _preprocessing_decision(preprocessing),
        _model_routing_decision(model_selection),
        _risk_decision(events, risk_alerts, detector_validation),
    ]
    if event_diagnoses:
        records.append(_diagnosis_decision(event_diagnoses))
    if work_order_drafts:
        records.append(_work_order_decision(work_order_drafts))
    if optimization_recommendations:
        records.append(_optimization_decision(optimization_recommendations))
    return records


def _preprocessing_decision(preprocessing: dict[str, Any]) -> AgentDecisionRecord:
    actions = preprocessing.get("actions") or []
    applied = [
        str(item.get("method") or item.get("action"))
        for item in actions
        if item.get("status") not in {"skipped", "未执行"}
    ]
    return AgentDecisionRecord(
        decision_id="adaptive_preprocessing",
        stage="数据治理",
        title="形成统一模型输入",
        status="已执行",
        trigger="新工业时序批次通过格式预检",
        evidence=(
            f"原始 {preprocessing.get('raw_row_count', 0)} 行",
            f"时间对齐新增 {preprocessing.get('inserted_row_count', 0)} 点",
            f"保守填补 {preprocessing.get('filled_count', 0)} 点",
            f"质量门：{preprocessing.get('quality_gate', '未记录')}",
        ),
        rule="按采样间隔、缺失模式和模型输入约束选择预处理动作，不读取异常标签。",
        action="；".join(applied[:4]) if applied else "保持原始时间轴并进入统一分析接口",
        target="异常检测、趋势预测和交叉验证工具链",
        confidence="规则确定",
        human_gate="质量门失败或关键字段缺失时阻断自动分析并要求人工修复数据源。",
        rollback_condition="保留原始快照；预处理结果异常时可按任务编号回溯原始批次。",
    )


def _model_routing_decision(model_selection: dict[str, Any]) -> AgentDecisionRecord:
    evidence = model_selection.get("data_evidence") or {}
    selected_name = model_selection.get("selected_detector_name", "未记录模型")
    return AgentDecisionRecord(
        decision_id="model_routing",
        stage="工具编排",
        title="选择主异常检测模型",
        status="已决策",
        trigger=f"分析目标：{model_selection.get('analysis_goal_name', '未记录')}",
        evidence=(
            f"数据规模：{evidence.get('row_count', 0)} 行",
            f"传感器数量：{evidence.get('sensor_count', 0)}",
            f"健康基线：{'可用' if evidence.get('healthy_baseline_available') else '不可用'}",
            f"设备配置：{evidence.get('device_profile_id') or '通用回退'}",
        ),
        rule=str(model_selection.get("reason") or "按冻结能力顺序和模型最低输入条件路由。"),
        action=(
            f"调用 {selected_name}，阈值 {model_selection.get('selected_threshold', '未记录')}"
        ),
        target="主异常检测任务",
        confidence="冻结规则",
        human_gate="实验模式允许显式指定模型；企业数据接入后需重新确认模型排序和阈值。",
        rollback_condition="主模型不满足输入条件或运行失败时，按候选排序回退至下一可用模型。",
    )


def _risk_decision(
    events: list[Any],
    risk_alerts: list[dict[str, Any]],
    detector_validation: dict[str, Any],
) -> AgentDecisionRecord:
    agreement = (detector_validation.get("agreement") or {}).get("level", "未启用")
    severities = [str(getattr(item, "severity", "未知")) for item in events]
    highest = severities[0] if severities else "未发现持续异常"
    if events:
        action = f"保留 {len(events)} 个异常事件，最高风险为 {highest}"
        status = "触发告警"
    else:
        action = "本批次不生成异常工单，继续监测后续数据"
        status = "继续监测"
    return AgentDecisionRecord(
        decision_id="risk_assessment",
        stage="风险研判",
        title="综合判断异常风险",
        status=status,
        trigger="主模型完成检测并执行事件合并与工况过滤",
        evidence=(
            f"异常事件：{len(events)} 个",
            f"风险预警：{len(risk_alerts)} 条",
            f"多模型一致性：{agreement}",
        ),
        rule="异常点必须满足持续时长和合并策略；跨模型一致性只作为可信度证据。",
        action=action,
        target="风险预警与运维处置链路",
        confidence="多证据综合" if detector_validation else "主模型判断",
        human_gate="异常检测只证明数据偏离，不直接认定设备物理故障。",
        rollback_condition="现场确认属于工况切换或采集问题后，回写结果并进入误报审计。",
    )


def _diagnosis_decision(event_diagnoses: list[Any]) -> AgentDecisionRecord:
    primary = next(
        (
            item.primary_candidate
            for item in event_diagnoses
            if getattr(item, "primary_candidate", None) is not None
        ),
        None,
    )
    candidate_count = sum(len(getattr(item, "candidates", ())) for item in event_diagnoses)
    primary_name = getattr(primary, "name", "暂无首要候选")
    primary_confidence = getattr(primary, "confidence_level", "待现场确认")
    return AgentDecisionRecord(
        decision_id="root_cause_ranking",
        stage="诊断研判",
        title="形成候选根因排查顺序",
        status="待现场确认",
        trigger=f"{len(event_diagnoses)} 个异常事件需要解释",
        evidence=(
            f"候选根因：{candidate_count} 个",
            f"首要候选：{primary_name}",
            f"候选可信等级：{primary_confidence}",
        ),
        rule="综合传感器变化、关系破坏、工况上下文、预测趋势、知识规则和历史案例排序。",
        action=f"优先核验“{primary_name}”，同时保留其他候选与证据缺口",
        target="现场排查人员",
        confidence=str(primary_confidence),
        human_gate="处理人员必须结合设备拓扑、控制记录和现场检查确认真实根因。",
        rollback_condition="首要候选被否定后按候选排序继续核验，并将确认结果回写案例库。",
    )


def _work_order_decision(work_order_drafts: list[Any]) -> AgentDecisionRecord:
    priorities = [str(getattr(item, "priority", "P3")) for item in work_order_drafts]
    roles = sorted({str(getattr(item, "assigned_role", "未指定岗位")) for item in work_order_drafts})
    highest = min(priorities, key=lambda value: int(value[1:]) if value[1:].isdigit() else 9)
    return AgentDecisionRecord(
        decision_id="work_order_routing",
        stage="主动执行",
        title="生成并分级路由运维工单",
        status="待责任人接单",
        trigger="异常事件已形成候选根因与建议处置动作",
        evidence=(
            f"工单草案：{len(work_order_drafts)} 张",
            f"最高优先级：{highest}",
            f"责任岗位：{'、'.join(roles)}",
        ),
        rule="按风险等级映射 P1/P2/P3，并依据设备、生产和监控职责分派岗位。",
        action="创建工单、写入责任岗位，并进入主动通知链路",
        target="、".join(roles),
        confidence="规则确定",
        human_gate="责任人接单后才能填写确诊根因、处置动作和复测结论。",
        rollback_condition="误派或无法接单时由生产负责人重新指派，所有变更保留审计记录。",
    )


def _optimization_decision(recommendations: list[Any]) -> AgentDecisionRecord:
    categories = sorted({str(getattr(item, "category", "运行优化")) for item in recommendations})
    return AgentDecisionRecord(
        decision_id="constrained_optimization",
        stage="辅助决策",
        title="生成受约束优化草案",
        status="待人工批准",
        trigger="预测、候选根因和设备边界已经形成结构化证据",
        evidence=(
            f"建议数量：{len(recommendations)}",
            f"建议类别：{'、'.join(categories)}",
            "每条建议均包含观察窗口、验证指标和回退条件",
        ),
        rule="没有设备安全范围时不输出控制设定值；所有建议保持小步、可观察、可回退。",
        action="提交参数或运行优化草案，不直接向真实设备下发控制指令",
        target="生产负责人和设备工程师",
        confidence="受约束建议",
        human_gate="必须由设备、工艺和安全负责人确认后方可执行。",
        rollback_condition="观察指标恶化、触发联锁边界或效果不符合预期时立即回退。",
    )
