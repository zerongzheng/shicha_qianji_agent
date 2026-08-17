# 时察千机：工业时序智能体

> 面向浙江省国际大学生创新大赛人工智能赛道“时序工业类”命题的工业时序异常诊断与运维决策系统。

时察千机持续监测工业多变量传感器数据，在新批次到达后自动完成采集、分析、风险判断、异常取证、根因诊断、工单生成与分级通知，并通过现场反馈沉淀历史故障案例。当前校赛阶段使用公开 SKAB 数据集完成工程验证，企业真实数据接入后主要替换数据适配和设备知识，不改变整体应用闭环。

## 项目定位

本项目不是单纯的聊天机器人，也不是只输出一个异常标签的检测脚本，而是一套面向工业运维闭环的时序智能体：

```text
Windows 任务计划程序 / Linux cron（只提供时间信号）
  -> 调用已发布的元景万悟工作流 API
  -> 万悟工作流执行巡检、状态判断和通知编排
  -> 目录 / HTTP 接口 / 企业数据平台检测新批次
  -> 内容去重与不可变快照留存
  -> 自动提交后台分析任务
  -> 数据质量检查与数据画像
  -> 多变量时序异常检测
  -> 连续异常事件合并与传感器归因
  -> 趋势预测与工况上下文分析
  -> 根因候选、证据缺口和验证步骤
  -> 生成优先级运维工单
  -> P1/P2/P3 分级路由与主动通知
  -> 未接单 SLA 催办与超时升级
  -> 现场确认、处置与同源新批次自动复检
  -> 历史案例沉淀与相似案例检索
```

工业数值计算由 Python 算法完成；大模型只用于受控的知识检索、结果解释和自然语言交互，不直接读取整份原始 CSV 猜测故障。这样可以保留算法可复现性、证据链和工程边界，也能降低比赛接口限流对核心分析的影响。

每次分析还会生成一条“自动分析链路”，记录文件接入、设备匹配、数据画像、异常检测、工况识别、
证据提取、趋势预测、根因排序和工单草案生成的执行状态、模块、核心输出与耗时。它记录的是系统
实际调用过的工具事实，不记录大模型隐式思维过程；前端总览和 Markdown 报告均可查看这条链路。

## 当前版本

当前代码已经形成可运行的校赛验证版本：

- 支持 SKAB 及通用多变量 CSV 数据上传，并可自动匹配设备数据契约；
- 支持目录和 HTTP CSV 数据源无人值守轮询，新批次自动去重、留存快照并提交分析；
- 支持异常工单按 P1/P2/P3 路由至不同岗位，并通过站内通知或企业微信群机器人主动推送；
- 支持未接单工单按风险等级自动催办和升级，并对“待验证”工单使用维修后同源新批次自动复检；
- 支持数据画像、缺失值检查和浏览器端文件预检；
- 支持 MAD、Isolation Forest、PCA 重构、滑动窗口 AutoEncoder、Hybrid 和时频关系多路径检测；
- 支持根据任务目标、设备配置、数据规模和健康基线自动选择主模型，并保留完整候选排序；
- 支持四类互补模型交叉验证；严格多数共识经独立测试未优于主模型，当前仅用于可信度证据；
- 支持异常事件合并、风险分级、主导传感器归因；
- 支持工况分段、传感器关系变化和领先/滞后线索分析；
- 支持最近值、指数平滑、局部线性、滞后岭回归和时频增强岭回归预测，并按滚动回测选择模型；
- 支持固定时间尾段预测评估与受控退化预警实验，量化基线改善、区间覆盖、方向判断和预警提前量；
- 支持候选根因、支持证据、证据缺口和现场验证步骤；
- 支持异步分析任务、任务状态轮询、取消排队任务和历史任务归档；
- 支持运维工单状态流转、现场反馈、归档和历史案例沉淀；
- 支持 Vue3 自动监测与工业运维工作台；手动上传集中在独立调试页，不与万悟无人值守主链混用；
- 支持一键登记默认 SKAB 样例，便于没有企业数据时完成完整演示；
- 支持由元景万悟工作流自主编排采集、任务追踪、结果读取、工单和企业微信通知；
- 支持独立的辅助智能体查询监测状态、解释证据和处理工单，对话不作为巡检启动条件；
- 支持生成创新算法证据矩阵：独立测试总体/分场景对照、任务目标驱动路由和时频关系路径消融；
  路由证据使用冻结策略，不根据测试标签逐文件挑选模型。

分析结果同时保留两类审计信息：`execution_trace` 记录系统实际调用了哪些工具和模块，
`agent_decisions` 记录系统基于哪些结构化证据、冻结规则和人工闸门采取了什么业务动作。
这两者都不展示或伪造大模型隐式思维过程。

当前结果只能作为 SKAB 校赛阶段验证，不应包装为企业现场成效。企业数据和企业设备知识库接入后，还需要重新标定阈值、模型和根因规则。

## 系统架构

```mermaid
flowchart LR
    T["任务计划程序 / cron 时间触发"] --> W["元景万悟已发布工作流"]
    W --> M["巡检 / 判断 / 任务追踪 / 通知编排"]
    M --> B["FastAPI 工业工具服务"]
    I["目录 / HTTP 数据源"] --> B
    A["Vue3 自动监测与运维工作台"] --> B
    Q["万悟辅助智能体"] --> B
    B --> C["工业时序分析引擎"]
    B --> D["PostgreSQL 数据源、任务、工单与通知仓储"]
    B --> E["本地工业知识库"]
B --> F["阿里云百炼 DashScope 可选模型"]
```

三部分职责不同：

| 部分 | 主要职责 | 当前用途 |
| --- | --- | --- |
| Vue3 前端 | 图表、风险总览、异常证据、预测、工单和历史案例 | 专业工业运维看板 |
| FastAPI 后端 | 文件接收、异步任务、算法调用、数据库读写、万悟接口 | 前后端和平台之间的业务服务层 |
| 万悟平台 | 无人值守工作流编排、执行记录、智能体、知识库和模型 | 竞赛自动化主入口与平台化展示 |

风险总览中的“自动分析链路”来自后端 `execution_trace` 字段。标准分析接口和本地历史任务返回完整步骤；
万悟快速诊断接口只返回紧凑摘要，以控制上下文长度和模型调用额度。

Vue3 不会因为导入 OpenAPI 自动出现在万悟网页内部。OpenAPI 让万悟工作流调用 FastAPI 工业工具；竞赛阶段采用“万悟自动化主入口 + Vue3 专业看板 + 万悟辅助问答”的组合方式。

## 目录结构

```text
shichi_qianji_agent/
├─ app/
│  ├─ analysis/                 # 数据画像、异常检测、预测、工况和流程编排
│  ├─ api/                      # FastAPI 接口、异步任务和万悟 OpenAPI
│  ├─ data/                     # CSV 加载与字段适配
│  ├─ diagnosis/                # 确定性根因排序、知识检索和诊断服务
│  ├─ experiments/              # 基准、消融、阈值调优和竞赛实验
│  ├─ integrations/             # 万悟文件接收、平台自检等适配
│  ├─ knowledge/                # 工业知识检索与向量索引
│  ├─ llm/                      # 大模型客户端、限流和降级策略
│  ├─ model_store/              # AutoEncoder 等模型的版本化存储
│  ├─ observability/            # 运行日志与可追溯记录
│  ├─ reporting/                # 报告、案例材料包和证据包
│  ├─ storage/                  # PostgreSQL 仓储、迁移、任务、工单和案例
│  ├─ cli.py                    # 命令行入口
│  └─ config.py                 # .env 配置读取
├─ frontend/                    # Vue3 + Vite 正式产品前端
│  ├─ src/App.vue               # 页面状态和整体布局
│  ├─ src/api.js                # FastAPI 请求封装
│  └─ src/components/           # 工单、历史、图表和弹窗组件
├─ resources/knowledge/         # 工业知识库文档
├─ tests/                       # 后端和核心流程测试
├─ docs/                        # 运行、算法和万悟接入文档
├─ scripts/                     # 基础服务启动、停止和检查脚本
├─ wanwu/                       # 万悟工作流说明、提示词、配置模板和定时触发脚本
├─ outputs/                     # 上传文件、日志和实验产物
├─ SKAB/                        # 外部数据集，与项目目录并列，不纳入本仓库
├─ .env                         # 本机密钥和路径，不提交
├─ .env.example                 # 可公开的配置模板
├─ api_main.py                  # FastAPI 启动入口
├─ main.py                      # 命令行分析和实验入口
└─ pyproject.toml               # uv、Python 依赖和命令配置
```

## 数据与知识库

SKAB 与项目目录并列：

```text
E:\大学课程\竞赛\SKAB
E:\大学课程\竞赛\shichi_qianji_agent
```

默认样例由根目录 `.env` 配置：

```dotenv
SKAB_DEFAULT_FILE=../SKAB/data/valve1/0.csv
SKAB_DEFAULT_DIR=../SKAB/data/valve1
HEALTHY_BASELINE_FILE=../SKAB/data/anomaly-free/anomaly-free.csv
```

项目不把完整 SKAB 数据集提交到 GitHub。后续企业数据也建议放在项目外部，通过“自动监测”页面配置目录或 HTTP 接口接入；手动上传仅保留为算法调试和对照实验入口。不同设备字段、单位、采样约定和健康基线通过 `resources/device_profiles/` 下的 JSON 配置适配，分析算法继续使用统一标准字段。

## 无人值守运行

电脑重启后在项目根目录执行一条命令；如果 Docker Desktop 尚未运行，脚本会自动启动并等待
Docker 引擎就绪：

```powershell
.\scripts\start_basic_stack.ps1
```

脚本会检查 PostgreSQL、启动低内存万悟基础容器、时察千机后端、Vue3 运维工作台和四个独立的万悟定时触发器：
无人值守巡检、SLA 督办、维修后复检、班次简报。四份 `outputs/wanwu_*_workflow.local.json`
会在启动前校验 JSON、已发布 UUID 和 API Key 配置；重复执行会按 PID 与进程命令行复用已有服务。
Vue3 前端用于查看图表和处置工单，但不是自动巡检的运行依赖；默认随基础栈一起启动。需要整体停止时执行
`.\scripts\stop_basic_stack.ps1`，它会停止后端、四个触发器和基础容器，数据库卷与业务数据会保留。

只有在纯后端调试或需要进一步节省内存时，才显式跳过 Vue3：

```powershell
.\scripts\start_basic_stack.ps1 -SkipFrontend
```

启动脚本默认管理 `shichi_qianji_frontend.pid`，统一停止命令不变。旧的 `-IncludeFrontend`
参数仍可使用，但已不再需要。

数据源既可在 Vue3“自动监测”页面配置，也可由万悟的“数据源接入配置”工作流调用
`configure_industrial_data_source` 保存，再调用 `verify_industrial_data_source` 做只读验收。
配置最终持久化到 PostgreSQL，万悟负责入口与编排，Vue3 保留专业运维看板和敏感配置管理。
演示数据源参数如下：

```text
名称：SKAB 演示实时目录
方式：监控目录
目录：E:\大学课程\竞赛\shichi_qianji_agent\outputs\demo_feed\skab_valve1
周期：30 秒（后台无人值守工作流触发频率为 60 秒）
P1：生产值班负责人
P2：设备工程师
P3：运行值班员
```

保存并启用后无需再上传或下达分析指令。竞赛配置使用 `AUTOMATION_ORCHESTRATOR=wanwu`：
外部定时器分别调用已发布的四个万悟工作流。无人值守巡检工作流以 SHA-256 内容指纹跳过已处理数据、
保存不可变快照、提交异步分析、读取结果并调用通知工具；SLA 督办和维修后复检由各自独立周期推进，
班次简报汇总最近 8 小时状态。启用企业微信群机器人后，风险等级、责任岗位、接收人员、数据来源和工单
编号会被主动推送至运维群；整个业务链可在万悟运行记录中查看。

万悟当前随附文档提供工作流 OpenAPI，但未发现画布内置 Cron 节点，因此时间信号由
Windows 任务计划程序、Linux cron 或 `wanwu/scripts/trigger_wanwu_workflow.ps1` 提供。
`start_basic_stack.ps1` 会为四个已发布工作流分别维护 PID 和日志文件；触发器不读取工业数据也不运行算法，
业务编排仍由万悟完成。完整配置见 `wanwu/WORKFLOW_SETUP.md`。

企业微信 Webhook 包含机器人访问密钥，只能写入被 Git 忽略的 `.env`，不能放入源码、前端表单或 `.env.example`：

```dotenv
WECOM_ENABLED=true
WECOM_WEBHOOK_URL=请填写企业微信群机器人的完整地址
WECOM_TIMEOUT_SECONDS=10
```

校赛演示使用一个运维群即可：系统仍按 P1/P2/P3 在 PostgreSQL 中完成责任岗位与接收人路由，同一个机器人负责实际送达，消息正文会明确标注应由谁处理。若企业后续要求分别推送到多个群，可在通知适配层增加“岗位到机器人”的部署配置，不需要修改异常检测和工单逻辑。

比赛演示可使用 `scripts/simulate_skab_live_feed.ps1` 将下一份公开 SKAB 样本投递到独立模拟目录，再展示万悟自动处理记录和分级通知。该脚本只模拟“新批次到达”，不修改原始数据，也不把公开数据包装成企业成效。当前目录轮询与 HTTP 轮询属于秒级或分钟级准实时采集，不宣称为 Kafka/MQTT 流式计算。企业后续提供消息队列、时序数据库或 CDC 接口时，只需新增采集适配器。

后台正式频率为 60 秒，单独执行 `-RunOnce` 可能额外等待 0 至 60 秒。录制演示视频时可在
投递后立即调用已发布的无人值守工作流，不修改正式频率：

```powershell
.\scripts\simulate_skab_live_feed.ps1 -RunOnce -TriggerAutonomousWorkflow
```

该命令可能发送企业微信通知，只在受控演示时执行；普通机制检查仍只使用 `-RunOnce`。
如果 SKAB 样本已经全部投完，需要重新从第一份开始，可使用：

```powershell
.\scripts\simulate_skab_live_feed.ps1 -Replay -RunOnce -TriggerAutonomousWorkflow
```

`-Replay` 只重置模拟器进度，并平移复制样本的时间列以形成新的演示批次，不删除历史任务、工单、
报告或 PostgreSQL 数据。本轮时间偏移会写入 `.feed_state.json`，后续不带 `-Replay` 的
`-RunOnce` 会自动继承，确保整轮样本都生成新的内容指纹。

设备配置采用“显式指定、自动匹配、通用回退”三层机制：

```text
resources/device_profiles/
├─ skab_valve.json          # 已启用的 SKAB 阀门测试台配置
└─ enterprise_template.json # 企业设备模板，信息未确认前保持停用
```

接入一类企业设备时，复制模板并完成以下内容：设备编号与版本、时间列和字段别名、必需与可选测点、单位与测点类别、采样周期、经企业确认的安全范围、健康基线路径、推荐检测参数和适用边界。不能确认的单位或阈值应保留为 `null`，不得为了页面完整而编造。

知识库文档放在：

```text
resources/knowledge/
```

当前知识库应优先保持“小而可靠”，每份资料保留标题、适用设备、故障现象、可观测信号、验证方法、处置建议和来源信息。企业手册、告警规则和维修工单接入后，再扩充设备专属知识。

## 环境准备

项目使用 uv 管理 Python 环境，当前要求 Python 3.13 或更高版本；前端使用 Node.js 和 npm。

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
& "E:\Tools\uv\uv.exe" sync
```

首次启动前配置本地环境变量：

```powershell
Copy-Item .env.example .env
```

然后编辑 `.env`，至少检查：

```dotenv
DATABASE_URL=postgresql://shichi_qianji_app:你的数据库密码@127.0.0.1:5432/shichi_qianji
DATABASE_SCHEMA=public
LLM_PROVIDER=dashscope
LLM_API_KEY=你的阿里云百炼 DashScope API Key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_CHAT_MODEL=qwen3.5-plus
LLM_EMBEDDING_MODEL=text-embedding-v4
FRONTEND_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
# 校赛演示需要展示真实人员责任闭环时启用；密码只写本地 .env。
AUTH_ENABLED=true
AUTH_SESSION_HOURS=12
AUTH_BOOTSTRAP_PASSWORD=请设置一个校赛演示密码
```

真实密钥只能写入本地 `.env`，不要写进代码、截图、前端源码或 GitHub。

模型配置分为两层：万悟智能体页面中的聊天模型和知识库向量模型由万悟单独管理，当前均使用
阿里云百炼 DashScope；项目 `.env` 中的 `LLM_*` 是时察千机后端的可选辅助模型配置，仅用于
人工诊断或受控解释，不参与无人值守巡检、算法计算、工单生成、通知和复检主链。若不启用后端
大模型，可留空 `LLM_API_KEY`，核心自动化仍可运行。

## 启动方式

### A. 单独启动 Vue3 调试前端

先开一个终端启动后端：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
& "E:\Tools\uv\uv.exe" run python api_main.py
```

再开第二个终端启动前端：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent\frontend"
& "E:\Tools\nodejs\npm.cmd" install
& "E:\Tools\nodejs\npm.cmd" run dev
```

浏览器访问：

```text
http://127.0.0.1:5173
```

后端健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

前端通过 `frontend/.env` 中的 `VITE_API_BASE_URL` 调用后端，默认值为 `http://127.0.0.1:8000`。前端不直接访问数据库，也不保存大模型密钥。

启用 `AUTH_ENABLED=true` 后，服务首次启动会创建 5 个预置账号，初始密码统一取自本地
`AUTH_BOOTSTRAP_PASSWORD`：`admin`、`production`、`engineer`、`operator`、`observer`。
它们分别对应系统管理员、生产负责人、设备工程师、运行值班员和观察人员。系统不开放公开注册；
生产环境应为每个账号设置独立密码，或对接企业统一身份平台。

### B. 只运行后端接口

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
& "E:\Tools\uv\uv.exe" run python api_main.py
```

API 文档：

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/openapi.json
```

服务自检摘要：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/system/diagnostics | ConvertTo-Json -Depth 6
```

该接口只返回数据库、知识库、SKAB 样例、模型配置、限流参数和任务队列是否就绪，
不返回 API Key、服务器绝对路径、原始 CSV 或数据库内容。`status=ready` 表示当前环境可直接演示；
`status=degraded` 表示基础服务仍可运行，但有需要关注的配置提醒。

### C. 联动本地万悟

只有需要验证平台接入时才启动万悟。Docker 基础模式和本地连接说明见：

```text
docs/BASIC_MODE_RUNBOOK.md
docs/LOCAL_WANWU_RUNBOOK.md
docs/WANWU_INTEGRATION.md
```

本地万悟网页通常为：

```text
http://localhost:8081
```

万悟调用时察千机完整工具集使用：

```text
http://host.docker.internal:8000/integrations/wanwu/openapi.json
```

比赛演示使用无人值守主工作流：

```text
定时触发 -> 自动发现新批次 -> 异步分析 -> 决策摘要 -> 主动告警
```

SLA 督办、维修后复检和班次简报分别由独立周期工作流执行。四条周期工作流不放大模型节点，
工具的 `presentation` 直接进入终态输出；大模型和 RAG 只供同一个辅助智能体按需解释。
`quick_industrial_diagnosis` 及快速 OpenAPI 只保留给人工上传调试。

未显式填写 `detector` 时，产品 API 默认采用自动模型选择；显式填写模型时进入手动模式，
用于固定实验和人工复核。可通过 `analysis_goal` 指定 `balanced`、`high_recall`、
`low_false_alarm`、`relationship_fault`、`nonlinear_pattern` 或 `fast_screening`。

## 主要接口

| 接口 | 用途 |
| --- | --- |
| `POST /api/v1/files` | 上传 CSV 并返回 `file_id` |
| `POST /api/v1/samples/skab/default` | 登记默认 SKAB 样例并返回可分析的 `file_id` |
| `GET /api/v1/files/{file_id}/preflight` | 返回受控 CSV 的真实行数、列名、测点数、缺失率和设备配置匹配结果 |
| `GET /api/v1/device-profiles` | 返回可选择的设备配置摘要 |
| `POST /api/v1/jobs` | 创建异步分析任务 |
| `GET /api/v1/jobs/{run_id}` | 查询任务状态 |
| `GET /api/v1/jobs/{run_id}/result` | 获取分析结果 |
| `GET /api/v1/runs` | 查询历史分析任务 |
| `GET /api/v1/work-orders` | 查询运维工单，可按 `run_id`、状态和优先级筛选 |
| `PATCH /api/v1/work-orders/{record_id}` | 保存现场反馈 |
| `POST /api/v1/auth/login` | 使用预置账号登录本地工作台 |
| `GET /api/v1/auth/me` | 获取当前登录人员和岗位 |
| `GET /api/v1/notifications/mine` | 查询当前人员的主动通知 |
| `POST /api/v1/notifications/acknowledge` | 签收主动通知并记录审计 |
| `POST /api/v1/work-orders/{record_id}/accept` | 当前责任人确认接单 |
| `GET /api/v1/cases` | 查询历史确认案例 |
| `POST /api/v1/wanwu/quick-diagnosis` | 万悟快速诊断入口 |
| `POST /api/v1/wanwu/jobs/submit` | 万悟异步任务入口 |
| `POST /api/v1/wanwu/automation/sla` | 万悟工单 SLA 督办与超时升级 |
| `POST /api/v1/wanwu/automation/reinspection` | 万悟维修后同源数据自动复检 |

完整万悟接口说明见 `docs/WANWU_INTEGRATION.md`。

## 数据库与文件

项目统一使用 PostgreSQL，不再创建或读取 SQLite 文件。本机默认连接信息由 `.env` 提供：

```dotenv
DATABASE_URL=postgresql://shichi_qianji_app:你的数据库密码@127.0.0.1:5432/shichi_qianji
DATABASE_SCHEMA=public
```

首次启动时，仓储会按 `app/storage/migrations/` 中的版本脚本创建表，并在
`schema_migrations` 中记录已应用版本。启动前可执行：

```powershell
& "E:\Tools\PostgreSQL\18\bin\pg_isready.exe" -h 127.0.0.1 -p 5432
```

数据库保存：

- 上传文件元数据；
- 分析任务状态和结构化结果；
- 工单状态、现场确认根因和复测反馈；
- 工单 SLA 层级、催办时间、复检任务和复检结论；
- 用户身份、岗位、可撤销登录会话和工单责任人；
- 个人告警通知、签收时间、接单时间和关键操作审计；
- 已确认历史案例；
- 归档时间和操作原因。
- 脱敏的大模型/Embedding 调用元数据，包括模型、耗时、Token 用量和状态。

模型调用审计不保存 API Key、提示词正文、模型回答或原始工业数据。原始 CSV 保存在
`outputs/api_uploads/`，不直接写入数据库；报告、实验结果、限流状态、脱敏审计日志和模型缓存也保存在
`outputs/` 下。`outputs/` 中的运行产物不会提交 GitHub。

当前 PostgreSQL 已实现用户身份、岗位权限、工单指派/接单、通知签收和操作审计。后续接入万悟时可通过身份适配层复用万悟登录，不需要重写工单业务。

## 实验与成果材料

```powershell
# 离线基础自检
& "E:\Tools\uv\uv.exe" run python main.py --check

# 生成校赛实验汇总
& "E:\Tools\uv\uv.exe" run python main.py --competition-report

# 生成案例材料包
& "E:\Tools\uv\uv.exe" run python main.py --case-package --file ..\SKAB\data\valve1\0.csv

# 评价趋势预测与提前预警成效
& "E:\Tools\uv\uv.exe" run python main.py --evaluate-forecast --data-root ..\SKAB\data

# 评价受约束参数优化建议机制
& "E:\Tools\uv\uv.exe" run python main.py --evaluate-optimization

# 生成证据包
& "E:\Tools\uv\uv.exe" run python main.py --evidence-pack --case-count 3
```

`--evidence-pack` 会在同一目录生成实验汇总、`other` 场景误报审计、三类典型案例和答辩索引：

```text
outputs/evidence_pack/
├─ EVIDENCE_PACK_INDEX.md
├─ experiments/
│  ├─ skab_competition_summary.md
│  ├─ forecast_effectiveness_*.md
│  ├─ forecast_backtest_*.csv
│  ├─ controlled_warning_*.csv
│  ├─ optimization_effectiveness_*.md
│  ├─ optimization_effectiveness_*.csv
│  ├─ time_frequency_relation_false_positive_analysis.md
│  ├─ time_frequency_relation_false_positive_events.csv
│  └─ time_frequency_relation_system_effectiveness.md
└─ cases/
   ├─ other/1/
   ├─ valve1/0/
   └─ valve2/0/
```

误报审计将独立测试集中的未匹配告警按“工况变点附近、工况切换期、传感器质量风险、待解释误报”分类，
用于解释 `other` 场景的告警来源。分类结果是公开数据实验线索，不等于企业现场故障结论；正式接入企业日志后仍需人工复核。

系统成效报告统计异常事件证据、候选根因诊断和工单草案的覆盖率。它只衡量公开数据上的系统输出完整性，
不代表诊断正确率或企业现场处置效率。也可以单独运行：

    uv run python main.py --system-effectiveness --data-root ..\SKAB\data

趋势预测实验在 17 份固定独立测试文件的 136 条传感器序列上保留最后 30 个采样点，
仅使用此前历史滚动选模。自动策略相对最近值持续模型的标准化 RMSE 平均改善 4.75%，
经验 95% 区间覆盖率为 95.12%。另有四类固定种子的受控退化场景用于验证提前预警机制，
它们不是企业数据或设备工程阈值；完整口径见 `docs/competition/SKAB_RESULTS.md`。

受约束优化实验使用四类固定受控风险轨迹和一个稳定对照，验证建议执行时的单步限幅、累计限幅、
稳定期不动作和人工回退边界。当前固定实验中风险场景平均越界暴露减少 77.11%，稳定对照干预为 0，
全部场景约束违规为 0。该数字仅表示模拟机制验证，不是企业设备控制效果、节能率或经济收益。

SKAB 实验协议当前为 `skab-competition-v6-joint-parameter-tuning`。协议固定记录时间对齐、持续状态与
瞬时变点标签的不同补齐语义、缺失填补和模型内缩放口径，并在验证集联合选择阈值、最短事件长度与合并间隔；
成果包发现旧协议时会自动重跑实验，
不会静默复用旧流水线指标。

当前固定独立测试集包含 17 份文件。稳健 MAD 的事件级 F1 为 `0.5647`、平均误报事件为
`1.41/文件`；时频关系多路径模型的事件级 F1 为 `0.6196`、事件召回为 `94.12%`、点级 F1 为
`0.3433`，平均误报事件为 `1.47/文件`。当前主模型冻结参数为阈值 `3.5`、最短事件长度 `12`、
合并间隔 `30`。产品按任务目标区分“稳健告警基线”和“时频关系解释主模型”，不宣称单一模型全面最优。
上述数字仅为 SKAB 公开数据实验结果，完整口径见 `docs/competition/SKAB_RESULTS.md`。

输出目录主要包括：

```text
outputs/competition/       实验汇总和指标
outputs/cases/             典型案例材料
outputs/evidence_pack/     网评与答辩证据包
outputs/api_uploads/       API 接收的 CSV
outputs/logs/              运行日志
```

## 测试

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
& "E:\Tools\uv\uv.exe" sync --extra dev
& "E:\Tools\uv\uv.exe" run pytest -q
```

数据库测试会为每个测试创建随机 PostgreSQL schema，结束后自动删除，不会读写正式 `public` 数据。可通过 `TEST_DATABASE_URL` 指向专用测试数据库。

前端构建检查：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent\frontend"
& "E:\Tools\nodejs\npm.cmd" run build
```

## 当前边界与下一步

当前项目处于“校赛可演示、企业数据待接入”的阶段。现有 SKAB 结果用于验证算法流程和工程闭环，不代表联通企业现场成效。

当前已经完成：

1. 固定 SKAB 文件划分、阈值调优、独立测试和消融实验；
2. 生成实验协议、模型横向对比和相对 MAD 基线的竞赛成效表；
3. 完成万悟无人值守接入、FastAPI 自动分析、Vue3 证据查看、工单确认和历史案例闭环；
4. 建立小而可靠的通用工业知识库，并保留后续企业文档替换入口；
5. 完成独立测试集误报审计，并将 `other → valve1 → valve2` 三类案例纳入成果包。
6. 完成 SKAB 时间尾段预测评估和受控退化提前预警实验，并纳入成果包。
7. 完成自适应时间对齐、缺失填补和模型缩放证据，并纳入执行链与分析报告。
8. 完成带设备边界、观察指标、人工确认和回退条件的参数/能耗优化建议。
9. 完成受约束优化机制实验，以及不保存提示词正文的模型调用审计。
10. 完成 PostgreSQL 人员身份、分级通知、个人工单、接单签收和操作审计闭环。
11. 完成 SLA 自动催办、超时升级、维修后同源数据自动复检和工单重新升级。
12. 完成万悟决策证据摘要工具，使模型选择、交叉验证、趋势风险和受约束优化建议可直接进入工作流节点。
13. 完成万悟知识库最小检索上下文和确定性班次简报工具，避免把原始 CSV 或大结果对象交给 RAG。
14. 完成四个独立工作流的本地 UUID/API Key 配置和一键自动化验收报告，区分只读检查、样本投递与真实工作流调用。

下一步按优先级推进：

1. 获取企业时序数据后，按设备配置重新标定阈值、模型和设备专属根因规则；
2. 用企业时序数据和运行日志复核误报分类，形成可引用的企业案例证据；
3. 在万悟原生知识库中导入设备说明、故障机理和处置规程，并把 `rag_context` 接入 RAG 辅助研判节点；
4. 在万悟发布独立班次简报工作流，并按交接班时间配置定时触发与推送；
5. 根据比赛方万悟配置确定公网部署和工作流发布方式；
6. 接入企业统一身份平台或万悟登录，并用企业真实组织架构替换校赛预置账号。

稳定的 GitHub 可公开实验摘要见 [`docs/competition/SKAB_RESULTS.md`](docs/competition/SKAB_RESULTS.md)。

## GitHub 提交边界

建议提交：

- `app/`、`frontend/src/`、`frontend/package.json`、`frontend/package-lock.json`；
- `tests/`、`docs/`、`scripts/`；
- `.env.example`、`frontend/.env.example`；
- `README.md` 和算法说明。

不要提交：

- `.env` 和任何真实 API Key；
- `frontend/node_modules/`、`frontend/dist/`；
- `.venv/`、缓存、日志和 `outputs/` 运行产物；
- 完整 SKAB 数据集、企业原始数据和内部设备文档。

`.gitignore` 已经配置上述忽略规则。
