import { computed, onBeforeUnmount, ref } from "vue";

/**
 * 分析任务控制器。
 *
 * App.vue 只负责页面状态和业务编排；上传、轮询、取消、刷新恢复以及
 * 进度计时都集中在这里，避免任务状态散落在多个页面事件中。
 */
export function useAnalysisJob({
  api,
  config,
  selectedFile,
  selectedSampleFileId,
  filePreflight,
  preflightAccepted,
  showPreflight,
  inspectCsvFile,
  confirmDiscardChanges,
  refreshHistory,
  activeTab,
  successMessage,
  errorMessage,
}) {
  const isAnalyzing = ref(false);
  const jobStatus = ref("");
  const progressStage = ref("idle");
  const progressPercent = ref(0);
  const progressDetail = ref("等待提交任务");
  const analysisElapsed = ref(0);
  const runId = ref("");
  const analysis = ref(null);
  const activeJobId = ref("");
  const cancellingJob = ref(false);
  const cancelRequested = ref(false);
  const progressTimer = ref(null);
  const activeJobStorageKey = "shicha_qianji_active_job";

  const progressSteps = computed(() => [
    { id: "uploading", label: "接收数据", detail: "校验 CSV 并登记文件" },
    { id: "queued", label: "任务排队", detail: "建立可追溯分析任务" },
    { id: "running", label: "智能分析", detail: "检测、预测与根因研判" },
    { id: "finalizing", label: "整理结果", detail: "生成证据和运维工单" },
  ]);

  function setProgress(stage, percent, detail) {
    progressStage.value = stage;
    progressPercent.value = percent;
    progressDetail.value = detail;
  }

  function stopProgressTimer() {
    if (progressTimer.value !== null) {
      window.clearInterval(progressTimer.value);
      progressTimer.value = null;
    }
  }

  function startProgressTimer() {
    stopProgressTimer();
    const startedAt = Date.now();
    analysisElapsed.value = 0;
    progressTimer.value = window.setInterval(() => {
      analysisElapsed.value = Math.floor((Date.now() - startedAt) / 1000);
      // 后端没有返回算法内部的精确百分比，因此前端只显示有上限的执行进度。
      if (progressStage.value === "running") {
        progressPercent.value = Math.min(88, Math.max(progressPercent.value, 38 + Math.floor(analysisElapsed.value / 3)));
      }
    }, 1000);
  }

  function persistActiveJob(jobId) {
    if (jobId) window.localStorage.setItem(activeJobStorageKey, jobId);
    else window.localStorage.removeItem(activeJobStorageKey);
  }

  function loadAnalysisResult(result, sourceRunId = "") {
    if (!result) return;
    analysis.value = result;
    runId.value = sourceRunId || result.run_id || "";
  }

  async function pollJob(id) {
    // 比无限轮询更适合比赛现场：超过两分钟后交给历史记录继续查看。
    const timeoutAt = Date.now() + 120000;
    while (Date.now() < timeoutAt) {
      if (cancelRequested.value) throw new Error("任务已取消");
      const status = await api.getJobStatus(id);
      if (cancelRequested.value) throw new Error("任务已取消");
      jobStatus.value = status.job_status === "queued"
        ? "排队中"
        : status.job_status === "running" ? "执行中" : status.job_status;
      if (status.job_status === "queued") {
        setProgress("queued", Math.max(progressPercent.value, 20), "任务已进入队列，等待分析引擎调度...");
      } else if (status.job_status === "running") {
        setProgress("running", Math.max(progressPercent.value, 38), "正在执行异常检测、趋势预测和根因研判...");
      }
      if (status.job_status === "success") {
        const result = await api.getJobResult(id);
        loadAnalysisResult(result.result, id);
        setProgress("finalizing", 94, "分析结果已生成，正在加载到工作台...");
        return;
      }
      if (["failed", "cancelled"].includes(status.job_status)) {
        throw new Error(status.error || `任务${status.job_status}`);
      }
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
    throw new Error("分析任务等待超时，请到历史记录查看任务状态。");
  }

  async function startAnalysis() {
    if (confirmDiscardChanges && !(await confirmDiscardChanges())) return;
    if (!selectedFile.value) throw new Error("请先选择一份 CSV 文件。");
    if (!filePreflight.value) await inspectCsvFile(selectedFile.value);
    if (!filePreflight.value) return;
    if (!preflightAccepted.value) {
      showPreflight.value = true;
      return;
    }

    showPreflight.value = false;
    preflightAccepted.value = false;
    isAnalyzing.value = true;
    cancelRequested.value = false;
    jobStatus.value = "准备中";
    setProgress("uploading", 8, "正在接收并校验 CSV 文件...");
    startProgressTimer();
    errorMessage.value = "";
    successMessage.value = "";
    analysis.value = null;
    try {
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const uploaded = selectedSampleFileId.value
        ? { file_id: selectedSampleFileId.value }
        : await api.uploadCsv(selectedFile.value);
      setProgress("queued", 20, "文件已接收，正在建立分析任务...");
      jobStatus.value = "已提交";
      const accepted = await api.createJob(uploaded.file_id, { ...config });
      runId.value = accepted.run_id;
      activeJobId.value = accepted.run_id;
      persistActiveJob(accepted.run_id);
      await pollJob(accepted.run_id);
      setProgress("finalizing", 94, "分析完成，正在整理风险证据和工单...");
      await new Promise((resolve) => setTimeout(resolve, 350));
      await refreshHistory();
      activeTab.value = "overview";
      setProgress("success", 100, "分析完成，结果已加载");
      jobStatus.value = "已完成";
      successMessage.value = "分析完成，已生成风险事件和运维工单。";
    } catch (error) {
      if (cancelRequested.value) {
        setProgress("failed", progressPercent.value, "任务已取消，原始文件和任务记录仍保留。");
        jobStatus.value = "已取消";
        successMessage.value = "分析任务已取消。";
      } else {
        setProgress("failed", progressPercent.value, "任务未完成，请查看下方错误信息");
        jobStatus.value = "失败";
        errorMessage.value = error.message;
      }
    } finally {
      isAnalyzing.value = false;
      activeJobId.value = "";
      persistActiveJob("");
      stopProgressTimer();
    }
  }

  async function cancelActiveJob(requestConfirmation) {
    if (!activeJobId.value || cancellingJob.value) return;
    if (requestConfirmation && !(await requestConfirmation())) return;
    cancellingJob.value = true;
    cancelRequested.value = true;
    try {
      await api.cancelJob(activeJobId.value);
      setProgress("failed", progressPercent.value, "任务已取消，原始文件和任务记录仍保留。");
      jobStatus.value = "已取消";
      successMessage.value = "分析任务已取消。";
      isAnalyzing.value = false;
      activeJobId.value = "";
      stopProgressTimer();
    } catch (error) {
      cancelRequested.value = false;
      errorMessage.value = error.message;
    } finally {
      cancellingJob.value = false;
    }
  }

  async function resumeActiveJob() {
    const savedJobId = window.localStorage.getItem(activeJobStorageKey);
    if (!savedJobId || isAnalyzing.value) return;
    try {
      const status = await api.getJobStatus(savedJobId);
      if (["success", "failed", "cancelled"].includes(status.job_status)) {
        persistActiveJob("");
        return;
      }
      runId.value = savedJobId;
      activeJobId.value = savedJobId;
      isAnalyzing.value = true;
      cancelRequested.value = false;
      jobStatus.value = status.job_status === "queued" ? "排队中" : "执行中";
      setProgress(status.job_status === "queued" ? "queued" : "running", status.job_status === "queued" ? 20 : 38, "检测到未完成任务，正在恢复分析进度...");
      startProgressTimer();
      await pollJob(savedJobId);
      await refreshHistory();
      activeTab.value = "overview";
      setProgress("success", 100, "分析完成，结果已恢复");
      jobStatus.value = "已完成";
      successMessage.value = "已恢复上次未完成的分析任务。";
    } catch (error) {
      setProgress("failed", progressPercent.value, "任务恢复失败，请到历史记录查看任务状态。");
      errorMessage.value = error.message;
    } finally {
      isAnalyzing.value = false;
      activeJobId.value = "";
      persistActiveJob("");
      stopProgressTimer();
    }
  }

  onBeforeUnmount(() => stopProgressTimer());

  return {
    isAnalyzing, jobStatus, progressStage, progressPercent, progressDetail,
    analysisElapsed, runId, analysis, activeJobId, cancellingJob, cancelRequested,
    progressSteps, setProgress, startProgressTimer, stopProgressTimer,
    persistActiveJob, loadAnalysisResult, pollJob, startAnalysis,
    cancelActiveJob, resumeActiveJob,
  };
}
