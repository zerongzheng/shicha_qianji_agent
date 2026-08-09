"""比赛大模型与向量接口适配层。"""

from app.llm.client import create_chat_model, embed_texts, format_llm_error

__all__ = ["create_chat_model", "embed_texts", "format_llm_error"]
