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
from app.integrations.wanwu_check import check_wanwu_integration


def test_wanwu_base64_submit_poll_and_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """万悟只发送 JSON 时，也应完成文件登记、异步分析、轮询和结果读取。"""

    test_settings = SimpleNamespace(
        database_path=tmp_path / "wanwu_jobs.db",
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
        "/api/v1/wanwu/jobs/cancel",
        "/api/v1/wanwu/cases/list",
        "/api/v1/wanwu/work-orders/list",
        "/api/v1/wanwu/work-orders/update",
    }
    assert schema["components"]["securitySchemes"]["IndustrialApiKey"]["name"] == "X-API-Key"
    for path, path_item in schema["paths"].items():
        assert "{" not in path
        for operation in path_item.values():
            assert operation["operationId"]
            assert "application/json" in operation["requestBody"]["content"]


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
        database_path=tmp_path / "quick.db",
        output_dir=tmp_path,
        knowledge_dir=tmp_path,
        llm_api_key="must-not-be-used",
        llm_embedding_model="must-not-be-used",
        anomaly_detector="mad",
        anomaly_threshold=3.0,
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
        assert payload["automatic_diagnosis"]["status"] == "deterministic"
        assert "evidence" not in payload["automatic_diagnosis"]
        assert "knowledge_sources" in payload["automatic_diagnosis"]
        assert payload["presentation"]

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


def test_wanwu_check_can_verify_platform_and_eight_tools(tmp_path, monkeypatch) -> None:
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
                    "cancel_industrial_analysis",
                    "list_industrial_work_orders",
                    "update_industrial_work_order",
                    "list_industrial_feedback_cases",
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

    assert result["tool_count"] == 8
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
