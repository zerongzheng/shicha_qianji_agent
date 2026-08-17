# 元景万悟接入说明

本项目与 `E:\大学课程\竞赛\wanwu` 保持两个独立工程：万悟负责智能体、工作流、知识库和模型编排，
时察千机负责工业 CSV 的确定性计算、预测、预警和结构化输出。不要把万悟源码复制进本项目。

## 调用链

```text
外部定时器（只提供时间信号）
    -> 调用已发布的万悟工作流 OpenAPI
    -> run_unattended_industrial_cycle 自动发现新数据并返回 run_id
    -> 万悟循环查询任务状态并读取决策摘要
    -> dispatch_industrial_alerts 完成分级路由和企业微信推送
    -> 独立 SLA 工作流催办和升级未接单工单
    -> 现场人员回写处置结果并将工单改为待验证
    -> 独立复检工作流只使用同一数据源的处置后新批次复检
    -> 独立班次简报工作流汇总最近 8 小时运行和闭环状态
```

竞赛主流程不要求用户上传 CSV，也不使用 `quick_industrial_diagnosis` 发起分析。该接口以及
`/api/v1/analyze`、`/api/v1/diagnose` 只保留给本地调试和受控问题复现。辅助智能体负责查询状态、
解释结构化证据和按需使用 RAG，不承担周期巡检、算法判断、工单生成、通知、SLA 或复检。

## 为什么增加万悟专用接口

对本地 `E:\大学课程\竞赛\wanwu` 源码检查后确认，当前 OpenAPI 工具执行器有两个兼容限制：

1. `multipart/form-data` 只会写普通文本字段，不会上传真实文件二进制；
2. 工具调用不会把 JSON 参数替换进 `/jobs/{run_id}` 这类路径参数。

因此，万悟不应直接使用普通 `/api/v1/files` 和带路径参数的任务接口。项目新增的
`/api/v1/wanwu/*` 全部使用 `POST + application/json`，文件通过临时下载 URL 或 Base64
传入，`run_id` 和 `record_id` 也都放在 JSON 请求体中。普通接口仍为 Vue3 工作台和
其他标准客户端保留。

精简 OpenAPI：

```text
http://host.docker.internal:8000/integrations/wanwu/openapi.json
```

它包含 18 个万悟可稳定调用的工具，并为每个工具固定英文 `operationId`。其中三个数据源工具
负责查询、配置和只读验收；无人值守巡检、SLA 督办、维修后复检和班次简报分别由独立工具负责；
`quick_industrial_diagnosis` 保留为人工上传调试入口。

人工上传调试专用 Schema：

```text
http://host.docker.internal:8000/integrations/wanwu/quick-openapi.json
```

该地址只暴露 `quick_industrial_diagnosis` 一个工具。竞赛主流程和现有辅助智能体使用完整 18 工具
Schema；单工具 Schema 只用于独立的人工上传调试，不应替换、覆盖或重复绑定现有完整工具集。

## 历史调试入口：低调用额度快速诊断

```text
POST /api/v1/wanwu/quick-diagnosis
```

该接口接收文件 URL 或 Base64 后，由时察千机后端依次完成数据画像、主模型异常检测、
四类互补检测器交叉验证、工况分析、根因候选排序、关键词知识检索和运维建议生成。
交叉验证复用主模型结果，只补跑轻量检测器，不会重复整条诊断管线。接口不会调用外部 Chat 或 Embedding 接口，
因此 `model_call_count` 固定为 `0`，不会额外消耗比赛方大模型 QPM。返回的 `presentation` 是
可直接展示给用户的中文摘要，`analysis` 保留结构化证据，便于万悟后续展示或生成卡片。
`automatic_diagnosis` 只保留诊断正文、使用边界和知识来源，不重复嵌套完整算法证据，避免
万悟结果上下文膨胀。
接口还会按文件内容哈希和完整分析配置复用最近一次成功结果；万悟重复提交时返回
`cache_hit=true`，不会重复计算或重复生成工单。

`analysis.detector_validation` 返回各检测器的阈值、异常点数、事件数、与主模型的一致性和
可用的 SKAB 标签指标。当前文件标签只用于离线评价，不参与在线模型切换；主模型与阈值仍由
冻结验证集和设备配置预先确定，避免使用测试数据挑选模型造成数据泄漏。

`analysis.model_selection` 返回任务目标、候选模型适用条件、最终主模型、实际阈值和选择原因。
万悟未指定 `detector` 时默认自动路由；显式传入 `detector` 时保持人工配置。SKAB 独立测试表明
四模型严格多数共识会损失事件召回，因此当前只作为可信度证据，不直接抑制主模型告警。

该接口不属于当前竞赛主流程。`/api/v1/diagnose` 仍保留给 Vue3 调试页和需要高质量自然语言
诊断的受控流程；大模型不可用时也不能影响已经完成的工业分析和工单事实。

## 启动本地分析服务

```powershell
uv run python api_main.py
```

服务地址：`http://127.0.0.1:8000`

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## 人工调试接口协议

### 上传 CSV（仅调试）

```text
POST /api/v1/files
Content-Type: multipart/form-data
字段：file
返回：{"file_id":"...","file_name":"..."}
```

上传成功后还会返回 `sha256` 和 `size_bytes`，数据库据此追踪同一份数据和文件完整性。

### 执行分析

```json
{
  "file_id": "上传接口返回的 file_id",
  "config": {
    "detector": "hybrid",
    "threshold": 5.5,
    "rolling_window": 61,
    "min_event_length": 3
  }
}
```

返回内容包括：

- `data_profile`：数据规模、时间范围、传感器和缺失值；
- `anomaly_events`：异常事件、峰值风险、主导传感器和时间范围；
- `model_selection`：场景驱动的主模型选择、候选排序、实际阈值和标签隔离说明；
- `detector_validation`：多模型交叉验证、共识异常点、模型一致性及失败隔离信息；
- `operating_regimes`：稳定工况分段、过渡强度和异常事件的工况上下文；
- `relationship_diagnostics`：异常前后相关性变化、领先与滞后测点及重点排查链路；
- `root_cause_diagnoses`：候选根因排序、置信度、支持证据、证据缺口和现场验证步骤；
- `historical_case_matches`：当前异常命中的已闭环案例、相似度及原工单来源；
- `work_order_drafts`：按异常事件生成的优先级、处置动作和反馈回写要求；
- `forecast_results`：各传感器未来窗口、方向、预测风险、置信区间和回测误差；
- `risk_alerts`：当前异常与趋势预测形成的结构化预警；
- `recommendations`：处置建议；
- `run_id`：用于日志和后续结果追踪。

### 获取决策证据摘要

```text
POST /api/v1/wanwu/jobs/decision-brief
```

请求体只传成功任务的 `run_id`。该工具复用 PostgreSQL 中的已落库结果，不重新执行算法，
也不调用大模型。返回值将完整结果压缩为 `model_selection`、`cross_validation`、
`trend_risk`、`optimization` 和 `work_order_summary` 五组稳定字段，便于万悟选择器、结束节点
和辅助智能体直接引用。任务尚未成功时返回 `409`，失败任务继续沿用结果接口的 `422`。

`optimization` 中每条建议均保留证据、观察窗口和回退条件，且顶层 `human_gate` 明确要求人工
确认；该接口不提供自动控制或参数下发能力。公开 SKAB 数据会在 `data_source_label` 中标识，
避免把公开数据验证误写为企业现场成效。

`rag_context` 是给万悟知识库节点准备的最小检索输入，只包含测点名、候选原因和证据缺口。
不要把原始 CSV、完整预测数组或整份分析结果传入 RAG。知识库用于补充故障机理、验证步骤和
处置规程，不能替代结构化候选排序或现场确诊。当前摘要还会返回 `retrieval_mode`、命中文档
来源、检索分数和命中数量；`hybrid_embedding` 表示 DashScope Embedding 与关键词混合检索，
`keyword_fallback` 表示向量接口异常时的可解释降级，`not_run` 表示本次任务没有执行自然语言
诊断层检索。这样可以向评委证明 RAG 实际引用了什么，也能说明网络或限流故障没有阻断确定性分析。

`model_audit` 返回本次任务关联的大模型和 Embedding 调用元数据，包括调用类型、模型、状态、耗时
和 Token 统计；永远不保存提示词、回答正文、API Key 或原始 CSV。模型调用也可以通过
`GET /api/v1/model-calls?run_id=...` 单独查询。模型审计是旁路能力，数据库暂时不可用时不会改变
分析结果。

## OpenClaw 式 Skill 与权限

本项目仍只有一个“时察千机工业时序智能体”。五个工作流以 Skill 形式组织，机器可读契约位于
`wanwu/skills/catalog.json`，说明文件位于 `wanwu/skills/README.md`。万悟导入的工具描述会同时
标注“只读查询”或“会产生副作用”。聊天默认只读；数据源配置、立即巡检、主动通知、SLA 督办和
人工复检必须先明确确认，四个已发布周期工作流则属于部署时预授权的后台任务。该确认规则由
`wanwu/agent_prompt.md` 和导入后的工具描述共同约束，不把确认逻辑交给大模型自由发挥。

### 生成班次简报

```text
POST /api/v1/wanwu/reports/shift-brief
```

默认汇总最近 8 小时 PostgreSQL 审计记录，包括任务状态、异常事件数、P1/P2/P3 工单、未闭环
事项、SLA 催办、超时升级、复检结果和通知投递。接口返回结构化字段及可直接展示的
`presentation`，不调用大模型。当前是滚动时间窗口；企业部署后应替换为正式班次日历。

### 自动化验收

`scripts/accept_wanwu_workflows.ps1` 默认只检查后端健康状态、PostgreSQL、18 个工具、万悟网关验证码
接口和四个运行工作流配置。传入 `-RunWorkflows` 才调用已发布工作流，传入 `-InjectSample` 才从
公开 SKAB 向模拟目录投递一个新批次。验收报告位于
`outputs/wanwu_acceptance_report.json`，不包含密钥和 Webhook。

本机一键启动和停止：

```powershell
.\scripts\start_basic_stack.ps1
.\scripts\stop_basic_stack.ps1
```

启动脚本会校验四份 `outputs/wanwu_*_workflow.local.json`，并分别启动无人值守巡检、
SLA 督办、维修后复检、班次简报触发器。后端 PID 与四个触发器 PID 都写入 `outputs/`；
重复启动按 PID 和命令行复用已有进程，停止脚本只停止本项目拥有的进程。

`work_order_drafts` 中的 `record_id` 是数据库全局唯一工单编号。后续万悟工单节点应保存并
传递这个字段，不要只使用可能在不同任务间重复的 `work_order_id`。

### 查询历史任务

```text
GET /api/v1/runs?limit=20&status=success
GET /api/v1/runs/{run_id}
```

列表接口返回任务摘要，详情接口返回算法参数、文件哈希、任务状态、耗时以及完整分析结果。
万悟可以据此搭建“历史诊断记录”页面，也可以在工作流异常后查询任务状态。

### 工单闭环

```text
GET /api/v1/work-orders?status=待确认&limit=50
GET /api/v1/work-orders?run_id=run_xxx
PATCH /api/v1/work-orders/{record_id}
```

现场反馈请求体示例：

```json
{
  "status": "已完成",
  "confirmed_cause": "阀门执行器卡滞",
  "feedback_note": "清理执行器并复测，压力与流量恢复正常",
  "handled_by": "设备运维组"
}
```

允许的状态为：`待确认`、`已确认`、`处理中`、`已完成`、`已关闭`。限制状态集合是为了让
万悟看板能够稳定统计完成率、处置周期和各类根因，而不是产生无法汇总的自由文本状态。

当工单状态为 `已确认`、`已完成` 或 `已关闭`，并填写了 `confirmed_cause` 后，系统会从
原分析结果中提取测点类别、变化方向、主导测点和工况上下文，动态构建历史故障案例。下一次
分析会按这些确定性特征检索相似案例。案例只作为独立证据参与排序，不会直接覆盖机理规则，
且每条结论都保留原 `run_id`、`record_id` 和处置反馈，便于追溯和纠错。

```text
GET  /api/v1/cases
POST /api/v1/wanwu/cases/list
```

### 一站式自动诊断

```text
POST /api/v1/diagnose
```

请求体与 `/api/v1/analyze` 相同。该端点先运行完整工业分析，再根据异常传感器、趋势预警
和关系证据生成确定性候选根因与工单草案，再检索本地工业知识，最后只调用一次项目配置的
DashScope 聊天模型
生成以下结构：

```text
诊断结论 → 关键证据 → 可能原因及来源 → 处置顺序 → 使用边界
```

返回的 `automatic_diagnosis.status` 为 `generated` 时表示 DashScope 聊天模型已生成诊断；为
`fallback` 时表示接口未配置、限流或网络异常，系统已返回不依赖大模型的确定性降级诊断。
两种状态都保留完整工业分析结果，原始 CSV 均不会发送给大模型。

当前 `root_cause_diagnoses` 使用内置通用故障模式，并非企业设备专属规则。万悟展示时必须
保留候选置信度、证据缺口和使用边界，不能将其改写为已经确诊的设备故障。后续企业手册和
维修工单接入后，设备专属规则仍复用同一 API 字段。

当前工况模块只提供解释证据，不建议在万悟条件节点中直接把“工况切换期事件”过滤掉。
固定 SKAB 验证表明简单抑制会降低事件召回，平台应优先提示人工核对控制指令和负载变化。

### 多模型交叉验证

```text
POST /api/v1/model-compare
```

请求体可以传入 `file_id`、`detectors` 和可选 `config`：

```json
{
  "file_id": "上传接口返回的 file_id",
  "detectors": ["mad", "isolation_forest", "pca_reconstruction", "hybrid"],
  "config": {"threshold": 5.5}
}
```

返回每个模型的点级 F1、事件级 F1、PR-AUC、检测延迟和误报事件。PCA 重构检测器用于
识别多传感器健康关系被破坏的异常，供万悟工作流进行结果
比较和解释。模型比较结果不能单独作为故障确诊依据。

### 健康模型仓库

```text
GET /api/v1/models
```

接口返回已持久化 AutoEncoder 健康模型的脱敏元数据，包括模型 ID 前缀、格式版本、训练时间、
传感器数量、窗口长度和文件大小，不返回完整健康数据指纹、本地路径或模型二进制。万悟可以
在工作流开始时查询模型状态，判断目标设备是否已有可复用健康模型。

### 预测模型比较

```text
POST /api/v1/forecast-compare
```

请求示例：

```json
{
  "file_id": "上传接口返回的 file_id",
  "sensors": ["Pressure", "Current"],
  "horizon": 30,
  "models": ["persistence", "moving_average", "linear_trend", "lag_ridge", "time_frequency_ridge"]
}
```

接口对每个传感器按时间顺序滚动回测，返回候选模型 MAE、RMSE、MAPE、最优模型、未来
预测曲线、95% 区间、频域特征和预测可信度。万悟负责把这些结构化证据组织成面向运维人员
的解释，不负责重新计算原始时序。

## 万悟工作流建议

### 当前竞赛主流程

现有配置由一个手动数据源配置工作流、四个周期工作流和一个辅助智能体组成，不再创建人工上传
工作流，也不需要把工作流转换为 Skill：

| 入口 | 触发方式 | 核心工具 | 是否依赖大模型 |
| --- | --- | --- | --- |
| 工业数据源接入配置 | 首次配置或修改时手动运行 | `configure_industrial_data_source`、`verify_industrial_data_source` | 否 |
| 无人值守工业巡检 | 每 60 秒 | `run_unattended_industrial_cycle`、状态查询、决策摘要、主动告警 | 否 |
| 工单 SLA 督办 | 每 300 秒 | `run_industrial_sla_cycle` | 否 |
| 维修后自动复检 | 每 300 秒 | `run_industrial_reinspection_cycle` | 否 |
| 工业班次简报 | 每 28800 秒 | `generate_industrial_shift_brief` | 否 |
| 辅助智能体 | 人员按需提问 | 只读查询、明确确认后的工单操作、知识库 | 仅解释和问答时使用 |

无人值守巡检画布固定为：

```text
开始
  -> run_unattended_industrial_cycle
  -> 按 cycle_status 分支
  -> analysis_queued 时循环 get_industrial_analysis_status
  -> success 时调用 get_industrial_decision_brief
  -> dispatch_industrial_alerts
  -> 终态输出 presentation
```

`no_data`、`partial_failure` 和 `busy` 都应输出明确状态并等待下一周期，不得静默伪装为正常分析。
主工作流读取的是监测目录或 HTTP 数据源发现的新批次，不要求用户在万悟对话框上传文件。

无人值守主工作流只负责新数据发现、分析、证据读取和主动告警；不要把 SLA 和复检塞进这条链路。
工单 SLA 督办工作流单独调用 `run_industrial_sla_cycle`，维修后自动复检工作流单独调用
`run_industrial_reinspection_cycle`。两个工具都不调用大模型：前者检查未接单工单并按时限催办或升级，
后者检查状态为 `待验证` 的工单是否产生同一 `source_id` 的处置后新任务。原异常主导测点不再出现
才自动完成，仍出现则退回处理中；没有新数据时保持等待。通知以“工单 + 接收人 + 渠道 + 通知类型 +
升级层级”幂等，万悟重试不会重复发送同阶段消息。两个工具均返回 `presentation` 短文本，消息输出
节点直接引用该字段，不需要经过变量聚合节点或大模型二次改写。

### 辅助解释、RAG 与模型日志

辅助智能体与五个工作流属于同一个“时察千机工业时序智能体”能力体系，不需要创建第二个智能体。
它可以查询任务、结果、工单和历史案例，并基于万悟知识库补充机理、验证步骤和规程来源。RAG 和
聊天模型只能解释后端已经形成的结构化证据，不能修改异常事件、风险等级、工单编号或复检结论。

在对话中调用会产生副作用的工具前必须得到用户明确确认；后台周期工作流视为预授权。模型调用日志
只记录提供方、模型、状态、耗时、Token 和输入输出规模，不保存 API Key、完整提示词、回答正文或
原始 CSV。主工作流即使遇到模型限流或网络错误也必须继续完成确定性分析和业务闭环。

人工上传、`quick_industrial_diagnosis`、`submit_industrial_analysis` 和 `/api/v1/diagnose` 仍可用于
开发排错，但不得出现在竞赛主流程图、答辩主演示步骤或无人值守能力表述中。

## 导入 OpenAPI

启动服务后，万悟应导入精简协议：

```text
http://host.docker.internal:8000/integrations/wanwu/openapi.json
```

也可以在浏览器打开该地址后导入万悟“资源库 → 自定义工具”。OpenAPI 中的
`servers` 来自 `.env` 的 `API_PUBLIC_BASE_URL`。时察千机和万悟都运行在 Docker 且加入
`wanwu-net` 时使用 `http://shichi-qianji-api:8000`；只有时察千机直接运行在 Windows
时才使用 `http://host.docker.internal:8000`。在线万悟必须改成公网 HTTPS 地址后重启 API。

## 暂无部署环境时怎么做

没有公网服务器不会阻止代码继续开发和验证。先启动本地 API：

```powershell
uv run python api_main.py
uv run shichi-qianji-wanwu-check
```

第二条命令会检查健康状态、完整 18 个万悟工具、快速单工具 Schema 和 OpenAPI 服务地址，并同时导出：

```text
outputs/wanwu_openapi.json
outputs/wanwu_quick_openapi.json
```

获得任意支持 Docker Compose 的服务器后执行：

```powershell
docker compose -f compose.wanwu.yml up -d --build
```

正式在线接入仍必须具备两个外部条件：一个在线万悟能够访问的公网 HTTPS 地址，以及在万悟
页面创建自定义工具和工作流的权限。代码无法凭空生成平台账号或公网服务器，但已经把获得
这些条件后的部署和导入工作压缩为固定命令和固定 Schema。

不要将完整 CSV 直接放进大模型提示词。大模型只读取分析 API 返回的结构化摘要和知识库证据。

## Ubuntu 服务器部署

老师提供的 Ubuntu 服务器上，万悟和时察千机应加入同一个外部 Docker 网络：

```bash
docker network create wanwu-net 2>/dev/null || true
```

时察千机 `.env` 推荐至少确认以下配置：

```dotenv
API_PUBLIC_BASE_URL=http://shichi-qianji-api:8000
API_BIND_ADDRESS=127.0.0.1
API_HOST_PORT=8000
SHICHI_OUTPUT_DIR=/data/shichi-qianji/outputs
SKAB_HOST_DIR=/data/datasets/SKAB
WANWU_ALLOW_PRIVATE_FILE_URLS=false
WANWU_ALLOWED_FILE_HOSTS=
INDUSTRIAL_API_KEY=请生成独立随机密钥
```

`API_BIND_ADDRESS=127.0.0.1` 使主机端口只允许服务器本机和 SSH 隧道访问；万悟容器仍可通过
`http://shichi-qianji-api:8000` 调用。不要为了方便直接把 API 暴露到 `0.0.0.0`。

万悟文件节点返回的临时 URL 可能指向 Docker 内部文件服务。先在工作流运行详情中确认
`file_url` 的主机名，再把该主机名加入 `WANWU_ALLOWED_FILE_HOSTS`，例如：

```dotenv
WANWU_ALLOWED_FILE_HOSTS=nginx-wanwu,minio-wanwu
```

只填写实际出现的容器主机名，不要填写整个内网网段，也不要把 `127.0.0.1` 当成万悟容器。
修改 `.env` 后重建时察千机 API，并执行服务器只读检查：

```bash
docker compose -f compose.wanwu.yml up -d --build
bash scripts/check_wanwu_server.sh
```

快速诊断会先按文件内容哈希和分析参数查询缓存。万悟因网络抖动重复提交同一文件时，系统直接
返回原成功结果，不再重复保存 CSV、执行分析或生成工单，避免服务器磁盘被演示重试持续占用。

## 比赛接口额度

比赛方规定聊天、Embedding 等每个接口每分钟最多调用 5 次。项目已经在
`outputs/rate_limits/` 中按接口维护跨进程请求间隔，避免 Vue3、万悟、FastAPI 和命令行
同时调用时互相抢占额度。`/api/v1/diagnose` 只使用一次聊天请求；多轮工具 Agent 通常包含
两次以上聊天请求，因此仅适合用户后续追问。

四条周期工作流不配置大模型节点：工业数值计算只调用时察千机 API，`presentation` 直接进入
终态输出。知识检索和自然语言解释只由辅助智能体按需触发。若平台侧还有其他应用共用同一
API Key，它们的请求不会进入本项目的本地限流记录，需要在万悟网关侧再配置全局限流。

## 安全约束

当 `.env` 配置 `INDUSTRIAL_API_KEY` 后，万悟 API 节点需要增加请求头：

```text
X-API-Key: 你的服务密钥
```

服务只接受 `file_id`，不接受 `E:\`、`..\` 或任意绝对路径。上传文件保存在 `outputs/api_uploads/`，
运行日志保存在 `outputs/logs/runs.jsonl`，任务、结果和工单保存在
PostgreSQL。正式部署时应将上传目录迁移到对象存储，并为 PostgreSQL 配置备份、最小权限账户，
同时增加文件过期清理、租户隔离和 HTTPS。

## 现阶段边界

当前预测器已形成最近值、指数平滑、局部线性、滞后岭回归和时频增强岭回归五模型体系，
并具备滚动回测选择、95% 区间和模型分歧评估。它属于可解释工程模型底座，竞赛最终版本
仍需在固定 SKAB 划分上增加跨文件训练的深度时序模型、消融实验和企业数据验证，并将设备
说明书、告警规则、维修工单迁移至万悟知识库。
