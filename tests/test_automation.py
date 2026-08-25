"""无人值守采集、自动任务与主动通知闭环测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import app.automation.notifications as notification_module
from app.api.server import _public_data_source
from app.automation import MonitoringService, dispatch_run_notifications
from app.storage.repository import IndustrialRepository


def _work_order_result() -> dict:
    """构造仓储生成工单所需的最小成功分析结果。"""

    return {
        "status": "success",
        "summary": {"异常事件数": 1},
        "work_order_drafts": [
            {
                "work_order_id": "WO-E01-AUTO",
                "event_number": 1,
                "priority": "P1",
                "title": "压力与流量关系异常",
                "status": "待确认",
                "assigned_role": "设备运维",
                "actions": ["检查阀门与管路"],
                "evidence_summary": ["压力和流量关系偏离健康基线"],
                "required_feedback": ["确认现场根因"],
            }
        ],
    }


def test_directory_monitor_submits_new_content_once_and_notifies_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """同一批次只能自动分析一次，成功后按优先级主动通知对应负责人。"""

    # 测试固定走站内通知，绝不读取本机真实机器人配置或向外部群发送消息。
    monkeypatch.setattr(
        notification_module,
        "get_settings",
        lambda: SimpleNamespace(
            wecom_enabled=False,
            wecom_webhook_url="",
            wecom_timeout_seconds=10,
        ),
    )

    repository = IndustrialRepository(tmp_path / "automation.db")
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "batch.csv").write_text(
        "datetime,Pressure,Flow\n2026-01-01 00:00:00,1.0,2.0\n",
        encoding="utf-8",
    )
    source = repository.upsert_data_source(
        {
            "name": "阀门测试台实时目录",
            "source_type": "directory",
            "endpoint": str(incoming),
            "interval_seconds": 5,
            "enabled": True,
            "config": {
                # all 让第二次轮询再次看见同一文件，以验证内容哈希去重。
                "initial_scan_mode": "all",
                "analysis_config": {"detector_selection_mode": "auto"},
                "routing": {
                    "priority_routes": {
                        "P1": [
                            {
                                "recipient_name": "张工",
                                "recipient_role": "生产值班负责人",
                            }
                        ]
                    }
                },
            },
        }
    )

    def submit_callback(current_source: dict, ingestion_id: str, snapshot: Path) -> str:
        file_id = f"auto_{ingestion_id}"
        run_id = f"run_{ingestion_id}"
        repository.register_file(file_id, snapshot.name, snapshot)
        repository.start_run(
            run_id,
            file_id,
            "analyze",
            "time_frequency_relation",
            {},
            status="running",
            source_id=current_source["source_id"],
            ingestion_id=ingestion_id,
        )
        repository.mark_ingestion_submitted(
            ingestion_id,
            run_id=run_id,
            storage_path=snapshot,
        )
        repository.finish_run(run_id, "success", 12.0, result=_work_order_result())
        return run_id

    service = MonitoringService(
        repository,
        tmp_path / "snapshots",
        submit_callback,
    )
    first = service.poll_once(source["source_id"])
    second = service.poll_once(source["source_id"])

    assert first == {
        "source_id": source["source_id"],
        "detected": 1,
        "submitted": 1,
        "duplicates": 0,
        "failed": 0,
        "run_ids": [f"run_{repository.list_ingestions(source_id=source['source_id'])[0]['ingestion_id']}"],
    }
    assert second["submitted"] == 0
    assert second["duplicates"] == 1
    ingestions = repository.list_ingestions(source_id=source["source_id"])
    assert len(ingestions) == 1
    assert ingestions[0]["status"] == "completed"

    notifications = dispatch_run_notifications(repository, ingestions[0]["run_id"])
    repeated = dispatch_run_notifications(repository, ingestions[0]["run_id"])

    assert len(notifications) == 1
    assert notifications[0]["recipient_name"] == "张工"
    assert notifications[0]["priority"] == "P1"
    assert notifications[0]["status"] == "sent"
    assert repeated[0]["notification_id"] == notifications[0]["notification_id"]
    assert len(repository.list_notifications()) == 1


class _FakeNotificationRepository:
    """只记录通知投递状态，避免企业微信协议单测依赖 PostgreSQL。"""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def mark_notification_sent(self, notification_id: str) -> None:
        self.sent.append(notification_id)

    def mark_notification_failed(self, notification_id: str, error: str) -> None:
        self.failed.append((notification_id, error))


class _FakeWecomResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.status = status
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _notification() -> dict:
    return {
        "notification_id": "ntf_test01",
        "priority": "P1",
        "recipient_name": "张工",
        "recipient_role": "生产负责人",
        "title": "压力与流量关系异常",
        "record_id": "run_demo:WO-E01",
    }


def test_wecom_delivery_uses_markdown_protocol(monkeypatch) -> None:
    """企业微信请求必须使用机器人 Markdown 协议，并校验业务成功码。"""

    repository = _FakeNotificationRepository()
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeWecomResponse({"errcode": 0, "errmsg": "ok"})

    monkeypatch.setattr(notification_module.urllib.request, "urlopen", fake_urlopen)
    notification_module._deliver_wecom(
        repository,
        _notification(),
        source={"name": "SKAB valve1 自动监测目录"},
        order={"source_file_name": "batch.csv"},
        webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-only",
        timeout_seconds=7,
    )

    assert captured["payload"]["msgtype"] == "markdown"
    content = captured["payload"]["markdown"]["content"]
    assert "时察千机工业异常告警" in content
    assert "P1" in content
    assert "生产负责人" in content
    assert "batch.csv" in content
    assert "run_demo:WO-E01" in content
    assert captured["timeout"] == 7
    assert repository.sent == ["ntf_test01"]
    assert repository.failed == []


def test_wecom_business_error_is_persisted_as_failure(monkeypatch) -> None:
    """企业微信可能以 HTTP 200 返回业务错误，此时仍必须记录投递失败。"""

    repository = _FakeNotificationRepository()
    monkeypatch.setattr(
        notification_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeWecomResponse(
            {"errcode": 93000, "errmsg": "invalid webhook key"}
        ),
    )

    notification_module._deliver_wecom(
        repository,
        _notification(),
        source={"name": "测试数据源"},
        order={"source_file_name": "test.csv"},
        webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret-value",
        timeout_seconds=10,
    )

    assert repository.sent == []
    assert repository.failed
    assert "errcode=93000" in repository.failed[0][1]
    assert "secret-value" not in repository.failed[0][1]


def test_public_data_source_never_exposes_webhook_secret() -> None:
    """兼容旧数据库配置时，浏览器接口也不能拿到机器人密钥。"""

    public = _public_data_source(
        {
            "source_id": "src_demo",
            "request_headers": {"Authorization": "Bearer secret"},
            "routing": {
                "webhook_url": "https://example.test/webhook?key=secret-value",
                "priority_routes": {"P1": [{"recipient_name": "张工"}]},
            },
        }
    )

    assert "request_headers" not in public
    assert public["request_header_count"] == 1
    assert "webhook_url" not in public["routing"]
    assert "secret-value" not in json.dumps(public, ensure_ascii=False)


def test_data_source_with_history_must_be_disabled_instead_of_deleted(tmp_path: Path) -> None:
    """已有采集证据的数据源不能物理删除，避免破坏自动任务审计链。"""

    repository = IndustrialRepository(tmp_path / "automation.db")
    source = repository.upsert_data_source(
        {
            "name": "测试目录",
            "source_type": "directory",
            "endpoint": str(tmp_path),
            "interval_seconds": 60,
            "enabled": True,
            "config": {},
        }
    )
    repository.reserve_ingestion(
        source_id=source["source_id"],
        fingerprint="abc",
        item_key="batch-1",
        file_name="batch.csv",
    )

    try:
        repository.delete_data_source(source["source_id"])
    except ValueError as exc:
        assert "改为停用" in str(exc)
    else:
        raise AssertionError("已有采集历史的数据源被错误删除")
