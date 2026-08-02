# 元景万悟接入说明

本项目与 `E:\大学课程\竞赛\wanwu` 保持两个独立工程：万悟负责智能体、工作流、知识库和模型编排，
时察千机负责工业 CSV 的确定性计算、预测、预警和结构化输出。不要把万悟源码复制进本项目。

## 调用链

```text
万悟 Agent / Workflow
    -> 上传 CSV 到时察千机 POST /api/v1/files
    -> 获得 file_id
    -> 调用时察千机 POST /api/v1/analyze
    -> 将 JSON 结果交给万悟知识库和大模型
    -> 输出异常证据、预测趋势、预警等级和处置建议
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
- `forecast_results`：各传感器未来窗口、方向、预测风险、置信区间和回测误差；
- `risk_alerts`：当前异常与趋势预测形成的结构化预警；
- `recommendations`：处置建议；
- `run_id`：用于日志和后续结果追踪。

### 多模型交叉验证

```text
POST /api/v1/model-compare
```

请求体可以传入 `file_id`、`detectors` 和可选 `config`：

```json
{
  "file_id": "上传接口返回的 file_id",
  "detectors": ["mad", "isolation_forest", "hybrid"],
  "config": {"threshold": 5.5}
}
```

返回每个模型的点级 F1、事件级 F1、PR-AUC、检测延迟和误报事件，供万悟工作流进行结果
比较和解释。模型比较结果不能单独作为故障确诊依据。

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

在万悟中使用 API 节点编排以下步骤：

1. 文件输入节点接收用户 CSV；
2. API 节点调用 `/api/v1/files`；
3. API 节点把 `file_id` 传给 `/api/v1/analyze`；
4. 需要异常检测模型比选时调用 `/api/v1/model-compare`，需要预测比选时调用 `/api/v1/forecast-compare`；
5. 条件节点判断 `risk_alerts` 是否为空；
6. 高风险时调用万悟工业知识库检索故障原因和 SOP；
7. 大模型节点按照“结论 → 算法证据 → 知识库依据 → 建议动作 → 人工确认事项”输出。

不要将完整 CSV 直接放进大模型提示词。大模型只读取分析 API 返回的结构化摘要和知识库证据。

## 安全约束

当 `.env` 配置 `INDUSTRIAL_API_KEY` 后，万悟 API 节点需要增加请求头：

```text
X-API-Key: 你的服务密钥
```

服务只接受 `file_id`，不接受 `E:\`、`..\` 或任意绝对路径。上传文件保存在 `outputs/api_uploads/`，
运行记录保存在 `outputs/logs/runs.jsonl`。正式部署时应将这两个目录迁移到对象存储和数据库，
并增加文件过期清理、租户隔离和 HTTPS。

## 现阶段边界

当前预测器已形成最近值、指数平滑、局部线性、滞后岭回归和时频增强岭回归五模型体系，
并具备滚动回测选择、95% 区间和模型分歧评估。它属于可解释工程模型底座，竞赛最终版本
仍需在固定 SKAB 划分上增加跨文件训练的深度时序模型、消融实验和企业数据验证，并将设备
说明书、告警规则、维修工单迁移至万悟知识库。
