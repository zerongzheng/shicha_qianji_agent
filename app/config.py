"""项目配置中心。

本项目不再使用 YAML。固定默认值写在 Python 数据类中，需要因机器或部署环境变化的内容
写入 `.env`。这样配置来源只有两处，初学者也能快速判断一个参数从哪里来。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# app/config.py 的上一级 app，再上一级就是项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    """集中保存项目运行参数，避免各模块重复读取环境变量。"""

    project_root: Path
    output_dir: Path
    database_path: Path
    knowledge_dir: Path
    default_skab_file: Path
    default_skab_dir: Path
    healthy_baseline_file: Path
    llm_provider: str
    llm_api_key: str
    llm_base_url: str
    llm_chat_model: str
    llm_embedding_model: str
    llm_ocr_model: str
    llm_vision_model: str
    document_parser_url: str
    llm_requests_per_minute: int
    embedding_requests_per_minute: int
    anomaly_detector: str
    anomaly_threshold: float
    rolling_window: int
    min_event_length: int
    merge_gap: int
    contamination: float
    forecast_horizon: int
    forecast_lookback: int
    forecast_holdout: int
    async_job_workers: int
    async_job_queue_size: int
    max_upload_bytes: int
    wanwu_download_timeout: float
    wanwu_allow_private_file_urls: bool
    api_public_base_url: str
    industrial_api_key: str
    frontend_allowed_origins: str

    @property
    def llm_enabled(self) -> bool:
        """只有配置了密钥时才启用大模型，基础分析不依赖网络。"""

        return bool(self.llm_api_key.strip())

    @property
    def embedding_enabled(self) -> bool:
        """配置了密钥和 Embedding 模型时才启用向量检索。"""

        return self.llm_enabled and bool(self.llm_embedding_model.strip())


def _resolve_path(raw_path: str) -> Path:
    """把 `.env` 中的相对路径统一解释为相对于项目根目录的路径。"""

    path = Path(raw_path).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取一次配置并缓存，保证整个程序使用同一份参数。"""

    return Settings(
        project_root=PROJECT_ROOT,
        output_dir=PROJECT_ROOT / "outputs",
        database_path=_resolve_path(
            os.getenv("DATABASE_PATH", "outputs/shichi_qianji.db")
        ),
        knowledge_dir=PROJECT_ROOT / "resources" / "knowledge",
        default_skab_file=_resolve_path(
            os.getenv("SKAB_DEFAULT_FILE", "../SKAB/data/valve1/0.csv")
        ),
        default_skab_dir=_resolve_path(
            os.getenv("SKAB_DEFAULT_DIR", "../SKAB/data/valve1")
        ),
        healthy_baseline_file=_resolve_path(
            os.getenv(
                "HEALTHY_BASELINE_FILE",
                "../SKAB/data/anomaly-free/anomaly-free.csv",
            )
        ),
        llm_provider=os.getenv("LLM_PROVIDER", "yuanjing_maas"),
        llm_api_key=os.getenv("LLM_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")),
        llm_base_url=os.getenv(
            "LLM_BASE_URL",
            os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://maas-api.ai-yuanjing.com/openapi/compatible-mode/v1",
            ),
        ).rstrip("/"),
        llm_chat_model=os.getenv(
            "LLM_CHAT_MODEL",
            os.getenv("DASHSCOPE_CHAT_MODEL", "glm-5"),
        ),
        llm_embedding_model=os.getenv("LLM_EMBEDDING_MODEL", "qwen3-embed-0.6b"),
        llm_ocr_model=os.getenv("LLM_OCR_MODEL", "glm-ocr"),
        llm_vision_model=os.getenv("LLM_VISION_MODEL", "YuanjingVL"),
        document_parser_url=os.getenv(
            "DOCUMENT_PARSER_URL",
            "https://maas-api.ai-yuanjing.com/openapi/v1/rag/model_parser_file",
        ),
        llm_requests_per_minute=int(os.getenv("LLM_REQUESTS_PER_MINUTE", "5")),
        embedding_requests_per_minute=int(
            os.getenv("EMBEDDING_REQUESTS_PER_MINUTE", "5")
        ),
        anomaly_detector=os.getenv("ANOMALY_DETECTOR", "time_frequency_relation"),
        anomaly_threshold=float(os.getenv("ANOMALY_THRESHOLD", "4.5")),
        rolling_window=int(os.getenv("ROLLING_WINDOW", "61")),
        min_event_length=int(os.getenv("MIN_EVENT_LENGTH", "3")),
        merge_gap=int(os.getenv("MERGE_GAP", "5")),
        contamination=float(os.getenv("CONTAMINATION", "0.08")),
        forecast_horizon=int(os.getenv("FORECAST_HORIZON", "30")),
        forecast_lookback=int(os.getenv("FORECAST_LOOKBACK", "120")),
        forecast_holdout=int(os.getenv("FORECAST_HOLDOUT", "30")),
        async_job_workers=max(1, int(os.getenv("ASYNC_JOB_WORKERS", "2"))),
        async_job_queue_size=max(0, int(os.getenv("ASYNC_JOB_QUEUE_SIZE", "8"))),
        max_upload_bytes=max(1024, int(os.getenv("MAX_UPLOAD_BYTES", "26214400"))),
        wanwu_download_timeout=max(
            1.0,
            float(os.getenv("WANWU_DOWNLOAD_TIMEOUT", "30")),
        ),
        wanwu_allow_private_file_urls=os.getenv(
            "WANWU_ALLOW_PRIVATE_FILE_URLS",
            "false",
        ).strip().lower()
        in {"true", "1", "yes", "on"},
        api_public_base_url=os.getenv(
            "API_PUBLIC_BASE_URL",
            "http://host.docker.internal:8000",
        ).rstrip("/"),
        industrial_api_key=os.getenv("INDUSTRIAL_API_KEY", ""),
        frontend_allowed_origins=os.getenv(
            "FRONTEND_ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ),
    )
