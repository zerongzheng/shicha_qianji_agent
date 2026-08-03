"""工业时序 Agent 服务。

主模型通过 OpenAI 兼容协议调用。当前默认接入比赛方提供的联通元景 MaaS，配置层仍兼容
原有 DashScope 环境变量，切换提供方不需要改动 Agent 工具和工业分析核心。
"""

from __future__ import annotations

from collections.abc import Iterator

from langchain.agents import create_agent
from langchain_core.messages import AIMessageChunk

from app.agent.tools import (
    analyze_industrial_file,
    analyze_industrial_folder,
    get_project_data_paths,
    search_industrial_knowledge,
)
from app.config import get_settings
from app.llm import create_chat_model

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
6. 分析结果已经包含确定性候选根因、置信度、证据缺口和工单草案；回答时优先解释这些
   结构化结论，知识检索只用于补充机理和现场验证步骤，不得把通用规则写成故障确诊。
""".strip()


class IndustrialAgent:
    """面向 Streamlit 的 Agent 封装。"""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.llm_enabled:
            raise RuntimeError(
                "未配置 LLM_API_KEY。请在项目根目录 .env 中填写比赛方接口密钥。"
            )

        model = create_chat_model(settings)
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
