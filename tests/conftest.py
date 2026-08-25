"""测试基础设施：每个数据库测试使用独立 PostgreSQL schema。"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

import app.storage.repository as repository_module
from app.config import get_settings


@pytest.fixture(autouse=True)
def isolate_postgresql_schemas(monkeypatch: pytest.MonkeyPatch):
    """兼容旧测试的临时路径参数，并保证测试数据在结束后全部清理。

    生产代码只接受 PostgreSQL URL。旧测试原先用 ``tmp_path / '*.db'`` 表达隔离范围，
    这里将该路径稳定映射成随机 schema，不会创建 SQLite 文件，也不会访问正式 public 表。
    """

    settings = get_settings()
    created_schemas: set[str] = set()
    original_init = repository_module.IndustrialRepository.__init__

    def isolated_init(self, database_url, schema: str = "public") -> None:
        if isinstance(database_url, Path):
            digest = hashlib.sha1(str(database_url).encode("utf-8")).hexdigest()[:8]
            schema = f"test_{digest}_{uuid.uuid4().hex[:8]}"
            database_url = os.getenv("TEST_DATABASE_URL", settings.database_url)
            created_schemas.add(schema)
        original_init(self, str(database_url), schema)

    monkeypatch.setattr(repository_module.IndustrialRepository, "__init__", isolated_init)
    yield

    database_url = os.getenv("TEST_DATABASE_URL", settings.database_url)
    if created_schemas:
        with psycopg.connect(database_url, autocommit=True) as connection:
            for schema in created_schemas:
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
                )
