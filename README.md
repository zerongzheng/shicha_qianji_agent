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
    -> SQLite 任务归档与工单反馈闭环
    -> Markdown 报告与 Streamlit 看板
    -> 万悟工作流/API 编排或本地 LangChain 备用智能问答
```

关键设计原则是：工业数值计算由 Python 算法完成，大模型只做任务编排、知识检索、结果
解释和自然语言交互。系统不会把整份原始 CSV 直接交给大模型猜测故障。

## 目录结构

```text
shichi_qianji_agent/
├─ app/                         # 所有可复用业务代码
│  ├─ agent/                   # LangChain 多轮工具 Agent，作为可选追问能力
│  ├─ analysis/                # 画像、检测、评估、趋势、建议和流程编排
│  ├─ api/                     # 万悟工作流可调用的 REST API
│  ├─ data/                    # SKAB / 通用 CSV 数据接入
│  ├─ diagnosis/               # 确定性分析 + RAG + 单次 GLM-5 自动诊断
│  ├─ experiments/             # 数据划分、阈值调优、基准对比和独立测试
│  ├─ knowledge/               # 关键词 + 比赛 Embedding 的混合 RAG 检索
│  ├─ observability/           # 运行链路和算法侧 JSONL 日志
│  ├─ storage/                 # SQLite 任务、结果和工单持久化
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

页面默认按照校赛演示流程组织：选择数据后，首页先展示设备健康总览、异常事件时间线、
重点传感器贡献、风险判断和优先处置建议；其他标签页再展开时序证据、工况、根因、关联、
报告和智能决策。报告页的“生成案例材料包”会复用当前分析结果，不会重新调用大模型，
并提供 Markdown、事件 CSV、交互式风险图 HTML 和结构化 JSON 下载。

启动工业分析 API（供万悟 API/工作流节点调用）：

```powershell
uv run python api_main.py
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

校赛阶段建议先运行离线自检：

```powershell
uv run python main.py --check
```

该命令只检查 SKAB 路径、输出目录、SQLite 目录和默认样例分析，不调用大模型，也不依赖
万悟。若提示目录没有写权限，优先检查 `outputs/` 和数据库文件的 Windows 权限；不要删除
已有数据库或运行结果。

生成可直接整理进网评材料的 SKAB 实验汇总：

```powershell
uv run python main.py --competition-report
```

命令默认复用最近一次固定划分实验；如果 `outputs/competition/` 还没有实验产物，会自动
执行一次阈值调优和独立测试。需要重新运行全部实验时使用：

```powershell
uv run python main.py --rerun-competition-report
```

报告和汇总表位于 `outputs/competition/`。报告明确标注当前结果来自 SKAB 公开数据，适合
支撑校赛阶段的模型对比、算法流程和工程可复现性，不把公开数据指标包装为企业现场成效。

为典型异常文件生成案例材料包：

```powershell
uv run python main.py --case-package --file ..\SKAB\data\valve1\0.csv
```

输出位于 `outputs/cases/<场景>/<文件名>/`，包括：

- `case_summary.md`：面向答辩讲解的案例摘要；
- `anomaly_events.csv`：异常事件和主导传感器明细；
- `risk_evidence.html`：可缩放的交互式风险曲线；
- `case_summary.json`：结构化结果，便于后续 API 或页面复用。

接口采用“先上传、后分析”的 `file_id` 模式，不接受万悟传入任意服务器路径：

```text
POST /api/v1/files       上传 CSV，返回 file_id
POST /api/v1/analyze     传入 file_id 和可选 config，返回统一 JSON
POST /api/v1/diagnose    一次完成分析、知识检索和单次大模型诊断
POST /api/v1/model-compare     比较异常检测器
POST /api/v1/forecast-compare  比较预测模型并返回最优模型与区间
POST /api/v1/jobs              提交异步分析或诊断任务，立即返回 run_id
GET  /api/v1/jobs/{run_id}     查询 queued/running/success/failed/cancelled 状态
DELETE /api/v1/jobs/{run_id}   取消尚未开始执行的排队任务
GET  /api/v1/jobs/{run_id}/result 获取成功任务的完整结果
GET  /api/v1/runs              查询历史分析任务
GET  /api/v1/runs/{run_id}     查询任务参数与完整结构化结果
GET  /api/v1/work-orders       查询工单队列
PATCH /api/v1/work-orders/{record_id} 回写工单状态与现场反馈
GET  /api/v1/cases             查询现场确认后沉淀的历史故障案例
```

万悟自定义工具不要直接导入完整 `/openapi.json`。项目提供了专用 JSON 协议，规避万悟当前
工具执行器不能正确上传 multipart 二进制文件、不能替换路径参数的问题：

```text
POST /api/v1/wanwu/jobs/submit        文件 URL/Base64 → 上传登记 → 异步任务
POST /api/v1/wanwu/jobs/status        JSON 传入 run_id 查询状态
POST /api/v1/wanwu/jobs/result        JSON 传入 run_id 获取结果
POST /api/v1/wanwu/jobs/cancel        JSON 传入 run_id 取消排队任务
POST /api/v1/wanwu/work-orders/list   查询工单
POST /api/v1/wanwu/work-orders/update 回写工单
POST /api/v1/wanwu/cases/list         查询可追溯的历史故障案例
```

精简 OpenAPI 导入地址：

```text
http://host.docker.internal:8000/integrations/wanwu/openapi.json
```

完整工程 Schema 适合后续搭建异步分析、工单回写和历史案例闭环；比赛现场的演示智能体建议只导入
下面这个单工具 Schema，避免万悟为多个工具自动规划多轮调用：

```text
http://host.docker.internal:8000/integrations/wanwu/quick-openapi.json
```

它只包含 `quick_industrial_diagnosis`。文件上传后，时察千机一次完成数据画像、异常检测、
工况分析、根因候选排序和运维建议生成，返回 `presentation` 中文摘要及结构化证据，且
`model_call_count=0`，不会额外消耗比赛方 GLM-5 QPM。比赛演示应优先使用：

```text
文件输入 → quick_industrial_diagnosis → 展示 presentation 和 analysis
```

快速接口还按文件内容哈希和完整分析配置复用最近一次成功结果。万悟重复提交相同文件时会返回
`cache_hit=true`，避免重复计算和重复生成工单。

本地 Streamlit 的高质量自然语言诊断仍可使用 `/api/v1/diagnose`，该端点会在后端完成
算法与知识检索后调用一次 GLM-5；不要在同一次万悟流程中再次叠加调用 `/api/v1/diagnose`。

正式万悟工作流建议使用异步接口，避免大文件分析、模型训练或预测计算超过 HTTP 节点超时：

```text
文件节点取得临时 URL 或 Base64
→ submit_industrial_analysis，保存 run_id
→ 循环调用 get_industrial_analysis_status
→ job_status=success 时调用 get_industrial_analysis_result
→ job_status=failed 时展示 error
→ 用户撤回或流程超时时调用 cancel_industrial_analysis
→ 进入万悟大模型解释和工单节点
→ 现场确认根因沉淀案例，下一次相似事件自动复用
```

同步 `/api/v1/analyze` 和 `/api/v1/diagnose` 保留用于本地调试和小文件快速调用。异步任务
默认同时执行 2 个、额外排队 8 个，可通过 `.env` 调整。服务重启后，遗留的排队或运行中
任务会自动标记失败，调用方可以重新提交，不会永久停留在运行状态。

在还没有公网服务器和比赛方万悟配置权限时，可以先完成本地接入自检：

```powershell
uv run python api_main.py
uv run shichi-qianji-wanwu-check
```

自检通过会同时生成两个可直接粘贴到万悟“自定义工具”的协议：

```text
outputs/wanwu_openapi.json          # 完整工程：8 个工具
outputs/wanwu_quick_openapi.json     # 比赛演示：1 个快速诊断工具
```
获得 Docker 部署环境后执行：

```powershell
docker compose -f compose.wanwu.yml up -d --build
```

在线万悟仍需要一个平台可访问的公网 HTTPS 地址。这是网络和平台权限条件，不能由项目代码
在没有服务器或平台账号的情况下自动创建；但后端镜像、接口协议、鉴权、状态轮询和自检均已
准备完成。

FastAPI 在线接口说明位于 `/docs`，标准协议位于 `/openapi.json`。其中 `servers` 地址来自：

```dotenv
API_PUBLIC_BASE_URL=http://host.docker.internal:8000
```

比赛方在线万悟接入时必须将其改为实际部署后的公网 HTTPS 地址。

## SQLite 数据库

项目默认把 SQLite 数据库保存到 `outputs/shichi_qianji.db`，该文件属于运行数据，不提交
GitHub。可以在 `.env` 中调整位置：

```dotenv
DATABASE_PATH=outputs/shichi_qianji.db
```

数据库包含三类业务记录：

- `uploaded_files`：保存上传文件编号、文件名、SHA-256、大小和受控存储位置；
- `analysis_runs`：保存分析任务状态、算法参数、耗时、错误和完整结构化结果；
- `work_orders`：保存任务下的工单、优先级、状态、现场确认根因和处置反馈。

原始 CSV 不写进 SQLite，仍保存在 `outputs/api_uploads/`。每张算法工单同时包含原始
`work_order_id` 和全局唯一 `record_id`；万悟更新工单时必须使用 `record_id`，避免不同
分析任务的事件编号相同而串单。

批量分析 valve1 文件夹前 5 个文件：

```powershell
uv run shichi-qianji --dir ..\SKAB\data\valve1 --max-files 5
```

运行五个检测器的全量基准对比：

```powershell
uv run python main.py --benchmark --data-root ..\SKAB\data
```

运行无数据泄漏的阈值调优与独立测试：

```powershell
uv run python main.py --tune --data-root ..\SKAB\data
```

运行 Hybrid 融合权重消融实验：

```powershell
uv run python main.py --ablate-hybrid --data-root ..\SKAB\data
```

该命令只使用验证集选择 MAD、Isolation Forest、PCA 的融合比例和告警阈值，随后冻结配置
运行独立测试集。消融结果不能使用测试集反向修改权重。

运行无监督工况识别与过渡期告警策略评测：

```powershell
uv run python main.py --evaluate-regimes --data-root ..\SKAB\data
```

工况算法不读取 `changepoint` 和 `anomaly`，两类标签只在专项实验中用于事后评价。当前
固定划分实验显示，过渡期抑制会降低事件召回和事件级 F1，因此产品默认只展示工况上下文，
不删除告警。

该命令会按完整文件划分验证集和测试集。健康文件只用于无监督标定；候选阈值仅在验证集
选择，参数冻结后再运行独立测试。`outputs/experiments/` 会保存数据划分、所有候选阈值、
最终测试指标和 Markdown 报告，可直接用于后续实验表格与答辩材料。

## 大模型配置

项目默认使用比赛方提供的联通元景 MaaS OpenAI 兼容接口。聊天、Embedding、OCR 和视觉
模型均通过 `.env` 配置，真实密钥不会进入 Git 仓库：

```dotenv
LLM_PROVIDER=yuanjing_maas
LLM_API_KEY=your_competition_api_key
LLM_BASE_URL=https://maas-api.ai-yuanjing.com/openapi/compatible-mode/v1
LLM_CHAT_MODEL=glm-5
LLM_EMBEDDING_MODEL=qwen3-embed-0.6b
LLM_REQUESTS_PER_MINUTE=5
EMBEDDING_REQUESTS_PER_MINUTE=5
```

本地知识检索使用关键词与语义向量混合评分。知识向量缓存在
`outputs/knowledge_index/`，知识内容或 Embedding 模型变化后会自动重建；比赛接口限流、
断网或鉴权失败时自动退回关键词检索，不影响工业算法和分析报告运行。

比赛接口的每个端点每分钟最多调用 5 次。项目通过 `outputs/rate_limits/` 中的共享状态文件
自动控制最小请求间隔，Streamlit、FastAPI 和命令行即使同时运行也会共同遵守额度。一次
自动诊断只调用一次聊天模型；多轮工具 Agent 通常需要两次以上请求，因此仅作为可选追问。

未配置密钥或接口限流时，数据分析、图表、指标和报告仍正常运行，自动诊断会返回基于算法
证据和本地知识的确定性降级结论。

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

当前已形成六类异常检测模型：滚动 MAD 识别局部偏离，Isolation Forest 识别多变量
动态工况异常，PCA 重构检测器识别健康变量关系被破坏的异常，滑动窗口 AutoEncoder
学习健康工况下的非线性时序关系，Hybrid 融合传统三类证据，时频关系多路径模型进一步
融合时域窗口、频谱形态和传感器耦合结构。固定划分消融选择了“时域 0.67 + 关系 0.33”、
阈值 4.50；该配置在独立测试上将事件级 F1 从 AutoEncoder 的 0.4500 提升至 0.4748，
平均误报事件从 3.53 降至 3.35，因此作为当前面向完整故障事件识别的推荐模型。
AutoEncoder 的点级 F1、PR-AUC、速度仍略优，保留为快速工程基线。频域路径在合成主频漂移
测试中有效，但当前 SKAB 消融未证明总体增益，默认权重为 0，待企业振动数据到位后重估。
AutoEncoder 的窗口分数只落在窗口结束点，避免未来信息提前影响历史告警；相同健康基线的
模型会在进程内缓存，并持久化到带版本与校验和的本地模型仓库。服务重启后可直接恢复，
首次训练后可被批量分析、FastAPI 和 Streamlit 请求复用。
系统还会比较异常前后的传感器相关性，在差分序列上搜索领先与滞后关系，为根因排查提供
传播线索，并通过无监督滚动水平聚类和因果变化强度识别稳定工况与切换期。工况证据进入
报告、预警和大模型诊断，但不直接将切换期异常判为误报。健康数据用于统一风险分数标尺，评估同时覆盖
点级 F1、PR-AUC、事件级 F1、检测延迟、误报事件和工况变点干扰。它们用于建立可信的
实验底座和系统闭环。当前多路径模型已经具备自研模型雏形，但仍需在企业数据上复验。

在企业知识库尚未到位时，项目已增加确定性根因排序层：系统计算事件前后测点变化方向，
融合主导传感器、关系时滞、工况上下文和预测趋势，与内置通用故障模式匹配，输出候选根因、
置信度、支持证据、证据缺口和待确认工单草案。内置模式不等于企业故障规则，置信度上限为
78%，详细设计与后续知识库替换方式见 `docs/ROOT_CAUSE_DIAGNOSIS.md`。

预测侧已经由单一局部线性基线升级为五模型候选体系：最近值持续、指数平滑、局部线性
趋势、滞后特征岭回归和时频特征增强岭回归。系统对每个传感器按时间顺序开展滚动回测，
以 RMSE 为主、MAE 为辅选择最优模型，并输出 MAPE、频域画像、95% 预测区间、模型分歧
和预测可信度。该版本是可部署、可解释的工程模型底座，后续深度时序模型必须在同一划分
和指标下与它公平比较。

下一阶段应按以下顺序升级：

1. 在企业振动数据上重估频域路径，验证其对转速、频带能量和周期漂移的识别价值。
2. 对窗口长度、瓶颈维度和动态特征开展结构消融，进一步降低检测延迟和训练时间。
3. 在当前多模型工程底座上增加跨文件训练的 TCN、PatchTST 或轻量 Transformer，并开展消融实验。
4. 将 changepoint、工况切换和真正设备异常分开建模，进一步降低状态切换误报。
5. 将设备手册、维修工单和告警规则迁移至万悟知识库，保留来源引用。
6. 通过现场反馈回写异常根因和处置结果，形成持续学习闭环。

## 运行测试

```powershell
uv sync --extra dev
uv run pytest
```

## 生成校赛成果包

为了便于制作网评 PPT 和答辩演示，项目提供统一成果包命令。它会复用或生成固定划分实验，
并选择不同 SKAB 场景导出典型案例、风险图、事件明细和答辩索引：

```powershell
uv run python main.py --evidence-pack --case-count 3
```

输出目录为 `outputs/evidence_pack/`，先阅读其中的 `EVIDENCE_PACK_INDEX.md`，再按索引查看
实验汇总和案例材料。该成果包只代表 SKAB 校赛阶段验证结果，不代表企业现场成效。

## 演示工单闭环

启动 Streamlit 后完成一次分析，页面会新增“运维闭环”页。选择工单后可以填写现场确认根因、
处置与复测记录并保存；状态为“已确认”“已完成”或“已关闭”且有确认根因的工单，会自动沉淀为
历史案例，后续相似异常可以在分析结果中检索到。

测试使用临时构造数据，不依赖本机 SKAB 路径；真实 SKAB 数据用于集成验证。
