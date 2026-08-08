# 时察千机 Vue 3 前端

这是时察千机的正式工业运维工作台，使用 Vue 3 + Vite 编写，通过 FastAPI 调用工业分析服务。

## 前端职责

前端负责专业工业应用界面，不直接访问数据库，也不直接调用比赛方大模型：

- 上传并预检工业 CSV；
- 展示风险总览、异常事件、传感器曲线和预测趋势；
- 展示异常证据、候选根因和证据缺口；
- 管理运维工单、现场确认、处置和复测反馈；
- 展示历史任务、归档记录和已确认案例。

完整系统的接口、算法、数据库和万悟适配均在项目根目录的 FastAPI 后端中。

## 页面组件

```text
src/
├─ App.vue                         # 页面布局、全局状态和业务事件协调
├─ api.js                          # FastAPI 请求封装
├─ components/
│  ├─ AnalysisProgressPanel.vue    # 异步分析进度和取消排队
│  ├─ PreflightModal.vue            # CSV 分析前预检
│  ├─ WorkOrderPanel.vue            # 工单列表、详情和现场反馈
│  ├─ HistoryPanel.vue              # 历史任务和已确认案例
│  ├─ CaseDrawer.vue                # 案例详情抽屉
│  ├─ TimeSeriesChart.vue           # 时序风险曲线
│  ├─ ForecastChart.vue             # 预测区间图
│  └─ ConfirmDialog.vue             # 统一确认弹窗
├─ styles.css                      # 全局样式与响应式布局
└─ main.js                         # Vue 应用入口
```

## 运行前提

需要先准备：

- Node.js 和 npm；
- 项目后端已经安装 uv 环境；
- FastAPI 运行在 `http://127.0.0.1:8000`；
- `frontend/.env` 中的 API 地址正确。

默认配置为：

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_API_KEY=
```

如果后端配置了 `INDUSTRIAL_API_KEY`，前端本地演示可以在 `frontend/.env` 中配置同样的服务密钥。不要把大模型 API Key 写入前端，因为 Vite 会把 `VITE_*` 变量打包到浏览器端；真实大模型密钥只能保存在后端根目录 `.env`。

## 启动

先在第一个终端启动后端：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent"
& "E:\Tools\uv\uv.exe" run python api_main.py
```

再在第二个终端启动前端：

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent\frontend"
& "E:\Tools\nodejs\npm.cmd" install
& "E:\Tools\nodejs\npm.cmd" run dev
```

浏览器访问：

```text
http://127.0.0.1:5173
```

修改 Vue 文件后，Vite 会自动热更新。后端必须保持运行，否则页面会显示 API 离线。

## 生产构建

```powershell
cd "E:\大学课程\竞赛\shichi_qianji_agent\frontend"
& "E:\Tools\nodejs\npm.cmd" run build
& "E:\Tools\nodejs\npm.cmd" run preview
```

构建产物在 `frontend/dist/`，该目录是本地构建产物，不提交 GitHub。

## 与万悟的关系

Vue 页面不会因为导入 OpenAPI 自动出现在万悟网页里。万悟通过 OpenAPI 调用 FastAPI，负责智能体、工作流、知识库和平台登录；Vue 负责更完整的工业图表、工单和案例工作台。

推荐的校赛展示方式：

```text
万悟工作流：上传 CSV -> 调用时察千机 API -> 返回诊断摘要
Vue3 工作台：查看详细证据 -> 查看预测 -> 完成工单闭环
```

后续如果平台支持 iframe 或外部应用嵌入，再考虑把部署后的 Vue 页面挂入万悟；这需要公网 HTTPS、CORS 和平台嵌入权限，不能仅靠 OpenAPI 完成。
