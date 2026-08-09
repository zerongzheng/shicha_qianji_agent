"""AutoEncoder 健康模型的版本化磁盘仓库。

仓库只保存项目本地训练产生的模型对象，不接收用户上传的模型文件。文件名由健康数据指纹
和模型参数共同决定，加载时再次校验格式版本、缓存键和内容校验和，避免错误模型静默参与分析。
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import joblib

from app.config import get_settings

MODEL_FORMAT_VERSION = 3
MODEL_KIND = "window_autoencoder"


@dataclass(frozen=True)
class AutoEncoderModelPackage:
    """可跨进程保存和恢复的 AutoEncoder 训练产物。"""

    cache_key: str
    scaler: Any
    model: Any
    training_window_raw: Any
    sensor_training_raw: dict[str, Any]
    feature_columns: tuple[str, ...]
    window_size: int
    trained_at: str
    training_window_count: int


def load_autoencoder_package(cache_key: str) -> AutoEncoderModelPackage | None:
    """读取并校验指定健康模型；不存在或损坏时返回空，由调用方重新训练。"""

    target = _model_path(cache_key)
    checksum_path = target.with_suffix(".sha256")
    if not target.is_file() or not checksum_path.is_file():
        return None
    try:
        expected_checksum = checksum_path.read_text(encoding="ascii").strip()
        if _file_checksum(target) != expected_checksum:
            return None
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Setting the shape on a NumPy array has been deprecated.*",
                category=DeprecationWarning,
            )
            payload = joblib.load(target)
        if not isinstance(payload, dict):
            return None
        if payload.get("format_version") != MODEL_FORMAT_VERSION:
            return None
        if payload.get("model_kind") != MODEL_KIND or payload.get("cache_key") != cache_key:
            return None
        package = payload.get("package")
        if not isinstance(package, AutoEncoderModelPackage):
            return None
        if package.cache_key != cache_key:
            return None
        return package
    # 模型缓存属于可再生成产物。反序列化、权限或文件损坏不应中断工业分析主流程。
    except (OSError, EOFError, ValueError, TypeError):
        return None


def save_autoencoder_package(package: AutoEncoderModelPackage) -> Path:
    """原子保存模型包及 SHA-256 校验和，返回最终模型路径。"""

    target = _model_path(package.cache_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".joblib.tmp")
    checksum_path = target.with_suffix(".sha256")
    checksum_temporary = checksum_path.with_suffix(".sha256.tmp")
    payload = {
        "format_version": MODEL_FORMAT_VERSION,
        "model_kind": MODEL_KIND,
        "cache_key": package.cache_key,
        "package": package,
    }
    try:
        joblib.dump(payload, temporary, compress=3)
        checksum_temporary.write_text(_file_checksum(temporary), encoding="ascii")
        os.replace(temporary, target)
        os.replace(checksum_temporary, checksum_path)
    finally:
        temporary.unlink(missing_ok=True)
        checksum_temporary.unlink(missing_ok=True)
    return target


def list_autoencoder_models() -> list[dict[str, Any]]:
    """返回可公开展示的模型元数据，不暴露本地路径和完整健康数据指纹。"""

    model_dir = get_settings().output_dir / "models" / MODEL_KIND
    if not model_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for model_path in sorted(model_dir.glob("*.joblib"), key=lambda path: path.stat().st_mtime, reverse=True):
        package = load_autoencoder_package(model_path.stem)
        if package is None:
            continue
        records.append(
            {
                "model_id": package.cache_key[:12],
                "model_kind": MODEL_KIND,
                "format_version": MODEL_FORMAT_VERSION,
                "trained_at": package.trained_at,
                "sensor_count": len(package.feature_columns) // 3,
                "feature_count": len(package.feature_columns),
                "window_size": package.window_size,
                "training_window_count": package.training_window_count,
                "file_size_bytes": model_path.stat().st_size,
            }
        )
    return records


def build_training_timestamp() -> str:
    """生成带时区的模型训练时间字符串。"""

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _model_path(cache_key: str) -> Path:
    """把十六进制缓存键转换为受控模型路径。"""

    if len(cache_key) != 64 or any(character not in "0123456789abcdef" for character in cache_key):
        raise ValueError("AutoEncoder 模型缓存键格式不合法。")
    return (
        get_settings().output_dir
        / "models"
        / MODEL_KIND
        / f"{cache_key}.joblib"
    )


def _file_checksum(path: Path) -> str:
    """流式计算文件校验和，避免大模型文件一次性读入内存。"""

    digest = sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
