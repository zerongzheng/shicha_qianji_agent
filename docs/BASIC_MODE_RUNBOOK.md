# 基础运行手册

本手册说明校赛阶段最常用的两种运行方式。当前项目统一使用 PostgreSQL，启动 FastAPI 前需要确认 PostgreSQL 服务可连接；Vue3、FastAPI 和万悟是三个可以独立运行的部分。

## 运行模式

| 模式 | 需要启动 | 用途 |
| --- | --- | --- |
| Vue3 本地调试 | FastAPI + Vue3 | 调试图表、证据和工单交互 |
| 竞赛完整演示 | Docker 万悟 + FastAPI + 四个触发器 + Vue3 | 推荐的答辩展示方式 |
| 本地万悟基础模式 | Docker 万悟 + FastAPI + 四个触发器 | 低内存联调和后台运行 |

## 目录

```text
时察千机：E:\大学课程\竞赛\shichi_qianji_agent
万悟平台：E:\大学课程\竞赛\wanwu
```

## 方式一：Vue3 正式工作台

第一个终端启动后端：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
& "E:\Tools\uv\uv.exe" run python api_main.py
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

第二个终端启动前端：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent\frontend"
& "E:\Tools\nodejs\npm.cmd" install
& "E:\Tools\nodejs\npm.cmd" run dev
```

访问：`http://127.0.0.1:5173`。

该方式用于前端和算法调试。页面中的 CSV 选择入口不属于竞赛无人值守主流程；正式展示应从
万悟工作流自动发现监测目录中的新批次，再到 Vue3 查看风险证据、处理工单和历史案例。

本地分析完成后，确定性诊断和工单草案覆盖全部异常事件；发送给大模型的摘要为控制提示词长度只截取部分工单，
完整结果仍保存在后端分析结果和数据库中。

风险总览中的“自动分析链路”会展示本次任务实际执行的步骤。展开步骤可以查看调用模块、核心输出、耗时和使用边界。
这里展示的是可审计的工程执行记录，不是大模型思维过程。旧历史任务没有该字段时会显示兼容提示，重新运行后即可生成完整轨迹。

## 方式二：本地万悟基础模式

本机内存不足时使用基础模式，不启动全部本体服务。先启动 Docker Desktop，然后执行：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
.\scripts\start_basic_stack.ps1
```

脚本会启动万悟基础服务、时察千机 FastAPI，以及无人值守巡检、SLA 督办、维修后复检、
班次简报四个独立的后台触发器。启动前会校验四份 `outputs/wanwu_*_workflow.local.json` 的
JSON、工作流 UUID 和 API Key 配置。检查：

```powershell
.\scripts\check_basic_stack.ps1
```

启动脚本会等待 `bff-service` 和 `workflow-wanwu` 就绪，再自动重启一次 `nginx-wanwu`。
这是因为电脑重启后 Docker 容器地址可能变化；若 Nginx 在后端之前启动，可能保留旧地址并导致
页面连续出现 `502 Bad Gateway`。脚本会自动处理该问题，不会删除任何万悟数据。

时察千机服务自检：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/system/diagnostics | ConvertTo-Json -Depth 6
```

该结果可以快速确认知识库文档、SKAB 默认样例、健康基线、模型配置和任务队列状态。

访问：

```text
万悟：http://localhost:8081
时察千机 API：http://127.0.0.1:8000/docs
```

### 竞赛完整演示模式

竞赛完整演示直接执行：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
.\scripts\start_basic_stack.ps1
```

脚本会同时启动项目内已安装的 Vite，并将 PID 和日志写入 `outputs/`。脚本不会自动
联网安装前端依赖；首次运行前若缺少 `frontend/node_modules`，先在 `frontend/` 执行一次
`npm install`。访问地址：

```text
万悟：http://127.0.0.1:8081
时察千机 API：http://127.0.0.1:8000
Vue3 运维工作台：http://127.0.0.1:5173
```

录制视频时，无人值守触发器的正式 60 秒周期会产生 0 至 60 秒随机等待。需要缩短空等时间时，
保持后台频率不变，投递后立即触发一次已发布工作流：

```powershell
.\scripts\simulate_skab_live_feed.ps1 -RunOnce -TriggerAutonomousWorkflow
```

该命令可能实际发送企业微信通知，必须在受控演示时使用。它只消除调度等待，不跳过万悟画布、
FastAPI 分析、PostgreSQL 落库、工单或通知步骤。

样本已经全部投完时，使用 `-Replay` 从第一份样本重新开始。该选项会平移复制文件的时间列，
使内容指纹发生变化，从而通过同一数据源的重复批次保护；不会删除历史报告或数据库记录。
重放偏移会保存在 `.feed_state.json`，后续普通 `-RunOnce` 会继续沿用，直到整轮样本投放完成：

```powershell
.\scripts\simulate_skab_live_feed.ps1 -Replay -RunOnce -TriggerAutonomousWorkflow
```

只启动万悟、不启动时察千机 API：

```powershell
.\scripts\start_basic_stack.ps1 -SkipApi
```

统一停止基础服务、后端、四个触发器和由启动脚本管理的 Vue3：

```powershell
.\scripts\stop_basic_stack.ps1
```

不要执行 `docker compose down -v` 或 `docker system prune`，它们可能删除万悟数据库、知识库、数据卷或镜像缓存。
启动脚本会在 `outputs/` 写入 `shichi_qianji_api.pid`、
`shichi_qianji_frontend.pid` 以及四个 `wanwu_*_trigger.pid`；停止脚本只回收这些项目拥有且
命令行匹配的进程，并清理失效 PID 文件。手工在其他终端启动的 Vite 不会被停止脚本误杀。
纯后端调试时可使用 `-SkipFrontend` 跳过 Vue3；旧的 `-IncludeFrontend` 参数仍兼容但无需再传。

## 停止本地服务

- 手工启动的 FastAPI、Vite：在对应终端按 `Ctrl+C`；
- 一键启动模式：执行 `.\scripts\stop_basic_stack.ps1`。

## 常见问题

### 前端显示 API 离线

确认后端终端仍在运行，并执行：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

### 页面端口被占用

默认端口为：

```text
FastAPI：8000
Vue3：5173
万悟：8081
```

关闭占用进程或让 Vite 使用其他端口；同时修改前端 `VITE_API_BASE_URL` 时只需要保持它指向后端地址。

### 万悟页面连续出现 Bad Gateway

先等待 Docker 容器启动完成，再执行：

```powershell
docker restart nginx-wanwu
.\scripts\check_basic_stack.ps1
```

`check_basic_stack.ps1` 中验证码接口正常返回，说明 Nginx 已经能够连接 BFF。该操作只刷新反向
代理，不会清空 MySQL、工作流、智能体或知识库数据。

### 前端依赖安装慢

可以先确认 Node.js 和 npm 已加入 PATH。项目提交了 `package-lock.json`，正常情况下在 `frontend/` 执行 `npm install` 即可，不要提交 `node_modules/`。

### 大模型接口限流

基础工业算法和四条周期工作流不依赖大模型。竞赛主流程使用已发布的无人值守巡检、SLA 督办、
维修后复检和班次简报工作流，工具返回的 `presentation` 直接进入终态输出，不增加大模型二次
总结。大模型和 RAG 只供辅助智能体按需解释；`quick_industrial_diagnosis` 只保留为人工上传调试入口。
本地模型密钥只配置在后端 `.env` 或万悟模型配置中，不写入仓库。
