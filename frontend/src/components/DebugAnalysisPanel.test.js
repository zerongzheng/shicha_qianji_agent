import { createApp } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import DebugAnalysisPanel from "./DebugAnalysisPanel.vue";

afterEach(() => {
  document.body.innerHTML = "";
});

describe("DebugAnalysisPanel", () => {
  it("明确显示为调试入口并提交单次分析", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const onStartAnalysis = vi.fn();
    const app = createApp(DebugAnalysisPanel, {
      selectedFile: { name: "sample.csv", isSample: true },
      config: {
        device_profile_id: null,
        detector: "time_frequency_relation",
        threshold: 3.5,
      },
      onStartAnalysis,
    });
    app.mount(container);

    expect(container.textContent).toContain("单文件调试分析");
    expect(container.textContent).toContain("不参与自动监测主链路");
    const submit = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes("开始调试分析"));
    submit.click();
    expect(onStartAnalysis).toHaveBeenCalledTimes(1);
    app.unmount();
  });
});
