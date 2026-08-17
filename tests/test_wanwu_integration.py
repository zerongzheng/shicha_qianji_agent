"""元景万悟专用 JSON 接口、文件适配和精简 OpenAPI 测试。"""

from __future__ import annotations

import base64
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app.api.jobs as jobs_module
import app.storage.repository as storage_module
from app.api import server
from app.integrations import receive_wanwu_csv
from app.integrations.wanwu import _validate_remote_url
from app.integrations.wanwu_check import check_wanwu_integration


def test_wanwu_base64_submit_poll_and_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """万悟只发送 JSON 时，也应完成文件登记、异步分析、轮询和结果读取。"""

    test_settings = SimpleNamespace(
        database_url=tmp_path / "wanwu_jobs",
        database_schema="public",
        async_job_workers=1,
        async_job_queue_size=2,
    )
    storage_module.get_repository.cache_clear()
    jobs_module.get_job_manager.cache_clear()
    monkeypatch.setattr(storage_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(jobs_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path / "uploads")

    rows = 180
    pressure = np.ones(rows)
    pressure[105:125] += 3.0
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=rows, freq="s"),
            "Pressure": pressure,
            "anomaly": [0] * 105 + [1] * 20 + [0] * 55,
            "changepoint": np.zeros(rows),
        }
    )
    csv_content = dataframe.to_csv(sep=";", index=False).encode("utf-8")

    try:
        with TestClient(server.app) as client:
            accepted = client.post(
                "/api/v1/wanwu/jobs/submit",
                json={
                    "file_base64": base64.b64encode(csv_content).decode("ascii"),
                    "file_name": "wanwu_sample.csv",
                    "operation": "analyze",
                    "config": {
                        "detector": "mad",
                        "threshold": 3.0,
                        "rolling_window": 31,
                        "min_event_length": 2,
                    },
                },
            )
            assert accepted.status_code == 202
            accepted_payload = accepted.json()
            assert accepted_payload["file_source"] == "base64"
            assert accepted_payload["size_bytes"] == len(csv_content)
            run_id = accepted_payload["run_id"]

            status_payload = None
            # 万悟提交的是完整工业分析任务，给首次模型加载保留合理时间。
            for _ in range(300):
                status_response = client.post(
                    "/api/v1/wanwu/jobs/status",
                    json={"run_id": run_id},
                )
                assert status_response.status_code == 200
                status_payload = status_response.json()
                if status_payload["job_status"] in {"success", "failed"}:
                    break
                time.sleep(0.05)

            assert status_payload is not None
            assert status_payload["job_status"] == "success"
            result_response = client.post(
                "/api/v1/wanwu/jobs/result",
                json={"run_id": run_id},
            )
            assert result_response.status_code == 200
            assert result_response.json()["result"]["data_profile"]["row_count"] == rows
    finally:
        storage_module.get_repository.cache_clear()
        jobs_module.get_job_manager.cache_clear()


def test_wanwu_openapi_only_exposes_json_tools() -> None:
    """精简 Schema 不应包含 multipart、路径参数或内部实验接口。"""

    with TestClient(server.app) as client:
        response = client.get("/integrations/wanwu/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert set(schema["paths"]) == {
        "/api/v1/wanwu/quick-diagnosis",
        "/api/v1/wanwu/jobs/submit",
        "/api/v1/wanwu/jobs/status",
        "/api/v1/wanwu/jobs/result",
        "/api/v1/wanwu/jobs/decision-brief",
        "/api/v1/wanwu/reports/shift-brief",
        "/api/v1/wanwu/jobs/cancel",
        "/api/v1/wanwu/cases/list",
        "/api/v1/wanwu/work-orders/list",
        "/api/v1/wanwu/work-orders/update",
        "/api/v1/wanwu/automation/cycle",
        "/api/v1/wanwu/automation/sla",
        "/api/v1/wanwu/automation/reinspection",
        "/api/v1/wanwu/automation/status",
        "/api/v1/wanwu/automation/notifications/dispatch",
        "/api/v1/wanwu/data-sources/list",
        "/api/v1/wanwu/data-sources/configure",
        "/api/v1/wanwu/data-sources/verify",
    }
    assert schema["components"]["securitySchemes"]["IndustrialApiKey"]["name"] == "X-API-Key"
    for path, path_item in schema["paths"].items():
        assert "{" not in path
        for operation in path_item.values():
            assert operation["operationId"]
            assert "application/json" in operation["requestBody"]["content"]


def test_wanwu_autonomous_cycle_returns_run_id_for_workflow_branch(monkeypatch) -> None:
    """无人值守工具必须给万悟选择器返回可继续追踪的主任务编号。"""

    class FakeMonitor:
        def poll_once(self, source_id: str, *, max_submissions: int | None = None) -> dict:
            assert source_id == "src_skab_valve1"
            assert max_submissions == 1
            return {
                "source_id": source_id,
                "detected": 1,
                "submitted": 1,
                "duplicates": 0,
                "failed": 0,
                "run_ids": ["run_autonomous001"],
            }

    monkeypatch.setattr(server, "get_monitoring_service", lambda: FakeMonitor())
    client = TestClient(server.app)
    response = client.post(
        "/api/v1/wanwu/automation/cycle",
        json={"source_id": "src_skab_valve1", "max_sources": 1},
    )
    client.close()

    assert response.status_code == 200
    payload = response.json()
    assert payload["cycle_status"] == "analysis_queued"
    assert payload["primary_run_id"] == "run_autonomous001"
    assert payload["submitted_count"] == 1
    assert "查询任务状态" in payload["next_action"]


def test_wanwu_can_manage_and_verify_directory_data_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """万悟应能配置和验收目录数据源，同时不回显后端保存的鉴权信息。"""

    incoming = tmp_path / "incoming"
    incoming.mkdir()
    sample = incoming / "batch.csv"
    sample.write_text("datetime,Pressure\n2026-01-01 00:00:00,1.0\n", encoding="utf-8")

    class FakeRepository:
        def __init__(self) -> None:
            self.sources = {
                "src_wanwu_demo": {
                    "source_id": "src_wanwu_demo",
                    "name": "旧名称",
                    "source_type": "directory",
                    "endpoint": str(incoming),
                    "interval_seconds": 60.0,
                    "enabled": False,
                    "analysis_config": {"detector_selection_mode": "auto"},
                    "request_headers": {"Authorization": "Bearer secret"},
                    "routing": {"priority_routes": {"P1": []}},
                    "initial_scan_mode": "latest",
                    "timeout_seconds": 15.0,
                    "last_poll_at": None,
                    "last_success_at": None,
                    "last_error": None,
                    "created_at": "2026-08-14T10:00:00+08:00",
                    "updated_at": "2026-08-14T10:00:00+08:00",
                }
            }
            self.saved_config: dict = {}

        def get_data_source(self, source_id: str) -> dict | None:
            return self.sources.get(source_id)

        def list_data_sources(self, *, enabled_only: bool = False) -> list[dict]:
            values = list(self.sources.values())
            return [item for item in values if item["enabled"]] if enabled_only else values

        def upsert_data_source(self, source: dict) -> dict:
            self.saved_config = dict(source["config"])
            current = self.sources[source["source_id"]]
            current.update(
                {
                    "name": source["name"],
                    "source_type": source["source_type"],
                    "endpoint": source["endpoint"],
                    "interval_seconds": float(source["interval_seconds"]),
                    "enabled": bool(source["enabled"]),
                    "analysis_config": source["config"]["analysis_config"],
                    "request_headers": source["config"]["request_headers"],
                    "routing": source["config"]["routing"],
                    "initial_scan_mode": source["config"]["initial_scan_mode"],
                    "timeout_seconds": source["config"]["timeout_seconds"],
                }
            )
            return current

    repository = FakeRepository()
    monkeypatch.setattr(server, "get_repository", lambda: repository)
    client = TestClient(server.app)
    configured = client.post(
        "/api/v1/wanwu/data-sources/configure",
        json={
            "source_id": "src_wanwu_demo",
            "name": "SKAB 演示实时目录",
            "source_type": "directory",
            "endpoint": str(incoming),
            "interval_seconds": 30,
            "enabled": True,
            "initial_scan_mode": "new_only",
        },
    )
    listed = client.post(
        "/api/v1/wanwu/data-sources/list",
        json={"enabled_only": True},
    )
    verified = client.post(
        "/api/v1/wanwu/data-sources/verify",
        json={"source_id": "src_wanwu_demo"},
    )
    client.close()

    assert configured.status_code == 200
    assert configured.json()["action"] == "updated"
    assert configured.json()["source"]["request_header_count"] == 1
    assert "secret" not in configured.text
    assert repository.saved_config["request_headers"] == {
        "Authorization": "Bearer secret"
    }
    assert listed.status_code == 200
    assert listed.json()["source_count"] == 1
    assert listed.json()["sources"][0]["source_id"] == "src_wanwu_demo"
    assert verified.status_code == 200
    assert verified.json()["reachable"] is True
    assert verified.json()["csv_file_count"] == 1
    assert verified.json()["latest_file_name"] == "batch.csv"


def test_wanwu_dispatches_notifications_only_after_success(monkeypatch) -> None:
    """主动告警节点必须校验任务成功，并返回不含内部错误详情的投递审计。"""

    class FakeRepository:
        def get_run(self, run_id: str) -> dict:
            return {"run_id": run_id, "status": "success"}

    repository = FakeRepository()
    monkeypatch.setattr(server, "get_repository", lambda: repository)
    monkeypatch.setattr(
        server,
        "dispatch_run_notifications",
        lambda current_repository, run_id: [
            {
                "notification_id": "ntf_test001",
                "run_id": run_id,
                "status": "sent",
                "channel": "wecom_robot",
                "error": "内部字段不应返回",
            }
        ],
    )
    client = TestClient(server.app)
    response = client.post(
        "/api/v1/wanwu/automation/notifications/dispatch",
        json={"run_id": "run_autonomous001"},
    )
    client.close()

    assert response.status_code == 200
    payload = response.json()
    assert payload["sent_count"] == 1
    assert payload["failed_count"] == 0
    assert "error" not in payload["notifications"][0]


def test_wanwu_openapi_uses_openapi_30_exclusive_bounds() -> None:
    """导出的排他数值边界必须符合 OpenAPI 3.0，而不是 3.1。"""

    with TestClient(server.app) as client:
        response = client.get("/integrations/wanwu/openapi.json")

    assert response.status_code == 200
    schema = response.json()

    def walk(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    schema_nodes = list(walk(schema))
    assert all(
        not (
            isinstance(node.get("exclusiveMinimum"), (int, float))
            and not isinstance(node.get("exclusiveMinimum"), bool)
        )
        for node in schema_nodes
    )
    assert all(
        not (
            isinstance(node.get("exclusiveMaximum"), (int, float))
            and not isinstance(node.get("exclusiveMaximum"), bool)
        )
        for node in schema_nodes
    )

    submit_schema = schema["paths"]["/api/v1/wanwu/jobs/submit"]["post"]["requestBody"]
    config_schema = submit_schema["content"]["application/json"]["schema"]["properties"]["config"]
    threshold_schema = config_schema["properties"]["threshold"]
    assert threshold_schema["minimum"] == 0.0
    assert threshold_schema["exclusiveMinimum"] is True


def test_wanwu_quick_diagnosis_returns_local_result_without_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """快速工具应一次返回结果，且不因外部大模型限流而失败。"""

    test_settings = replace(
        server.get_settings(),
        database_url=tmp_path / "quick",
        database_schema="public",
        output_dir=tmp_path,
        knowledge_dir=tmp_path,
        llm_api_key="must-not-be-used",
        llm_embedding_model="must-not-be-used",
        anomaly_detector="time_frequency_relation",
        anomaly_threshold=4.5,
        rolling_window=31,
        min_event_length=2,
    )
    storage_module.get_repository.cache_clear()
    monkeypatch.setattr(storage_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(server, "settings", test_settings)
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path / "uploads")
    rows = 180
    pressure = np.ones(rows)
    pressure[105:125] += 3.0
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=rows, freq="s"),
            "Pressure": pressure,
            "anomaly": [0] * 105 + [1] * 20 + [0] * 55,
            "changepoint": np.zeros(rows),
        }
    )
    csv_content = dataframe.to_csv(sep=";", index=False).encode("utf-8")
    try:
        with TestClient(server.app) as client:
            response = client.post(
                "/api/v1/wanwu/quick-diagnosis",
                json={
                    "file_base64": base64.b64encode(csv_content).decode("ascii"),
                    "file_name": "quick.csv",
                },
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["model_call_count"] == 0
        assert payload["diagnosis_mode"] == "deterministic"
        assert payload["analysis_version"] == server.QUICK_DIAGNOSIS_VERSION
        assert payload["automatic_diagnosis"]["status"] == "deterministic"
        assert "evidence" not in payload["automatic_diagnosis"]
        assert "knowledge_sources" in payload["automatic_diagnosis"]
        assert payload["presentation"]
        assert "device_profile" in payload["analysis"]
        assert "data_quality" in payload["analysis"]
        assert payload["analysis"]["execution_trace"]
        assert payload["analysis"]["model_selection"]["mode"] == "automatic"
        assert payload["analysis"]["model_selection"]["selected_detector"] == "mad"
        assert payload["analysis"]["detector_validation"]["model_count"] >= 3
        assert payload["analysis"]["detector_validation"]["conclusion"]
        assert payload["analysis"]["summary"]["智能体执行摘要"]["自动完成数"] > 0
        assert payload["analysis"]["work_order_drafts"]
        assert len(
            storage_module.get_repository().list_work_orders(run_id=payload["run_id"])
        ) == len(payload["analysis"]["work_order_drafts"])
        assert storage_module.get_repository().get_run(payload["run_id"])["detector"] == "mad"
        legacy_payload = dict(payload)
        legacy_payload.pop("analysis_version")
        assert (
            server._quick_cached_response(
                {"result": legacy_payload},
                file_source="base64",
            )
            is None
        )
        uploaded_files = list((tmp_path / "uploads").glob("*/*.csv"))
        assert len(uploaded_files) == 1

        repeated = client.post(
            "/api/v1/wanwu/quick-diagnosis",
            json={
                "file_base64": base64.b64encode(csv_content).decode("ascii"),
                "file_name": "quick.csv",
            },
        )
        assert repeated.status_code == 200
        repeated_payload = repeated.json()
        assert repeated_payload["cache_hit"] is True
        assert repeated_payload["run_id"] == payload["run_id"]
        assert len(list((tmp_path / "uploads").glob("*/*.csv"))) == 1
    finally:
        storage_module.get_repository.cache_clear()


def test_wanwu_can_list_confirmed_feedback_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """万悟应能读取现场确认案例，用于展示持续学习闭环。"""

    repository = storage_module.IndustrialRepository(tmp_path / "cases.db")
    monkeypatch.setattr(server, "get_repository", lambda: repository)
    monkeypatch.setattr(
        repository,
        "list_confirmed_cases",
        lambda limit=50: [
            {
                "case_id": "CASE-run_old:wo_old",
                "confirmed_cause": "阀门执行器卡滞",
                "source_run_id": "run_old",
                "source_record_id": "run_old:wo_old",
                "evidence_summary": ["压力升高且流量下降"],
                "feedback_note": "清理后恢复",
                "handled_by": "运维组",
                "closed_at": "2026-07-01T10:00:00+08:00",
                "signature": {
                    "groups": {"pressure", "flow"},
                    "directions": {"pressure:up", "flow:down"},
                    "dominant_groups": {"pressure", "flow"},
                    "regime": "稳定工况内事件",
                },
            }
        ][:limit],
    )

    with TestClient(server.app) as client:
        response = client.post("/api/v1/wanwu/cases/list", json={"limit": 10})

    assert response.status_code == 200
    payload = response.json()
    assert payload["case_count"] == 1
    assert payload["cases"][0]["confirmed_cause"] == "阀门执行器卡滞"
    assert payload["cases"][0]["direction_features"] == ["flow:down", "pressure:up"]


def test_wanwu_file_adapter_rejects_private_url_by_default() -> None:
    """文件 URL 不能默认访问本机或局域网，避免形成 SSRF 入口。"""

    with pytest.raises(ValueError, match="禁止访问本机、局域网"):
        receive_wanwu_csv(
            file_url="http://127.0.0.1/private.csv",
            file_base64=None,
            requested_file_name=None,
            max_bytes=1024,
            download_timeout=1,
            allow_private_urls=False,
        )


def test_wanwu_file_adapter_allows_only_configured_private_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """服务器联调只能精确放行万悟文件容器，不能顺带开放整个内网。"""

    monkeypatch.setattr(
        "app.integrations.wanwu.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("172.20.0.8", 8081))],
    )
    _validate_remote_url(
        "http://nginx-wanwu:8081/minio/download/api/sample.csv",
        allow_private_urls=False,
        allowed_private_hosts=("nginx-wanwu",),
    )
    with pytest.raises(ValueError, match="WANWU_ALLOWED_FILE_HOSTS"):
        _validate_remote_url(
            "http://other-service:8081/private.csv",
            allow_private_urls=False,
            allowed_private_hosts=("nginx-wanwu",),
        )


def test_wanwu_file_adapter_requires_exactly_one_source() -> None:
    """适配器必须拒绝没有来源或重复来源的模糊请求。"""

    with pytest.raises(ValueError, match="必须提供"):
        receive_wanwu_csv(
            file_url=None,
            file_base64=None,
            requested_file_name=None,
            max_bytes=1024,
            download_timeout=1,
            allow_private_urls=False,
        )


def test_wanwu_check_can_verify_platform_and_complete_toolset(tmp_path, monkeypatch) -> None:
    """接入自检应同时报告算法服务、完整工具、快速工具和万悟网页状态。"""

    schema = {
        "paths": {
            f"/tool/{index}": {"post": {"operationId": operation_id}}
            for index, operation_id in enumerate(
                [
                    "quick_industrial_diagnosis",
                    "submit_industrial_analysis",
                    "get_industrial_analysis_status",
                    "get_industrial_analysis_result",
                    "get_industrial_decision_brief",
                    "generate_industrial_shift_brief",
                    "cancel_industrial_analysis",
                    "list_industrial_work_orders",
                    "update_industrial_work_order",
                    "list_industrial_feedback_cases",
                        "run_unattended_industrial_cycle",
                        "get_unattended_monitoring_status",
                        "dispatch_industrial_alerts",
                        "list_industrial_data_sources",
                        "configure_industrial_data_source",
                        "verify_industrial_data_source",
                    "run_industrial_sla_cycle",
                    "run_industrial_reinspection_cycle",
                    ]
            )
        },
        "servers": [{"url": "http://host.docker.internal:8000"}],
    }

    quick_schema = {
        "paths": {
            "/api/v1/wanwu/quick-diagnosis": {
                "post": {"operationId": "quick_industrial_diagnosis"}
            }
        },
        "servers": [{"url": "http://host.docker.internal:8000"}],
    }

    def fake_get_json(url: str):
        """分别模拟健康检查、完整 Schema 和比赛演示 Schema。"""

        if url.endswith("/health"):
            return {"status": "ok", "service": "shichi-qianji"}
        if url.endswith("/quick-openapi.json"):
            return quick_schema
        return schema

    monkeypatch.setattr(
        "app.integrations.wanwu_check._get_json",
        fake_get_json,
    )
    monkeypatch.setattr("app.integrations.wanwu_check._check_http", lambda _url: 200)

    result = check_wanwu_integration(
        "http://127.0.0.1:8000",
        tmp_path / "wanwu.json",
        platform_url="http://127.0.0.1:8081",
        quick_output_path=tmp_path / "wanwu-quick.json",
    )

    assert result["tool_count"] == 18
    assert result["platform_http_status"] == 200
    assert result["schema_server"] == "http://host.docker.internal:8000"
    assert result["quick_tool_count"] == 1
    assert result["quick_operation_ids"] == ["quick_industrial_diagnosis"]
    assert (tmp_path / "wanwu.json").exists()
    assert (tmp_path / "wanwu-quick.json").exists()


def test_wanwu_quick_schema_contains_only_one_demo_tool() -> None:
    """比赛演示 Schema 只暴露快速诊断工具。"""

    with TestClient(server.app) as client:
        response = client.get("/integrations/wanwu/quick-openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert set(schema["paths"]) == {"/api/v1/wanwu/quick-diagnosis"}
    operation = schema["paths"]["/api/v1/wanwu/quick-diagnosis"]["post"]
    assert operation["operationId"] == "quick_industrial_diagnosis"
    assert "不要继续调用其他工业分析工具" in operation["description"]

    # 万悟工具节点的输出字段来自 200 响应 Schema，不能只保留响应描述。
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["type"] == "object"
    assert {
        "status",
        "run_id",
        "file_name",
        "presentation",
        "analysis",
        "automatic_diagnosis",
    }.issubset(response_schema["properties"])
    assert all("const" not in item for item in response_schema["properties"].values())


def test_wanwu_quick_payload_keeps_decision_ledger(monkeypatch) -> None:
    """快速万悟协议也必须保留决策账本，避免平台只看到一段无依据的结论。"""

    from app.api.server import _quick_analysis_payload

    payload = {
        "device_profile": {},
        "data_profile": {},
        "data_quality": {},
        "visualization": {},
        "anomaly_events": [],
        "model_selection": {},
        "detector_validation": {},
        "operating_regimes": {},
        "relationship_diagnostics": [],
        "root_cause_diagnoses": [],
        "historical_case_matches": {},
        "work_order_drafts": [],
        "forecast_results": {},
        "risk_alerts": [],
        "recommendations": [],
        "optimization_recommendations": [],
        "execution_trace": [],
        "agent_decisions": [
            {
                "decision_id": "model_routing",
                "stage": "工具编排",
                "title": "选择主异常检测模型",
                "status": "已决策",
                "trigger": "任务目标已确定",
                "evidence": ["传感器数量：2"],
                "rule": "冻结策略",
                "action": "调用时频关系多路径检测器",
                "target": "分析任务",
                "confidence": "冻结规则",
                "human_gate": "企业数据接入后复核",
                "rollback_condition": "运行失败时回退",
            }
        ],
        "summary": {},
        "limitations": [],
    }

    monkeypatch.setattr(server, "_result_payload", lambda _run_id, _result: payload)
    result = _quick_analysis_payload("run_test", object())
    assert result["agent_decisions"][0]["decision_id"] == "model_routing"


def test_wanwu_decision_brief_exposes_compact_algorithm_evidence(monkeypatch) -> None:
    """决策摘要应显式返回选模、交叉验证、趋势风险、优化建议和工单信息。"""

    stored_result = {
        "detector": "time_frequency_relation",
        "device_profile": {
            "profile_id": "skab_water_loop_valve",
            "display_name": "SKAB 水循环阀门测试台",
        },
        "data_profile": {"source_name": "valve1/0.csv"},
        "model_selection": {
            "mode": "automatic",
            "analysis_goal_name": "综合平衡",
            "selected_detector": "time_frequency_relation",
            "selected_detector_name": "时频关系多路径",
            "selected_threshold": 3.5,
            "reason": "设备冻结配置与任务目标共同选择",
            "candidate_ranking": [{"detector": "time_frequency_relation"}],
        },
        "detector_validation": {
            "status": "completed",
            "model_count": 4,
            "models": [
                {"detector": "mad", "detector_name": "稳健 MAD"},
                {
                    "detector": "time_frequency_relation",
                    "detector_name": "时频关系多路径",
                },
            ],
            "agreement": {"level": "中"},
            "conclusion": "两类互补模型支持当前告警，仍需现场确认。",
            "failed_models": [],
        },
        "forecast_results": {
            "Pressure": {
                "模型名称": "时频特征增强岭回归模型",
                "方向": "持续上升",
                "风险": "高风险",
                "当前值": 1.2,
                "预测末值": 1.8,
                "回测": {"RMSE": 0.12},
                "不确定度": {"预测可信度": "中"},
            }
        },
        "risk_alerts": [
            {
                "alert_id": "forecast-risk-001",
                "类型": "趋势预测预警",
                "等级": "高风险",
                "可信度": "中",
                "传感器": ["Pressure"],
                "建议动作": "提前安排人工复核",
            }
        ],
        "optimization_recommendations": [
            {
                "recommendation_id": "OPT-PARAM-001",
                "category": "参数稳定",
                "target": "Pressure",
                "action": "核对工况后分级调整",
                "adjustment_direction": "抑制继续上升",
                "suggested_range": "待企业确认",
                "confidence": "中",
                "evidence": ["预测持续上升", "风险等级高"],
                "observation_window": "连续观察 30 个采样点",
                "rollback_condition": "风险继续上升时立即回退",
                "status": "待人工确认",
            }
        ],
        "work_order_drafts": [
            {"record_id": "run_decision001:WO-001", "priority": "P1"}
        ],
        "limitations": ["公开数据验证不代表企业现场成效。"],
    }
    monkeypatch.setattr(
        server,
        "_job_result_payload",
        lambda run_id: {"status": "success", "run_id": run_id, "result": stored_result},
    )

    payload = server._wanwu_decision_brief_payload("run_decision001")

    assert payload["data_source_label"].startswith("公开 SKAB 验证数据")
    assert payload["model_selection"]["selected_threshold"] == 3.5
    assert payload["cross_validation"]["model_count"] == 4
    assert payload["trend_risk"]["highest_risk"] == "高风险"
    assert payload["trend_risk"]["forecast_summaries"][0]["backtest_rmse"] == 0.12
    assert payload["optimization"]["recommendation_count"] == 1
    assert "不直接下发控制指令" in payload["optimization"]["human_gate"]
    assert payload["work_order_summary"]["highest_priority"] == "P1"
    assert payload["rag_context"]["sensor_terms"] == ["Pressure"]
    assert "知识库只补充" in payload["rag_context"]["usage_rule"]
    assert "【时察千机自动巡检结果】" in payload["presentation"]
    assert "风险等级：高风险" in payload["presentation"]
    assert "系统不直接下发控制指令" in payload["presentation"]


def test_wanwu_decision_brief_reuses_job_state_errors(monkeypatch) -> None:
    """任务未成功时应沿用结果接口的 409，而不是返回不完整证据。"""

    from fastapi import HTTPException

    def unfinished(_run_id: str) -> dict:
        raise HTTPException(status_code=409, detail="任务尚未完成")

    monkeypatch.setattr(server, "_job_result_payload", unfinished)
    with pytest.raises(HTTPException) as exc_info:
        server._wanwu_decision_brief_payload("run_waiting001")
    assert exc_info.value.status_code == 409


def test_wanwu_shift_brief_aggregates_runs_orders_and_aftercare(monkeypatch) -> None:
    """班次简报应从数据库审计记录聚合任务、工单、SLA、复检和通知结果。"""

    now = pd.Timestamp.now(tz="Asia/Shanghai")

    class FakeRepository:
        def list_runs(self, **_kwargs):
            return [
                {
                    "run_id": "run_shift_success",
                    "status": "success",
                    "started_at": now.isoformat(),
                },
                {
                    "run_id": "run_shift_failed",
                    "status": "failed",
                    "started_at": now.isoformat(),
                },
            ]

        def get_run(self, run_id: str):
            assert run_id == "run_shift_success"
            return {
                "result": {
                    "anomaly_events": [{"severity": "高风险"}],
                    "risk_alerts": [{"等级": "高风险"}],
                }
            }

        def list_work_orders(self, **_kwargs):
            return [
                {
                    "record_id": "run_shift_success:WO-001",
                    "priority": "P1",
                    "status": "待确认",
                    "title": "核查压力异常",
                    "assigned_role": "设备运维",
                    "sla_level": 2,
                    "reinspection_status": None,
                    "created_at": now.isoformat(),
                },
                {
                    "record_id": "run_old:WO-002",
                    "priority": "P2",
                    "status": "已完成",
                    "title": "历史工单",
                    "assigned_role": "工艺人员",
                    "sla_level": 0,
                    "reinspection_status": "passed",
                    "created_at": (now - pd.Timedelta(hours=20)).isoformat(),
                },
            ]

        def list_notifications(self, **_kwargs):
            return [
                {
                    "status": "sent",
                    "notification_kind": "sla_reminder",
                    "created_at": now.isoformat(),
                },
                {
                    "status": "sent",
                    "notification_kind": "sla_escalation",
                    "created_at": now.isoformat(),
                },
                {
                    "status": "failed",
                    "notification_kind": "reinspection_failed",
                    "created_at": now.isoformat(),
                },
            ]

    monkeypatch.setattr(server, "get_repository", lambda: FakeRepository())
    payload = server._wanwu_shift_brief_payload(hours=8, max_records=100)

    assert payload["run_summary"]["total"] == 2
    assert payload["run_summary"]["anomaly_event_count"] == 1
    assert payload["work_order_summary"]["created_count"] == 1
    assert payload["work_order_summary"]["unresolved_p1_count"] == 1
    assert payload["aftercare_summary"]["reminder_count"] == 1
    assert payload["aftercare_summary"]["escalation_count"] == 1
    assert payload["aftercare_summary"]["reinspection_failed_count"] == 1
    assert payload["notification_summary"]["failed"] == 1
    assert "公开 SKAB 演示结果不代表企业现场收益" in payload["presentation"]


def test_wanwu_schema_declares_api_key_when_remote_auth_is_enabled() -> None:
    """服务器启用服务密钥时，导出的工具协议必须同步声明鉴权。"""

    from app.api.wanwu_openapi import build_wanwu_openapi

    schema = build_wanwu_openapi(
        server.app.openapi(),
        "http://shichi-qianji-api:8000",
        quick_only=True,
        api_key_required=True,
    )
    operation = schema["paths"]["/api/v1/wanwu/quick-diagnosis"]["post"]
    assert operation["security"] == [{"IndustrialApiKey": []}]
