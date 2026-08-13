# 元景万悟接入说明

本项目与 `E:\大学课程\竞赛\wanwu` 保持两个独立工程：万悟负责智能体、工作流、知识库和模型编排，
时察千机负责工业 CSV 的确定性计算、预测、预警和结构化输出。不要把万悟源码复制进本项目。

## 调用链

```text
万悟 Agent / Workflow
    -> 文件节点获得 CSV 临时 URL，或将小文件转换为 Base64
    -> 比赛演示：POST /api/v1/wanwu/quick-diagnosis，一次返回确定性分析和中文摘要
    -> 正式工程：POST /api/v1/wanwu/jobs/submit，保存 run_id 后轮询状态和结果
    -> 万悟直接展示异常证据、趋势预警和处置顺序
    -> 查询 GET /api/v1/work-orders 展示待办工单
    -> PATCH /api/v1/work-orders/{record_id} 回写现场结果
    -> 已确认根因自动沉淀为历史案例
    -> 下一次相似事件检索案例并参与候选根因排序
```

小文件本地调试仍可直接调用 `/api/v1/analyze` 或 `/api/v1/diagnose`。万悟比赛演示优先使用
快速诊断接口，避免低 QPM 模型被多轮工具编排消耗；正式部署再使用异步链路。

## 为什么增加万悟专用接口

对本地 `E:\大学课程\竞赛\wanwu` 源码检查后确认，当前 OpenAPI 工具执行器有两个兼容限制：

1. `multipart/form-data` 只会写普通文本字段，不会上传真实文件二进制；
2. 工具调用不会把 JSON 参数替换进 `/jobs/{run_id}` 这类路径参数。

因此，万悟不应直接使用普通 `/api/v1/files` 和带路径参数的任务接口。项目新增的
`/api/v1/wanwu/*` 全部使用 `POST + application/json`，文件通过临时下载 URL 或 Base64
传入，`run_id` 和 `record_id` 也都放在 JSON 请求体中。普通接口仍为 Vue3 工作台、
Streamlit 备用页面和其他标准客户端保留。

精简 OpenAPI：

```text
http://host.docker.internal:8000/integrations/wanwu/openapi.json
```

它包含八个万悟可稳定调用的工具，并为每个工具固定英文 `operationId`。其中
`quick_industrial_diagnosis` 是低调用额度演示入口。

比赛演示专用 Schema：

```text
http://host.docker.internal:8000/integrations/wanwu/quick-openapi.json
```

该地址只暴露 `quick_industrial_diagnosis` 一个工具。创建比赛演示智能体时应优先导入这个地址；
完整八工具 Schema 留给后续正式工作流，不要把两份 Schema 同时绑定到同一个演示智能体。

## 低调用额度快速诊断

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

这不是删除大模型能力，而是把比赛现场的第一轮结果交给确定性算法完成；`/api/v1/diagnose`
仍保留给 Streamlit 和需要高质量自然语言诊断的本地流程。

## 启动本地分析服务

```powershell
uv run python api_main.py
```

服务地址：`http://127.0.0.1:8000`

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## 接口协议

### 上传 CSV

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
和关系证据生成确定性候选根因与工单草案，再检索本地工业知识，最后只调用一次 GLM-5
生成以下结构：

```text
诊断结论 → 关键证据 → 可能原因及来源 → 处置顺序 → 使用边界
```

返回的 `automatic_diagnosis.status` 为 `generated` 时表示 GLM-5 已生成诊断；为
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

### 比赛演示不要使用普通智能体链路

普通智能体的执行方式是“ChatModel 判断 -> 工具 -> ChatModel 组织最终回复”。即使
`quick_industrial_diagnosis` 已经返回 `model_call_count=0`，智能体仍可能在工具成功后
再次请求平台 ChatModel；截图中 `node_1, ChatModel` 的 429 就属于这次二次请求，而不是
时察千机 API 失败。

比赛演示建议创建一个**工作流**，将最终回复交给结束节点：

```text
开始节点（File 类型 CSV）
    -> 工具节点（quick_industrial_diagnosis）
    -> 结束节点（返回变量或返回文本）
```

工作流中不要放大模型节点，也不要把工作流再包装成普通智能体后让智能体负责二次总结。
这样一次演示只产生平台入口所需的最少模型调用；工具返回后的最终输出由结束节点直接完成。

#### 工作流页面配置

1. 在万悟进入“工作流”，新建工作流，不要进入“智能体”页面继续修改当前智能体。
2. 开始节点添加参数 `industrial_file`，类型选择 `File`，描述填写“工业时序 CSV 文件”，设为必填。
3. 从自定义工具中添加 `quick_industrial_diagnosis` 工具节点。
4. 工具节点参数映射建议如下：
   - `file_url`：引用开始节点 `industrial_file` 的文件 URL 输出；
   - `file_name`：引用开始节点文件名输出，或填写固定值 `industrial_sample.csv`；
   - `file_base64`：留空，不要与 `file_url` 同时传入；
   - `config`：留空，使用时察千机默认参数。
5. 添加结束节点，选择“返回变量”模式，输出变量引用工具节点的：
   - `presentation`：面向评委的中文诊断摘要；
   - `analysis`：结构化证据，可用于卡片展示；
   - `automatic_diagnosis`：规则诊断和使用边界；
   - `cache_hit`：是否复用已有结果。
6. 不添加“大模型节点”。结束节点文档明确支持直接返回上游节点的结构化变量。
7. 点击“试运行”，上传 CSV，确认工具节点成功且结束节点能直接返回结果。
8. 试运行成功后发布工作流。之后通过工作流入口或发布后的工作流 API 调用。

如果工作流界面不允许直接把 File 类型映射为 `file_url`，改用开始节点的字符串参数
`file_url`，让文件节点先提供平台可访问的临时下载地址，再映射到工具节点的 `file_url`。
不要把 Windows 路径 `E:\\...` 传给服务端。

比赛演示优先使用异步工作流，避免工业分析超过万悟 HTTP 节点超时时间：

1. 文件输入节点接收用户 CSV，并取得平台临时下载 URL；小文件也可传 Base64；
2. 工具节点调用 `submit_industrial_analysis`，保存返回的 `run_id`；
3. 循环节点调用 `get_industrial_analysis_status`；
4. 选择器判断 `job_status`：`queued/running` 继续等待，`success` 进入结果节点，`failed` 展示错误，`cancelled` 结束流程；
5. 工具节点调用 `get_industrial_analysis_result`；
6. 展示 `root_cause_diagnoses`、`work_order_drafts` 和结构化风险证据；
7. 由一个万悟大模型节点生成面向运维人员的解释文本；
8. 将 `work_order_drafts[].record_id` 传给工单卡片，现场确认后调用 `update_industrial_work_order` 回写结果。
9. 调用 `list_industrial_feedback_cases` 展示已沉淀案例；后续分析结果中的 `historical_case_matches` 会自动引用相似案例。

异步任务请求示例：

```json
{
  "file_url": "文件节点返回的临时 HTTPS 下载地址",
  "file_name": "industrial_sample.csv",
  "operation": "analyze",
  "config": {
    "detector": "time_frequency_relation",
    "threshold": 4.5,
    "rolling_window": 61,
    "min_event_length": 3
  }
}
```

提交成功返回 HTTP 202：

```json
{
  "status": "queued",
  "run_id": "run_xxxxxxxxxxxx",
  "file_id": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "file_source": "url",
  "operation": "analyze",
  "status_url": "/api/v1/jobs/run_xxxxxxxxxxxx",
  "result_url": "/api/v1/jobs/run_xxxxxxxxxxxx/result"
}
```

建议循环间隔设置为 2 至 5 秒，并设置最大循环次数。不要毫秒级连续查询 PostgreSQL。
用户撤回分析或工作流达到最大等待次数时，可调用 `cancel_industrial_analysis` 取消仍在
排队的任务。已经进入 `running` 的计算不会被强制中断，接口会返回 HTTP 409，此时应继续
查询最终状态，避免算法仍在运行而数据库被错误标记为取消。

若比赛平台要求使用万悟知识库和大模型节点，可改用 `/api/v1/analyze`，再由万悟完成知识
检索和最终解释。两种模式不要在同一次请求中重复调用大模型。

新的异步工作流中将 `operation` 设为 `analyze`，再使用一个万悟大模型节点；若设为
`diagnose`，Python 会自行调用 GLM-5，万悟只负责展示，两种模式同样不要叠加。

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

第二条命令会检查健康状态、完整八个万悟工具、快速单工具 Schema 和 OpenAPI 服务地址，并同时导出：

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
`outputs/rate_limits/` 中按接口维护跨进程请求间隔，避免 Vue3、Streamlit、FastAPI 和命令行
同时调用时互相抢占额度。`/api/v1/diagnose` 只使用一次聊天请求；多轮工具 Agent 通常包含
两次以上聊天请求，因此仅适合用户后续追问。

万悟工作流侧也应减少不必要的大模型节点：工业数值计算只调用时察千机 API，知识检索按需
触发，最终解释集中在一个大模型节点完成。若平台侧还有其他应用共用同一 API Key，它们的
请求不会进入本项目的本地限流记录，需要在万悟网关侧再配置全局限流。

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
