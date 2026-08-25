"""人员身份、通知签收与工单责任闭环测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.security import hash_password, verify_password
from app.storage.repository import IndustrialRepository
from tests.test_storage import _register_sample_file, _sample_result


def test_password_hash_never_contains_plaintext() -> None:
    """数据库所用密码摘要不能包含可恢复的明文。"""

    encoded = hash_password("Competition-Only-Password")
    assert "Competition-Only-Password" not in encoded
    assert verify_password("Competition-Only-Password", encoded) is True
    assert verify_password("wrong-password", encoded) is False


def test_user_can_receive_notification_and_accept_assigned_order(tmp_path: Path) -> None:
    """真实人员应能成为通知接收者、责任人并完成接单。"""

    repository = IndustrialRepository(tmp_path / "identity.db")
    user = repository.upsert_user(
        username="engineer",
        display_name="设备工程师",
        role="设备工程师",
        password_hash=hash_password("test-password"),
    )
    file_id = _register_sample_file(repository, tmp_path)
    run_id = "run_identity_test"
    repository.start_run(run_id, file_id, "analyze", "mad", {})
    repository.finish_run(run_id, "success", 10.0, result=_sample_result(run_id))
    record_id = f"{run_id}:WO-E001-000010"

    assigned = repository.assign_work_order(record_id, user["user_id"])
    notification = repository.create_notification(
        run_id=run_id,
        record_id=record_id,
        priority="P1",
        recipient_name=user["display_name"],
        recipient_role=user["role"],
        recipient_user_id=user["user_id"],
        channel="in_app",
        title="阀门异常",
        message="请进入工单查看异常证据。",
    )
    accepted = repository.accept_work_order(record_id, user["user_id"])
    acknowledged = repository.acknowledge_notification(
        notification["notification_id"],
        user["user_id"],
    )

    assert assigned["assigned_user_name"] == "设备工程师"
    assert accepted["accepted_at"]
    assert accepted["accepted_by"] == user["user_id"]
    assert acknowledged["acknowledged_at"]
    assert repository.list_notifications(recipient_user_id=user["user_id"])


def test_other_user_cannot_accept_assigned_order(tmp_path: Path) -> None:
    """工单已指派后，其他普通人员不能抢占责任。"""

    repository = IndustrialRepository(tmp_path / "identity_guard.db")
    first = repository.upsert_user(
        username="engineer",
        display_name="设备工程师",
        role="设备工程师",
        password_hash=hash_password("first-password"),
    )
    second = repository.upsert_user(
        username="operator",
        display_name="运行值班员",
        role="运行值班员",
        password_hash=hash_password("second-password"),
    )
    file_id = _register_sample_file(repository, tmp_path)
    run_id = "run_identity_guard"
    repository.start_run(run_id, file_id, "analyze", "mad", {})
    repository.finish_run(run_id, "success", 10.0, result=_sample_result(run_id))
    record_id = f"{run_id}:WO-E001-000010"
    repository.assign_work_order(record_id, first["user_id"])

    with pytest.raises(PermissionError, match="其他人员"):
        repository.accept_work_order(record_id, second["user_id"])
