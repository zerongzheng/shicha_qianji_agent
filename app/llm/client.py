"""OpenAI 兼容模型客户端。

当前默认使用阿里云百炼 DashScope。所有密钥只从配置中心读取；本模块不记录请求正文，避免
工业数据或认证信息进入日志。网络、鉴权和限流错误统一转换为可展示的中文提示。
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from app.config import Settings, get_settings
from app.llm.rate_limit import acquire_embedding_slot, create_chat_rate_limiter
from app.observability import ModelCallAudit


def create_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    """创建适配 DashScope OpenAI 兼容接口的 LangChain 聊天模型。"""

    settings = settings or get_settings()
    if not settings.llm_enabled:
        raise RuntimeError("未配置 LLM_API_KEY。")
    extra_body: dict[str, object] | None = None
# 兼容显式选择 GLM-5 的旧部署；当前 DashScope 默认模型为 qwen3.5-plus，不走该分支。
    if settings.llm_chat_model.lower() == "glm-5":
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
    return ChatOpenAI(
        model=settings.llm_chat_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=0.1,
        streaming=True,
        # SDK 内部重试不会再次经过 LangChain 限流器，比赛接口下应直接返回友好错误。
        max_retries=0,
        timeout=60,
        extra_body=extra_body,
        rate_limiter=create_chat_rate_limiter(settings),
    )


def embed_texts(
    texts: Sequence[str],
    settings: Settings | None = None,
    *,
    run_id: str | None = None,
) -> list[list[float]]:
    """调用比赛方 Embedding 接口，返回与输入顺序一致的浮点向量。"""

    settings = settings or get_settings()
    if not settings.embedding_enabled:
        raise RuntimeError("未配置可用的比赛方 Embedding 接口。")
    clean_texts = [text.strip() for text in texts if text.strip()]
    if not clean_texts:
        return []
    # 知识库可批量向量化为一次请求；查询向量单独请求。两类请求共享 Embedding 接口额度。
    acquire_embedding_slot(settings)
    audit = ModelCallAudit(
        operation=("knowledge_embedding_query" if run_id else "knowledge_embedding_index"),
        provider=settings.llm_provider,
        model=settings.llm_embedding_model,
        input_character_count=sum(len(item) for item in clean_texts),
        run_id=run_id,
    )
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=60,
        max_retries=0,
    )
    try:
        response = client.embeddings.create(
            model=settings.llm_embedding_model,
            input=clean_texts,
            encoding_format="float",
        )
    except Exception as exc:
        audit.finish("failed", error_type=type(exc).__name__)
        raise
    usage = getattr(response, "usage", None)
    audit.finish(
        "success",
        output_character_count=len(response.data),
        usage=(usage.model_dump() if hasattr(usage, "model_dump") else {}),
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    return [list(item.embedding) for item in ordered]


def format_llm_error(error: Exception) -> str:
    """把常见比赛接口错误转成不泄露密钥和底层请求内容的提示。"""

    if isinstance(error, RateLimitError):
        return "比赛大模型接口触发调用频率限制，请稍后再试。"
    if isinstance(error, APIConnectionError):
        return "无法连接比赛大模型接口，请检查网络后重试。"
    if isinstance(error, APIStatusError):
        if error.status_code in {401, 403}:
            return "比赛大模型接口鉴权失败，请检查本地 LLM_API_KEY。"
        return f"比赛大模型接口返回错误状态 {error.status_code}。"
    return "大模型调用失败，请查看本地运行日志并稍后重试。"
