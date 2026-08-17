"""工业知识混合检索。

检索同时利用可解释的关键词重叠和比赛方 Embedding 语义相似度。向量接口不可用、触发
限流或本地缓存损坏时会自动退回关键词检索，因此核心工业分析不会依赖外部网络。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from openai import OpenAIError

from app.config import get_settings
from app.knowledge.vector_index import cosine_similarities, load_or_build_index
from app.llm import embed_texts

KEYWORD_WEIGHT = 0.35
VECTOR_WEIGHT = 0.65


@dataclass(frozen=True)
class KnowledgeChunk:
    """知识文件切分后的一段可检索文本。"""

    source: str
    text: str
    score: float = 0.0
    retrieval_mode: str = "keyword"


def search_knowledge(
    query: str,
    top_k: int = 4,
    *,
    use_embeddings: bool = True,
    audit_run_id: str | None = None,
) -> list[KnowledgeChunk]:
    """检索工业知识；确定性模式可显式关闭外部 Embedding 请求。"""

    clean_query = query.strip()
    if not clean_query or top_k <= 0:
        return []
    settings = get_settings()
    chunks = _load_chunks(settings.knowledge_dir)
    if not chunks:
        return []

    keyword_scores = _keyword_scores(clean_query, chunks)
    if not use_embeddings or not settings.embedding_enabled:
        return _rank_chunks(
            chunks,
            keyword_scores,
            top_k,
            require_positive=True,
            retrieval_mode="keyword",
        )

    try:
        index = load_or_build_index(chunks, settings)
        if audit_run_id:
            query_vectors = embed_texts(
                [clean_query],
                settings,
                run_id=audit_run_id,
            )
        else:
            query_vectors = embed_texts([clean_query], settings)
        if len(query_vectors) != 1:
            raise ValueError("Embedding 接口未返回查询向量。")
        vector_scores = cosine_similarities(query_vectors[0], index.vectors)
        combined = (
            KEYWORD_WEIGHT * _min_max_normalize(keyword_scores)
            + VECTOR_WEIGHT * _min_max_normalize(vector_scores)
        )
        return _rank_chunks(
            chunks,
            combined,
            top_k,
            require_positive=True,
            retrieval_mode="hybrid_embedding",
        )
    # RAG 是辅助能力。网络、限流、鉴权或缓存异常不能阻断 Agent 的确定性分析工具。
    except (OpenAIError, OSError, RuntimeError, TypeError, ValueError):
        return _rank_chunks(
            chunks,
            keyword_scores,
            top_k,
            require_positive=True,
            retrieval_mode="keyword_fallback",
        )


def format_knowledge_context(query: str, top_k: int = 4) -> str:
    """把检索结果整理成可直接放入大模型提示词的证据上下文。"""

    chunks = search_knowledge(query, top_k=top_k)
    if not chunks:
        return "未检索到直接相关的本地工业知识。"
    return "\n\n".join(
        f"[资料：{chunk.source}]\n{chunk.text}" for chunk in chunks
    )


def _keyword_scores(query: str, chunks: list[KnowledgeChunk]) -> np.ndarray:
    """计算每个知识片段的关键词重叠分数。"""

    query_tokens = _tokenize(query)
    scores = np.zeros(len(chunks), dtype=np.float32)
    if not query_tokens:
        return scores
    for index, chunk in enumerate(chunks):
        chunk_tokens = _tokenize(chunk.text)
        overlap = query_tokens & chunk_tokens
        if overlap:
            weighted_overlap = sum(1.0 + min(len(token), 8) / 8 for token in overlap)
            scores[index] = weighted_overlap / max(len(chunk_tokens) ** 0.35, 1.0)
    return scores


def _rank_chunks(
    chunks: list[KnowledgeChunk],
    scores: np.ndarray,
    top_k: int,
    *,
    require_positive: bool,
    retrieval_mode: str,
) -> list[KnowledgeChunk]:
    """按分数稳定排序，并保留分数用于后续调试和评测。"""

    ranked_indices = sorted(range(len(chunks)), key=lambda index: (-float(scores[index]), index))
    results: list[KnowledgeChunk] = []
    for index in ranked_indices:
        score = float(scores[index])
        if require_positive and score <= 0:
            continue
        results.append(
            KnowledgeChunk(
                chunks[index].source,
                chunks[index].text,
                score,
                retrieval_mode,
            )
        )
        if len(results) >= top_k:
            break
    return results


def _min_max_normalize(scores: np.ndarray) -> np.ndarray:
    """把不同量纲的关键词分数和余弦相似度映射到 0 到 1。"""

    values = np.asarray(scores, dtype=np.float32)
    if values.size == 0:
        return values
    minimum = float(values.min())
    maximum = float(values.max())
    if maximum - minimum <= 1e-12:
        return np.ones_like(values) if maximum > 0 else np.zeros_like(values)
    return (values - minimum) / (maximum - minimum)


def _load_chunks(knowledge_dir: Path) -> list[KnowledgeChunk]:
    """读取 Markdown/TXT，并按标题或空行切成适中的知识片段。"""

    if not knowledge_dir.exists():
        return []
    chunks: list[KnowledgeChunk] = []
    for path in sorted(knowledge_dir.glob("*")):
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8")
        blocks = re.split(r"\n\s*\n|(?=^#{1,3}\s)", text, flags=re.MULTILINE)
        for block in blocks:
            clean_block = block.strip()
            if len(clean_block) >= 20:
                chunks.append(KnowledgeChunk(source=path.name, text=clean_block))
    return chunks


def _tokenize(text: str) -> set[str]:
    """同时提取中文双字片段和英文数字词，满足工业中英文术语检索。"""

    normalized = text.lower()
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    chinese_sequences = re.findall(r"[\u4e00-\u9fff]+", normalized)
    chinese_tokens: set[str] = set()
    for sequence in chinese_sequences:
        if len(sequence) == 1:
            chinese_tokens.add(sequence)
        else:
            chinese_tokens.update(
                sequence[index : index + 2] for index in range(len(sequence) - 1)
            )
    return words | chinese_tokens
