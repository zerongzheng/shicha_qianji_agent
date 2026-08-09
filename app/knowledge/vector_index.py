"""小型工业知识库的本地向量索引。

当前知识库规模只有设备机理、故障经验和运维规则等少量文本，不需要提前引入独立向量
数据库。本模块调用比赛方 Embedding 接口生成向量，并将结果缓存为 NumPy 压缩文件。
知识内容或模型名称变化时缓存键会自动变化，不会误用旧向量。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from app.config import Settings, get_settings
from app.llm import embed_texts


@dataclass(frozen=True)
class KnowledgeVectorIndex:
    """知识片段和对应向量组成的只读索引。"""

    cache_key: str
    sources: tuple[str, ...]
    texts: tuple[str, ...]
    vectors: np.ndarray


class VectorizableChunk(Protocol):
    """向量索引只要求片段提供来源和正文，避免与检索器产生循环依赖。"""

    source: str
    text: str


def load_or_build_index(
    chunks: Sequence[VectorizableChunk],
    settings: Settings | None = None,
) -> KnowledgeVectorIndex:
    """读取匹配的磁盘缓存；缓存不存在时调用 Embedding 接口创建索引。"""

    settings = settings or get_settings()
    sources = tuple(str(chunk.source) for chunk in chunks)
    texts = tuple(str(chunk.text) for chunk in chunks)
    if not texts:
        return KnowledgeVectorIndex("", (), (), np.empty((0, 0), dtype=np.float32))

    cache_key = _build_cache_key(settings.llm_embedding_model, sources, texts)
    cache_dir = settings.output_dir / "knowledge_index"
    cache_path = cache_dir / f"{cache_key}.npz"
    cached = _load_index(cache_path, cache_key, sources, texts)
    if cached is not None:
        return cached

    # 小型知识库一次批量向量化即可，减少接口请求次数，也更适合比赛接口的 QPM 限制。
    vectors = np.asarray(embed_texts(texts, settings), dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[0] != len(texts):
        raise ValueError("Embedding 接口返回的向量数量与知识片段数量不一致。")
    vectors = _normalize_rows(vectors)
    index = KnowledgeVectorIndex(cache_key, sources, texts, vectors)
    _save_index(cache_path, index)
    return index


def cosine_similarities(query_vector: Sequence[float], matrix: np.ndarray) -> np.ndarray:
    """计算查询向量与知识向量的余弦相似度。"""

    if matrix.size == 0:
        return np.empty(0, dtype=np.float32)
    query = np.asarray(query_vector, dtype=np.float32)
    if query.ndim != 1 or matrix.ndim != 2 or query.shape[0] != matrix.shape[1]:
        raise ValueError("查询向量维度与知识索引不一致。")
    norm = float(np.linalg.norm(query))
    if norm <= 1e-12:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    return matrix @ (query / norm)


def _build_cache_key(model: str, sources: Sequence[str], texts: Sequence[str]) -> str:
    """将模型名、来源和正文共同写入哈希，确保缓存与知识版本严格对应。"""

    payload = json.dumps(
        {"model": model, "sources": list(sources), "texts": list(texts)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    """预先归一化知识向量，使查询阶段只需一次矩阵乘法。"""

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms <= 1e-12] = 1.0
    return vectors / norms


def _load_index(
    path: Path,
    cache_key: str,
    sources: tuple[str, ...],
    texts: tuple[str, ...],
) -> KnowledgeVectorIndex | None:
    """校验缓存元数据和矩阵形状，损坏或过期时交由上层重新构建。"""

    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            metadata = json.loads(str(data["metadata"].item()))
            vectors = np.asarray(data["vectors"], dtype=np.float32)
        if metadata.get("cache_key") != cache_key:
            return None
        if tuple(metadata.get("sources", [])) != sources:
            return None
        if tuple(metadata.get("texts", [])) != texts:
            return None
        if vectors.ndim != 2 or vectors.shape[0] != len(texts):
            return None
        return KnowledgeVectorIndex(cache_key, sources, texts, vectors)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _save_index(path: Path, index: KnowledgeVectorIndex) -> None:
    """先写临时文件再原子替换，避免程序中断留下半个索引文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = json.dumps(
        {
            "cache_key": index.cache_key,
            "sources": index.sources,
            "texts": index.texts,
        },
        ensure_ascii=False,
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f"{path.stem}-",
            suffix=".npz",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        np.savez_compressed(temporary_path, metadata=np.asarray(metadata), vectors=index.vectors)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)
