"""PostgreSQL 持久化入口。

上层业务只从这里导入仓储对象，不直接操作 psycopg。这样数据库结构继续演进时，分析算法、
FastAPI 路由和万悟工作流协议都不需要整体重写。
"""

from app.storage.repository import IndustrialRepository, get_repository

__all__ = ["IndustrialRepository", "get_repository"]
