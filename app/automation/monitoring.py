"""工业数据源轮询与增量任务触发。

当前校赛版本实现两种可实际演示的数据源：受控目录和返回 CSV 的 HTTP 接口。采集层只负责
发现新批次、保存不可变快照和提交分析任务，不包含异常检测逻辑。企业后续提供 Kafka、MQTT、
数据库 CDC 或时序数据库时，只需增加新的采集适配器，后面的分析、工单和通知链路无需改写。
"""

from __future__ import annotations

import hashlib
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.storage import IndustrialRepository


@dataclass(frozen=True)
class DataCandidate:
    """数据源一次轮询发现的一个完整、可分析数据批次。"""

    item_key: str
    file_name: str
    content: bytes

    @property
    def fingerprint(self) -> str:
        """内容指纹用于跨轮询去重，文件改名不会造成重复分析。"""

        return hashlib.sha256(self.content).hexdigest()


SubmitCallback = Callable[[dict[str, Any], str, Path], str]


class MonitoringService:
    """单进程轮询调度器；所有事实状态写入 PostgreSQL。"""

    def __init__(
        self,
        repository: IndustrialRepository,
        storage_dir: str | Path,
        submit_callback: SubmitCallback,
        *,
        max_bytes: int = 25 * 1024 * 1024,
        tick_seconds: float = 1.0,
    ) -> None:
        self.repository = repository
        self.storage_dir = Path(storage_dir).expanduser().resolve()
        self.submit_callback = submit_callback
        self.max_bytes = max(1024, int(max_bytes))
        self.tick_seconds = max(0.2, float(tick_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._poll_lock = threading.Lock()

    def start(self) -> None:
        """启动守护线程；重复调用不会创建第二个调度器。"""

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="shicha-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """停止轮询线程，不强制终止已经进入分析队列的任务。"""

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))
        self._thread = None

    def status(self) -> dict[str, Any]:
        """返回不含数据源密钥和原始内容的运行状态。"""

        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "poll_in_progress": self._poll_lock.locked(),
            "tick_seconds": self.tick_seconds,
            "storage_dir": str(self.storage_dir),
        }

    def poll_due_sources(self) -> int:
        """轮询所有到期数据源，返回实际尝试的数据源数量。"""

        return len(self.poll_due_sources_detailed())

    def poll_due_sources_detailed(
        self,
        *,
        max_sources: int | None = None,
        max_submissions_per_source: int | None = None,
    ) -> list[dict[str, Any]]:
        """轮询到期数据源并返回逐源结果，供万悟工作流展示本轮执行证据。

        后台线程只关心轮询数量，万悟工作流则需要知道发现了哪些批次、提交了哪些
        ``run_id``。两种入口复用同一把非阻塞锁，避免定时脚本和本地线程同时采集。
        """

        if not self._poll_lock.acquire(blocking=False):
            return []
        try:
            sources = self.repository.list_due_data_sources()
            if max_sources is not None:
                sources = sources[: max(1, int(max_sources))]
            return [
                self.poll_source(
                    source,
                    max_submissions=max_submissions_per_source,
                )
                for source in sources
            ]
        finally:
            self._poll_lock.release()

    def poll_once(
        self,
        source_id: str,
        *,
        max_submissions: int | None = None,
    ) -> dict[str, Any]:
        """立即轮询指定数据源，供配置校验和比赛演示使用。"""

        source = self.repository.get_data_source(source_id)
        if source is None:
            raise LookupError(f"找不到数据源：{source_id}")
        return self.poll_source(source, max_submissions=max_submissions)

    def poll_source(
        self,
        source: dict[str, Any],
        *,
        max_submissions: int | None = None,
    ) -> dict[str, Any]:
        """采集一个数据源，并把新批次提交给异步分析队列。"""

        source_id = str(source["source_id"])
        detected = submitted = duplicates = failed = 0
        run_ids: list[str] = []
        try:
            candidates = self._collect(source)
            detected = len(candidates)
            for candidate in candidates:
                # 万悟一次工作流只追踪一个主任务。达到本轮提交上限后停止，但下一轮仍会
                # 依靠内容指纹跳过旧批次并继续找到尚未处理的数据，不会造成数据丢失。
                if max_submissions is not None and submitted >= max(1, max_submissions):
                    break
                ingestion = self.repository.reserve_ingestion(
                    source_id=source_id,
                    fingerprint=candidate.fingerprint,
                    item_key=candidate.item_key,
                    file_name=candidate.file_name,
                )
                if ingestion is None:
                    duplicates += 1
                    continue
                ingestion_id = str(ingestion["ingestion_id"])
                try:
                    snapshot = self._write_snapshot(source_id, ingestion_id, candidate)
                    run_id = self.submit_callback(source, ingestion_id, snapshot)
                    run_ids.append(run_id)
                    submitted += 1
                except Exception as exc:  # noqa: BLE001 - 单批次失败不能阻塞同源后续数据。
                    failed += 1
                    self.repository.mark_ingestion_failed(ingestion_id, str(exc))
            self.repository.record_source_poll(source_id, success=True, error=None)
        except Exception as exc:  # noqa: BLE001 - 轮询线程必须保存错误后继续服务其他数据源。
            failed += 1
            self.repository.record_source_poll(source_id, success=False, error=str(exc))
        return {
            "source_id": source_id,
            "detected": detected,
            "submitted": submitted,
            "duplicates": duplicates,
            "failed": failed,
            "run_ids": run_ids,
        }

    def _run(self) -> None:
        """按短间隔检查到期源；单次错误已在 poll_source 内部隔离。"""

        while not self._stop_event.is_set():
            self.poll_due_sources()
            self._stop_event.wait(self.tick_seconds)

    def _collect(self, source: dict[str, Any]) -> list[DataCandidate]:
        source_type = source["source_type"]
        if source_type == "directory":
            return self._collect_directory(source)
        if source_type == "http_csv":
            return [self._collect_http(source)]
        raise ValueError(f"不支持的数据源类型：{source_type}")

    def _collect_directory(self, source: dict[str, Any]) -> list[DataCandidate]:
        directory = Path(str(source["endpoint"])).expanduser().resolve()
        if not directory.is_dir():
            raise NotADirectoryError(f"监控目录不存在：{directory}")
        paths = sorted(directory.glob("*.csv"), key=_arrival_timestamp)
        # 默认只用目录中最新一批完成首次接入验收。之后只接收数据源创建后产生或更新的文件，
        # 避免把设备目录多年历史数据一次性塞满后台任务队列。
        created_at = datetime.fromisoformat(str(source["created_at"]))
        created_timestamp = created_at.timestamp()
        scan_mode = str(source.get("initial_scan_mode") or "latest")
        if scan_mode != "all":
            new_paths = [path for path in paths if _arrival_timestamp(path) > created_timestamp]
            if not source.get("last_poll_at") and scan_mode == "latest" and paths:
                paths = sorted({*new_paths, paths[-1]}, key=_arrival_timestamp)
            else:
                paths = new_paths
        candidates: list[DataCandidate] = []
        for path in paths:
            content = path.read_bytes()
            self._validate_content(path.name, content)
            candidates.append(
                DataCandidate(
                    item_key=f"{path.name}:{path.stat().st_mtime_ns}",
                    file_name=path.name,
                    content=content,
                )
            )
        return candidates

    def _collect_http(self, source: dict[str, Any]) -> DataCandidate:
        headers = {"Accept": "text/csv,application/csv,text/plain"}
        headers.update(source.get("request_headers") or {})
        request = urllib.request.Request(str(source["endpoint"]), headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=float(source.get("timeout_seconds", 15))) as response:
            content = response.read(self.max_bytes + 1)
            disposition = response.headers.get("Content-Disposition", "")
        file_name = _filename_from_disposition(disposition) or f"{source['source_id']}.csv"
        self._validate_content(file_name, content)
        return DataCandidate(
            item_key=f"http:{candidate_time_key()}",
            file_name=file_name,
            content=content,
        )

    def _validate_content(self, file_name: str, content: bytes) -> None:
        if not file_name.lower().endswith(".csv"):
            raise ValueError("自动数据源当前只接收 CSV 批次")
        if not content:
            raise ValueError("数据源返回了空文件")
        if len(content) > self.max_bytes:
            raise ValueError(f"数据批次超过 {self.max_bytes} 字节限制")

    def _write_snapshot(
        self,
        source_id: str,
        ingestion_id: str,
        candidate: DataCandidate,
    ) -> Path:
        """保存不可变快照，避免上游文件变化导致分析结果无法复现。"""

        target_dir = self.storage_dir / source_id
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(candidate.file_name).name
        target = target_dir / f"{ingestion_id}_{safe_name}"
        target.write_bytes(candidate.content)
        return target


def candidate_time_key() -> str:
    """HTTP 接口没有批次编号时使用轮询时刻作为审计键，去重仍以内容哈希为准。"""

    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def _arrival_timestamp(path: Path) -> float:
    """兼容复制时保留修改时间的文件，以创建/变更时间识别其进入监控目录的时刻。"""

    stat = path.stat()
    return max(stat.st_mtime, stat.st_ctime)


def _filename_from_disposition(value: str) -> str | None:
    """只提取简单 filename，最终仍由 Path.name 去除目录。"""

    for part in value.split(";"):
        key, separator, raw = part.strip().partition("=")
        if separator and key.lower() == "filename":
            name = Path(raw.strip().strip('"')).name
            return name if name.lower().endswith(".csv") else None
    return None
