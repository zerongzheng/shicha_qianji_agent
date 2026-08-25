# GitHub 发布检查清单

这份清单用于校赛阶段发布“时察千机”的可复现代码。发布前应确认提交的是完整工程，
而不是只提交了前一次可以运行的旧版本。

## 必须提交

- `app/`：分析、诊断、任务 API、数据库、知识检索和平台适配代码；
- `frontend/src/`、`frontend/package.json`、`frontend/package-lock.json`：Vue3 前端和测试；
- `tests/`：后端回归测试；
- `resources/knowledge/`：当前校赛阶段的正式通用知识库；
- `resources/device_profiles/`：SKAB 设备配置和企业接入模板；
- `docs/`、`scripts/`：运行说明、实验协议和启动脚本；
- `README.md`、`pyproject.toml`、`uv.lock`、`.env.example`、`frontend/.env.example`。

## 不要提交

- `.env`、API Key、密码、证书和任何真实企业数据；
- `outputs/` 中的数据库、上传文件、日志、缓存、报告和模型产物；
- `SKAB` 完整数据集。SKAB 与项目目录并列，按 README 中的路径准备；
- `frontend/node_modules/`、`frontend/dist/`、`.venv/`、`__pycache__/` 和测试缓存；
- `resources/knowledge_drafts/` 中的整理过程稿；
- 企业设备手册、维修记录和内部工单原文。正式接入前应先脱敏并确认授权范围。

## 提交前本地验证

在项目根目录执行：

```powershell
& "E:\Tools\uv\uv.exe" sync --extra dev --frozen
& "E:\Tools\uv\uv.exe" run ruff check app tests
& "E:\Tools\uv\uv.exe" run pytest -q
git diff --check
```

再在 `frontend` 目录执行：

```powershell
& "E:\Tools\nodejs\npm.cmd" ci
& "E:\Tools\nodejs\npm.cmd" test -- --run
& "E:\Tools\nodejs\npm.cmd" run build
```

## 检查 Git 是否漏提交新增代码

```powershell
git status --short
git add app frontend/src tests resources/knowledge resources/device_profiles docs scripts README.md pyproject.toml uv.lock
git diff --cached --stat
git diff --cached --name-only
```

看到 `??` 的正式代码、测试、知识库或设备配置时，说明它们还没有进入暂存区；
看到 `.env`、数据库、CSV 或 `node_modules` 时，应先检查 `.gitignore`，不要把敏感信息加入提交。

## 当前校赛边界

SKAB 实验只能证明公开数据上的检测和工程闭环能力。事件覆盖率、诊断覆盖率和工单覆盖率表示
系统是否形成相应结构化输出，不等同于诊断准确率、企业收益或现场处置效率。企业数据到位后，
应在独立时间段重新标定阈值，并补充人工确认准确率、误报率、处置时长和复测结果。
