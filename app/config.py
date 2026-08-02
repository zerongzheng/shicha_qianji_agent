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
    knowledge_dir: Path
    default_skab_file: Path
    default_skab_dir: Path
    healthy_baseline_file: Path
    dashscope_api_key: str
    dashscope_base_url: str
    chat_model: str
    anomaly_detector: str
    anomaly_threshold: float
    rolling_window: int
    min_event_length: int
    merge_gap: int
    contamination: float
    forecast_horizon: int
    forecast_lookback: int
    forecast_holdout: int
    industrial_api_key: str

    @property
    def llm_enabled(self) -> bool:
        """只有配置了密钥时才启用大模型，基础分析不依赖网络。"""

        return bool(self.dashscope_api_key.strip())


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
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        dashscope_base_url=os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        chat_model=os.getenv("DASHSCOPE_CHAT_MODEL", "qwen-plus"),
        anomaly_detector=os.getenv("ANOMALY_DETECTOR", "hybrid"),
        anomaly_threshold=float(os.getenv("ANOMALY_THRESHOLD", "4.5")),
        rolling_window=int(os.getenv("ROLLING_WINDOW", "61")),
        min_event_length=int(os.getenv("MIN_EVENT_LENGTH", "3")),
        merge_gap=int(os.getenv("MERGE_GAP", "5")),
        contamination=float(os.getenv("CONTAMINATION", "0.08")),
        forecast_horizon=int(os.getenv("FORECAST_HORIZON", "30")),
        forecast_lookback=int(os.getenv("FORECAST_LOOKBACK", "120")),
        forecast_holdout=int(os.getenv("FORECAST_HOLDOUT", "30")),
        industrial_api_key=os.getenv("INDUSTRIAL_API_KEY", ""),
    )
