"""工业分析结果与工单闭环的 SQLite 仓储。

SQLite 适合当前单机开发、比赛演示和小规模试运行：无需额外服务，数据库文件可以随项目
部署，同时具备事务、索引和结构化查询能力。本模块使用 Python 标准库 ``sqlite3``，避免
在项目早期引入 ORM 和迁移框架带来的额外复杂度。

数据库保存文件元数据、任务参数、分析结果和现场反馈。原始 CSV 仍保存在受控上传目录，
不写进数据库，避免数据库快速膨胀，也便于以后迁移到对象存储。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.diagnosis.patterns import classify_sensor
from app.models import HistoricalCaseMatch

WORK_ORDER_STATUSES = {"待确认", "已确认", "处理中", "待验证", "已完成", "已关闭"}


class IndustrialRepository:
    """封装建表、事务和常用业务查询。"""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """为每次操作创建短连接，适配 FastAPI 多线程请求。"""

        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        """创建当前版本所需表和索引；重复启动不会破坏已有数据。"""

        with self._connect() as connection:
            # WAL 让看板读取和分析任务写入更好地并行。
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS uploaded_files (
                    file_id TEXT PRIMARY KEY,
                    file_name TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS analysis_runs (
                    run_id TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    detector TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms REAL,
                    archived_at TEXT,
                    archive_reason TEXT,
                    FOREIGN KEY (file_id) REFERENCES uploaded_files(file_id)
                );

                CREATE TABLE IF NOT EXISTS work_orders (
                    record_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    source_work_order_id TEXT NOT NULL,
                    event_number INTEGER NOT NULL,
                    priority TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_role TEXT NOT NULL,
                    actions_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    required_feedback_json TEXT NOT NULL,
                    confirmed_cause TEXT,
                    feedback_note TEXT,
                    handled_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT,
                    archive_reason TEXT,
                    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
                    UNIQUE (run_id, source_work_order_id)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_file_created
                    ON analysis_runs(file_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_status_created
                    ON analysis_runs(status, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_work_orders_status_priority
                    ON work_orders(status, priority, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_work_orders_run
                    ON work_orders(run_id, event_number);
                """
            )
            # 兼容旧数据库：新增字段通过迁移补齐，不要求删除已有分析记录。
            self._add_column_if_missing(connection, "analysis_runs", "archived_at", "TEXT")
            self._add_column_if_missing(connection, "analysis_runs", "archive_reason", "TEXT")
            self._add_column_if_missing(connection, "work_orders", "archived_at", "TEXT")
            self._add_column_if_missing(connection, "work_orders", "archive_reason", "TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_archived_created "
                "ON analysis_runs(archived_at, started_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_orders_archived_updated "
                "ON work_orders(archived_at, updated_at DESC)"
            )

    @staticmethod
    def _add_column_if_missing(
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        """给旧表补列；表名和列名均来自代码常量，不接受外部输入。"""

        columns = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )

    def register_file(
        self,
        file_id: str,
        file_name: str,
        storage_path: Path,
    ) -> dict[str, Any]:
        """登记上传文件元数据并计算内容哈希。"""

        metadata = {
            "file_id": file_id,
            "file_name": file_name,
            "storage_path": str(storage_path.resolve()),
            "sha256": _sha256(storage_path),
            "size_bytes": storage_path.stat().st_size,
            "created_at": _now(),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO uploaded_files (
                    file_id, file_name, storage_path, sha256, size_bytes, created_at
                ) VALUES (
                    :file_id, :file_name, :storage_path, :sha256, :size_bytes, :created_at
                )
                ON CONFLICT(file_id) DO UPDATE SET
                    file_name = excluded.file_name,
                    storage_path = excluded.storage_path,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes
                """,
                metadata,
            )
        return metadata

    def start_run(
        self,
        run_id: str,
        file_id: str,
        operation: str,
        detector: str,
        config: dict[str, Any],
        status: str = "running",
    ) -> None:
        """在耗时计算开始前登记任务，异常退出后仍能留下记录。"""

        if status not in {"queued", "running"}:
            raise ValueError("新任务状态只能是 queued 或 running")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_runs (
                    run_id, file_id, operation, detector, status, config_json, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    file_id,
                    operation,
                    detector,
                    status,
                    _to_json(config),
                    _now(),
                ),
            )

    def mark_run_running(self, run_id: str) -> None:
        """后台线程真正获得执行槽位后，把排队任务切换为运行中。"""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE analysis_runs
                SET status = 'running', error = NULL
                WHERE run_id = ? AND status = 'queued'
                """,
                (run_id,),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"找不到可启动的排队任务：{run_id}")

    def fail_incomplete_runs(self, reason: str) -> int:
        """服务重启时关闭遗留 queued/running 任务，避免状态永久悬挂。"""

        timestamp = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE analysis_runs
                SET status = 'failed', error = ?, finished_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (reason, timestamp),
            )
        return cursor.rowcount

    def cancel_run(self, run_id: str, reason: str) -> bool:
        """把仍在排队的任务标记为已取消。

        条件更新保证已进入 ``running`` 的任务不会被误标为取消。返回 ``False`` 说明任务
        已开始、已结束或不存在，调用方应重新读取当前状态后决定如何响应。
        """

        timestamp = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE analysis_runs
                SET status = 'cancelled', error = ?, finished_at = ?, duration_ms = 0
                WHERE run_id = ? AND status = 'queued'
                """,
                (reason, timestamp, run_id),
            )
        return cursor.rowcount == 1

    def finish_run(
        self,
        run_id: str,
        status: str,
        duration_ms: float,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """原子更新任务结果，并在成功时同步生成工单记录。"""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE analysis_runs
                SET status = ?, result_json = ?, error = ?, finished_at = ?, duration_ms = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    _to_json(result) if result is not None else None,
                    error,
                    _now(),
                    duration_ms,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"找不到分析任务：{run_id}")
            if status == "success" and result is not None:
                self._upsert_work_orders(
                    connection,
                    run_id,
                    result.get("work_order_drafts", []),
                )

    def record_local_analysis(
        self,
        source_path: str | Path,
        operation: str,
        detector: str,
        config: dict[str, Any],
        result: Any,
    ) -> str:
        """把 Streamlit 直接完成的分析也写入任务表，形成可回写闭环。

        FastAPI 异步任务天然会记录 ``analysis_runs``，但 Streamlit 为了交互速度会直接
        调用分析流水线。若不在这里补一次持久化，页面上生成的工单无法被现场确认，也不会
        沉淀为下一次分析可检索的历史案例。因此本方法复用同一套 SQLite 表和工单结构。
        """

        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"分析文件不存在：{source}")
        file_id = f"local-{_sha256(source)[:24]}"
        run_id = f"run-local-{uuid.uuid4().hex[:20]}"
        self.register_file(file_id, source.name, source)
        started = time.perf_counter()
        self.start_run(run_id, file_id, operation, detector, config, status="running")
        try:
            payload = _analysis_result_record(run_id, result)
            self.finish_run(
                run_id,
                status="success",
                duration_ms=(time.perf_counter() - started) * 1000,
                result=payload,
            )
        except Exception as exc:
            self.finish_run(
                run_id,
                status="failed",
                duration_ms=(time.perf_counter() - started) * 1000,
                error=str(exc),
            )
            raise
        return run_id

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """读取单次任务及其完整结构化结果。"""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, f.file_name, f.sha256, f.size_bytes
                FROM analysis_runs AS r
                JOIN uploaded_files AS f ON f.file_id = r.file_id
                WHERE r.run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return _run_record(row, include_result=True) if row else None

    def find_successful_run(
        self,
        *,
        file_sha256: str,
        operation: str,
        detector: str,
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        """查找同一文件和同一配置下最近一次成功任务。

        万悟在网络抖动或结果解析失败时可能重复提交同一个工具请求。快速演示接口使用
        该查询实现幂等复用，避免重复计算、重复写入工单，也避免重复触发后续平台编排。
        配置使用统一 JSON 序列化后比较，保证字典键顺序不会影响匹配结果。
        """

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, f.file_name, f.sha256, f.size_bytes
                FROM analysis_runs AS r
                JOIN uploaded_files AS f ON f.file_id = r.file_id
                WHERE f.sha256 = ?
                  AND r.operation = ?
                  AND r.detector = ?
                  AND r.config_json = ?
                  AND r.status = 'success'
                  AND r.archived_at IS NULL
                  AND r.result_json IS NOT NULL
                ORDER BY r.started_at DESC
                LIMIT 1
                """,
                (file_sha256, operation, detector, _to_json(config)),
            ).fetchone()
        return _run_record(row, include_result=True) if row else None

    def list_runs(
        self,
        limit: int = 20,
        status: str | None = None,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[dict[str, Any]]:
        """按时间倒序返回任务摘要，供万悟历史任务页使用。"""

        query = """
            SELECT r.*, f.file_name, f.sha256, f.size_bytes
            FROM analysis_runs AS r
            JOIN uploaded_files AS f ON f.file_id = r.file_id
        """
        parameters: list[Any] = []
        conditions: list[str] = []
        if archived_only:
            conditions.append("r.archived_at IS NOT NULL")
        elif not include_archived:
            conditions.append("r.archived_at IS NULL")
        if status:
            conditions.append("r.status = ?")
            parameters.append(status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY r.started_at DESC LIMIT ?"
        parameters.append(max(1, min(200, limit)))
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_run_record(row, include_result=False) for row in rows]

    def list_work_orders(
        self,
        limit: int = 50,
        status: str | None = None,
        run_id: str | None = None,
        search: str | None = None,
        priority: str | None = None,
        offset: int = 0,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[dict[str, Any]]:
        """查询工单队列，可按状态或分析任务过滤。"""

        conditions, parameters = self._work_order_filters(
            status=status,
            run_id=run_id,
            search=search,
            priority=priority,
            include_archived=include_archived,
            archived_only=archived_only,
        )
        query = "SELECT w.* FROM work_orders AS w JOIN analysis_runs AS r ON r.run_id = w.run_id"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += (
            " ORDER BY CASE priority WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,"
            " updated_at DESC LIMIT ? OFFSET ?"
        )
        parameters.extend([max(1, min(200, limit)), max(0, offset)])
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_work_order_record(row) for row in rows]

    def count_work_orders(
        self,
        status: str | None = None,
        run_id: str | None = None,
        search: str | None = None,
        priority: str | None = None,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> int:
        """统计当前筛选条件下的工单数量，供前端分页显示总页数。"""

        conditions, parameters = self._work_order_filters(
            status=status,
            run_id=run_id,
            search=search,
            priority=priority,
            include_archived=include_archived,
            archived_only=archived_only,
        )
        query = "SELECT COUNT(*) FROM work_orders AS w JOIN analysis_runs AS r ON r.run_id = w.run_id"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        with self._connect() as connection:
            return int(connection.execute(query, parameters).fetchone()[0])

    @staticmethod
    def _work_order_filters(
        *,
        status: str | None,
        run_id: str | None,
        search: str | None,
        priority: str | None,
        include_archived: bool,
        archived_only: bool,
    ) -> tuple[list[str], list[Any]]:
        """集中构造工单列表和工单总数共用的 SQL 过滤条件。"""

        conditions: list[str] = []
        parameters: list[Any] = []
        if archived_only:
            conditions.append("(w.archived_at IS NOT NULL OR r.archived_at IS NOT NULL)")
        elif not include_archived:
            conditions.append("w.archived_at IS NULL AND r.archived_at IS NULL")
        if status:
            conditions.append("w.status = ?")
            parameters.append(status)
        if run_id:
            conditions.append("w.run_id = ?")
            parameters.append(run_id)
        if priority:
            conditions.append("w.priority = ?")
            parameters.append(priority)
        if search and search.strip():
            keyword = f"%{search.strip().lower()}%"
            conditions.append(
                "(LOWER(w.record_id) LIKE ? OR LOWER(w.title) LIKE ? "
                "OR LOWER(w.assigned_role) LIKE ?)"
            )
            parameters.extend([keyword, keyword, keyword])
        return conditions, parameters

    def archive_run(self, run_id: str, reason: str | None = None) -> dict[str, Any]:
        """归档已结束的分析任务，保留任务结果、文件和关联工单。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, archived_at FROM analysis_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"找不到分析任务：{run_id}")
            if row["status"] in {"queued", "running"}:
                raise ValueError("排队中或运行中的任务不能归档")
            active_orders = connection.execute(
                "SELECT COUNT(*) FROM work_orders "
                "WHERE run_id = ? AND status NOT IN ('已完成', '已关闭')",
                (run_id,),
            ).fetchone()[0]
            if active_orders:
                raise ValueError("该任务仍有未闭环工单，请先完成或关闭工单")
            if row["archived_at"] is None:
                connection.execute(
                    "UPDATE analysis_runs SET archived_at = ?, archive_reason = ? WHERE run_id = ?",
                    (_now(), _optional_text(reason), run_id),
                )
        archived = self.get_run(run_id)
        if archived is None:
            raise LookupError(f"找不到分析任务：{run_id}")
        return archived

    def restore_run(self, run_id: str) -> dict[str, Any]:
        """恢复分析任务，使其重新出现在默认历史列表中。"""

        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE analysis_runs SET archived_at = NULL, archive_reason = NULL "
                "WHERE run_id = ?",
                (run_id,),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"找不到分析任务：{run_id}")
        restored = self.get_run(run_id)
        if restored is None:
            raise LookupError(f"找不到分析任务：{run_id}")
        return restored

    def archive_work_order(
        self,
        record_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """归档已完成或已关闭工单；归档不会删除其历史案例和分析证据。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, archived_at FROM work_orders WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"找不到工单：{record_id}")
            if row["status"] not in {"已完成", "已关闭"}:
                raise ValueError("只有已完成或已关闭的工单可以归档")
            if row["archived_at"] is None:
                timestamp = _now()
                connection.execute(
                    "UPDATE work_orders SET archived_at = ?, archive_reason = ?, updated_at = ? "
                    "WHERE record_id = ?",
                    (timestamp, _optional_text(reason), timestamp, record_id),
                )
        archived = self._get_work_order(record_id)
        if archived is None:
            raise LookupError(f"找不到工单：{record_id}")
        return archived

    def restore_work_order(self, record_id: str) -> dict[str, Any]:
        """恢复已归档工单，使工单和对应历史案例重新出现在默认列表中。"""

        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE work_orders SET archived_at = NULL, archive_reason = NULL, updated_at = ? "
                "WHERE record_id = ?",
                (_now(), record_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"找不到工单：{record_id}")
        restored = self._get_work_order(record_id)
        if restored is None:
            raise LookupError(f"找不到工单：{record_id}")
        return restored

    def _get_work_order(self, record_id: str) -> dict[str, Any] | None:
        """读取单条工单，供归档/恢复后返回最新状态。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM work_orders WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        return _work_order_record(row) if row else None

    def update_work_order(
        self,
        record_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """回写现场状态、确认根因和处置反馈。"""

        status = str(updates.get("status", "")).strip()
        if status not in WORK_ORDER_STATUSES:
            raise ValueError(
                "工单状态只能是：" + "、".join(sorted(WORK_ORDER_STATUSES))
            )
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM work_orders WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            if current is None:
                raise LookupError(f"找不到工单：{record_id}")
            if current["archived_at"] is not None:
                raise ValueError("已归档工单不可直接修改，请先恢复")
            # 只有填写现场确认根因，工单才具备沉淀为历史案例的必要信息。
            confirmed_cause = (
                _optional_text(updates["confirmed_cause"])
                if "confirmed_cause" in updates
                else current["confirmed_cause"]
            )
            if status in {"已确认", "已完成", "已关闭"} and not confirmed_cause:
                raise ValueError("已确认、已完成或已关闭的工单必须填写确认根因")
            # PATCH 只修改请求中明确给出的字段，避免状态流转时清空既有现场反馈。
            values = {
                "record_id": record_id,
                "status": status,
                "confirmed_cause": (
                    confirmed_cause
                    if "confirmed_cause" in updates
                    else current["confirmed_cause"]
                ),
                "feedback_note": (
                    _optional_text(updates["feedback_note"])
                    if "feedback_note" in updates
                    else current["feedback_note"]
                ),
                "handled_by": (
                    _optional_text(updates["handled_by"])
                    if "handled_by" in updates
                    else current["handled_by"]
                ),
                "updated_at": _now(),
            }
            connection.execute(
                """
                UPDATE work_orders
                SET status = :status,
                    confirmed_cause = :confirmed_cause,
                    feedback_note = :feedback_note,
                    handled_by = :handled_by,
                    updated_at = :updated_at
                WHERE record_id = :record_id
                """,
                values,
            )
            row = connection.execute(
                "SELECT * FROM work_orders WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"找不到工单：{record_id}")
        return _work_order_record(row)

    def list_confirmed_cases(
        self,
        limit: int = 100,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[dict[str, Any]]:
        """把已确认现场根因的工单转换为可追溯案例。

        案例特征来自当时保存的算法结果，人工只负责确认根因和补充处置反馈。这样既保留
        现场经验，也能追溯到原始任务、异常事件和算法证据。
        """

        query = """
            SELECT w.*, r.result_json
            FROM work_orders AS w
            JOIN analysis_runs AS r ON r.run_id = w.run_id
            WHERE w.status IN ('已确认', '已完成', '已关闭')
              AND w.confirmed_cause IS NOT NULL
              AND TRIM(w.confirmed_cause) <> ''
              AND r.status = 'success'
              AND r.result_json IS NOT NULL
        """
        if include_archived and not archived_only:
            pass
        elif archived_only:
            query += " AND (w.archived_at IS NOT NULL OR r.archived_at IS NOT NULL)"
        else:
            query += " AND w.archived_at IS NULL AND r.archived_at IS NULL"
        query += " ORDER BY w.updated_at DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(
                query,
                (max(1, min(500, limit)),),
            ).fetchall()
        cases = [_confirmed_case_record(row) for row in rows]
        return [item for item in cases if item is not None]

    def remove_confirmed_case(self, case_id: str) -> dict[str, Any]:
        """永久移除一条案例记忆，但保留来源工单、分析任务和原始数据证据。

        当前案例并没有独立的数据表，而是由工单中的现场确认字段动态生成。
        因此这里清空确认根因、现场反馈和处理人员，并将工单退回待确认状态，
        这样案例不会继续参与历史案例展示和相似案例检索，同时不破坏分析结果。
        """

        prefix = "CASE-"
        if not case_id.startswith(prefix):
            raise ValueError("案例编号格式不正确")
        record_id = case_id[len(prefix):]
        if not record_id:
            raise ValueError("案例编号不能为空")
        timestamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_id, status, confirmed_cause FROM work_orders WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"找不到案例来源工单：{case_id}")
            if not row["confirmed_cause"]:
                raise LookupError(f"案例不存在或已经移除：{case_id}")
            connection.execute(
                """
                UPDATE work_orders
                SET status = '待确认',
                    confirmed_cause = NULL,
                    feedback_note = NULL,
                    handled_by = NULL,
                    updated_at = ?
                WHERE record_id = ?
                """,
                (timestamp, record_id),
            )
        return {
            "case_id": case_id,
            "record_id": record_id,
            "status": "removed",
            "message": "案例记忆已永久移除，来源分析证据仍保留",
        }

    def find_similar_cases(
        self,
        sensor_changes: list[dict[str, Any]],
        dominant_sensors: list[str],
        regime_context: str,
        limit: int = 3,
        minimum_similarity: float = 0.35,
    ) -> list[HistoricalCaseMatch]:
        """按测点类别、变化方向、主导测点和工况检索历史案例。"""

        query_signature = _case_signature(
            sensor_changes,
            dominant_sensors,
            regime_context,
        )
        matches: list[HistoricalCaseMatch] = []
        for case in self.list_confirmed_cases(limit=200):
            similarity, groups, directions = _case_similarity(
                query_signature,
                case["signature"],
            )
            if similarity < minimum_similarity:
                continue
            matches.append(
                HistoricalCaseMatch(
                    case_id=case["case_id"],
                    confirmed_cause=case["confirmed_cause"],
                    similarity=round(similarity, 4),
                    source_run_id=case["source_run_id"],
                    source_record_id=case["source_record_id"],
                    matched_sensor_groups=tuple(groups),
                    matched_directions=tuple(directions),
                    evidence_summary=tuple(case["evidence_summary"][:4]),
                    feedback_note=case["feedback_note"],
                    handled_by=case["handled_by"],
                    closed_at=case["closed_at"],
                )
            )
        matches.sort(key=lambda item: (item.similarity, item.closed_at), reverse=True)
        return matches[: max(1, min(10, limit))]

    def _upsert_work_orders(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        work_orders: list[dict[str, Any]],
    ) -> None:
        """把算法工单草案映射为带任务命名空间的持久化工单。"""

        timestamp = _now()
        for draft in work_orders:
            source_id = str(draft["work_order_id"])
            record_id = str(draft.get("record_id") or f"{run_id}:{source_id}")
            connection.execute(
                """
                INSERT INTO work_orders (
                    record_id, run_id, source_work_order_id, event_number, priority,
                    title, status, assigned_role, actions_json, evidence_json,
                    required_feedback_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source_work_order_id) DO UPDATE SET
                    priority = excluded.priority,
                    title = excluded.title,
                    assigned_role = excluded.assigned_role,
                    actions_json = excluded.actions_json,
                    evidence_json = excluded.evidence_json,
                    required_feedback_json = excluded.required_feedback_json,
                    updated_at = excluded.updated_at
                """,
                (
                    record_id,
                    run_id,
                    source_id,
                    int(draft["event_number"]),
                    str(draft["priority"]),
                    str(draft["title"]),
                    str(draft.get("status", "待确认")),
                    str(draft["assigned_role"]),
                    _to_json(draft.get("actions", [])),
                    _to_json(draft.get("evidence_summary", [])),
                    _to_json(draft.get("required_feedback", [])),
                    timestamp,
                    timestamp,
                ),
            )


@lru_cache(maxsize=1)
def get_repository() -> IndustrialRepository:
    """返回进程内共享仓储；数据库连接仍按操作短暂创建。"""

    return IndustrialRepository(get_settings().database_path)


def _run_record(row: sqlite3.Row, include_result: bool) -> dict[str, Any]:
    """将 SQLite 行转换为万悟可直接使用的字典。"""

    record = {
        "run_id": row["run_id"],
        "file_id": row["file_id"],
        "file_name": row["file_name"],
        "file_sha256": row["sha256"],
        "file_size_bytes": row["size_bytes"],
        "operation": row["operation"],
        "detector": row["detector"],
        "status": row["status"],
        "config": _from_json(row["config_json"], {}),
        "error": row["error"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_ms": row["duration_ms"],
        "archived_at": row["archived_at"],
        "archive_reason": row["archive_reason"],
    }
    if include_result:
        record["result"] = _from_json(row["result_json"], None)
    else:
        result = _from_json(row["result_json"], {})
        record["summary"] = result.get("summary") if isinstance(result, dict) else None
    return record


def _analysis_result_record(run_id: str, result: Any) -> dict[str, Any]:
    """将页面分析结果转换为历史案例和工单闭环需要的结构化记录。"""

    regimes = result.operating_regimes
    return {
        "run_id": run_id,
        "status": "success",
        "file_id": f"local-{_sha256(Path(result.source_path))[:24]}",
        "file_name": result.profile.source_name,
        "size_bytes": Path(result.source_path).stat().st_size,
        "detector": result.detector_name,
        "data_profile": {
            "source_name": result.profile.source_name,
            "row_count": result.profile.row_count,
            "sensor_columns": result.profile.sensor_columns,
            "missing_total": result.profile.missing_total,
            "start_time": result.profile.start_time,
            "end_time": result.profile.end_time,
        },
        "anomaly_events": [asdict(item) for item in result.events],
        "operating_regimes": (
            {
                "state_count": regimes.state_count,
                "segments": regimes.segments,
                "event_contexts": regimes.event_contexts,
                "suppression_applied": regimes.suppression_applied,
                "suppressed_event_count": regimes.suppressed_event_count,
            }
            if regimes is not None
            else None
        ),
        "relationship_diagnostics": result.relationship_diagnostics,
        "root_cause_diagnoses": [asdict(item) for item in result.event_diagnoses],
        "work_order_drafts": [
            {
                **asdict(item),
                "record_id": f"{run_id}:{item.work_order_id}",
            }
            for item in result.work_order_drafts
        ],
        "forecast_results": result.forecast_results,
        "risk_alerts": result.risk_alerts,
        "recommendations": result.recommendations,
        "summary": result.to_summary(),
    }


def _work_order_record(row: sqlite3.Row) -> dict[str, Any]:
    """反序列化工单中的列表字段。"""

    return {
        "record_id": row["record_id"],
        "run_id": row["run_id"],
        "work_order_id": row["source_work_order_id"],
        "event_number": row["event_number"],
        "priority": row["priority"],
        "title": row["title"],
        "status": row["status"],
        "assigned_role": row["assigned_role"],
        "actions": _from_json(row["actions_json"], []),
        "evidence_summary": _from_json(row["evidence_json"], []),
        "required_feedback": _from_json(row["required_feedback_json"], []),
        "confirmed_cause": row["confirmed_cause"],
        "feedback_note": row["feedback_note"],
        "handled_by": row["handled_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "archived_at": row["archived_at"],
        "archive_reason": row["archive_reason"],
    }


def _confirmed_case_record(row: sqlite3.Row) -> dict[str, Any] | None:
    """从历史任务结果中提取指定事件的稳定案例特征。"""

    result = _from_json(row["result_json"], {})
    diagnoses = result.get("root_cause_diagnoses", [])
    event_number = int(row["event_number"])
    diagnosis = next(
        (
            item
            for item in diagnoses
            if int(item.get("event_number", 0)) == event_number
        ),
        None,
    )
    if not diagnosis:
        return None
    sensor_changes = diagnosis.get("sensor_changes", [])
    event = next(
        (
            item
            for index, item in enumerate(result.get("anomaly_events", []), start=1)
            if index == event_number
        ),
        {},
    )
    regime_context = str(diagnosis.get("regime_context", ""))
    return {
        "case_id": f"CASE-{row['record_id']}",
        "confirmed_cause": str(row["confirmed_cause"]),
        "source_run_id": str(row["run_id"]),
        "source_record_id": str(row["record_id"]),
        "evidence_summary": _from_json(row["evidence_json"], []),
        "feedback_note": row["feedback_note"],
        "handled_by": row["handled_by"],
        "closed_at": str(row["updated_at"]),
        "archived_at": row["archived_at"],
        "archive_reason": row["archive_reason"],
        "signature": _case_signature(
            sensor_changes,
            list(event.get("dominant_sensors", [])),
            regime_context,
        ),
    }


def _case_signature(
    sensor_changes: list[dict[str, Any]],
    dominant_sensors: list[str],
    regime_context: str,
) -> dict[str, set[str] | str]:
    """把一次事件压缩成不包含原始数值的可比较特征。"""

    groups = {
        str(item.get("类别") or classify_sensor(str(item.get("传感器", ""))))
        for item in sensor_changes
        if abs(float(item.get("变化标准差", 0.0))) >= 0.8
    }
    directions = {
        f"{item.get('类别') or classify_sensor(str(item.get('传感器', '')))!s}:"
        f"{item.get('direction_code', 'flat')!s}"
        for item in sensor_changes
        if str(item.get("direction_code", "flat")) != "flat"
    }
    dominant_groups = {classify_sensor(sensor) for sensor in dominant_sensors}
    return {
        "groups": groups - {"other"},
        "directions": directions,
        "dominant_groups": dominant_groups - {"other"},
        "regime": regime_context.strip(),
    }


def _case_similarity(
    query: dict[str, set[str] | str],
    candidate: dict[str, set[str] | str],
) -> tuple[float, list[str], list[str]]:
    """计算案例相似度，并返回可展示的共同特征。"""

    query_groups = set(query["groups"])
    candidate_groups = set(candidate["groups"])
    query_directions = set(query["directions"])
    candidate_directions = set(candidate["directions"])
    query_dominant = set(query["dominant_groups"])
    candidate_dominant = set(candidate["dominant_groups"])

    group_score = _jaccard(query_groups, candidate_groups)
    direction_score = _jaccard(query_directions, candidate_directions)
    dominant_score = _jaccard(query_dominant, candidate_dominant)
    regime_score = 1.0 if query["regime"] and query["regime"] == candidate["regime"] else 0.0
    similarity = (
        0.40 * group_score
        + 0.35 * direction_score
        + 0.15 * dominant_score
        + 0.10 * regime_score
    )
    return (
        similarity,
        sorted(query_groups.intersection(candidate_groups)),
        sorted(query_directions.intersection(candidate_directions)),
    )


def _jaccard(left: set[str], right: set[str]) -> float:
    """空集合不构成正证据；其余使用交并比。"""

    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / len(left.union(right))


def _sha256(path: Path) -> str:
    """流式计算文件哈希，避免大文件一次读入内存。"""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    """数据库统一保存带时区的 ISO 时间。"""

    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def _to_json(value: Any) -> str:
    """统一处理时间戳、元组和 NumPy 标量等可字符串化对象。"""

    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _from_json(value: str | None, default: Any) -> Any:
    """数据库旧记录为空时返回稳定默认值。"""

    return json.loads(value) if value else default


def _optional_text(value: Any) -> str | None:
    """把空字符串规范化为空值，避免数据库出现大量无意义文本。"""

    text = str(value).strip() if value is not None else ""
    return text or None
