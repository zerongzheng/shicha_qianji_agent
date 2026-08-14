"""工业分析结果与工单闭环的 PostgreSQL 仓储。

仓储统一使用 PostgreSQL，支撑多人登录、并发工单、主动通知和后续服务器部署。上层业务
仍只依赖本类提供的稳定方法，不直接拼接数据库连接或感知驱动细节。

数据库保存文件元数据、任务参数、分析结果和现场反馈。原始 CSV 仍保存在受控上传目录，
不写进数据库，避免数据库快速膨胀，也便于以后迁移到对象存储。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from app.config import get_settings
from app.diagnosis.patterns import classify_sensor
from app.models import HistoricalCaseMatch

WORK_ORDER_STATUSES = {"待确认", "已确认", "处理中", "待验证", "已完成", "已关闭"}


class PostgresConnection:
    """给 psycopg 提供仓储内部使用的轻量执行接口。

    旧仓储方法已经统一使用参数绑定，并没有把用户输入拼进 SQL。这里集中转换占位符，
    能保留稳定的业务方法，同时让底层彻底切换为 PostgreSQL。
    """

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection

    def execute(
        self,
        query: str,
        parameters: Mapping[str, Any] | tuple[Any, ...] | list[Any] | None = None,
    ) -> psycopg.Cursor:
        converted = query
        if isinstance(parameters, Mapping):
            converted = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", query)
        elif parameters is not None:
            converted = query.replace("?", "%s")
        return self._connection.execute(converted, parameters)


def _scalar(row: Mapping[str, Any] | None) -> Any:
    """读取聚合查询的第一列，避免依赖数据库驱动的数字下标行为。"""

    return next(iter(row.values())) if row else None


class IndustrialRepository:
    """封装建表、事务和常用业务查询。"""

    def __init__(self, database_url: str, schema: str = "public") -> None:
        if not str(database_url).startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL 必须是 PostgreSQL 连接地址")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
            raise ValueError("DATABASE_SCHEMA 只能包含字母、数字和下划线")
        self.database_url = str(database_url)
        self.schema = schema
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[PostgresConnection]:
        """为每次操作创建短连接，适配 FastAPI 多线程请求。"""

        raw = psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=10)
        raw.execute(sql.SQL("SET search_path TO {}") .format(sql.Identifier(self.schema)))
        connection = PostgresConnection(raw)
        try:
            yield connection
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    def _initialize(self) -> None:
        """创建当前版本所需表和索引；重复启动不会破坏已有数据。"""

        migration_dir = Path(__file__).with_name("migrations")
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=10) as raw:
            raw.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            raw.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
            applied = {
                row["version"]
                for row in raw.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for migration in sorted(migration_dir.glob("*.sql")):
                if migration.name in applied:
                    continue
                for statement in migration.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        raw.execute(statement)
                raw.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (migration.name,),
                )

    def ping(self) -> bool:
        """执行真实数据库查询，供健康检查判断 PostgreSQL 是否可用。"""

        with self._connect() as connection:
            return _scalar(connection.execute("SELECT 1").fetchone()) == 1

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

    def record_model_call(self, record: dict[str, Any]) -> None:
        """保存脱敏模型调用元数据，不接受提示词或模型正文。"""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO model_call_logs (
                    call_id, run_id, timestamp, operation, provider, model, status,
                    duration_ms, input_character_count, output_character_count,
                    prompt_tokens, completion_tokens, total_tokens, error_type,
                    content_stored
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET
                    run_id = excluded.run_id,
                    timestamp = excluded.timestamp,
                    operation = excluded.operation,
                    provider = excluded.provider,
                    model = excluded.model,
                    status = excluded.status,
                    duration_ms = excluded.duration_ms,
                    input_character_count = excluded.input_character_count,
                    output_character_count = excluded.output_character_count,
                    prompt_tokens = excluded.prompt_tokens,
                    completion_tokens = excluded.completion_tokens,
                    total_tokens = excluded.total_tokens,
                    error_type = excluded.error_type,
                    content_stored = excluded.content_stored
                """,
                (
                    record["call_id"],
                    record.get("run_id"),
                    record["timestamp"],
                    record["operation"],
                    record["provider"],
                    record["model"],
                    record["status"],
                    float(record["duration_ms"]),
                    int(record.get("input_character_count", 0)),
                    int(record.get("output_character_count", 0)),
                    record.get("prompt_tokens"),
                    record.get("completion_tokens"),
                    record.get("total_tokens"),
                    record.get("error_type"),
                    int(bool(record.get("content_stored", False))),
                ),
            )

    def list_model_calls(
        self,
        *,
        limit: int = 100,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """按时间倒序查询脱敏模型调用记录。"""

        clauses: list[str] = []
        values: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            values.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(1000, int(limit))))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM model_call_logs {where} ORDER BY timestamp DESC LIMIT ?",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_data_source(self, source: dict[str, Any]) -> dict[str, Any]:
        """新增或更新自动数据源，密钥等扩展配置统一保存在 config_json。"""

        source_id = str(source.get("source_id") or f"src_{uuid.uuid4().hex[:12]}")
        timestamp = _now()
        values = {
            "source_id": source_id,
            "name": str(source["name"]).strip(),
            "source_type": str(source["source_type"]),
            "endpoint": str(source["endpoint"]).strip(),
            "interval_seconds": float(source["interval_seconds"]),
            "enabled": int(bool(source.get("enabled", True))),
            "config_json": _to_json(source.get("config") or {}),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO data_sources (
                    source_id, name, source_type, endpoint, interval_seconds, enabled,
                    config_json, created_at, updated_at
                ) VALUES (
                    :source_id, :name, :source_type, :endpoint, :interval_seconds, :enabled,
                    :config_json, :created_at, :updated_at
                )
                ON CONFLICT(source_id) DO UPDATE SET
                    name = excluded.name,
                    source_type = excluded.source_type,
                    endpoint = excluded.endpoint,
                    interval_seconds = excluded.interval_seconds,
                    enabled = excluded.enabled,
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                values,
            )
        result = self.get_data_source(source_id)
        if result is None:
            raise RuntimeError("数据源保存后未能读取")
        return result

    def get_data_source(self, source_id: str) -> dict[str, Any] | None:
        """读取一个数据源的完整内部配置。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM data_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return _data_source_record(row) if row else None

    def list_data_sources(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        """按创建时间返回数据源，供调度器和配置页使用。"""

        query = "SELECT * FROM data_sources"
        parameters: tuple[Any, ...] = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_data_source_record(row) for row in rows]

    def list_due_data_sources(self) -> list[dict[str, Any]]:
        """返回已启用且达到轮询周期的数据源。"""

        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        due: list[dict[str, Any]] = []
        for source in self.list_data_sources(enabled_only=True):
            last_poll_at = source.get("last_poll_at")
            if not last_poll_at:
                due.append(source)
                continue
            try:
                next_poll = datetime.fromisoformat(str(last_poll_at)) + timedelta(
                    seconds=float(source["interval_seconds"])
                )
            except ValueError:
                due.append(source)
                continue
            if next_poll <= now:
                due.append(source)
        return due

    def delete_data_source(self, source_id: str) -> dict[str, Any]:
        """删除尚未产生采集历史的数据源；已有证据时只允许停用。"""

        source = self.get_data_source(source_id)
        if source is None:
            raise LookupError(f"找不到数据源：{source_id}")
        with self._connect() as connection:
            count = int(
                _scalar(connection.execute(
                    "SELECT COUNT(*) FROM data_ingestions WHERE source_id = ?",
                    (source_id,),
                ).fetchone())
            )
            if count:
                raise ValueError("已有采集历史的数据源不能删除，请改为停用以保留审计链")
            connection.execute("DELETE FROM data_sources WHERE source_id = ?", (source_id,))
        return source

    def record_source_poll(
        self,
        source_id: str,
        *,
        success: bool,
        error: str | None,
    ) -> None:
        """记录轮询结果；采集失败不会禁用数据源。"""

        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE data_sources
                SET last_poll_at = ?,
                    last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
                    last_error = ?, updated_at = ?
                WHERE source_id = ?
                """,
                (timestamp, bool(success), timestamp, error, timestamp, source_id),
            )

    def reserve_ingestion(
        self,
        *,
        source_id: str,
        fingerprint: str,
        item_key: str,
        file_name: str,
    ) -> dict[str, Any] | None:
        """原子预留新数据批次；同一源相同内容只允许分析一次。"""

        ingestion_id = f"ing_{uuid.uuid4().hex[:12]}"
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO data_ingestions (
                        ingestion_id, source_id, fingerprint, item_key, file_name,
                        status, detected_at
                    ) VALUES (?, ?, ?, ?, ?, 'detected', ?)
                    """,
                    (
                        ingestion_id,
                        source_id,
                        fingerprint,
                        item_key,
                        file_name,
                        _now(),
                    ),
                )
        except psycopg.IntegrityError:
            return None
        return self.get_ingestion(ingestion_id)

    def mark_ingestion_submitted(
        self,
        ingestion_id: str,
        *,
        run_id: str,
        storage_path: Path,
    ) -> None:
        """关联不可变快照和异步分析任务。"""

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE data_ingestions
                SET status = 'submitted', storage_path = ?, run_id = ?, submitted_at = ?, error = NULL
                WHERE ingestion_id = ?
                """,
                (str(storage_path.resolve()), run_id, _now(), ingestion_id),
            )

    def mark_ingestion_failed(self, ingestion_id: str, error: str) -> None:
        """保存采集或任务提交失败信息。"""

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE data_ingestions
                SET status = 'failed', error = ?, finished_at = ?
                WHERE ingestion_id = ?
                """,
                (error, _now(), ingestion_id),
            )

    def get_ingestion(self, ingestion_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM data_ingestions WHERE ingestion_id = ?",
                (ingestion_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_ingestions(
        self,
        *,
        limit: int = 100,
        source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        # 永久删除自动分析任务后仍保留采集指纹，防止同一文件被轮询器再次提交；
        # 这类最小去重记录不再出现在监测时间线中。
        query = "SELECT * FROM data_ingestions WHERE status != 'removed'"
        parameters: list[Any] = []
        if source_id:
            query += " AND source_id = ?"
            parameters.append(source_id)
        query += " ORDER BY detected_at DESC LIMIT ?"
        parameters.append(max(1, min(500, int(limit))))
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def get_data_source_for_run(self, run_id: str) -> dict[str, Any] | None:
        """查找自动任务所属数据源；手工任务返回空。"""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.* FROM analysis_runs AS r
                JOIN data_sources AS s ON s.source_id = r.source_id
                WHERE r.run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return _data_source_record(row) if row else None

    def create_notification(
        self,
        *,
        run_id: str,
        record_id: str,
        priority: str,
        recipient_name: str,
        recipient_role: str,
        channel: str,
        title: str,
        message: str,
        recipient_user_id: str | None = None,
        notification_kind: str = "initial",
        escalation_level: int = 0,
    ) -> dict[str, Any]:
        """创建或读取幂等通知记录。

        同一工单可以分别保留首次告警、SLA 催办、升级告警和复检结果；同一阶段重复调用
        仍只会得到同一条记录，从而适配万悟定时工作流的重试语义。
        """

        notification_id = f"ntf_{uuid.uuid4().hex[:12]}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO notifications (
                    notification_id, run_id, record_id, priority, recipient_name,
                    recipient_role, recipient_user_id, channel, title, message, status, created_at
                    , notification_kind, escalation_level
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                ON CONFLICT(
                    record_id, recipient_name, channel, notification_kind, escalation_level
                ) DO NOTHING
                """,
                (
                    notification_id,
                    run_id,
                    record_id,
                    priority,
                    recipient_name,
                    recipient_role,
                    recipient_user_id,
                    channel,
                    title,
                    message,
                    _now(),
                    notification_kind,
                    max(0, int(escalation_level)),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM notifications
                WHERE record_id = ? AND recipient_name = ? AND channel = ?
                  AND notification_kind = ? AND escalation_level = ?
                """,
                (
                    record_id,
                    recipient_name,
                    channel,
                    notification_kind,
                    max(0, int(escalation_level)),
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("通知创建后未能读取")
        return dict(row)

    def get_notification(self, notification_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM notifications WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
        return dict(row) if row else None

    def mark_notification_sent(self, notification_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE notifications
                SET status = 'sent', attempts = attempts + 1, error = NULL, sent_at = ?
                WHERE notification_id = ?
                """,
                (_now(), notification_id),
            )

    def mark_notification_failed(self, notification_id: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE notifications
                SET status = 'failed', attempts = attempts + 1, error = ?
                WHERE notification_id = ?
                """,
                (error, notification_id),
            )

    def list_notifications(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
        recipient_user_id: str | None = None,
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM notifications"
        parameters: list[Any] = []
        conditions: list[str] = []
        if status:
            conditions.append("status = ?")
            parameters.append(status)
        if recipient_user_id:
            conditions.append("recipient_user_id = ?")
            parameters.append(recipient_user_id)
        if unread_only:
            conditions.append("read_at IS NULL")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(max(1, min(500, int(limit))))
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def upsert_user(
        self,
        *,
        username: str,
        display_name: str,
        role: str,
        password_hash: str,
        active: bool = True,
    ) -> dict[str, Any]:
        """新增演示账号；已有账号只同步姓名、角色和启用状态，不覆盖其密码。"""

        timestamp = _now()
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    user_id, username, display_name, role, password_hash,
                    active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    display_name = excluded.display_name,
                    role = excluded.role,
                    active = excluded.active,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    username.strip().lower(),
                    display_name.strip(),
                    role.strip(),
                    password_hash,
                    int(active),
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?",
                (username.strip().lower(),),
            ).fetchone()
        if row is None:
            raise RuntimeError("账号保存后未能读取")
        return _public_user(row)

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """读取登录校验所需的账号记录，返回值包含密码摘要且仅限后端使用。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?",
                (username.strip().lower(),),
            ).fetchone()
        return dict(row) if row else None

    def list_active_users_for_role(self, role: str) -> list[dict[str, Any]]:
        """按通知角色查找真实人员，并兼容算法工单中的相近角色称呼。"""

        aliases = {
            "设备工程师": ("设备工程师", "设备运维"),
            "设备运维": ("设备运维", "设备工程师"),
            "运行值班员": ("运行值班员", "运行监控"),
            "运行监控": ("运行监控", "运行值班员"),
            "生产值班负责人": ("生产值班负责人", "生产负责人"),
            "生产负责人": ("生产负责人", "生产值班负责人"),
        }
        candidates = aliases.get(role, (role,))
        placeholders = ",".join("?" for _ in candidates)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM users WHERE active = 1 AND role IN ({placeholders}) "
                "ORDER BY created_at ASC",
                list(candidates),
            ).fetchall()
        return [_public_user(row) for row in rows]

    def list_users(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        """返回可指派人员列表，不暴露密码摘要。"""

        query = "SELECT * FROM users"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY role, display_name"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [_public_user(row) for row in rows]

    def create_session(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: str,
    ) -> dict[str, Any]:
        """保存登录会话摘要，并更新账号最后登录时间。"""

        timestamp = _now()
        session_id = f"ses_{uuid.uuid4().hex[:16]}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_sessions (
                    session_id, user_id, token_hash, created_at, expires_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, user_id, token_hash, timestamp, expires_at, timestamp),
            )
            connection.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE user_id = ?",
                (timestamp, timestamp, user_id),
            )
        return {"session_id": session_id, "expires_at": expires_at}

    def get_user_by_session(self, token_hash: str) -> dict[str, Any] | None:
        """校验未撤销且未过期的会话，并返回可公开的当前用户信息。"""

        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT u.*, s.session_id, s.expires_at
                FROM user_sessions AS s
                JOIN users AS u ON u.user_id = s.user_id
                WHERE s.token_hash = ? AND s.revoked_at IS NULL
                  AND s.expires_at > ? AND u.active = 1
                """,
                (token_hash, now),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE user_sessions SET last_seen_at = ? WHERE session_id = ?",
                    (now, row["session_id"]),
                )
        return _public_user(row) if row else None

    def revoke_session(self, token_hash: str) -> bool:
        """撤销当前令牌；重复退出保持幂等。"""

        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE user_sessions SET revoked_at = ? "
                "WHERE token_hash = ? AND revoked_at IS NULL",
                (_now(), token_hash),
            )
        return cursor.rowcount == 1

    def record_audit(
        self,
        *,
        user_id: str | None,
        action: str,
        target_type: str,
        target_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """记录登录、接单、签收等关键人工动作，形成可追溯责任链。"""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_logs (
                    audit_id, user_id, action, target_type, target_id, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"aud_{uuid.uuid4().hex[:16]}",
                    user_id,
                    action,
                    target_type,
                    target_id,
                    _to_json(detail or {}),
                    _now(),
                ),
            )

    def acknowledge_notification(
        self,
        notification_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """由通知接收人签收告警；管理员可以通过上层权限另行处理。"""

        timestamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM notifications WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"找不到通知：{notification_id}")
            if row["recipient_user_id"] and row["recipient_user_id"] != user_id:
                raise PermissionError("只能签收发送给自己的通知")
            connection.execute(
                """
                UPDATE notifications
                SET read_at = COALESCE(read_at, ?),
                    acknowledged_at = COALESCE(acknowledged_at, ?),
                    acknowledged_by = COALESCE(acknowledged_by, ?)
                WHERE notification_id = ?
                """,
                (timestamp, timestamp, user_id, notification_id),
            )
            updated = connection.execute(
                "SELECT * FROM notifications WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
        return dict(updated)

    def start_run(
        self,
        run_id: str,
        file_id: str,
        operation: str,
        detector: str,
        config: dict[str, Any],
        status: str = "running",
        source_id: str | None = None,
        ingestion_id: str | None = None,
    ) -> None:
        """在耗时计算开始前登记任务，异常退出后仍能留下记录。"""

        if status not in {"queued", "running"}:
            raise ValueError("新任务状态只能是 queued 或 running")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_runs (
                    run_id, file_id, operation, detector, status, config_json, started_at,
                    source_id, ingestion_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    file_id,
                    operation,
                    detector,
                    status,
                    _to_json(config),
                    _now(),
                    source_id,
                    ingestion_id,
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
                analysis = _result_analysis_section(result)
                self._upsert_work_orders(
                    connection,
                    run_id,
                    analysis.get("work_order_drafts", []),
                )
            # 自动采集批次跟随分析任务进入终态，便于监控页定位失败环节。
            connection.execute(
                """
                UPDATE data_ingestions
                SET status = ?, error = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (
                    "completed" if status == "success" else status,
                    error,
                    _now(),
                    run_id,
                ),
            )

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
        assigned_user_id: str | None = None,
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
            assigned_user_id=assigned_user_id,
            include_archived=include_archived,
            archived_only=archived_only,
        )
        # 同时取出分析任务文件名，让前端能区分同标题但来自不同数据文件的工单。
        query = (
            "SELECT w.*, f.file_name AS source_file_name, "
            "r.started_at AS source_run_started_at, "
            "u.display_name AS assigned_user_name "
            "FROM work_orders AS w "
            "JOIN analysis_runs AS r ON r.run_id = w.run_id "
            "JOIN uploaded_files AS f ON f.file_id = r.file_id "
            "LEFT JOIN users AS u ON u.user_id = w.assigned_user_id"
        )
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
        assigned_user_id: str | None = None,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> int:
        """统计当前筛选条件下的工单数量，供前端分页显示总页数。"""

        conditions, parameters = self._work_order_filters(
            status=status,
            run_id=run_id,
            search=search,
            priority=priority,
            assigned_user_id=assigned_user_id,
            include_archived=include_archived,
            archived_only=archived_only,
        )
        query = "SELECT COUNT(*) FROM work_orders AS w JOIN analysis_runs AS r ON r.run_id = w.run_id"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        with self._connect() as connection:
            return int(_scalar(connection.execute(query, parameters).fetchone()))

    @staticmethod
    def _work_order_filters(
        *,
        status: str | None,
        run_id: str | None,
        search: str | None,
        priority: str | None,
        assigned_user_id: str | None,
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
        if assigned_user_id:
            conditions.append("w.assigned_user_id = ?")
            parameters.append(assigned_user_id)
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
            active_orders = _scalar(connection.execute(
                "SELECT COUNT(*) FROM work_orders "
                "WHERE run_id = ? AND status NOT IN ('已完成', '已关闭')",
                (run_id,),
            ).fetchone())
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

    def delete_archived_work_order(self, record_id: str) -> dict[str, Any]:
        """永久删除归档工单及其通知；来源分析任务和原始分析证据继续保留。"""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT w.record_id, w.run_id, w.archived_at, r.archived_at AS run_archived_at
                FROM work_orders AS w
                JOIN analysis_runs AS r ON r.run_id = w.run_id
                WHERE w.record_id = ?
                """,
                (record_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"找不到工单：{record_id}")
            if row["archived_at"] is None and row["run_archived_at"] is None:
                raise ValueError("只有已归档工单才能永久删除")

            # 通知表通过 record_id 关联工单，当前数据库版本没有工单外键，因此显式清理。
            notification_count = _scalar(connection.execute(
                "SELECT COUNT(*) FROM notifications WHERE record_id = ?",
                (record_id,),
            ).fetchone())
            connection.execute("DELETE FROM notifications WHERE record_id = ?", (record_id,))
            connection.execute("DELETE FROM work_orders WHERE record_id = ?", (record_id,))
        return {
            "record_id": record_id,
            "run_id": row["run_id"],
            "deleted_notification_count": int(notification_count),
        }

    def delete_archived_run(self, run_id: str) -> dict[str, Any]:
        """永久删除归档分析任务及关联工单、通知和模型调用审计。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id, file_id, archived_at, ingestion_id FROM analysis_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"找不到分析任务：{run_id}")
            if row["archived_at"] is None:
                raise ValueError("只有已归档分析任务才能永久删除")

            work_order_count = _scalar(connection.execute(
                "SELECT COUNT(*) FROM work_orders WHERE run_id = ?",
                (run_id,),
            ).fetchone())
            notification_count = _scalar(connection.execute(
                "SELECT COUNT(*) FROM notifications WHERE run_id = ?",
                (run_id,),
            ).fetchone())
            model_call_count = _scalar(connection.execute(
                "SELECT COUNT(*) FROM model_call_logs WHERE run_id = ?",
                (run_id,),
            ).fetchone())

            # 自动采集只留下内容指纹，不再关联已删除任务，也不再出现在监测时间线中。
            connection.execute(
                """
                UPDATE data_ingestions
                SET status = 'removed', run_id = NULL, error = NULL, finished_at = ?
                WHERE run_id = ?
                """,
                (_now(), run_id),
            )
            connection.execute("DELETE FROM model_call_logs WHERE run_id = ?", (run_id,))
            # work_orders 和 notifications 均通过 ON DELETE CASCADE 跟随任务删除。
            connection.execute("DELETE FROM analysis_runs WHERE run_id = ?", (run_id,))

            # 多次分析可能复用同一上传文件，只清理已经没有任务引用的文件元数据。
            remaining_runs = _scalar(connection.execute(
                "SELECT COUNT(*) FROM analysis_runs WHERE file_id = ?",
                (row["file_id"],),
            ).fetchone())
            if remaining_runs == 0:
                connection.execute("DELETE FROM uploaded_files WHERE file_id = ?", (row["file_id"],))

        return {
            "run_id": run_id,
            "deleted_work_order_count": int(work_order_count),
            "deleted_notification_count": int(notification_count),
            "deleted_model_call_count": int(model_call_count),
        }

    def _get_work_order(self, record_id: str) -> dict[str, Any] | None:
        """读取单条工单，供归档/恢复后返回最新状态。"""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT w.*, u.display_name AS assigned_user_name
                FROM work_orders AS w
                LEFT JOIN users AS u ON u.user_id = w.assigned_user_id
                WHERE w.record_id = ?
                """,
                (record_id,),
            ).fetchone()
        return _work_order_record(row) if row else None

    def assign_work_order(self, record_id: str, user_id: str) -> dict[str, Any]:
        """把未归档工单指派给真实人员，保留算法建议角色作为路由依据。"""

        timestamp = _now()
        with self._connect() as connection:
            user = connection.execute(
                "SELECT user_id, active FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if user is None or not bool(user["active"]):
                raise ValueError("指派对象不存在或已停用")
            cursor = connection.execute(
                """
                UPDATE work_orders
                SET assigned_user_id = ?, accepted_at = NULL, accepted_by = NULL,
                    updated_at = ?
                WHERE record_id = ? AND archived_at IS NULL
                """,
                (user_id, timestamp, record_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"找不到可指派工单：{record_id}")
        assigned = self._get_work_order(record_id)
        if assigned is None:
            raise LookupError(f"找不到工单：{record_id}")
        return assigned

    def accept_work_order(self, record_id: str, user_id: str) -> dict[str, Any]:
        """接收人确认接单；未明确指派时首次接单者自动成为负责人。"""

        timestamp = _now()
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM work_orders WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            if current is None:
                raise LookupError(f"找不到工单：{record_id}")
            if current["archived_at"] is not None:
                raise ValueError("已归档工单不能接单")
            if current["assigned_user_id"] and current["assigned_user_id"] != user_id:
                raise PermissionError("该工单已指派给其他人员")
            connection.execute(
                """
                UPDATE work_orders
                SET assigned_user_id = COALESCE(assigned_user_id, ?),
                    accepted_at = COALESCE(accepted_at, ?),
                    accepted_by = COALESCE(accepted_by, ?),
                    updated_at = ?
                WHERE record_id = ?
                """,
                (user_id, timestamp, user_id, timestamp, record_id),
            )
        accepted = self._get_work_order(record_id)
        if accepted is None:
            raise LookupError(f"找不到工单：{record_id}")
        return accepted

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
            if status in {"已确认", "待验证", "已完成", "已关闭"} and not confirmed_cause:
                raise ValueError("已确认、待验证、已完成或已关闭的工单必须填写确认根因")
            feedback_note = (
                _optional_text(updates["feedback_note"])
                if "feedback_note" in updates
                else current["feedback_note"]
            )
            if status == "待验证" and not feedback_note:
                raise ValueError("进入待验证前必须填写现场处置反馈")
            timestamp = _now()
            entering_reinspection = status == "待验证" and current["status"] != "待验证"
            leaving_reinspection = status != "待验证" and current["status"] == "待验证"
            # PATCH 只修改请求中明确给出的字段，避免状态流转时清空既有现场反馈。
            values = {
                "record_id": record_id,
                "status": status,
                "confirmed_cause": (
                    confirmed_cause
                    if "confirmed_cause" in updates
                    else current["confirmed_cause"]
                ),
                "feedback_note": feedback_note,
                "handled_by": (
                    _optional_text(updates["handled_by"])
                    if "handled_by" in updates
                    else current["handled_by"]
                ),
                "updated_at": timestamp,
                "entering_reinspection": entering_reinspection,
                "leaving_reinspection": leaving_reinspection,
            }
            connection.execute(
                """
                UPDATE work_orders
                SET status = :status,
                    confirmed_cause = :confirmed_cause,
                    feedback_note = :feedback_note,
                    handled_by = :handled_by,
                    reinspection_status = CASE
                        WHEN :entering_reinspection THEN 'pending'
                        WHEN :leaving_reinspection AND reinspection_status = 'pending' THEN 'cancelled'
                        ELSE reinspection_status
                    END,
                    reinspection_scheduled_at = CASE
                        WHEN :entering_reinspection THEN :updated_at
                        ELSE reinspection_scheduled_at
                    END,
                    reinspection_run_id = CASE
                        WHEN :entering_reinspection THEN NULL
                        ELSE reinspection_run_id
                    END,
                    reinspection_summary = CASE
                        WHEN :entering_reinspection THEN NULL
                        ELSE reinspection_summary
                    END,
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

    def mark_work_order_sla(self, record_id: str, level: int) -> dict[str, Any] | None:
        """原子提升未接单工单的 SLA 层级；重复或过期动作不会回退状态。"""

        level = max(1, min(2, int(level)))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE work_orders
                SET sla_level = ?, last_sla_action_at = ?, updated_at = ?
                WHERE record_id = ?
                  AND archived_at IS NULL
                  AND status = '待确认'
                  AND accepted_at IS NULL
                  AND sla_level < ?
                """,
                (level, _now(), _now(), record_id, level),
            )
        return self._get_work_order(record_id) if cursor.rowcount == 1 else None

    def find_latest_successful_source_run(
        self,
        *,
        source_id: str,
        after: str,
        exclude_run_id: str,
    ) -> dict[str, Any] | None:
        """读取同一数据源在复检开始后的最新成功任务及完整结构化结果。"""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, f.file_name, f.sha256, f.size_bytes
                FROM analysis_runs AS r
                JOIN uploaded_files AS f ON f.file_id = r.file_id
                WHERE r.source_id = ?
                  AND r.run_id <> ?
                  AND r.status = 'success'
                  AND r.started_at > ?
                  AND r.result_json IS NOT NULL
                ORDER BY r.started_at DESC
                LIMIT 1
                """,
                (source_id, exclude_run_id, after),
            ).fetchone()
        return _run_record(row, include_result=True) if row else None

    def finalize_reinspection(
        self,
        record_id: str,
        *,
        passed: bool,
        reinspection_run_id: str,
        summary: str,
    ) -> dict[str, Any] | None:
        """原子提交复检结论；仅待验证且 pending 的工单可被自动流转一次。"""

        target_status = "已完成" if passed else "处理中"
        reinspection_status = "passed" if passed else "failed"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE work_orders
                SET status = ?, reinspection_status = ?, reinspection_run_id = ?,
                    reinspection_summary = ?, updated_at = ?
                WHERE record_id = ?
                  AND status = '待验证'
                  AND reinspection_status = 'pending'
                  AND archived_at IS NULL
                """,
                (
                    target_status,
                    reinspection_status,
                    reinspection_run_id,
                    summary,
                    _now(),
                    record_id,
                ),
            )
        return self._get_work_order(record_id) if cursor.rowcount == 1 else None

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
        connection: PostgresConnection,
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

    settings = get_settings()
    return IndustrialRepository(settings.database_url, settings.database_schema)


def _run_record(row: Mapping[str, Any], include_result: bool) -> dict[str, Any]:
    """将 PostgreSQL 行转换为万悟可直接使用的字典。"""

    result = _from_json(row["result_json"], {})
    analysis = _result_analysis_section(result)
    selection = analysis.get("model_selection") or {}
    # detector 列仍保存请求配置，用于快速诊断幂等缓存；任务成功后对外展示实际路由模型，
    # 避免“请求默认 TFR、实际因单传感器选择 MAD”时历史记录显示错误。
    effective_detector = selection.get("selected_detector") or row["detector"]
    record = {
        "run_id": row["run_id"],
        "file_id": row["file_id"],
        "file_name": row["file_name"],
        "file_sha256": row["sha256"],
        "file_size_bytes": row["size_bytes"],
        "operation": row["operation"],
        "detector": effective_detector,
        "status": row["status"],
        "config": _from_json(row["config_json"], {}),
        "error": row["error"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_ms": row["duration_ms"],
        "archived_at": row["archived_at"],
        "archive_reason": row["archive_reason"],
        "source_id": row["source_id"],
        "ingestion_id": row["ingestion_id"],
    }
    if include_result:
        record["result"] = result or None
    else:
        record["summary"] = analysis.get("summary")
    return record


def _data_source_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """转换数据源记录，并把内部 JSON 拆成分析、请求头和通知路由配置。"""

    config = _from_json(row["config_json"], {})
    return {
        "source_id": row["source_id"],
        "name": row["name"],
        "source_type": row["source_type"],
        "endpoint": row["endpoint"],
        "interval_seconds": row["interval_seconds"],
        "enabled": bool(row["enabled"]),
        "analysis_config": config.get("analysis_config") or {},
        "request_headers": config.get("request_headers") or {},
        "routing": config.get("routing") or {},
        "initial_scan_mode": config.get("initial_scan_mode") or "latest",
        "timeout_seconds": config.get("timeout_seconds", 15),
        "last_poll_at": row["last_poll_at"],
        "last_success_at": row["last_success_at"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


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
        "model_selection": result.model_selection if hasattr(result, "model_selection") else {},
        "detector_validation": (
            result.detector_validation if hasattr(result, "detector_validation") else {}
        ),
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
        # 兼容升级前的分析结果替身和旧历史对象；新流水线会始终写入完整台账。
        "agent_decisions": [
            asdict(item) for item in getattr(result, "agent_decisions", [])
        ],
        "summary": result.to_summary(),
    }


def _work_order_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """反序列化工单中的列表字段。"""

    # 列表查询会联表返回来源信息；更新单条工单的旧查询只返回 work_orders 本身，
    # 因此这里用兼容读取，避免为了展示字段破坏已有的更新、归档和恢复逻辑。
    row_keys = set(row.keys())

    return {
        "record_id": row["record_id"],
        "run_id": row["run_id"],
        "source_file_name": row["source_file_name"] if "source_file_name" in row_keys else None,
        "source_run_started_at": (
            row["source_run_started_at"] if "source_run_started_at" in row_keys else None
        ),
        "work_order_id": row["source_work_order_id"],
        "event_number": row["event_number"],
        "priority": row["priority"],
        "title": row["title"],
        "status": row["status"],
        "assigned_role": row["assigned_role"],
        "assigned_user_id": row["assigned_user_id"] if "assigned_user_id" in row_keys else None,
        "assigned_user_name": (
            row["assigned_user_name"] if "assigned_user_name" in row_keys else None
        ),
        "accepted_at": row["accepted_at"] if "accepted_at" in row_keys else None,
        "accepted_by": row["accepted_by"] if "accepted_by" in row_keys else None,
        "sla_level": int(row["sla_level"]) if "sla_level" in row_keys else 0,
        "last_sla_action_at": (
            row["last_sla_action_at"] if "last_sla_action_at" in row_keys else None
        ),
        "reinspection_status": (
            row["reinspection_status"] if "reinspection_status" in row_keys else None
        ),
        "reinspection_scheduled_at": (
            row["reinspection_scheduled_at"]
            if "reinspection_scheduled_at" in row_keys
            else None
        ),
        "reinspection_run_id": (
            row["reinspection_run_id"] if "reinspection_run_id" in row_keys else None
        ),
        "reinspection_summary": (
            row["reinspection_summary"] if "reinspection_summary" in row_keys else None
        ),
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


def _confirmed_case_record(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """从历史任务结果中提取指定事件的稳定案例特征。"""

    result = _from_json(row["result_json"], {})
    analysis = _result_analysis_section(result)
    diagnoses = analysis.get("root_cause_diagnoses", [])
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
            for index, item in enumerate(analysis.get("anomaly_events", []), start=1)
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


def _result_analysis_section(result: Any) -> dict[str, Any]:
    """统一读取普通分析响应和万悟快速诊断响应中的分析主体。

    普通接口把 ``work_order_drafts``、``summary`` 等字段放在响应顶层；
    ``quick_industrial_diagnosis`` 为了同时返回文件元数据和展示文本，将相同字段放在
    ``analysis`` 下。仓储层必须兼容两种稳定协议，否则快速工作流虽然分析成功，却不会
    生成可回写工单，也无法从现场反馈沉淀历史案例。
    """

    if not isinstance(result, dict):
        return {}
    nested = result.get("analysis")
    return nested if isinstance(nested, dict) else result


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


def _public_user(row: Mapping[str, Any]) -> dict[str, Any]:
    """移除密码摘要等内部字段，只返回前端和通知路由需要的身份信息。"""

    keys = set(row.keys())
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "active": bool(row["active"]),
        "last_login_at": row["last_login_at"],
        "session_id": row["session_id"] if "session_id" in keys else None,
        "expires_at": row["expires_at"] if "expires_at" in keys else None,
    }


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
