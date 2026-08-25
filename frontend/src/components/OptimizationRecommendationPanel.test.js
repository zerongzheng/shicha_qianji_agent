import { createApp } from "vue";
import { afterEach, describe, expect, it } from "vitest";
import OptimizationRecommendationPanel from "./OptimizationRecommendationPanel.vue";

afterEach(() => {
  document.body.innerHTML = "";
});

function mountPanel(recommendations) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const app = createApp(OptimizationRecommendationPanel, { recommendations });
  app.mount(container);
  return { app, container };
}

const recommendation = {
  recommendation_id: "OPT-PARAM-001",
  category: "参数稳定",
  target: "Pressure",
  action: "核对工况后分级调整并观察趋势",
  adjustment_direction: "抑制继续上升并回归健康区间",
  suggested_range: "仅允许在企业确认范围内调整",
  confidence: "中",
  evidence: ["预测呈上升趋势", "事件首要候选为阀门阻塞"],
  constraints: ["不得绕过安全联锁"],
  validation_metrics: ["异常事件数", "趋势风险"],
  observation_window: "至少观察 30 个采样点",
  rollback_condition: "风险恶化时恢复原策略",
  status: "待人工确认",
};

describe("OptimizationRecommendationPanel", () => {
  it("展示建议数量、目标和调整方向，并默认保留人工状态", () => {
    const { app, container } = mountPanel([recommendation]);
    expect(container.textContent).toContain("1 条建议");
    expect(container.textContent).toContain("Pressure");
    expect(container.textContent).toContain("抑制继续上升");
    expect(container.textContent).toContain("待人工确认");
    expect(container.textContent).not.toContain("自动下发控制指令已执行");
    app.unmount();
  });

  it("展开后展示证据、观察窗口和回退条件", () => {
    const { app, container } = mountPanel([recommendation]);
    const details = container.querySelector("details");
    details.open = true;
    expect(container.textContent).toContain("预测呈上升趋势");
    expect(container.textContent).toContain("至少观察 30 个采样点");
    expect(container.textContent).toContain("风险恶化时恢复原策略");
    app.unmount();
  });

  it("没有建议时显示空状态", () => {
    const { app, container } = mountPanel([]);
    expect(container.textContent).toContain("暂无优化建议");
    app.unmount();
  });
});
