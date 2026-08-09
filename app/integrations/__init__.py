"""外部平台适配层。

该目录只处理万悟等平台与本项目之间的协议差异，不放异常检测和诊断算法。这样以后更换
智能体平台时，只需增加新的适配器，不需要修改工业时序核心流程。
"""

from app.integrations.wanwu import IncomingCsv, receive_wanwu_csv

__all__ = ["IncomingCsv", "receive_wanwu_csv"]
