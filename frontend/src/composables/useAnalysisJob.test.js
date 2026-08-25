import { createApp, h, reactive, ref, shallowRef } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAnalysisJob } from "./useAnalysisJob";

function createHarness(overrides = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  // shallowRef 避免 Vue 对 composable 返回对象做深层解包，测试可以直接检查各个 ref。
  const exposed = shallowRef(null);
  const deps = {
    api: {
      getJobStatus: vi.fn(),
      getJobResult: vi.fn(),
      uploadCsv: vi.fn(),
      createJob: vi.fn(),
      cancelJob: vi.fn(),
    },
    config: reactive({ detector: "mad", threshold: 4.5 }),
    selectedFile: ref(null),
    selectedSampleFileId: ref(""),
    filePreflight: ref(null),
    preflightAccepted: ref(false),
    showPreflight: ref(false),
    inspectCsvFile: vi.fn(),
    confirmDiscardChanges: vi.fn(async () => true),
    refreshHistory: vi.fn(async () => {}),
    activeTab: ref("overview"),
    successMessage: ref(""),
    errorMessage: ref(""),
    ...overrides,
  };

  const app = createApp({
    setup() {
      exposed.value = useAnalysisJob(deps);
      return () => h("div");
    },
  });
  app.mount(container);
  return { app, container, state: exposed.value, deps };
}

afterEach(() => {
  document.body.innerHTML = "";
  localStorage.clear();
  vi.useRealTimers();
});

describe("useAnalysisJob", () => {
  it("按 queued -> running -> success 推进，并加载最终结果", async () => {
    const { app, state, deps } = createHarness();
    deps.api.getJobStatus
      .mockResolvedValueOnce({ job_status: "queued" })
      .mockResolvedValueOnce({ job_status: "running" })
      .mockResolvedValueOnce({ job_status: "success" });
    deps.api.getJobResult.mockResolvedValue({ result: { run_id: "run-1", anomaly_events: [] } });

    await state.pollJob("run-1");

    expect(deps.api.getJobStatus).toHaveBeenCalledTimes(3);
    expect(deps.api.getJobResult).toHaveBeenCalledWith("run-1");
    expect(state.analysis.value.run_id).toBe("run-1");
    expect(state.progressStage.value).toBe("finalizing");
    app.unmount();
  });

  it("失败状态会抛出后端错误信息", async () => {
    const { app, state, deps } = createHarness();
    deps.api.getJobStatus.mockResolvedValue({
      job_status: "failed",
      error: "CSV 格式不正确",
    });
    state.setProgress("queued", 20, "等待");
    state.cancelRequested.value = false;
    const statusPromise = state.pollJob("run-failed");
    app.unmount();
    await expect(statusPromise).rejects.toThrow("CSV 格式不正确");
  });
});
