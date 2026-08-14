# 基础运行手册

本手册说明校赛阶段最常用的两种运行方式。当前项目统一使用 PostgreSQL，启动 FastAPI 前需要确认 PostgreSQL 服务可连接；Vue3、FastAPI 和万悟是三个可以独立运行的部分。

## 运行模式

| 模式 | 需要启动 | 用途 |
| --- | --- | --- |
| Vue3 正式工作台 | FastAPI + Vue3 | 推荐的项目演示方式 |
| 本地万悟联动 | Docker 万悟 + FastAPI，可选 Vue3 | 验证平台工具和工作流接入 |

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

页面流程：选择 CSV -> 文件预检 -> 开始智能分析 -> 查看风险与证据 -> 处理运维工单 -> 保存现场反馈 -> 查看历史案例。

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

脚本会启动万悟基础服务和时察千机 FastAPI。检查：

```powershell
.\scripts\check_basic_stack.ps1
```

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

只启动万悟、不启动时察千机 API：

```powershell
.\scripts\start_basic_stack.ps1 -SkipApi
```

只停止基础服务：

```powershell
.\scripts\stop_basic_stack.ps1
```

不要执行 `docker compose down -v` 或 `docker system prune`，它们可能删除万悟数据库、知识库、数据卷或镜像缓存。

## 停止本地服务

- FastAPI、Vite：在对应终端按 `Ctrl+C`；
- 万悟基础模式：执行 `.\scripts\stop_basic_stack.ps1`。

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

### 前端依赖安装慢

可以先确认 Node.js 和 npm 已加入 PATH。项目提交了 `package-lock.json`，正常情况下在 `frontend/` 执行 `npm install` 即可，不要提交 `node_modules/`。

### 大模型接口限流

基础工业算法不依赖大模型。万悟比赛演示优先使用 `quick_industrial_diagnosis` 工作流，不要在工具后再叠加普通智能体总结；本地大模型密钥只配置在后端 `.env`。
