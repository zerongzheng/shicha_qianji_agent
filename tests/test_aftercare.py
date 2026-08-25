"""万悟工单 SLA 与维修后自动复检测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.automation.notifications as notification_module
from app.automation import AftercarePolicy, run_reinspection_cycle, run_sla_cycle
from app.storage.repository import IndustrialRepository


@pytest.fixture(autouse=True)
def disable_external_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试只写站内通知，绝不调用本机真实企业微信 Webhook。"""

    monkeypatch.setattr(
        notification_module,
        "get_settings",
        lambda: SimpleNamespace(
            wecom_enabled=False,
            wecom_webhook_url="",
            wecom_timeout_seconds=10,
        ),
    )


def _result(run_id: str, sensors: list[str], *, create_order: bool) -> dict:
    event = {"dominant_sensors": sensors} if sensors else None
    return {
        "run_id": run_id,
        "status": "success",
        "summary": {"异常事件数": int(bool(event))},
        "anomaly_events": [event] if event else [],
        "root_cause_diagnoses": [],
        "work_order_drafts": (
            [
                {
                    "work_order_id": "WO-E01-AFTERCARE",
                    "event_number": 1,
                    "priority": "P1",
                    "title": "压力与流量关系异常",
                    "status": "待确认",
                    "assigned_role": "设备运维",
                    "actions": ["检查阀门与管路"],
                    "evidence_summary": ["关系偏离健康基线"],
                    "required_feedback": ["填写根因与处置结果"],
                }
            ]
            if create_order
            else []
        ),
    }


def _source(repository: IndustrialRepository, tmp_path: Path) -> dict:
    return repository.upsert_data_source(
        {
            "name": "SKAB 公开仿真数据源",
            "source_type": "directory",
            "endpoint": str(tmp_path),
            "interval_seconds": 60,
            "enabled": True,
            "config": {},
        }
    )


def _successful_run(
    repository: IndustrialRepository,
    tmp_path: Path,
    *,
    source_id: str,
    run_id: str,
    sensors: list[str],
    create_order: bool,
) -> None:
    csv_path = tmp_path / f"{run_id}.csv"
    csv_path.write_text("datetime,Pressure\n2026-01-01,1.0\n", encoding="utf-8")
    file_id = f"file_{run_id}"
    repository.register_file(file_id, csv_path.name, csv_path)
    repository.start_run(
        run_id,
        file_id,
        "analyze",
        "time_frequency_relation",
        {},
        source_id=source_id,
    )
    repository.finish_run(
        run_id,
        "success",
        10,
        result=_result(run_id, sensors, create_order=create_order),
    )


def _policy() -> AftercarePolicy:
    return AftercarePolicy({"P1": (5, 15), "P2": (10, 20), "P3": (20, 40)})


def test_sla_reminder_and_escalation_are_idempotent(tmp_path: Path) -> None:
    """周期重试不重复催办，达到更高阈值后只新增一次升级告警。"""

    repository = IndustrialRepository(tmp_path / "aftercare.db")
    source = _source(repository, tmp_path)
    _successful_run(
        repository,
        tmp_path,
        source_id=source["source_id"],
        run_id="run_sla_original",
        sensors=["Pressure"],
        create_order=True,
    )
    order = repository.list_work_orders()[0]
    created_at = datetime.fromisoformat(order["created_at"])

    first = run_sla_cycle(repository, _policy(), now=created_at + timedelta(seconds=6))
    repeated = run_sla_cycle(repository, _policy(), now=created_at + timedelta(seconds=8))
    escalated = run_sla_cycle(repository, _policy(), now=created_at + timedelta(seconds=16))
    repeated_escalation = run_sla_cycle(
        repository, _policy(), now=created_at + timedelta(seconds=30)
    )

    assert first["reminder_count"] == 1
    assert "SLA 督办" in first["presentation"]
    assert repeated["reminder_count"] == 0
    assert escalated["escalation_count"] == 1
    assert repeated_escalation["escalation_count"] == 0
    notifications = repository.list_notifications()
    assert {item["notification_kind"] for item in notifications} == {
        "sla_reminder",
        "sla_escalation",
    }
    assert repository.list_work_orders()[0]["sla_level"] == 2


@pytest.mark.parametrize(
    ("new_sensors", "expected_status", "expected_reinspection", "count_key"),
    [
        ([], "已完成", "passed", "reinspection_passed_count"),
        (["Pressure"], "处理中", "failed", "reinspection_failed_count"),
    ],
)
def test_reinspection_uses_new_same_source_run(
    tmp_path: Path,
    new_sensors: list[str],
    expected_status: str,
    expected_reinspection: str,
    count_key: str,
) -> None:
    """同源新批次中原测点消失则完成，仍出现则退回处理中。"""

    repository = IndustrialRepository(tmp_path / "reinspection.db")
    source = _source(repository, tmp_path)
    _successful_run(
        repository,
        tmp_path,
        source_id=source["source_id"],
        run_id="run_reinspection_original",
        sensors=["Pressure"],
        create_order=True,
    )
    order = repository.list_work_orders()[0]
    scheduled = repository.update_work_order(
        order["record_id"],
        {
            "status": "待验证",
            "confirmed_cause": "阀门执行机构卡滞",
            "feedback_note": "已清理并重新标定，等待新批次复测",
            "handled_by": "设备运维组",
        },
    )
    assert scheduled["reinspection_status"] == "pending"

    _successful_run(
        repository,
        tmp_path,
        source_id=source["source_id"],
        run_id="run_reinspection_new",
        sensors=new_sensors,
        create_order=False,
    )
    result = run_reinspection_cycle(repository)
    repeated = run_reinspection_cycle(repository)
    updated = repository._get_work_order(order["record_id"])

    assert result[count_key] == 1
    assert "维修后自动复检" in result["presentation"]
    assert repeated[count_key] == 0
    assert updated is not None
    assert updated["status"] == expected_status
    assert updated["reinspection_status"] == expected_reinspection
    assert updated["reinspection_run_id"] == "run_reinspection_new"
    assert len(repository.list_notifications()) == 1


def test_reinspection_waits_when_no_new_source_run_exists(tmp_path: Path) -> None:
    """没有维修后新数据时保持待验证，不把“未采到数据”误判为恢复。"""

    repository = IndustrialRepository(tmp_path / "waiting.db")
    source = _source(repository, tmp_path)
    _successful_run(
        repository,
        tmp_path,
        source_id=source["source_id"],
        run_id="run_waiting_original",
        sensors=["Pressure"],
        create_order=True,
    )
    order = repository.list_work_orders()[0]
    repository.update_work_order(
        order["record_id"],
        {
            "status": "待验证",
            "confirmed_cause": "阀门执行机构卡滞",
            "feedback_note": "维修完成，等待复测数据",
        },
    )

    result = run_reinspection_cycle(repository)

    assert result["cycle_status"] == "waiting_for_data"
    assert result["reinspection_waiting_count"] == 1
    assert repository._get_work_order(order["record_id"])["status"] == "待验证"
    assert repository.list_notifications() == []
