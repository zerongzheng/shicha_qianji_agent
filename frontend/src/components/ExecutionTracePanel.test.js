import { createApp } from "vue";
import { afterEach, describe, expect, it } from "vitest";
import ExecutionTracePanel from "./ExecutionTracePanel.vue";

afterEach(() => {
  document.body.innerHTML = "";
});

function mountPanel(steps) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const app = createApp(ExecutionTracePanel, { steps });
  app.mount(container);
  return { app, container };
}

describe("ExecutionTracePanel", () => {
  it("展示已完成和跳过步骤的真实状态", () => {
    const { app, container } = mountPanel([
      {
        step_id: "data_ingestion",
        title: "文件接入与预检",
        module: "app.data.loader.load_time_series_with_context",
        status: "completed",
        output_summary: { row_count: 1000, sensor_count: 8 },
        duration_seconds: 0.012,
        limitation: "仅支持 CSV。",
      },
      {
        step_id: "feature_construction",
        title: "多变量特征构建",
        module: "app.analysis.detection._build_multivariate_features",
        status: "completed",
        output_summary: { feature_count: 24 },
        duration_seconds: null,
        limitation: "特征元数据已记录。",
      },
      {
        step_id: "forecast_analysis",
        title: "趋势预测与风险外推",
        module: "app.analysis.forecast.forecast_sensors",
        status: "skipped",
        output_summary: { reason: "run_forecast=False" },
        duration_seconds: null,
        limitation: "当前未启用预测。",
      },
    ]);

    expect(container.textContent).toContain("2/3 完成");
    expect(container.textContent).toContain("数据点 1000");
    expect(container.textContent).toContain("已跳过");
    expect(container.textContent).toContain("已记录");
    expect(container.querySelector(".trace-completed")).not.toBeNull();
    expect(container.querySelector(".trace-skipped")).not.toBeNull();
    app.unmount();
  });

  it("兼容没有执行轨迹的旧历史结果", () => {
    const { app, container } = mountPanel([]);
    expect(container.textContent).toContain("该历史任务未保存执行轨迹");
    app.unmount();
  });
});
