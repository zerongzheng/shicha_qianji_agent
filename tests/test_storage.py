"""SQLite 任务归档和工单闭环测试。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import ClassVar

import pytest

from app.storage.repository import IndustrialRepository


def _sample_result(run_id: str) -> dict[str, object]:
    """构造接近分析 API 的最小成功响应。"""

    return {
        "run_id": run_id,
        "status": "success",
        "summary": {"异常事件数": 1, "最高风险等级": "高风险"},
        "anomaly_events": [
            {
                "dominant_sensors": ["Pressure", "Volume Flow RateRMS"],
            }
        ],
        "root_cause_diagnoses": [
            {
                "event_number": 1,
                "regime_context": "稳定工况内事件",
                "sensor_changes": [
                    {
                        "传感器": "Pressure",
                        "类别": "pressure",
                        "direction_code": "up",
                        "变化标准差": 3.2,
                    },
                    {
                        "传感器": "Volume Flow RateRMS",
                        "类别": "flow",
                        "direction_code": "down",
                        "变化标准差": -2.6,
                    },
                ],
            }
        ],
        "work_order_drafts": [
            {
                "record_id": f"{run_id}:WO-E001-000010",
                "work_order_id": "WO-E001-000010",
                "event_number": 1,
                "priority": "P1",
                "title": "核查：阀门卡滞",
                "status": "待确认",
                "assigned_role": "设备运维与工艺联合复核",
                "actions": ["核对阀门开度", "复测压力与流量"],
                "evidence_summary": ["压力上升且流量下降"],
                "required_feedback": ["确认根因", "记录复测结果"],
            }
        ],
    }


def _register_sample_file(repository: IndustrialRepository, tmp_path: Path) -> str:
    """创建并登记一个不会依赖 SKAB 的临时 CSV。"""

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("datetime;Pressure\n2026-01-01;1.0\n", encoding="utf-8")
    repository.register_file("file_test", csv_path.name, csv_path)
    return "file_test"


def test_repository_archives_run_and_creates_work_order(tmp_path: Path) -> None:
    """成功任务应保存完整结果，并把工单草案拆分为可查询记录。"""

    repository = IndustrialRepository(tmp_path / "industrial.db")
    file_id = _register_sample_file(repository, tmp_path)
    run_id = "run_storage_test"

    repository.start_run(
        run_id=run_id,
        file_id=file_id,
        operation="analyze",
        detector="time_frequency_relation",
        config={"threshold": 4.5},
    )
    result = _sample_result(run_id)
    repository.finish_run(run_id, "success", 123.4, result=result)

    stored_run = repository.get_run(run_id)
    assert stored_run is not None
    assert stored_run["status"] == "success"
    assert stored_run["result"] == result
    assert repository.list_runs()[0]["summary"]["异常事件数"] == 1

    work_orders = repository.list_work_orders(run_id=run_id)
    assert len(work_orders) == 1
    assert work_orders[0]["record_id"] == f"{run_id}:WO-E001-000010"
    assert work_orders[0]["source_file_name"] == "sample.csv"
    assert work_orders[0]["source_run_started_at"]
    assert work_orders[0]["actions"] == ["核对阀门开度", "复测压力与流量"]

    reused = repository.find_successful_run(
        file_sha256=stored_run["file_sha256"],
        operation="analyze",
        detector="time_frequency_relation",
        config={"threshold": 4.5},
    )
    assert reused is not None
    assert reused["run_id"] == run_id


def test_repository_supports_nested_quick_diagnosis_result(tmp_path: Path) -> None:
    """万悟快速诊断的嵌套结果也必须生成工单、任务摘要和案例记忆。"""

    repository = IndustrialRepository(tmp_path / "quick_nested.db")
    file_id = _register_sample_file(repository, tmp_path)
    run_id = "run_quick_nested"
    repository.start_run(run_id, file_id, "quick_diagnose", "mad", {})
    analysis = _sample_result(run_id)
    quick_result = {
        "status": "success",
        "run_id": run_id,
        "file_id": file_id,
        "file_name": "sample.csv",
        "analysis": analysis,
        "presentation": "检测到 1 个异常事件。",
    }

    repository.finish_run(run_id, "success", 30.0, result=quick_result)

    assert repository.list_runs()[0]["summary"]["异常事件数"] == 1
    work_orders = repository.list_work_orders(run_id=run_id)
    assert len(work_orders) == 1
    record_id = work_orders[0]["record_id"]
    repository.update_work_order(
        record_id,
        {
            "status": "已完成",
            "confirmed_cause": "阀门执行器卡滞",
            "feedback_note": "现场清理后复测恢复正常",
            "handled_by": "设备运维组",
        },
    )
    cases = repository.list_confirmed_cases()
    assert len(cases) == 1
    assert cases[0]["source_run_id"] == run_id
    assert cases[0]["confirmed_cause"] == "阀门执行器卡滞"


def test_repository_paginates_and_filters_work_orders(tmp_path: Path) -> None:
    """工单列表应支持后端搜索、优先级筛选和偏移分页，不能只依赖前端截取。"""

    repository = IndustrialRepository(tmp_path / "work_order_paging.db")
    file_id = _register_sample_file(repository, tmp_path)
    run_id = "run_work_order_paging"
    repository.start_run(run_id, file_id, "analyze", "mad", {})

    result = _sample_result(run_id)
    drafts = []
    for index, (priority, title, role) in enumerate(
        [
            ("P1", "核查：阀门卡滞", "设备运维组"),
            ("P2", "复核：压力波动", "工艺复核组"),
            ("P3", "巡检：流量偏移", "现场巡检组"),
        ],
        start=1,
    ):
        draft = deepcopy(result["work_order_drafts"][0])
        draft["record_id"] = f"{run_id}:WO-E{index:03d}-000010"
        draft["work_order_id"] = f"WO-E{index:03d}-000010"
        draft["event_number"] = index
        draft["priority"] = priority
        draft["title"] = title
        draft["assigned_role"] = role
        drafts.append(draft)
    result["work_order_drafts"] = drafts
    repository.finish_run(run_id, "success", 20.0, result=result)

    assert repository.count_work_orders() == 3
    assert len(repository.list_work_orders(limit=1, offset=1)) == 1
    assert repository.list_work_orders(limit=1, offset=1)[0]["priority"] == "P2"
    assert repository.count_work_orders(priority="P1") == 1
    assert repository.list_work_orders(search="压力波动")[0]["title"] == "复核：压力波动"
    assert repository.list_work_orders(search="巡检组")[0]["assigned_role"] == "现场巡检组"


def test_repository_updates_work_order_feedback(tmp_path: Path) -> None:
    """现场反馈应更新状态、确认根因、处理人和反馈说明。"""

    repository = IndustrialRepository(tmp_path / "industrial.db")
    file_id = _register_sample_file(repository, tmp_path)
    run_id = "run_feedback_test"
    repository.start_run(run_id, file_id, "analyze", "mad", {})
    repository.finish_run(run_id, "success", 50.0, result=_sample_result(run_id))

    record_id = f"{run_id}:WO-E001-000010"
    updated = repository.update_work_order(
        record_id,
        {
            "status": "已完成",
            "confirmed_cause": "阀门执行器卡滞",
            "feedback_note": "清理后复测恢复正常",
            "handled_by": "设备运维组",
        },
    )

    assert updated["status"] == "已完成"
    assert updated["confirmed_cause"] == "阀门执行器卡滞"
    assert updated["handled_by"] == "设备运维组"

    status_only = repository.update_work_order(record_id, {"status": "已关闭"})
    assert status_only["status"] == "已关闭"
    assert status_only["confirmed_cause"] == "阀门执行器卡滞"
    assert status_only["handled_by"] == "设备运维组"

    cases = repository.list_confirmed_cases()
    assert len(cases) == 1
    assert cases[0]["confirmed_cause"] == "阀门执行器卡滞"
    matches = repository.find_similar_cases(
        sensor_changes=[
            {
                "传感器": "Pressure",
                "类别": "pressure",
                "direction_code": "up",
                "变化标准差": 2.9,
            },
            {
                "传感器": "Volume Flow RateRMS",
                "类别": "flow",
                "direction_code": "down",
                "变化标准差": -2.1,
            },
        ],
        dominant_sensors=["Pressure", "Volume Flow RateRMS"],
        regime_context="稳定工况内事件",
    )
    assert matches[0].confirmed_cause == "阀门执行器卡滞"
    assert matches[0].similarity == 1.0


def test_repository_removes_case_memory_without_deleting_analysis_evidence(tmp_path: Path) -> None:
    """移除案例只清空现场确认信息，来源分析任务和工单本体仍应保留。"""

    repository = IndustrialRepository(tmp_path / "remove_case.db")
    file_id = _register_sample_file(repository, tmp_path)
    run_id = "run_remove_case"
    repository.start_run(run_id, file_id, "analyze", "mad", {})
    repository.finish_run(run_id, "success", 50.0, result=_sample_result(run_id))
    record_id = f"{run_id}:WO-E001-000010"
    repository.update_work_order(
        record_id,
        {
            "status": "已完成",
            "confirmed_cause": "阀门执行器卡滞",
            "feedback_note": "清理后复测恢复正常",
            "handled_by": "设备运维组",
        },
    )

    removed = repository.remove_confirmed_case(f"CASE-{record_id}")

    assert removed["status"] == "removed"
    assert repository.list_confirmed_cases() == []
    order = repository.list_work_orders(run_id=run_id)[0]
    assert order["status"] == "待确认"
    assert order["confirmed_cause"] is None
    stored_run = repository.get_run(run_id)
    assert stored_run is not None
    assert stored_run["status"] == "success"
    assert stored_run["result"] is not None


def test_repository_archives_and_restores_without_deleting_evidence(tmp_path: Path) -> None:
    """归档只改变默认展示范围，分析结果、工单和案例数据仍可恢复。"""

    repository = IndustrialRepository(tmp_path / "archive.db")
    file_id = _register_sample_file(repository, tmp_path)
    run_id = "run_archive_test"
    repository.start_run(run_id, file_id, "analyze", "mad", {})
    repository.finish_run(run_id, "success", 20.0, result=_sample_result(run_id))
    record_id = f"{run_id}:WO-E001-000010"
    repository.update_work_order(
        record_id,
        {
            "status": "已关闭",
            "confirmed_cause": "阀门执行器卡滞",
            "feedback_note": "清理后复测恢复正常",
            "handled_by": "设备运维组",
        },
    )

    archived_order = repository.archive_work_order(record_id, "演示数据归档")
    assert archived_order["archived_at"] is not None
    assert repository.list_work_orders() == []
    assert repository.list_confirmed_cases() == []
    assert len(repository.list_work_orders(include_archived=True)) == 1
    assert len(repository.list_confirmed_cases(include_archived=True)) == 1
    assert len(repository.list_work_orders(archived_only=True)) == 1
    assert len(repository.list_confirmed_cases(archived_only=True)) == 1

    archived_run = repository.archive_run(run_id, "演示数据归档")
    assert archived_run["archived_at"] is not None
    assert repository.list_runs() == []
    assert len(repository.list_runs(include_archived=True)) == 1

    repository.restore_run(run_id)
    repository.restore_work_order(record_id)
    assert len(repository.list_runs()) == 1
    assert len(repository.list_work_orders()) == 1
    assert len(repository.list_confirmed_cases()) == 1


def test_repository_does_not_archive_active_run_or_open_work_order(tmp_path: Path) -> None:
    """运行中的任务和未闭环工单不能被误归档。"""

    repository = IndustrialRepository(tmp_path / "archive_guard.db")
    file_id = _register_sample_file(repository, tmp_path)
    repository.start_run("run_active_guard", file_id, "analyze", "mad", {}, status="running")
    with pytest.raises(ValueError, match="不能归档"):
        repository.archive_run("run_active_guard")

    repository.finish_run(
        "run_active_guard",
        "success",
        20.0,
        result=_sample_result("run_active_guard"),
    )
    record_id = "run_active_guard:WO-E001-000010"
    with pytest.raises(ValueError, match="已完成或已关闭"):
        repository.archive_work_order(record_id)


def test_repository_cannot_archive_run_with_open_work_order(tmp_path: Path) -> None:
    """任务仍有待确认工单时不能被整体归档，避免运维队列被意外隐藏。"""

    repository = IndustrialRepository(tmp_path / "archive_open_order.db")
    file_id = _register_sample_file(repository, tmp_path)
    run_id = "run_open_order_guard"
    repository.start_run(run_id, file_id, "analyze", "mad", {})
    repository.finish_run(run_id, "success", 20.0, result=_sample_result(run_id))

    with pytest.raises(ValueError, match="未闭环工单"):
        repository.archive_run(run_id)


def test_repository_rejects_unknown_work_order_status(tmp_path: Path) -> None:
    """状态机只接受约定状态，避免万悟写入不可统计的自由文本。"""

    repository = IndustrialRepository(tmp_path / "industrial.db")

    with pytest.raises(ValueError, match="工单状态只能是"):
        repository.update_work_order("missing", {"status": "随便填写"})


def test_repository_records_failed_run(tmp_path: Path) -> None:
    """算法失败也应保留参数、错误和耗时，便于比赛演示时追踪。"""

    repository = IndustrialRepository(tmp_path / "industrial.db")
    file_id = _register_sample_file(repository, tmp_path)
    repository.start_run("run_failed", file_id, "analyze", "mad", {"threshold": 3})
    repository.finish_run("run_failed", "failed", 12.5, error="数据列不足")

    stored = repository.get_run("run_failed")
    assert stored is not None
    assert stored["status"] == "failed"
    assert stored["error"] == "数据列不足"
    assert stored["result"] is None


def test_repository_records_local_analysis_result(tmp_path: Path) -> None:
    """Streamlit 直接分析的结果也应进入任务表并生成可回写工单。"""

    repository = IndustrialRepository(tmp_path / "industrial.db")
    csv_path = tmp_path / "local.csv"
    csv_path.write_text("datetime;Pressure\n2026-01-01;1.0\n", encoding="utf-8")

    class Profile:
        source_name = "local.csv"
        row_count = 1
        sensor_columns: ClassVar[list[str]] = ["Pressure"]
        missing_total = 0
        start_time = "2026-01-01"
        end_time = "2026-01-01"

    class Event:
        pass

    class Result:
        source_path = csv_path
        detector_name = "mad"
        profile = Profile()
        events: ClassVar[list[object]] = []
        operating_regimes = None
        relationship_diagnostics: ClassVar[list[object]] = []
        event_diagnoses: ClassVar[list[object]] = []
        work_order_drafts: ClassVar[list[object]] = []
        forecast_results: ClassVar[dict[str, object]] = {}
        risk_alerts: ClassVar[list[object]] = []
        recommendations: ClassVar[list[str]] = []

        @staticmethod
        def to_summary() -> dict[str, object]:
            return {"异常事件数": 0}

    run_id = repository.record_local_analysis(
        csv_path,
        operation="streamlit_analyze",
        detector="mad",
        config={"threshold": 5.5},
        result=Result(),
    )

    stored = repository.get_run(run_id)
    assert stored is not None
    assert stored["status"] == "success"
    assert stored["result"]["summary"]["异常事件数"] == 0
    assert repository.list_work_orders(run_id=run_id) == []


def test_repository_transitions_queued_job_and_recovers_interrupted_runs(
    tmp_path: Path,
) -> None:
    """异步任务应从排队进入运行，重启恢复时关闭所有未完成任务。"""

    repository = IndustrialRepository(tmp_path / "industrial.db")
    file_id = _register_sample_file(repository, tmp_path)
    repository.start_run(
        "run_queued",
        file_id,
        "analyze",
        "mad",
        {"threshold": 3},
        status="queued",
    )
    repository.mark_run_running("run_queued")
    assert repository.get_run("run_queued")["status"] == "running"

    repository.start_run(
        "run_waiting",
        file_id,
        "diagnose",
        "mad",
        {},
        status="queued",
    )
    recovered = repository.fail_incomplete_runs("服务重启导致任务中断")

    assert recovered == 2
    assert repository.get_run("run_queued")["status"] == "failed"
    assert repository.get_run("run_waiting")["error"] == "服务重启导致任务中断"


def test_repository_only_cancels_queued_run(tmp_path: Path) -> None:
    """取消操作只能改变排队任务，不能覆盖运行中任务的真实状态。"""

    repository = IndustrialRepository(tmp_path / "industrial.db")
    file_id = _register_sample_file(repository, tmp_path)
    repository.start_run("run_cancelled", file_id, "analyze", "mad", {}, status="queued")
    repository.start_run("run_running", file_id, "analyze", "mad", {}, status="queued")
    repository.mark_run_running("run_running")

    assert repository.cancel_run("run_cancelled", "平台取消任务") is True
    assert repository.cancel_run("run_running", "平台取消任务") is False
    assert repository.get_run("run_cancelled")["status"] == "cancelled"
    assert repository.get_run("run_running")["status"] == "running"
