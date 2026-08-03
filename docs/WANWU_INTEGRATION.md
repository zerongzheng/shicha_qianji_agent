# 元景万悟接入说明

本项目与 `E:\大学课程\竞赛\wanwu` 保持两个独立工程：万悟负责智能体、工作流、知识库和模型编排，
时察千机负责工业 CSV 的确定性计算、预测、预警和结构化输出。不要把万悟源码复制进本项目。

## 调用链

```text
万悟 Agent / Workflow
    -> 上传 CSV 到时察千机 POST /api/v1/files
    -> 获得 file_id
    -> 调用时察千机 POST /api/v1/diagnose
    -> Python 完成分析与知识检索，GLM-5 单次生成诊断
    -> 万悟直接展示异常证据、趋势预警和处置顺序
    -> 查询 GET /api/v1/work-orders 展示待办工单
    -> PATCH /api/v1/work-orders/{record_id} 回写现场结果
```

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
- `operating_regimes`：稳定工况分段、过渡强度和异常事件的工况上下文；
- `relationship_diagnostics`：异常前后相关性变化、领先与滞后测点及重点排查链路；
- `root_cause_diagnoses`：候选根因排序、置信度、支持证据、证据缺口和现场验证步骤；
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

比赛演示优先使用低调用额度工作流：

1. 文件输入节点接收用户 CSV；
2. API 节点调用 `/api/v1/files`；
3. API 节点把 `file_id` 传给 `/api/v1/diagnose`；
4. 展示 `root_cause_diagnoses`、`work_order_drafts` 和结构化风险证据；
5. 使用 `automatic_diagnosis.diagnosis` 作为面向运维人员的解释文本；
6. 需要模型比选时再调用 `/api/v1/model-compare` 或 `/api/v1/forecast-compare`。
7. 将 `work_order_drafts[].record_id` 传给工单卡片；现场确认后调用 PATCH 接口回写结果。

若比赛平台要求使用万悟知识库和大模型节点，可改用 `/api/v1/analyze`，再由万悟完成知识
检索和最终解释。两种模式不要在同一次请求中重复调用大模型。

不要将完整 CSV 直接放进大模型提示词。大模型只读取分析 API 返回的结构化摘要和知识库证据。

## 比赛接口额度

比赛方规定聊天、Embedding 等每个接口每分钟最多调用 5 次。项目已经在
`outputs/rate_limits/` 中按接口维护跨进程请求间隔，避免 Streamlit、FastAPI 和命令行
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
`outputs/shichi_qianji.db`。正式部署时应将上传目录迁移到对象存储，将 SQLite 迁移到
PostgreSQL，并增加文件过期清理、租户隔离和 HTTPS。

## 现阶段边界

当前预测器已形成最近值、指数平滑、局部线性、滞后岭回归和时频增强岭回归五模型体系，
并具备滚动回测选择、95% 区间和模型分歧评估。它属于可解释工程模型底座，竞赛最终版本
仍需在固定 SKAB 划分上增加跨文件训练的深度时序模型、消融实验和企业数据验证，并将设备
说明书、告警规则、维修工单迁移至万悟知识库。
