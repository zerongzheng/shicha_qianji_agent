# 时察千机：工业时序预测决策智能体

“时察千机”面向浙江省国际大学生创新大赛人工智能赛道时序工业类命题，目标是把工业
多变量传感器数据自动转化为可追踪的异常事件、风险证据和运维动作。当前版本使用 SKAB
公开数据集验证完整流程，后续接入企业数据时主要替换数据适配器和模型，不推翻上层应用。

## 项目现在能做什么

系统已经跑通以下闭环：

```text
SKAB / 企业 CSV
    -> 字段识别与数据质量检查
    -> 多变量稳健异常检测
    -> 连续异常事件合并与传感器归因
    -> SKAB 标签量化评估
    -> 近期趋势与漂移研判
    -> 运维处置建议
    -> Markdown 报告与 Streamlit 看板
    -> 万悟工作流/API 编排或本地 LangChain 备用智能问答
```

关键设计原则是：工业数值计算由 Python 算法完成，大模型只做任务编排、知识检索、结果
解释和自然语言交互。系统不会把整份原始 CSV 直接交给大模型猜测故障。

## 目录结构

```text
shichi_qianji_agent/
├─ app/                         # 所有可复用业务代码
│  ├─ agent/                   # LangChain 工具与 DashScope Agent
│  ├─ analysis/                # 画像、检测、评估、趋势、建议和流程编排
│  ├─ api/                     # 万悟工作流可调用的 REST API
│  ├─ data/                    # SKAB / 通用 CSV 数据接入
│  ├─ experiments/             # 数据划分、阈值调优、基准对比和独立测试
│  ├─ knowledge/               # 轻量本地 RAG 检索
│  ├─ observability/           # 运行链路和算法侧 JSONL 日志
│  ├─ models/                  # 模块之间共享的数据结构
│  ├─ reporting/               # Markdown 报告生成
│  ├─ ui/                      # Streamlit 页面
│  ├─ cli.py                   # 命令行入口
│  └─ config.py                # .env 与 Python 默认配置
├─ resources/knowledge/         # 工业机理和运维知识资料
├─ tests/                       # 核心流程回归测试
├─ outputs/                     # 分析报告和页面临时上传文件
├─ .env                         # 本机密钥与路径，不提交 GitHub
├─ .env.example                 # 可公开的环境变量模板
├─ main.py                      # PyCharm 直接运行入口
├─ streamlit_app.py             # Streamlit 启动入口
└─ pyproject.toml               # uv 依赖与项目配置
```

这里没有 YAML。运行环境差异使用 `.env`，稳定的代码默认值和数据结构使用 Python。

## 数据放在哪里

SKAB 数据集和项目保持并列，这是更接近真实工程的数据治理方式：

```text
E:\大学课程\竞赛\SKAB
E:\大学课程\竞赛\shichi_qianji_agent
```

默认样例路径在 `.env` 中：

```dotenv
SKAB_DEFAULT_FILE=../SKAB/data/valve1/0.csv
SKAB_DEFAULT_DIR=../SKAB/data/valve1
```

竞赛后续提供新数据时，可以在 Streamlit 页面直接上传 CSV，也可以把数据放在项目外部任意
目录，然后在页面输入绝对路径。若列格式不同，只需在 `app/data/loader.py` 增加适配逻辑。

## 从零运行

在 PyCharm 中打开整个目录：

```text
E:\大学课程\竞赛\shichi_qianji_agent
```

然后在 PyCharm Terminal 执行：

```powershell
uv sync
uv run python main.py
```

`main.py` 会分析 `.env` 中的默认 SKAB 文件，并在 `outputs/` 生成报告。

启动前端：

```powershell
uv run streamlit run streamlit_app.py
```

浏览器通常会自动打开 `http://localhost:8501`。

启动工业分析 API（供万悟 API/工作流节点调用）：

```powershell
uv run python api_main.py
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

接口采用“先上传、后分析”的 `file_id` 模式，不接受万悟传入任意服务器路径：

```text
POST /api/v1/files       上传 CSV，返回 file_id
POST /api/v1/analyze     传入 file_id 和可选 config，返回统一 JSON
POST /api/v1/model-compare     比较异常检测器
POST /api/v1/forecast-compare  比较预测模型并返回最优模型与区间
```

万悟侧建议使用工作流的 API 节点串联：文件上传 → 工业分析 API → 万悟知识库 → 大模型
总结。万悟负责 Agent 编排，本项目负责确定性工业计算。

批量分析 valve1 文件夹前 5 个文件：

```powershell
uv run shichi-qianji --dir ..\SKAB\data\valve1 --max-files 5
```

运行三个检测器的全量基准对比：

```powershell
uv run python main.py --benchmark --data-root ..\SKAB\data
```

运行无数据泄漏的阈值调优与独立测试：

```powershell
uv run python main.py --tune --data-root ..\SKAB\data
```

该命令会按完整文件划分验证集和测试集。健康文件只用于无监督标定；候选阈值仅在验证集
选择，参数冻结后再运行独立测试。`outputs/experiments/` 会保存数据划分、所有候选阈值、
最终测试指标和 Markdown 报告，可直接用于后续实验表格与答辩材料。

## 大模型配置

项目使用阿里云百炼的 OpenAI 兼容接口，变量命名与 `dinner-agent` 保持一致：

```dotenv
DASHSCOPE_API_KEY=你的密钥
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_CHAT_MODEL=qwen-plus
```

若 Windows 系统环境变量中已经存在 `DASHSCOPE_API_KEY`，`.env` 中可以留空。
未配置密钥时，数据分析、图表、指标和报告仍然能够正常工作，只有 Agent 对话不可用。

## 元景万悟接入

万悟源码位于独立目录 `E:\大学课程\竞赛\wanwu`。本项目不复制万悟源码，而是通过 REST API
把工业分析能力提供给万悟的 API 节点或工作流。详细接口协议见
`docs/WANWU_INTEGRATION.md`。

```powershell
uv run python api_main.py
```

当前 API 采用“上传 CSV -> 返回 file_id -> 按 file_id 分析”的方式，避免万悟访问本机路径，
同时输出异常事件、趋势预测、风险预警、运维建议和 `run_id`。

## 当前算法处于什么阶段

当前已形成三类可解释工程基线：滚动 MAD 识别局部偏离，Isolation Forest 识别多变量
动态工况异常，混合检测器融合两类证据。健康数据用于统一风险分数标尺，评估同时覆盖
点级 F1、PR-AUC、事件级 F1、检测延迟、误报事件和工况变点干扰。它们用于建立可信的
实验底座和系统闭环，仍不应包装成最终自研竞赛模型。

预测侧已经由单一局部线性基线升级为五模型候选体系：最近值持续、指数平滑、局部线性
趋势、滞后特征岭回归和时频特征增强岭回归。系统对每个传感器按时间顺序开展滚动回测，
以 RMSE 为主、MAE 为辅选择最优模型，并输出 MAPE、频域画像、95% 预测区间、模型分歧
和预测可信度。该版本是可部署、可解释的工程模型底座，后续深度时序模型必须在同一划分
和指标下与它公平比较。

下一阶段应按以下顺序升级：

1. 在当前固定划分上增加 PCA/AutoEncoder、LSTM-AE 或 TranAD 等候选模型。
2. 在当前多模型工程底座上增加跨文件训练的 TCN、PatchTST 或轻量 Transformer，并开展消融实验。
3. 将 changepoint、工况切换和真正设备异常分开建模，进一步降低状态切换误报。
4. 将设备手册、维修工单和告警规则迁移至万悟知识库，保留来源引用。
5. 通过现场反馈回写异常根因和处置结果，形成持续学习闭环。

## 运行测试

```powershell
uv sync --extra dev
uv run pytest
```

测试使用临时构造数据，不依赖本机 SKAB 路径；真实 SKAB 数据用于集成验证。
