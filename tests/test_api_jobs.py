"""Pydantic API 协议和异步任务端到端测试。"""

from __future__ import annotations

import time
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api.jobs as jobs_module
import app.storage.repository as storage_module
from app.api import server
from app.api.jobs import BackgroundJobManager, JobQueueFullError
from app.api.schemas import JobCreateRequest


def test_job_request_rejects_unknown_fields_and_invalid_threshold() -> None:
    """万悟变量拼错或参数越界时，应在进入算法前由 Pydantic 拒绝。"""

    with pytest.raises(ValidationError):
        JobCreateRequest.model_validate(
            {
                "file_id": "file_12345678",
                "operation": "analyze",
                "config": {"threshold": -1},
            }
        )


def test_background_job_manager_rejects_when_capacity_is_full() -> None:
    """并发槽位和排队容量占满后，新任务应立即失败而不是无限堆积。"""

    manager = BackgroundJobManager(max_workers=1, max_queue_size=0)
    release = Event()

    try:
        manager.submit("run_blocking", release.wait, 5)
        with pytest.raises(JobQueueFullError, match="任务队列已满"):
            manager.submit("run_rejected", lambda: None)
    finally:
        release.set()
        manager.shutdown()
    with pytest.raises(ValidationError):
        JobCreateRequest.model_validate(
            {
                "file_id": "file_12345678",
                "unknown_field": "拼写错误",
            }
        )


def test_background_job_manager_cancels_queued_job_and_releases_capacity() -> None:
    """取消排队任务后应释放容量，且关闭管理器时不发生信号量重复释放。"""

    manager = BackgroundJobManager(max_workers=1, max_queue_size=1)
    release = Event()

    try:
        manager.submit("run_active", release.wait, 5)
        manager.submit("run_queued", lambda: None)
        assert manager.cancel("run_queued") is True
        manager.submit("run_replacement", lambda: None)
    finally:
        release.set()
        manager.shutdown()


def test_background_job_manager_shutdown_reports_cancelled_jobs() -> None:
    """服务关闭时只取消排队任务，正在运行的任务仍应正常结束。"""

    manager = BackgroundJobManager(max_workers=1, max_queue_size=1)
    active_started = Event()
    release = Event()

    def wait_until_release() -> None:
        active_started.set()
        release.wait(5)

    manager.submit("run_active", wait_until_release)
    assert active_started.wait(1)
    manager.submit("run_queued", lambda: None)

    cancelled: list[str] = []

    def shutdown_manager() -> None:
        cancelled.extend(manager.shutdown())

    shutdown_thread = Thread(target=shutdown_manager)
    shutdown_thread.start()
    time.sleep(0.05)
    release.set()
    shutdown_thread.join(timeout=2)

    assert not shutdown_thread.is_alive()
    assert cancelled == ["run_queued"]


def test_async_job_can_cancel_queued_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """万悟应能通过 HTTP 取消排队任务，并读取稳定的 cancelled 状态。"""

    test_settings = SimpleNamespace(
        database_path=tmp_path / "cancel_jobs.db",
        async_job_workers=1,
        async_job_queue_size=1,
    )
    storage_module.get_repository.cache_clear()
    jobs_module.get_job_manager.cache_clear()
    monkeypatch.setattr(storage_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(jobs_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path / "uploads")

    active_started = Event()
    release = Event()

    def blocking_job(
        run_id: str,
        _file_id: str,
        _source_path: Path,
        _operation: str,
        _config: object,
    ) -> None:
        repository = storage_module.get_repository()
        repository.mark_run_running(run_id)
        active_started.set()
        release.wait(5)
        repository.finish_run(run_id, "failed", 0.0, error="测试任务结束")

    monkeypatch.setattr(server, "_execute_analysis_job", blocking_job)
    csv_path = tmp_path / "cancel_sample.csv"
    csv_path.write_text(
        "datetime;Pressure\n2026-01-01 00:00:00;1.0\n",
        encoding="utf-8",
    )

    try:
        with TestClient(server.app) as client:
            with csv_path.open("rb") as file:
                upload = client.post(
                    "/api/v1/files",
                    files={"file": (csv_path.name, file, "text/csv")},
                )
            file_id = upload.json()["file_id"]

            active = client.post("/api/v1/jobs", json={"file_id": file_id})
            assert active.status_code == 202
            assert active_started.wait(1)

            queued = client.post("/api/v1/jobs", json={"file_id": file_id})
            assert queued.status_code == 202
            queued_run_id = queued.json()["run_id"]

            cancelled = client.delete(f"/api/v1/jobs/{queued_run_id}")
            assert cancelled.status_code == 200
            assert cancelled.json()["job_status"] == "cancelled"

            status = client.get(f"/api/v1/jobs/{queued_run_id}")
            assert status.status_code == 200
            assert status.json()["job_status"] == "cancelled"
            assert status.json()["result_ready"] is False

            result = client.get(f"/api/v1/jobs/{queued_run_id}/result")
            assert result.status_code == 409
            assert result.json()["detail"] == "任务已取消，没有可用结果"
            release.set()
    finally:
        release.set()
        storage_module.get_repository.cache_clear()
        jobs_module.get_job_manager.cache_clear()


def test_async_job_upload_poll_and_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 链路应完成上传、立即受理、轮询和结果获取。"""

    test_settings = SimpleNamespace(
        database_path=tmp_path / "jobs.db",
        async_job_workers=1,
        async_job_queue_size=2,
    )
    storage_module.get_repository.cache_clear()
    jobs_module.get_job_manager.cache_clear()
    monkeypatch.setattr(storage_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(jobs_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path / "uploads")

    csv_path = tmp_path / "async_sample.csv"
    row_count = 180
    pressure = np.ones(row_count)
    pressure[105:125] += 3.0
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "Pressure": pressure,
            "anomaly": [0] * 105 + [1] * 20 + [0] * 55,
            "changepoint": np.zeros(row_count),
        }
    )
    dataframe.to_csv(csv_path, sep=";", index=False)

    try:
        with TestClient(server.app) as client:
            with csv_path.open("rb") as file:
                upload = client.post(
                    "/api/v1/files",
                    files={"file": (csv_path.name, file, "text/csv")},
                )
            assert upload.status_code == 200
            file_id = upload.json()["file_id"]

            accepted = client.post(
                "/api/v1/jobs",
                json={
                    "file_id": file_id,
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
            run_id = accepted.json()["run_id"]

            status_payload = None
            # 完整任务包含多模型预测和根因诊断，CI 或首次模型加载时可能超过 5 秒。
            for _ in range(300):
                status_response = client.get(f"/api/v1/jobs/{run_id}")
                assert status_response.status_code == 200
                status_payload = status_response.json()
                if status_payload["job_status"] in {"success", "failed"}:
                    break
                time.sleep(0.05)

            assert status_payload is not None
            assert status_payload["job_status"] == "success"
            assert status_payload["result_ready"] is True

            result_response = client.get(f"/api/v1/jobs/{run_id}/result")
            assert result_response.status_code == 200
            result = result_response.json()["result"]
            assert result["run_id"] == run_id
            assert result["status"] == "success"
            assert result["data_profile"]["row_count"] == row_count
            assert result["data_quality"]["sampling_seconds"] == 1.0
            assert result["data_quality"]["label_columns"] == ["anomaly", "changepoint"]
            assert result["data_quality"]["missing_total"] == 0
            visualization = result["visualization"]
            assert len(visualization["timestamps"]) == len(visualization["risk_scores"])
            assert len(visualization["risk_scores"]) <= 360
            assert visualization["sensor_columns"]
            assert visualization["series"]["Pressure"]
            assert "event_ranges" in visualization
            assert "sensor_contributions" in visualization
            assert visualization["threshold"] == 3.0
    finally:
        storage_module.get_repository.cache_clear()
        jobs_module.get_job_manager.cache_clear()
