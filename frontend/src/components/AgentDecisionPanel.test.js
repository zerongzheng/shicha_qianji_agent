import { createApp } from "vue";
import { afterEach, describe, expect, it } from "vitest";
import AgentDecisionPanel from "./AgentDecisionPanel.vue";

afterEach(() => {
  document.body.innerHTML = "";
});

describe("AgentDecisionPanel", () => {
  it("展示可审计决策、人工闸门和回退条件", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const app = createApp(AgentDecisionPanel, {
      decisions: [{
        decision_id: "model_routing",
        stage: "工具编排",
        title: "选择主异常检测模型",
        status: "已决策",
        trigger: "新批次通过质量门",
        evidence: ["传感器数量：8", "健康基线：可用"],
        rule: "按冻结能力顺序选择",
        action: "调用时频关系多路径检测器",
        target: "主异常检测任务",
        confidence: "冻结规则",
        human_gate: "企业数据到位后重新校准",
        rollback_condition: "运行失败时回退下一可用模型",
      }],
    });
    app.mount(container);

    expect(container.textContent).toContain("1 项决策");
    expect(container.textContent).toContain("调用时频关系多路径检测器");
    expect(container.textContent).toContain("企业数据到位后重新校准");
    expect(container.textContent).toContain("运行失败时回退下一可用模型");
    app.unmount();
  });

  it("兼容没有决策账本的旧历史任务", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const app = createApp(AgentDecisionPanel, { decisions: [] });
    app.mount(container);
    expect(container.textContent).toContain("未保存决策账本");
    app.unmount();
  });
});
