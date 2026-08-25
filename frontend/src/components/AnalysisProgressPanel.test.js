import { createApp } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import AnalysisProgressPanel from "./AnalysisProgressPanel.vue";

afterEach(() => {
  document.body.innerHTML = "";
});

describe("AnalysisProgressPanel", () => {
  it("显示当前任务进度和失败状态", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const app = createApp(AnalysisProgressPanel, {
      runId: "run-demo",
      stage: "failed",
      status: "失败",
      percent: 42,
      detail: "数据列校验失败",
      elapsed: 12,
      steps: [{ id: "running", label: "智能分析", detail: "检测数据" }],
      activeJobId: "",
    });
    app.mount(container);

    expect(container.textContent).toContain("分析任务未完成");
    expect(container.textContent).toContain("数据列校验失败");
    expect(container.querySelector('[role="progressbar"]').getAttribute("aria-valuenow")).toBe("42");
    app.unmount();
  });

  it("排队状态显示取消按钮并派发事件", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const onCancel = vi.fn();
    const app = createApp(AnalysisProgressPanel, {
      stage: "queued",
      status: "排队中",
      percent: 20,
      activeJobId: "run-demo",
      onCancel,
    });
    app.mount(container);
    container.querySelector("button").click();
    expect(onCancel).toHaveBeenCalledTimes(1);
    app.unmount();
  });
});
