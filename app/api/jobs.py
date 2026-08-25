"""有界后台任务执行器。

当前竞赛部署是单机 FastAPI，因此使用线程池即可避免万悟 HTTP 节点长时间阻塞。任务状态和
结果全部写入 PostgreSQL，线程池只负责执行，不作为事实数据源。正式多机部署时可把本模块替换
为 Celery、RQ 或平台任务队列，外部 API 协议无需变化。
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import lru_cache
from threading import BoundedSemaphore, Lock
from typing import Any

from app.config import get_settings


class JobQueueFullError(RuntimeError):
    """并发槽位和等待队列均已占满。"""


class BackgroundJobManager:
    """限制并发数和等待任务数的进程内调度器。"""

    def __init__(self, max_workers: int, max_queue_size: int) -> None:
        if max_workers < 1 or max_queue_size < 0:
            raise ValueError("异步任务并发数至少为 1，队列长度不能为负数")
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="shicha-job",
        )
        self._slots = BoundedSemaphore(max_workers + max_queue_size)
        self._futures: dict[str, Future[Any]] = {}
        self._lock = Lock()

    def submit(self, run_id: str, function: Callable[..., Any], *args: Any) -> None:
        """非阻塞提交任务；容量已满时让 API 立即返回 503。"""

        if not self._slots.acquire(blocking=False):
            raise JobQueueFullError("分析任务队列已满，请稍后重试")
        try:
            future = self._executor.submit(function, *args)
        except Exception:
            self._slots.release()
            raise
        with self._lock:
            self._futures[run_id] = future
        future.add_done_callback(lambda completed: self._release(run_id, completed))

    def cancel(self, run_id: str) -> bool:
        """取消仍在排队的任务；已经开始运行的线程不能被强制终止。

        ``Future.cancel()`` 会同步触发完成回调，因此不能在持有 ``_lock`` 时调用，
        否则回调再次获取同一把锁会造成死锁。
        """

        with self._lock:
            future = self._futures.get(run_id)
        return future.cancel() if future is not None else False

    def shutdown(self) -> list[str]:
        """关闭线程池，并返回成功取消的排队任务编号。

        已经运行的任务会等待完成，保证其分析结果能够正常落库；尚未开始的任务会被取消，
        由 API 生命周期把对应 PostgreSQL 状态更新为 ``cancelled``。
        """

        with self._lock:
            pending = list(self._futures.items())
        cancelled_run_ids = [
            run_id for run_id, future in pending if future.cancel()
        ]
        self._executor.shutdown(wait=True, cancel_futures=True)
        return cancelled_run_ids

    def diagnostics(self) -> dict[str, int]:
        """返回不包含任务内容的队列运行指标，供健康诊断接口使用。"""

        with self._lock:
            tracked_jobs = len(self._futures)
        return {
            "max_workers": self._max_workers,
            "tracked_jobs": tracked_jobs,
        }

    def _release(self, run_id: str, _future: Future[Any]) -> None:
        """任务结束后释放容量，并移除仅用于进程内管理的 Future。"""

        with self._lock:
            self._futures.pop(run_id, None)
        self._slots.release()


@lru_cache(maxsize=1)
def get_job_manager() -> BackgroundJobManager:
    """按环境配置创建当前进程唯一的后台任务管理器。"""

    settings = get_settings()
    return BackgroundJobManager(
        max_workers=settings.async_job_workers,
        max_queue_size=settings.async_job_queue_size,
    )
