"""工单 SLA 催办、超时升级与维修后自动复检。

万悟分别定时调用 SLA 周期和复检周期推进售后动作。这里不调用大模型，也不控制 PLC：SLA
由明确时限判断，复检只比较同一数据源维修前后的结构化异常测点，现场处置仍由人员完成。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.automation.notifications import dispatch_work_order_notification
from app.config import Settings
from app.storage import IndustrialRepository


@dataclass(frozen=True)
class AftercarePolicy:
    """不同风险级别的未接单提醒和升级阈值，单位为秒。"""

    thresholds: dict[str, tuple[int, int]]

    @classmethod
    def from_settings(cls, settings: Settings) -> AftercarePolicy:
        return cls(
            thresholds={
                "P1": (
                    settings.sla_p1_reminder_seconds,
                    settings.sla_p1_escalation_seconds,
                ),
                "P2": (
                    settings.sla_p2_reminder_seconds,
                    settings.sla_p2_escalation_seconds,
                ),
                "P3": (
                    settings.sla_p3_reminder_seconds,
                    settings.sla_p3_escalation_seconds,
                ),
            }
        )


def run_sla_cycle(
    repository: IndustrialRepository,
    policy: AftercarePolicy,
    *,
    max_work_orders: int = 100,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """只执行 SLA 催办和超时升级，供万悟独立督办工作流调用。"""

    current_time = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    limit = max(1, min(200, int(max_work_orders)))
    actions: list[dict[str, Any]] = []
    checked, reminders, escalations = _run_sla_actions(
        repository,
        policy,
        current_time=current_time,
        limit=limit,
        dry_run=dry_run,
        actions=actions,
    )
    return _build_cycle_result(
        cycle_name="SLA 督办",
        dry_run=dry_run,
        sla_checked=checked,
        reminders=reminders,
        escalations=escalations,
        reinspection={"checked": 0, "passed": 0, "failed": 0, "waiting": 0},
        actions=actions,
    )


def run_reinspection_cycle(
    repository: IndustrialRepository,
    *,
    max_work_orders: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    """只执行维修后同源数据复检，供万悟复检工作流调用。"""

    limit = max(1, min(200, int(max_work_orders)))
    actions: list[dict[str, Any]] = []
    reinspection = _run_reinspections(
        repository,
        limit=limit,
        dry_run=dry_run,
        actions=actions,
    )
    return _build_cycle_result(
        cycle_name="维修后自动复检",
        dry_run=dry_run,
        sla_checked=0,
        reminders=0,
        escalations=0,
        reinspection=reinspection,
        actions=actions,
    )


def _build_cycle_result(
    *,
    cycle_name: str,
    dry_run: bool,
    sla_checked: int,
    reminders: int,
    escalations: int,
    reinspection: dict[str, int],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """统一构造 SLA 工作流和复检工作流共用的输出结构。"""

    action_count = reminders + escalations + reinspection["passed"] + reinspection["failed"]
    if action_count:
        cycle_status = "actions_planned" if dry_run else "actions_completed"
        next_action = "已完成预演，确认动作无误后使用 dry_run=false 正式执行" if dry_run else "等待下一周期继续检查"
    elif reinspection["waiting"]:
        cycle_status = "waiting_for_data"
        next_action = "待验证工单尚无同源新批次，等待下一次定时触发"
    else:
        cycle_status = "no_action"
        next_action = "当前没有待处理动作，结束本轮"
    presentation = (
        f"【时察千机{cycle_name}】"
        f"本轮检查 {sla_checked if cycle_name == 'SLA 督办' else reinspection['checked']} 项，"
        f"已完成动作 {action_count} 项。"
    )
    if cycle_name == "SLA 督办":
        presentation += f"催办 {reminders} 项，超时升级 {escalations} 项。"
    else:
        presentation += (
            f"复检通过 {reinspection['passed']} 项，"
            f"复检未通过 {reinspection['failed']} 项，"
            f"等待新数据 {reinspection['waiting']} 项。"
        )
    presentation += f"{next_action}。"
    if dry_run:
        presentation += "当前为预演模式，未修改工单且未发送通知。"
    return {
        "status": "success",
        "presentation": presentation,
        "cycle_status": cycle_status,
        "dry_run": dry_run,
        "sla_checked_count": sla_checked,
        "reminder_count": reminders,
        "escalation_count": escalations,
        "reinspection_checked_count": reinspection["checked"],
        "reinspection_passed_count": reinspection["passed"],
        "reinspection_failed_count": reinspection["failed"],
        "reinspection_waiting_count": reinspection["waiting"],
        "actions": actions,
        "next_action": next_action,
    }


def _run_sla_actions(
    repository: IndustrialRepository,
    policy: AftercarePolicy,
    *,
    current_time: datetime,
    limit: int,
    dry_run: bool,
    actions: list[dict[str, Any]],
) -> tuple[int, int, int]:
    """对尚未接单的待确认工单执行一次提醒或升级。"""

    candidates = [
        order
        for order in repository.list_work_orders(status="待确认", limit=limit)
        if not order.get("accepted_at")
    ]
    reminders = 0
    escalations = 0
    for order in candidates:
        reminder_after, escalation_after = policy.thresholds.get(
            str(order["priority"]), policy.thresholds["P3"]
        )
        age_seconds = max(0, int((current_time - _parse_time(order["created_at"])).total_seconds()))
        current_level = int(order.get("sla_level") or 0)
        desired_level = 2 if age_seconds >= escalation_after else 1 if age_seconds >= reminder_after else 0
        if desired_level <= current_level:
            continue

        kind = "sla_escalation" if desired_level == 2 else "sla_reminder"
        action = {
            "action": kind,
            "record_id": order["record_id"],
            "priority": order["priority"],
            "age_seconds": age_seconds,
            "level": desired_level,
            "dry_run": dry_run,
        }
        if not dry_run:
            source = repository.get_data_source_for_run(order["run_id"])
            title = (
                f"工单超时升级：{order['title']}"
                if desired_level == 2
                else f"工单接单提醒：{order['title']}"
            )
            message = (
                f"{order['priority']} 工单已等待 {age_seconds // 60} 分钟仍未接单，"
                + ("现升级至生产负责人，请立即协调处置。" if desired_level == 2 else "请及时确认接单。")
            )
            notifications = dispatch_work_order_notification(
                repository,
                order,
                source=source,
                notification_kind=kind,
                escalation_level=desired_level,
                title=title,
                message=message,
            )
            updated = repository.mark_work_order_sla(order["record_id"], desired_level)
            if updated is None:
                continue
            action["notification_ids"] = [item["notification_id"] for item in notifications]
        actions.append(action)
        if desired_level == 2:
            escalations += 1
        else:
            reminders += 1
    return len(candidates), reminders, escalations


def _run_reinspections(
    repository: IndustrialRepository,
    *,
    limit: int,
    dry_run: bool,
    actions: list[dict[str, Any]],
) -> dict[str, int]:
    """使用维修后同源新批次，判断原异常测点是否仍然出现。"""

    candidates = [
        order
        for order in repository.list_work_orders(status="待验证", limit=limit)
        if order.get("reinspection_status") == "pending"
    ]
    counts = {"checked": len(candidates), "passed": 0, "failed": 0, "waiting": 0}
    for order in candidates:
        source = repository.get_data_source_for_run(order["run_id"])
        if source is None:
            _append_waiting(actions, order, "原工单不是自动数据源任务，无法定位同源复检批次")
            counts["waiting"] += 1
            continue
        scheduled_at = order.get("reinspection_scheduled_at")
        if not scheduled_at:
            _append_waiting(actions, order, "工单缺少复检计划时间")
            counts["waiting"] += 1
            continue
        candidate_run = repository.find_latest_successful_source_run(
            source_id=source["source_id"],
            after=str(scheduled_at),
            exclude_run_id=order["run_id"],
        )
        if candidate_run is None:
            _append_waiting(actions, order, "等待同一数据源产生维修后的新分析批次")
            counts["waiting"] += 1
            continue

        baseline_sensors = _event_sensors(
            repository.get_run(order["run_id"]),
            event_number=int(order["event_number"]),
        )
        if not baseline_sensors:
            _append_waiting(actions, order, "原异常缺少主导测点，不能自动判定复检结果")
            counts["waiting"] += 1
            continue
        candidate_sensors = _all_event_sensors(candidate_run)
        overlap = sorted(baseline_sensors & candidate_sensors)
        passed = not overlap
        summary = (
            "维修后同源批次未再检出原异常主导测点：" + "、".join(sorted(baseline_sensors))
            if passed
            else "维修后同源批次仍检出原异常主导测点：" + "、".join(overlap)
        )
        action = {
            "action": "reinspection_passed" if passed else "reinspection_failed",
            "record_id": order["record_id"],
            "source_id": source["source_id"],
            "reinspection_run_id": candidate_run["run_id"],
            "baseline_sensors": sorted(baseline_sensors),
            "overlapping_sensors": overlap,
            "summary": summary,
            "dry_run": dry_run,
        }
        if not dry_run:
            updated = repository.finalize_reinspection(
                order["record_id"],
                passed=passed,
                reinspection_run_id=candidate_run["run_id"],
                summary=summary,
            )
            # 条件更新失败说明并发周期已经处理，不再重复通知。
            if updated is None:
                continue
            notifications = dispatch_work_order_notification(
                repository,
                {**order, **updated},
                source=source,
                notification_kind=action["action"],
                escalation_level=0,
                title=("维修复检通过：" if passed else "维修复检未通过：") + order["title"],
                message=summary + ("，工单已自动完成。" if passed else "，工单已退回处理中。"),
            )
            action["notification_ids"] = [item["notification_id"] for item in notifications]
        actions.append(action)
        counts["passed" if passed else "failed"] += 1
    return counts


def _event_sensors(run: dict[str, Any] | None, *, event_number: int) -> set[str]:
    """按工单事件编号读取原异常主导测点，事件编号从 1 开始。"""

    events = _analysis_section(run).get("anomaly_events", [])
    index = event_number - 1
    if index < 0 or index >= len(events):
        return set()
    return _normalized_sensors(events[index].get("dominant_sensors", []))


def _all_event_sensors(run: dict[str, Any] | None) -> set[str]:
    sensors: set[str] = set()
    for event in _analysis_section(run).get("anomaly_events", []):
        sensors.update(_normalized_sensors(event.get("dominant_sensors", [])))
    return sensors


def _analysis_section(run: dict[str, Any] | None) -> dict[str, Any]:
    if not run:
        return {}
    result = run.get("result") or {}
    nested = result.get("analysis")
    return nested if isinstance(nested, dict) else result if isinstance(result, dict) else {}


def _normalized_sensors(values: list[Any]) -> set[str]:
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))


def _append_waiting(
    actions: list[dict[str, Any]],
    order: dict[str, Any],
    reason: str,
) -> None:
    actions.append(
        {
            "action": "reinspection_waiting",
            "record_id": order["record_id"],
            "reason": reason,
        }
    )
