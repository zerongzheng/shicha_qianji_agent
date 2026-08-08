# 时察千机 Vue 3 前端

这里是时察千机的正式产品前端，使用 Vue 3 + Vite 编写，通过 FastAPI 调用工业分析服务。

## 与 Streamlit 的分工

- `frontend/`：正式产品页面，面向评委演示和后续企业部署。
- 根目录 `streamlit_app.py`：算法调试、快速验证和离线备用演示入口。

两者共享同一套 FastAPI 接口、SQLite 数据库和工业分析核心，不会产生两套算法结果。

## 启动方式

先启动后端：

```powershell
cd E:\大学课程\竞赛\shichi_qianji_agent
E:\Tools\uv\uv.exe run python api_main.py
```

再打开新的终端启动前端：

```powershell
cd E:\大学课程\竞赛\shichi_qianji_agent\frontend
Copy-Item .env.example .env
E:\Tools\nodejs\npm.cmd install
E:\Tools\nodejs\npm.cmd run dev
```

浏览器访问 `http://127.0.0.1:5173`。

## 页面流程

1. 选择 CSV 文件并设置检测器和阈值。
2. 点击“开始智能分析”，前端上传文件并轮询异步任务。
3. 在“风险总览”和“异常证据”查看结果。
4. 在“运维工单”填写现场根因、处置和复测记录。
5. 在“历史记录”查看任务、工单和已确认案例。

企业部署时，只需要修改 `frontend/.env` 中的 `VITE_API_BASE_URL`，前端业务代码不需要修改。
