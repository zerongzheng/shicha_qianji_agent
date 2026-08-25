"""默认 SKAB 样例接口测试。"""

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import server


def test_default_skab_sample_can_be_registered_and_analyzed(monkeypatch, tmp_path: Path) -> None:
    """样例接口返回的固定 file_id 必须能被后续任务定位。"""

    sample = tmp_path / "0.csv"
    sample.write_text(
        "datetime;Pressure;RateRMS\n"
        "2026-01-01 00:00:00;1;2\n"
        "2026-01-01 00:00:01;1.1;2.1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        server,
        "settings",
        replace(
            server.settings,
            default_skab_file=sample,
            auth_bootstrap_password="",
        ),
    )
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path / "uploads")

    class FakeRepository:
        def fail_incomplete_runs(self, _reason):
            # TestClient 会执行 FastAPI 的启动生命周期；这里模拟仓储的启动清理钩子。
            return 0

        def register_file(self, file_id, file_name, storage_path):
            return {
                "file_id": file_id,
                "file_name": file_name,
                "storage_path": str(storage_path),
                "sha256": "test-sha256",
                "size_bytes": storage_path.stat().st_size,
            }

    monkeypatch.setattr(server, "get_repository", lambda: FakeRepository())

    with TestClient(server.app) as client:
        response = client.post("/api/v1/samples/skab/default")

    assert response.status_code == 200
    payload = response.json()
    assert payload["file_id"] == "sample_0"
    assert payload["file_name"] == "0.csv"


def test_default_skab_sample_preflight_returns_real_profile(monkeypatch, tmp_path: Path) -> None:
    """默认样例的预检结果必须来自真实 CSV，而不是前端写死的数量。"""

    sample = tmp_path / "0.csv"
    sample.write_text(
        "datetime;Pressure;RateRMS;anomaly\n"
        "2026-01-01 00:00:00;1;2;0\n"
        "2026-01-01 00:00:01;1.1;;1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        server,
        "settings",
        replace(
            server.settings,
            default_skab_file=sample,
            auth_bootstrap_password="",
        ),
    )
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path / "uploads")

    class FakeRepository:
        def fail_incomplete_runs(self, _reason):
            return 0

        def register_file(self, file_id, file_name, storage_path):
            return {
                "file_id": file_id,
                "file_name": file_name,
                "storage_path": str(storage_path),
                "sha256": "test-sha256",
                "size_bytes": storage_path.stat().st_size,
            }

    monkeypatch.setattr(server, "get_repository", lambda: FakeRepository())

    with TestClient(server.app) as client:
        response = client.get("/api/v1/files/sample_0/preflight")

    assert response.status_code == 200
    payload = response.json()
    assert payload["row_count"] == 2
    assert payload["sensor_count"] == 2
    assert payload["datetime_column"] == "datetime"
    assert payload["missing_rate"] > 0
    assert "anomaly" in payload["columns"]
