"""异常工单分级路由与企业微信主动通知。

系统始终先把通知写入 PostgreSQL，保留告警、责任人和投递状态的完整证据链。部署环境启用
企业微信群机器人后，再将同一告警主动推送到运维群。通知按工单、接收人与渠道幂等，服务
重试不会重复推送已经成功送达的告警。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from app.config import get_settings
from app.storage import IndustrialRepository

DEFAULT_ROUTES = {
    "P1": [{"recipient_name": "生产值班负责人", "recipient_role": "生产负责人"}],
    "P2": [{"recipient_name": "设备工程师", "recipient_role": "设备运维"}],
    "P3": [{"recipient_name": "运行值班员", "recipient_role": "运行监控"}],
}


def dispatch_run_notifications(
    repository: IndustrialRepository,
    run_id: str,
) -> list[dict[str, Any]]:
    """为自动分析产生的工单创建分级通知，并按需推送企业微信。"""

    source = repository.get_data_source_for_run(run_id)
    if source is None:
        return []
    notifications: list[dict[str, Any]] = []
    for order in repository.list_work_orders(run_id=run_id, limit=200):
        notifications.extend(
            dispatch_work_order_notification(
                repository,
                order,
                source=source,
                notification_kind="initial",
                escalation_level=0,
                title=order["title"],
                message=_notification_message(order),
            )
        )
    return notifications


def dispatch_work_order_notification(
    repository: IndustrialRepository,
    order: dict[str, Any],
    *,
    source: dict[str, Any] | None,
    notification_kind: str,
    escalation_level: int,
    title: str,
    message: str,
) -> list[dict[str, Any]]:
    """投递一个工单阶段通知，供首次告警、SLA 和自动复检共用。

    SLA 二级升级固定转给 P1 路由，确保未接单事件能够到达生产负责人；其他阶段沿用工单
    原风险等级路由。通知密钥仍只由后端读取，不进入万悟请求或数据库业务配置。
    """

    routing = (source or {}).get("routing") or {}
    routes = routing.get("priority_routes") or DEFAULT_ROUTES
    route_priority = "P1" if escalation_level >= 2 else order["priority"]
    recipients = routes.get(route_priority) or DEFAULT_ROUTES[route_priority]
    settings = get_settings()
    wecom_configured = settings.wecom_enabled and bool(settings.wecom_webhook_url)
    notifications: list[dict[str, Any]] = []

    for recipient in recipients:
        role = str(recipient.get("recipient_role") or order["assigned_role"])
        # 配置了人员台账时按角色展开到真实账号；未配置时仍保留岗位级通知证据。
        users = repository.list_active_users_for_role(role)
        targets = users or [
            {
                "user_id": None,
                "display_name": str(recipient.get("recipient_name") or "未指定人员"),
                "role": role,
            }
        ]
        for target in targets:
            if (
                notification_kind == "initial"
                and target.get("user_id")
                and not order.get("assigned_user_id")
            ):
                order = repository.assign_work_order(order["record_id"], target["user_id"])
            notification = repository.create_notification(
                run_id=order["run_id"],
                record_id=order["record_id"],
                priority=order["priority"],
                recipient_name=str(target["display_name"]),
                recipient_role=str(target["role"]),
                recipient_user_id=target.get("user_id"),
                channel="wecom_robot" if wecom_configured else "in_app",
                title=title,
                message=message,
                notification_kind=notification_kind,
                escalation_level=escalation_level,
            )
            if notification["status"] == "pending" and wecom_configured:
                _deliver_wecom(
                    repository,
                    notification,
                    source=source or {"name": "时察千机工单中心"},
                    order=order,
                    webhook_url=settings.wecom_webhook_url,
                    timeout_seconds=settings.wecom_timeout_seconds,
                )
                notification = repository.get_notification(notification["notification_id"])
            elif notification["status"] == "pending":
                repository.mark_notification_sent(notification["notification_id"])
                notification = repository.get_notification(notification["notification_id"])
            if notification is not None:
                notifications.append(notification)
    return notifications


def _notification_message(order: dict[str, Any]) -> str:
    return (
        f"系统自动发现 {order['priority']} 异常并生成工单。"
        f"来源：{order.get('source_file_name') or '自动数据源'}；"
        f"建议角色：{order['assigned_role']}；请进入运维工单查看证据和处置动作。"
    )


def _wecom_markdown(
    notification: dict[str, Any],
    *,
    source: dict[str, Any],
    order: dict[str, Any],
) -> str:
    """生成适合手机阅读的企业微信 Markdown 告警正文。"""

    priority = str(notification["priority"])
    color = {"P1": "warning", "P2": "warning", "P3": "info"}.get(priority, "info")
    source_name = str(source.get("name") or "自动数据源")
    source_file = str(order.get("source_file_name") or "未记录文件名")
    headings = {
        "initial": "时察千机工业异常告警",
        "sla_reminder": "时察千机工单接单提醒",
        "sla_escalation": "时察千机工单超时升级",
        "reinspection_passed": "时察千机维修复检通过",
        "reinspection_failed": "时察千机维修复检未通过",
    }
    kind = str(notification.get("notification_kind") or "initial")
    heading = headings.get(kind, "时察千机工单通知")
    return (
        f"## {heading}\n"
        f"> 风险等级：<font color=\"{color}\">{priority}</font>\n"
        f"> 责任岗位：{notification['recipient_role']}\n"
        f"> 接收人员：{notification['recipient_name']}\n\n"
        f"**异常事件**\n{notification['title']}\n\n"
        f"**数据来源**\n{source_name} / {source_file}\n\n"
        f"**工单编号**\n{notification['record_id']}\n\n"
        f"{notification.get('message') or '请及时查看工单详情并完成处置。'}"
    )


def _deliver_wecom(
    repository: IndustrialRepository,
    notification: dict[str, Any],
    *,
    source: dict[str, Any],
    order: dict[str, Any],
    webhook_url: str,
    timeout_seconds: float,
) -> None:
    """投递企业微信群机器人，并校验 HTTP 与企业微信业务状态。"""

    payload = json.dumps(
        {
            "msgtype": "markdown",
            "markdown": {
                "content": _wecom_markdown(notification, source=source, order=order)
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if response.status >= 400:
                raise RuntimeError(f"企业微信通知接口返回 HTTP {response.status}")
            body = response.read().decode("utf-8")
            result = json.loads(body)
            if result.get("errcode") != 0:
                raise RuntimeError(
                    "企业微信拒绝通知："
                    f"errcode={result.get('errcode')}，errmsg={result.get('errmsg', '未知错误')}"
                )
    except urllib.error.HTTPError as exc:
        # 错误信息只记录状态码，不记录包含机器人密钥的请求 URL。
        repository.mark_notification_failed(
            notification["notification_id"],
            f"企业微信通知接口返回 HTTP {exc.code}",
        )
    except (urllib.error.URLError, TimeoutError) as exc:
        repository.mark_notification_failed(
            notification["notification_id"],
            f"企业微信通知连接失败：{exc.reason if hasattr(exc, 'reason') else '请求超时'}",
        )
    except (json.JSONDecodeError, RuntimeError) as exc:
        repository.mark_notification_failed(notification["notification_id"], str(exc))
    except Exception:  # noqa: BLE001 - 告警失败不能反向中断工业分析任务。
        repository.mark_notification_failed(
            notification["notification_id"],
            "企业微信通知发生未预期错误",
        )
    else:
        repository.mark_notification_sent(notification["notification_id"])
