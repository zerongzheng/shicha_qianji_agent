"""轻量本地 RAG 检索。

竞赛初版不急于引入向量数据库。知识文件规模较小时，先用中文字符与英文单词的词频
匹配完成可解释检索，部署简单、没有额外持久化目录。资料增长到设备手册和大量工单后，
再把本模块替换为 Embedding + 向量库，Agent 和页面接口都无需改变。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings


@dataclass(frozen=True)
class KnowledgeChunk:
    """知识文件切分后的一段可检索文本。"""

    source: str
    text: str
    score: float = 0.0


def search_knowledge(query: str, top_k: int = 4) -> list[KnowledgeChunk]:
    """从 resources/knowledge 中检索与问题最相关的文本片段。"""

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    scored_chunks: list[KnowledgeChunk] = []
    for chunk in _load_chunks(get_settings().knowledge_dir):
        chunk_tokens = _tokenize(chunk.text)
        overlap = query_tokens & chunk_tokens
        if not overlap:
            continue
        # 长词通常包含更多业务信息，给予略高权重；除以文本长度防止长段落天然占优。
        score = sum(1.0 + min(len(token), 8) / 8 for token in overlap)
        score /= max(len(chunk_tokens) ** 0.35, 1.0)
        scored_chunks.append(KnowledgeChunk(chunk.source, chunk.text, score))

    return sorted(scored_chunks, key=lambda item: item.score, reverse=True)[:top_k]


def format_knowledge_context(query: str, top_k: int = 4) -> str:
    """把检索结果整理成可直接放入大模型提示词的证据上下文。"""

    chunks = search_knowledge(query, top_k=top_k)
    if not chunks:
        return "未检索到直接相关的本地工业知识。"
    return "\n\n".join(
        f"[资料：{chunk.source}]\n{chunk.text}" for chunk in chunks
    )


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
    """同时提取中文双字片段和英文数字词，满足当前小型知识库的检索需求。"""

    normalized = text.lower()
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    chinese_sequences = re.findall(r"[\u4e00-\u9fff]+", normalized)
    chinese_tokens: set[str] = set()
    for sequence in chinese_sequences:
        if len(sequence) == 1:
            chinese_tokens.add(sequence)
        else:
            chinese_tokens.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return words | chinese_tokens
