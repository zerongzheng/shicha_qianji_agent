# 时察千机万悟自动化包

本目录保存万悟平台侧的可公开配置、工作流搭建说明和外部定时触发脚本。它不保存万悟
API Key、工业服务 API Key 或企业微信 Webhook。

## 运行分工

```text
Windows 任务计划程序 / Linux cron
  -> 调用已发布的万悟工作流 API
  -> 万悟工作流执行无人值守巡检节点
  -> 时察千机后端完成确定性工业计算并写入 PostgreSQL
  -> 万悟工作流判断任务状态并调用主动告警节点
  -> 后端按风险等级向企业微信投递并保存审计记录
```

外部脚本只提供“时间触发”，不读取 SKAB、不运行分析、不调用企业微信，因此万悟仍是
业务流程的编排与执行入口。对话智能体只用于查询状态、解释诊断证据、处理工单和追问。

## 文件

- `autonomous_workflow.example.json`：无人值守巡检的非敏感运行模板；
- `sla_workflow.example.json`：工单 SLA 督办的非敏感运行模板；
- `reinspection_workflow.example.json`：维修后自动复检的非敏感运行模板；
- `shift_brief_workflow.example.json`：班次简报工作流的非敏感运行模板；
- `WORKFLOW_SETUP.md`：万悟画布逐节点配置；
- `agent_prompt.md`：辅助对话智能体角色说明；
- `skills/catalog.json`：一个工业智能体的五个 Skill、权限和周期契约；
- `skills/README.md`：Skill 调用和确认规则；
- `scripts/trigger_wanwu_workflow.ps1`：按周期调用万悟官方工作流 API。

四个运行工作流可以使用不同的万悟发布密钥。配置文件通过 `api_key_env` 指定环境变量名，默认
无人值守巡检、SLA 督办和维修后复检使用 `WANWU_WORKFLOW_API_KEY`，班次简报使用
`WANWU_SHIFT_BRIEF_API_KEY`，密钥仍只放在 `.env` 或系统环境变量中。

本机一键启动会同时管理四个周期触发器，建议频率如下：

| 工作流 | 建议间隔 | 本地 PID 文件 |
| --- | ---: | --- |
| 无人值守工业巡检 | 60 秒 | `outputs/wanwu_autonomous_trigger.pid` |
| 工单 SLA 督办 | 300 秒 | `outputs/wanwu_sla_trigger.pid` |
| 维修后自动复检 | 300 秒 | `outputs/wanwu_reinspection_trigger.pid` |
| 工业班次简报 | 28800 秒（8 小时） | `outputs/wanwu_shift_brief_trigger.pid` |

完成画布配置后，在项目根目录运行一键验收：

```powershell
.\scripts\accept_wanwu_workflows.ps1
```

默认只检查服务、19 个工具和本地工作流配置，不触发分析或告警。需要完整演示验收时显式执行：

```powershell
.\scripts\accept_wanwu_workflows.ps1 -InjectSample -RunWorkflows
```

结果写入 `outputs/wanwu_acceptance_report.json`，该目录不会上传 GitHub。报告只记录配置是否
就绪和工作流返回结果，不记录 API Key、企业微信 Webhook 或原始 CSV。

四个本地配置与模板的对应关系如下。只填写已发布工作流的 `workflow_uuid`；
`api_key_env` 只填写环境变量名，不填写密钥本身：

| 本地配置 | 对应工作流 |
| --- | --- |
| `outputs/wanwu_autonomous_workflow.local.json` | 时察千机无人值守工业巡检 |
| `outputs/wanwu_sla_workflow.local.json` | 时察千机工单 SLA 督办 |
| `outputs/wanwu_reinspection_workflow.local.json` | 时察千机维修后自动复检 |
| `outputs/wanwu_shift_brief_workflow.local.json` | 时察千机工业班次简报 |

首次使用时，在万悟发布工作流并创建 OpenAPI Key，然后将密钥放进当前终端环境变量：

```powershell
$env:WANWU_WORKFLOW_API_KEY = "万悟发布配置生成的 API Key"
```

不要将上述命令和真实密钥写进仓库文件。
