"""比赛接口的跨进程请求节流。

比赛方规定每个接口每分钟最多调用 5 次。FastAPI、万悟辅助调用和命令行可能来自不同进程，
仅使用内存计数无法共享额度，因此这里用一个很小的状态文件记录上次请求时间。聊天和
Embedding 使用不同状态文件，对应“每个接口”分别限流。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from langchain_core.rate_limiters import BaseRateLimiter

from app.config import Settings, get_settings


class FileIntervalRateLimiter(BaseRateLimiter):
    """用独占文件锁保证多个本地进程按固定最小间隔发起请求。"""

    def __init__(self, state_path: Path, requests_per_minute: int) -> None:
        if requests_per_minute <= 0:
            raise ValueError("每分钟请求数必须大于 0。")
        self.state_path = state_path
        # 服务端在整分钟窗口内统计时，精确 12 秒仍可能落在边界内，额外留 0.2 秒余量。
        self.minimum_interval = 60.0 / requests_per_minute + 0.2

    def acquire(self, *, blocking: bool = True) -> bool:
        """等待可用请求时隙，并以原子创建文件的方式竞争跨进程锁。"""

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_path.with_suffix(".lock")
        while True:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(descriptor)
            except FileExistsError:
                if not blocking:
                    return False
                # 异常退出可能留下旧锁；超过一分钟可安全清理，避免后续请求永久等待。
                try:
                    if time.time() - lock_path.stat().st_mtime > 60:
                        lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                time.sleep(0.1)
                continue

            try:
                wait_seconds = self._seconds_until_available()
                if wait_seconds > 0 and not blocking:
                    return False
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                self.state_path.write_text(f"{time.time():.6f}", encoding="ascii")
                return True
            finally:
                lock_path.unlink(missing_ok=True)

    async def aacquire(self, *, blocking: bool = True) -> bool:
        """异步场景在线程中执行等待，避免阻塞事件循环。"""

        return await asyncio.to_thread(self.acquire, blocking=blocking)

    def _seconds_until_available(self) -> float:
        """根据上次真实请求时间计算剩余等待秒数。"""

        try:
            last_request = float(self.state_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return 0.0
        return max(0.0, self.minimum_interval - (time.time() - last_request))


def create_chat_rate_limiter(settings: Settings | None = None) -> FileIntervalRateLimiter:
    """创建聊天接口限流器。"""

    settings = settings or get_settings()
    return FileIntervalRateLimiter(
        settings.output_dir / "rate_limits" / "chat.timestamp",
        settings.llm_requests_per_minute,
    )


def acquire_embedding_slot(settings: Settings | None = None) -> None:
    """在调用 Embedding 接口前占用一个请求时隙。"""

    settings = settings or get_settings()
    FileIntervalRateLimiter(
        settings.output_dir / "rate_limits" / "embedding.timestamp",
        settings.embedding_requests_per_minute,
    ).acquire()
