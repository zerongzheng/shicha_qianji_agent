"""竞赛启动脚本和万悟主流程文档的一致性回归测试。"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8-sig")


def test_basic_stack_starts_and_manages_vue_frontend_by_default() -> None:
    start_script = _read("scripts/start_basic_stack.ps1")
    stop_script = _read("scripts/stop_basic_stack.ps1")

    assert "[switch]$SkipFrontend" in start_script
    assert "[switch]$IncludeFrontend" in start_script
    assert "$startFrontend = -not $SkipFrontend" in start_script
    assert "if ($startFrontend)" in start_script
    assert "shicha_qianji_frontend.pid" in start_script
    assert "--strictPort" in start_script
    assert 'Test-TrackedProcess $frontendPidPath @("vite", "frontend")' in start_script
    assert "shicha_qianji_frontend.pid" in stop_script
    assert 'Stop-RecordedProcess $frontendPidPath @("vite", "frontend")' in stop_script


def test_competition_docs_do_not_recommend_legacy_upload_workflow() -> None:
    documents = "\n".join(
        _read(path)
        for path in (
            "README.md",
            "docs/BASIC_MODE_RUNBOOK.md",
            "docs/WANWU_INTEGRATION.md",
            "docs/LOCAL_WANWU_RUNBOOK.md",
        )
    )

    forbidden_recommendations = (
        "比赛演示优先使用 `quick_industrial_diagnosis`",
        "创建比赛演示智能体时应优先导入这个地址",
        "文件输入 -> quick_industrial_diagnosis -> 结束节点",
        "由一个万悟大模型节点生成面向运维人员的解释文本",
        "最终解释集中在一个大模型节点完成",
    )
    assert all(item not in documents for item in forbidden_recommendations)
    assert "竞赛主流程不要求用户上传 CSV" in documents
    assert ".\\scripts\\start_basic_stack.ps1" in documents
    assert "-SkipFrontend" in documents


def test_demo_feed_can_explicitly_trigger_published_workflow_once() -> None:
    simulator = _read("scripts/simulate_skab_live_feed.ps1")

    assert "[switch]$TriggerAutonomousWorkflow" in simulator
    assert "$TriggerAutonomousWorkflow -and -not $RunOnce" in simulator
    assert '"wanwu\\scripts\\trigger_wanwu_workflow.ps1"' in simulator
    assert "-ConfigPath $AutonomousWorkflowConfig -RunOnce" in simulator
    assert "可能发送企业微信通知" in simulator
    assert "[switch]$Replay" in simulator
    assert "平移复制样本的时间列" in simulator or "时间轴已平移" in simulator
    assert "replay_offset_milliseconds" in simulator
    assert "$replayActive = $true" in simulator
    assert "if ($replayActive)" in simulator
