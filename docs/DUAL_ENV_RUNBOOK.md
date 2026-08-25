# 本机与服务器双运行方式

本项目支持两套相互独立的运行环境。本机用于离线调试和备用演示，服务器用于完整万悟和正式展示。两套环境不要共用数据库、工作流 UUID、工作流 API Key 或运行输出目录。

## 本机模式

本机是 Windows + Docker Desktop + PostgreSQL，继续使用现有入口：

```powershell
cd "E:\大学课程\竞赛\shicha_qianji_agent"
.\scripts\start_basic_stack.ps1
```

访问地址：

```text
万悟：http://127.0.0.1:8081
FastAPI：http://127.0.0.1:8000
Vue3：http://127.0.0.1:5173
```

停止：

```powershell
.\scripts\stop_basic_stack.ps1
```

本机工作流配置位于 `outputs/wanwu_*_workflow.local.json`，其中的 UUID 和 API Key 只对应本机万悟。

## 服务器模式

服务器目录建议如下：

```text
/home/ubuntu/apps/
├── wanwu/
├── shicha_qianji_agent/
└── SKAB/
```

服务器万悟先按官方 Compose 启动，并确保已创建外部 Docker 网络：

```bash
docker network inspect wanwu-net >/dev/null 2>&1 || docker network create wanwu-net
```

服务器项目 `.env` 需要单独填写数据库密码、DashScope 配置、四个服务器工作流 API Key 和新的 UUID。服务器模式使用项目 Compose 将 FastAPI、PostgreSQL、Vue3 接入 `wanwu-net`，万悟调用地址为：

```text
http://shicha-qianji-api:8000/integrations/wanwu/openapi.json
```

启动：

```bash
cd /home/ubuntu/apps/shicha_qianji_agent
bash scripts/start_server_stack.sh --skip-triggers
```

第一次迁移时先使用 `--skip-triggers`，完成服务器万悟工作流重新发布并填写四个新 UUID/API Key 后，再执行不带参数的启动命令开启四个周期触发器。

停止：

```bash
bash scripts/stop_server_stack.sh
```

### 服务器演示投放

服务器栈启动后，不需要每次在 Windows 本机执行 PowerShell 投放器，也不需要手动把 CSV 拖到服务器。服务器项目应与 `SKAB` 并列放置：

```text
/home/ubuntu/apps/shicha_qianji_agent/outputs/demo_feed/skab_valve1
/home/ubuntu/apps/SKAB/data/valve1
```

四个触发器运行期间，每次演示直接在服务器执行：

```bash
cd /home/ubuntu/apps/shicha_qianji_agent
bash scripts/simulate_skab_live_feed.sh --run-once
```

投放器会记录 `.feed_state.json`，下一次自动投放下一份样本。要从第一份重新开始，可执行：

```bash
bash scripts/simulate_skab_live_feed.sh --replay --run-once
```

该命令不删除历史数据；它只重置投放进度，并平移重放样本的时间列，使新批次不会因内容指纹重复而被去重。投放后等待无人值守巡检触发器的 60 秒周期即可。由于服务器 `.env` 中启用了企业微信通知，真实投放可能产生群消息，录制前应确认接收群和演示时机。

也可以使用现有服务器投放器在投放后立即调用无人值守工作流：

```bash
bash scripts/simulate_skab_live_feed.sh --run-once --trigger-autonomous-workflow
```

从第一份样本重放并立即调用：

```bash
bash scripts/simulate_skab_live_feed.sh --replay --run-once --trigger-autonomous-workflow
```

该参数只能与 `--run-once` 一起使用，避免连续投放时重复发送通知。

后台 60 秒触发器仍可同时运行；重复发现由数据库内容指纹和通知幂等机制处理。每次执行可能发送企业微信消息。

服务器网页通过 SSH 隧道访问：

```powershell
ssh -N -p <SSH_PORT> -L 8081:127.0.0.1:8081 ubuntu@<SSH_HOST>
```

`<SSH_HOST>` 和 `<SSH_PORT>` 请使用服务器管理员单独提供的连接信息，不要写入公开仓库。

浏览器打开 `http://127.0.0.1:8081`。Vue3 和 FastAPI 如需从本机查看，可分别转发 `5173` 和 `8000`。

## 迁移边界

服务器是新万悟实例。必须从本机万悟导出并在服务器重新导入、发布五个工作流，再更新服务器 `outputs` 下的四份 local JSON。知识库、模型、19 个自定义工具和辅助智能体也需要在服务器重新配置或导入。本机 UUID 不得直接写入服务器配置。

不要执行 `docker compose down -v` 或 `docker volume prune`，否则可能删除持久化数据。
