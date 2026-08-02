"""DashScope 工业时序 Agent 服务。

模型通过 OpenAI 兼容接口连接阿里云百炼，与 dinner-agent 中已经配置的
`DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` 命名保持一致。
"""

from __future__ import annotations

from collections.abc import Iterator

from langchain.agents import create_agent
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI

from app.agent.tools import (
    analyze_industrial_file,
    analyze_industrial_folder,
    get_project_data_paths,
    search_industrial_knowledge,
)
from app.config import get_settings

SYSTEM_PROMPT = """
你是“时察千机”工业时序预测决策智能体，服务对象是设备运维人员和工业数据工程师。

工作原则：
1. 涉及数据行数、异常数量、风险分数、F1、趋势和传感器贡献时，必须先调用分析工具，
   不得凭语言模型自行估计。
2. 原始 CSV 由 Python 工具完成计算，你只解释结构化结果，不要求用户粘贴整份数据。
3. 解释故障原因或处置措施时，优先调用工业知识检索工具，并明确区分“算法证据”、
   “可能原因”和“建议验证动作”。
4. 不把异常检测等同于故障确诊；证据不足时直接说明需要哪些工况、维修或设备信息。
5. 输出先给结论与风险等级，再给证据、可能原因和下一步动作，语言简洁、工程化。
""".strip()


class IndustrialAgent:
    """面向 Streamlit 的 Agent 封装。"""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.llm_enabled:
            raise RuntimeError(
                "未配置 DASHSCOPE_API_KEY。请在项目根目录 .env 中填写，"
                "或保留已有 Windows 系统环境变量。"
            )

        model = ChatOpenAI(
            model=settings.chat_model,
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
            temperature=0.1,
            streaming=True,
        )
        self._agent = create_agent(
            model=model,
            tools=[
                analyze_industrial_file,
                analyze_industrial_folder,
                search_industrial_knowledge,
                get_project_data_paths,
            ],
            system_prompt=SYSTEM_PROMPT,
        )

    def invoke(self, question: str) -> str:
        """同步调用 Agent，返回最终文本。"""

        result = self._agent.invoke({"messages": [{"role": "user", "content": question}]})
        return str(result["messages"][-1].content)

    def stream(self, question: str) -> Iterator[str]:
        """按模型消息片段输出，供 Streamlit 实时显示。"""

        for message, _metadata in self._agent.stream(
            {"messages": [{"role": "user", "content": question}]},
            stream_mode="messages",
        ):
            content = getattr(message, "content", "")
            # 只把模型生成的文本片段显示给用户，工具内部 JSON 留在 Agent 推理过程中。
            if isinstance(message, AIMessageChunk) and isinstance(content, str) and content:
                yield content
