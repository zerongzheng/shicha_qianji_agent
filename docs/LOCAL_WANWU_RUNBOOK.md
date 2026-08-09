# 本地万悟接入运行手册

本手册描述 Windows 本机运行时察千机、Docker 万悟和 Vue3 前端之间的关系。万悟和时察千机是两个独立工程，通过 FastAPI REST API 对接。

```text
时察千机 FastAPI：Windows，0.0.0.0:8000
时察千机 Vue3：Windows，127.0.0.1:5173
元景万悟：Docker，网页映射到 localhost:8081
容器访问 Windows API：http://host.docker.internal:8000
```

Vue3 页面不会因为导入 OpenAPI 自动嵌入万悟。OpenAPI 只描述工具接口；万悟网页负责智能体和工作流，Vue3 负责独立的专业工业运维看板。

## 启动顺序

### 1. 启动 Docker Desktop

确认 Docker Desktop 已启动并处于运行状态。

### 2. 启动时察千机 FastAPI

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
& "E:\Tools\uv\uv.exe" run python api_main.py
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

### 3. 启动 Vue3 前端（需要专业看板时）

另开终端：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent\frontend"
& "E:\Tools\nodejs\npm.cmd" run dev
```

访问：`http://127.0.0.1:5173`。

### 4. 启动万悟基础服务

如果内存足够，可以从万悟目录启动完整配置；内存不足时使用项目脚本的基础模式：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
.\scripts\start_basic_stack.ps1 -SkipApi
```

脚本会从 `E:\大学课程\竞赛\wanwu` 启动基础服务，但不会启动时察千机 API。因为上一步已经手动启动了 API，所以这里使用 `-SkipApi`。

也可以手动执行万悟 Compose：

```powershell
cd "E:\大学课程\竞赛\wanwu"
docker compose --env-file .env --env-file .env.ontology --env-file .env.image.amd64 up -d
```

内存不足时不建议启动全部本体扩展，优先使用 `start_basic_stack.ps1`。

## 访问地址

```text
万悟网页：http://localhost:8081
时察千机 API 文档：http://127.0.0.1:8000/docs
时察千机健康检查：http://127.0.0.1:8000/health
万悟完整 OpenAPI：http://127.0.0.1:8000/integrations/wanwu/openapi.json
万悟快速 OpenAPI：http://127.0.0.1:8000/integrations/wanwu/quick-openapi.json
Vue3 工业看板：http://127.0.0.1:5173
```

万悟容器内部访问时察千机使用：

```text
http://host.docker.internal:8000
```

### 登录

万悟登录账号和密码以本地万悟配置为准。首次登录后应立即修改默认密码，不要把账号密码写进 GitHub 文档或截图。

## 导入工具

推荐在万悟“资源库 -> 自定义工具”中导入快速协议：

```text
http://host.docker.internal:8000/integrations/wanwu/quick-openapi.json
```

如果页面不支持 URL 导入，可以在时察千机 API 运行后生成本地协议文件：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
& "E:\Tools\uv\uv.exe" run shichi-qianji-wanwu-check
```

导出文件：

```text
outputs/wanwu_quick_openapi.json
outputs/wanwu_openapi.json
```

比赛演示推荐只导入快速协议，避免平台自动规划多个工具产生额外模型调用和限流。

## 推荐工作流

快速演示：

```text
开始节点（File 类型 industrial_file）
  -> quick_industrial_diagnosis
  -> 结束节点直接返回 presentation
```

工具参数：

- `file_url`：映射开始节点文件的临时 URL；
- `file_name`：映射文件名，或填写 `industrial_sample.csv`；
- `file_base64`：留空，不要和 `file_url` 同时填写；
- `config`：可以留空，使用后端默认分析参数。

不要把 Windows 本地路径，例如 `E:\...\valve1\0.csv`，传给万悟工具；Docker 容器无法直接读取 Windows 文件路径。

如果需要正式工单闭环，使用异步工作流：

```text
文件输入
  -> submit_industrial_analysis
  -> 保存 run_id
  -> 循环调用 get_industrial_analysis_status
  -> success 后调用 get_industrial_analysis_result
  -> 展示风险和工单
  -> update_industrial_work_order 回写现场反馈
  -> list_industrial_feedback_cases 查询历史案例
```

## 与 Vue3 的配合

推荐校赛展示顺序：

1. 在万悟工作流中上传 SKAB CSV，展示智能体返回的诊断摘要；
2. 打开 Vue3 工业看板，查看同一后端产生的完整风险曲线和异常证据；
3. 在 Vue3 工单页填写现场确认根因、处置和复测；
4. 在历史记录页展示案例沉淀。

万悟体现平台化智能体和工作流能力，Vue3 体现工业场景的专业可视化和业务闭环，两者共同使用 FastAPI 和 SQLite。

## 联合自检

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
.\scripts\check_basic_stack.ps1
```

检查内容包括：

- Docker 基础容器状态；
- 时察千机 API 健康状态；
- OpenAPI 地址；
- 万悟验证码接口；
- 本地服务之间的访问链路。

## 停止

停止 Vue3 和 FastAPI：在对应终端按 `Ctrl+C`。

停止万悟但保留数据：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
.\scripts\stop_basic_stack.ps1
```

不要执行：

```powershell
docker compose down -v
docker system prune
```

这些命令可能删除万悟数据库、知识库数据卷或镜像缓存。

## 在线部署边界

本地 `host.docker.internal` 只适用于 Docker Desktop 本机环境。比赛方在线万悟访问时察千机必须使用公网 HTTPS 地址，并在根目录 `.env` 配置：

```dotenv
API_PUBLIC_BASE_URL=https://你的公网域名
INDUSTRIAL_API_KEY=你的服务密钥
```

然后在万悟工具中配置 `X-API-Key`。在线部署还需要处理 HTTPS、反向代理、文件临时 URL、数据库持久化、CORS 和日志。
