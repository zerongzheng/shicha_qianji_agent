# 万悟无人值守巡检工作流配置

## 一、导入工具

启动时察千机 FastAPI 后，在万悟“资源库 -> 自定义工具”导入：

```text
http://host.docker.internal:8000/integrations/wanwu/openapi.json
```

若 `.env` 配置了 `INDUSTRIAL_API_KEY`，在工具鉴权中设置 `X-API-Key`。不要把企业微信
Webhook 配到万悟，通知工具只接收 `run_id`，密钥由时察千机后端保管。

后端升级后需要在万悟重新导入或刷新完整 OpenAPI。可用 API 应显示 19 个，其中数据源管理
工具为 `list_industrial_data_sources`、`configure_industrial_data_source` 和
`verify_industrial_data_source`；工单售后能力已经拆成两个独立工具：
`run_industrial_sla_cycle` 和 `run_industrial_reinspection_cycle`；决策证据工具为
`get_industrial_decision_brief`，班次简报工具为 `generate_industrial_shift_brief`。

## 二、创建数据源接入配置工作流

名称建议填写“时察千机工业数据源接入配置”。开始节点添加：

| 参数 | 类型 | 示例值 |
| --- | --- | --- |
| `source_id` | String | 更新时填写；新建时留空 |
| `name` | String | SKAB 演示实时目录 |
| `source_type` | String | directory |
| `endpoint` | String | `E:\大学课程\竞赛\shicha_qianji_agent\outputs\demo_feed\skab_valve1` |
| `interval_seconds` | Number | 30 |
| `enabled` | Boolean | true |
| `initial_scan_mode` | String | new_only |

画布连接：

```text
开始
 -> configure_industrial_data_source
 -> verify_industrial_data_source
 -> 结束：返回 source_id、reachable、CSV 数量和 message
```

配置节点的同名输入引用开始节点；验证节点的 `source_id` 引用配置节点输出
`source.source_id`。配置写入时察千机 PostgreSQL，万悟不保存 HTTP 鉴权头、企业微信
Webhook 等密钥。目录路径由时察千机后端访问，不要求万悟容器直接挂载 Windows 目录。

当前万悟工作流只保留一个结束节点即可。成功和失败都通过 `reachable`、`message` 等
结构化输出区分，失败时不需要再增加第二个结束节点。

需要查看已有编号时，可单独运行 `list_industrial_data_sources`，设置
`enabled_only=true`。更新已有数据源时必须传入列表返回的 `source_id`，避免误建重复配置。

## 三、创建无人值守巡检工作流

名称建议填写“时察千机无人值守工业巡检”。开始节点添加：

| 参数 | 类型 | 默认值 | 用途 |
| --- | --- | --- | --- |
| `source_id` | String | 空 | 空值轮询到期数据源；演示时可固定数据源编号 |
| `max_sources` | Integer | 1 | 单轮固定处理一个数据源，保证任务完整追踪 |

画布按以下顺序连接：

```text
开始
 -> run_unattended_industrial_cycle
 -> 选择器：cycle_status == analysis_queued
    -> 循环 get_industrial_analysis_status(primary_run_id)
    -> 选择器：job_status
       -> queued/running：等待 2 至 5 秒后继续查询
       -> success：get_industrial_decision_brief(run_id)
          -> dispatch_industrial_alerts(run_id)
          -> 结束：返回本轮采集、决策证据和通知状态
       -> failed/cancelled：结束并返回任务错误
 -> no_data：结束并返回“本轮无新数据”
 -> partial_failure/busy：结束并返回巡检状态，等待下一周期重试
```

无人值守巡检工作流只负责“发现新数据 -> 分析 -> 读取证据 -> 主动告警”。SLA 督办和维修后复检
由后面的两个独立定时工作流负责，不能继续塞进这条主链，否则一次数据巡检会同时触发多个售后动作。

## 四、创建工单 SLA 督办工作流

名称建议填写“时察千机工单 SLA 督办”。该工作流由万悟定时触发，不依赖用户上传文件或发送消息：

```text
开始（max_work_orders=100，dry_run=false）
 -> run_industrial_sla_cycle
 -> 消息输出（直接引用 `run_industrial_sla_cycle.presentation`）
```

在当前万悟画布中，消息输出节点就是该工作流的终态输出：不要再把它接到“变量聚合”或普通
“结束”节点，也不要把工具的 `actions` 数组直接拼进消息。若你的万悟版本强制显示结束节点，
按画布能够保存的终态出口配置，但消息正文仍只引用工具节点的 `presentation`。

工具自动完成：

1. 按 P1/P2/P3 SLA 检查尚未接单的工单；
2. 到期后催办，继续超时则升级到生产负责人；
3. 使用通知幂等记录，避免同一工单同一升级层级被重复推送。

建议频率：校赛演示每 1 至 5 分钟；正式企业场景按 P1/P2/P3 的 SLA 约定设置。第一次配置可把
`dry_run` 设为 `true`，确认候选动作后再改为 `false`。

## 五、创建维修后自动复检工作流

名称建议填写“时察千机维修后自动复检”。该工作流由万悟定时触发，不需要人工重新上传复测文件：

```text
开始（max_work_orders=100，dry_run=false）
 -> run_industrial_reinspection_cycle
 -> 消息输出（直接引用 `run_industrial_reinspection_cycle.presentation`）
```

工具自动完成：

1. 查找状态为“待验证”的工单；
2. 等待同一数据源产生维修后的成功分析批次；
3. 原异常主导测点消失时标记复检通过并完成工单；
4. 原异常主导测点仍出现时退回“处理中”并再次主动通知；
5. 没有新批次时保持等待，不把“没有数据”误判为设备恢复。

该工作流不调用大模型、不直接写 PLC 或控制参数，所有优化建议仍需人工确认。

## 六、配置现场处置与复检

现场人员在 Vue3 运维看板或万悟工单工具中填写确认根因和处置反馈，再把工单状态改为
`待验证`。系统会记录复检计划时间；之后无需人工上传复测文件，也无需在对话框下达指令。
同一数据源的新批次进入下一轮自动分析后，独立的复检工作流会使用新任务完成确定性复检。

关键变量映射：

| 下游输入 | 上游输出 |
| --- | --- |
| 状态工具 `run_id` | 巡检工具 `primary_run_id` |
| 决策摘要工具 `run_id` | 巡检工具 `primary_run_id` |
| 通知工具 `run_id` | 巡检工具 `primary_run_id` |

决策摘要节点不调用大模型，也不重新计算分析。它直接读取已落库任务结果，并显式输出：

- `model_selection`：主模型、任务目标、实际阈值、候选数量和选择依据；
- `cross_validation`：参与核验的模型、模型数量、一致性和交叉验证结论；
- `trend_risk`：预测测点数、最高风险、有限告警摘要和滚动回测结果；
- `optimization`：带证据、观察窗口、回退条件和人工闸门的优化建议；
- `work_order_summary`：本次工单数量、最高优先级和全局唯一工单编号。
- `rag_context`：测点、候选原因和证据缺口组成的最小知识库检索输入。

结束节点和消息输出节点优先展示 `get_industrial_decision_brief.presentation`。该字段已压缩为数据
来源、风险等级、异常摘要、模型依据、工单状态、处置建议和安全边界，适合直接用于万悟试运行
和竞赛演示，不应再把 `model_selection`、`trend_risk` 等嵌套 JSON 直接拼接到消息中。公开 SKAB
运行会明确标注“公开 SKAB 验证数据”，不得把该结果表述为企业现场收益。优化建议只能进入人工
确认或工单，不能由工作流直接写入 PLC、控制器或设备参数。

若需要在通知工具后补充企业微信发送数量，保留一个代码节点即可。万悟 Python 代码节点必须使用
`async def main(args: Args) -> Output`，输入通过 `args.params` 读取，不能直接引用上游变量。输出
参数只保留字符串 `output`，可使用以下代码：

```python
async def main(args: Args) -> Output:
    params = args.params
    presentation = str(params.get("presentation") or "巡检已完成，暂无可展示摘要。")
    sent_count = params.get("sent_count", 0)
    failed_count = params.get("failed_count", 0)

    output = (
        presentation
        + chr(10)
        + f"通知结果：已发送 {sent_count} 条，失败 {failed_count} 条。"
    )
    return {"output": output}
```

该代码节点只需声明三个输入：`presentation` 引用决策摘要的同名字段，`sent_count` 和
`failed_count` 引用 `dispatch_industrial_alerts` 的对应字段；输出 `output` 类型设为 String。

自动巡检主链路不必再调用 `get_industrial_analysis_result`。完整结果包含预测曲线和较多事件明细，
保留给辅助智能体或人工详情查询；主工作流使用决策摘要即可完成分支、知识检索、通知和结果展示。

若在万悟增加知识库节点，只把决策摘要的 `rag_context.query` 作为检索问题，并把
`sensor_terms`、`candidate_causes` 和 `evidence_gaps` 作为上下文。知识库返回结果用于补充故障
机理、验证步骤和处置规程，不得覆盖确定性候选根因排序。原始 CSV、完整预测曲线和整份分析
JSON 不进入 RAG 节点。

工作流不需要大模型节点。异常检测、预测、根因候选和工单路由均为确定性工具能力；这样不会
因大模型限流中断自动巡检。需要自然语言解释时，由单独的辅助智能体按需查询结果。

辅助解释智能体的工具调用方式：

```text
用户询问某次已完成任务
 -> 读取已有 run_id
 -> explain_industrial_run(run_id，可选 question)
 -> 直接展示 presentation，或根据 knowledge_sources 和 model_audit 展开证据
```

`explain_industrial_run` 不接受 CSV，不重新运行异常检测，也不触发告警、工单或设备控制。
它只对已落库的结构化结果执行一次知识库检索和受控解释，并将脱敏的 Embedding/聊天模型调用
日志按同一 `run_id` 保存。解释失败时返回确定性回退文本，不能改变原分析任务状态。

## 七、创建班次简报工作流

名称建议填写“时察千机工业班次简报”。该工作流与无人值守巡检分开发布，由万悟或外部调度
每 8 小时、交接班前或每日固定时间触发一次：

```text
开始（hours=8，max_records=200）
 -> generate_industrial_shift_brief
 -> 结束：返回 presentation、run_summary、work_order_summary、aftercare_summary、
          notification_summary、unresolved_items 和 next_action
```

工具会从 PostgreSQL 审计记录汇总任务成功/失败、异常事件、P1/P2/P3 工单、未闭环风险、
SLA 催办、超时升级、维修复检和通知投递，不调用大模型。`presentation` 已可直接推送或展示；
若答辩需要更自然的行文，可在结束前增加一个低频模型节点，只润色 `presentation`，不得修改
任何数字、工单编号、风险等级和使用边界。

当前使用滚动小时窗口模拟班次。企业接入后应将 `hours` 与正式班次日历、交接班时间和组织
架构对齐。

发布工作流后，将以下模板复制为 `outputs/` 下的本地配置，并分别填写已发布的工作流 UUID：

```text
autonomous_workflow.example.json -> wanwu_autonomous_workflow.local.json
sla_workflow.example.json         -> wanwu_sla_workflow.local.json
reinspection_workflow.example.json -> wanwu_reinspection_workflow.local.json
shift_brief_workflow.example.json -> wanwu_shift_brief_workflow.local.json
```

前三个默认共用 `WANWU_WORKFLOW_API_KEY`，班次简报默认使用
`WANWU_SHIFT_BRIEF_API_KEY`。若平台为每个工作流生成独立密钥，只修改本地 JSON 的
`api_key_env` 和 `.env` 中对应的变量名；不要把真实密钥写进 JSON。

公开 SKAB 演示时，可以先处置一张工单并改为 `待验证`，再运行一次
`simulate_skab_live_feed.ps1 -RunOnce` 投递下一份公开样本。演示中应称为“闭环机制验证”，
不能把公开样本结果表述为企业维修成效。

## 八、发布并取得调用参数

1. 试运行工作流，确认无新数据分支和新数据分支均可结束。
2. 点击“发布”，在发布配置中创建 OpenAPI。
3. 记录工作流 UUID 和 API Key。
4. 按上面的四个模板复制本地配置文件并填写 UUID；这些路径已被 `.gitignore` 忽略。
5. 将 API Key 只写入 `.env` 或系统环境变量，不要写入 JSON。

万悟官方工作流调用地址为：

```text
POST /service/api/openapi/v1/workflow/run
Authorization: Bearer {API Key}
```

## 九、启动无人值守触发器

本机竞赛环境优先使用一键启动脚本。电脑重启后直接执行；Docker Desktop 未运行时会被
自动启动：

```powershell
cd "E:\大学课程\竞赛\shicha_qianji_agent"
.\scripts\start_basic_stack.ps1
```

它会依次检查 PostgreSQL、启动万悟基础容器、时察千机后端和四个隐藏触发器，并通过
PID 文件与进程命令行避免重复启动。

答辩需要同时启动 Vue3 运维工作台时改用：

```powershell
.\scripts\start_basic_stack.ps1 -IncludeFrontend
```

前端使用独立 PID 文件管理，仍由同一个停止脚本回收。停止时执行：

```powershell
.\scripts\stop_basic_stack.ps1
```

该命令停止后端、四个触发器和万悟基础容器，不删除 PostgreSQL 数据、Docker 卷或有效输出报告。
下面的命令只用于需要在前台观察触发器输出时单独运行。

```powershell
$env:WANWU_WORKFLOW_API_KEY = "本机密钥"
.\wanwu\scripts\trigger_wanwu_workflow.ps1 `
  -ConfigPath ".\outputs\wanwu_autonomous_workflow.local.json"
```

脚本每隔配置的秒数调用一次万悟工作流。停止时在终端按 `Ctrl+C`。正式部署可改由 Windows
任务计划程序、Linux cron 或企业调度平台按分钟执行 `-RunOnce`，业务流程无需改变。

## 十、比赛展示

当前本地基础版万悟网页的工作流画布主要提供试运行、发布和历史版本；官方操作文档没有承诺在
工作流详情页提供可长期查询的逐次运行记录。运行证据应分层展示：万悟统计看板证明工作流调用
发生过，API 统计详情（若当前版本提供）查看请求/响应，Vue3 看板展示 FastAPI 返回的详细算法
执行轨迹；不要把右上角“历史版本”误认为运行记录。

1. 在万悟运行数据源接入配置工作流，确认 `reachable=true` 并取得 `source_id`；
2. 使用项目脚本把下一份公开 SKAB 样本投递到独立模拟目录，模拟设备产生新批次：

   ```powershell
   cd "E:\大学课程\竞赛\shicha_qianji_agent"
   .\scripts\simulate_skab_live_feed.ps1 -RunOnce
   ```

   自动数据源目录应配置为 `E:\大学课程\竞赛\shicha_qianji_agent\outputs\demo_feed\skab_valve1`。
   持续演示时可省略 `-RunOnce`，脚本默认每隔 60 秒投递下一份样本。
   录制视频时若不希望额外等待无人值守触发器的 60 秒周期，可显式立即触发一次已发布工作流：

   ```powershell
   .\scripts\simulate_skab_live_feed.ps1 -RunOnce -TriggerAutonomousWorkflow
   ```

   该命令仍完整经过万悟画布和后端闭环，并可能实际发送企业微信通知；它只消除周期调度空等，
   不得在普通只读检查中执行。
   样本全部投完后增加 `-Replay` 即可从第一份样本重新开始。它只平移复制样本的时间列以形成
   新演示批次，不删除历史任务、工单、报告或 PostgreSQL 数据：

   ```powershell
   .\scripts\simulate_skab_live_feed.ps1 -Replay -RunOnce -TriggerAutonomousWorkflow
   ```
3. 等待定时触发；使用立即触发参数时直接调用已发布工作流，不发送任何聊天消息；
4. 在万悟“应用观测 -> 统计看板 -> 应用”中按“工作流”筛选，确认四条工作流的调用次数、
   失败次数和耗时；如果当前版本的 API 统计提供详情，可进一步查看请求和响应内容；
   采集、分析、状态判断、结果和通知节点的完整细节在 Vue3“自动分析链路”中展示；
5. 在企业微信查看主动告警；
6. 回写处置结果并将工单改为“待验证”，投递下一批样本；
7. 在下一次万悟统计调用结果和 Vue3历史任务中展示复检通过或退回处理的自动决策；
8. 最后进入辅助智能体询问“解释最近一次 P1/P2 工单依据”。

应明确说明当前数据来自公开 SKAB，投递脚本只模拟企业数据接口产生新批次；后续替换为 HTTP、
MQTT、Kafka 或时序数据库适配器，不改变万悟工作流和后续分析闭环。

正式录制演示前先运行：

```powershell
.\scripts\accept_wanwu_workflows.ps1
```

只有需要验证“新数据产生到主动告警”的完整链路时，才运行
`-InjectSample -RunWorkflows`。避免重复投递公开样本和重复触发工作流。
