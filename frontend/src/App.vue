<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import {
  createJob,
  archiveRun,
  archiveWorkOrder,
  cancelJob,
  getJobResult,
  getJobStatus,
  getRun,
  health,
  listCases,
  listRuns,
  listWorkOrders,
  removeCase,
  restoreRun,
  restoreWorkOrder,
  updateWorkOrder,
  uploadCsv,
} from "./api";
import TimeSeriesChart from "./components/TimeSeriesChart.vue";
import ForecastChart from "./components/ForecastChart.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import AnalysisProgressPanel from "./components/AnalysisProgressPanel.vue";
import PreflightModal from "./components/PreflightModal.vue";
import WorkOrderPanel from "./components/WorkOrderPanel.vue";
import HistoryPanel from "./components/HistoryPanel.vue";

const tabs = [
  { id: "overview", label: "风险总览" },
  { id: "evidence", label: "异常证据" },
  { id: "forecast", label: "趋势研判" },
  { id: "work-orders", label: "运维工单" },
  { id: "history", label: "历史记录" },
];

const activeTab = ref("overview");
const selectedFile = ref(null);
const fileInput = ref(null);
const filePreflight = ref(null);
const showPreflight = ref(false);
const preflightAccepted = ref(false);
const isAnalyzing = ref(false);
const jobStatus = ref("");
const progressStage = ref("idle");
const progressPercent = ref(0);
const progressDetail = ref("等待提交任务");
const analysisElapsed = ref(0);
const progressTimer = ref(null);
const apiStatus = ref("检查中");
const errorMessage = ref("");
const successMessage = ref("");
const runId = ref("");
const analysis = ref(null);
const runs = ref([]);
const workOrders = ref([]);
const cases = ref([]);
const selectedWorkOrder = ref(null);
const selectedWorkOrderAnalysis = ref(null);
const workOrderLoading = ref(false);
const historyRunLoading = ref("");
const selectedCase = ref(null);
const selectedCaseAnalysis = ref(null);
const caseLoading = ref(false);
const workOrderSearch = ref("");
const workOrderStatusFilter = ref("");
const workOrderPriorityFilter = ref("");
const workOrderPage = ref(1);
const workOrderPageSize = 10;
const workOrderTotal = ref(0);
const historyWorkOrderTotal = ref(0);
const workOrdersLoading = ref(false);
const workOrderFilterTimer = ref(null);
const workOrderActionId = ref("");
const historyStatus = ref("");
const historySearch = ref("");
const historyPage = ref(1);
const historyPageSize = 10;
const historyActionId = ref("");
const showArchived = ref(false);
const refreshingHistory = ref(false);
const lastDataSyncAt = ref(null);
const autoRefreshTimer = ref(null);
const selectedSensor = ref("");
const selectedForecastSensor = ref("");
const activeJobId = ref("");
const cancellingJob = ref(false);
const cancelRequested = ref(false);
const retryingRunId = ref("");

const config = reactive({
  detector: "time_frequency_relation",
  threshold: 4.5,
  rolling_window: 61,
  min_event_length: 3,
});

const feedback = reactive({
  status: "待确认",
  confirmed_cause: "",
  feedback_note: "",
  handled_by: "",
});
const feedbackDraftSnapshot = ref("");
const savingFeedback = ref(false);
const feedbackNotice = ref(null);
const toastNotice = ref(null);
const toastTimer = ref(null);
const confirmDialog = ref(null);
let confirmResolver = null;
let workOrderDetailRequestToken = 0;
let caseDetailRequestToken = 0;

const events = computed(() => analysis.value?.anomaly_events || []);
const diagnoses = computed(() => analysis.value?.root_cause_diagnoses || []);
const forecasts = computed(() => analysis.value?.forecast_results || {});
const relationships = computed(() => analysis.value?.relationship_diagnostics || []);
const regimes = computed(() => analysis.value?.operating_regimes || null);
const riskAlertCount = computed(() => (analysis.value?.risk_alerts || []).length);
const visualization = computed(() => analysis.value?.visualization || null);
const chartSensors = computed(() => visualization.value?.sensor_columns || []);
const selectedSensorValues = computed(() => visualization.value?.series?.[selectedSensor.value] || []);
const forecastSensors = computed(() => Object.keys(forecasts.value));
const selectedForecast = computed(() => forecasts.value[selectedForecastSensor.value] || null);
const selectedWorkOrderEvent = computed(() => {
  const order = selectedWorkOrder.value;
  const result = selectedWorkOrderAnalysis.value;
  if (!order || !result) return null;
  return (result.anomaly_events || [])[Number(order.event_number) - 1] || null;
});
const selectedWorkOrderDiagnosis = computed(() => {
  const order = selectedWorkOrder.value;
  const result = selectedWorkOrderAnalysis.value;
  if (!order || !result) return null;
  return (result.root_cause_diagnoses || []).find(
    (item) => Number(item.event_number) === Number(order.event_number),
  ) || null;
});
const filteredWorkOrders = computed(() => {
  return workOrders.value;
});
const workOrderPageCount = computed(() => Math.max(1, Math.ceil(workOrderTotal.value / workOrderPageSize)));
const filteredHistoryRuns = computed(() => {
  const keyword = historySearch.value.trim().toLowerCase();
  if (!keyword) return runs.value;
  return runs.value.filter((run) => [run.file_name, run.run_id, run.detector, run.status]
    .some((value) => String(value || "").toLowerCase().includes(keyword)));
});
const historyPageCount = computed(() => Math.max(1, Math.ceil(filteredHistoryRuns.value.length / historyPageSize)));
const paginatedHistoryRuns = computed(() => {
  const start = (historyPage.value - 1) * historyPageSize;
  return filteredHistoryRuns.value.slice(start, start + historyPageSize);
});
const feedbackDirty = computed(() => (
  Boolean(selectedWorkOrder.value) && feedbackDraftSnapshot.value !== feedbackSnapshot()
));
let workOrderRequestToken = 0;
const caseEventNumber = computed(() => {
  const source = selectedCase.value?.source_record_id || "";
  const match = source.match(/:WO-E(\d+)-/);
  return match ? Number(match[1]) : 0;
});
const selectedCaseEvent = computed(() =>
  selectedCaseAnalysis.value?.anomaly_events?.[caseEventNumber.value - 1] || null,
);
const selectedCaseDiagnosis = computed(() =>
  selectedCaseAnalysis.value?.root_cause_diagnoses?.find(
    (item) => Number(item.event_number) === caseEventNumber.value,
  ) || null,
);
const dataQuality = computed(() => analysis.value?.data_quality || {});
const closedLoop = computed(() => ({
  dataPoints: analysis.value?.data_profile?.row_count || 0,
  events: events.value.length,
  diagnoses: diagnoses.value.length,
  workOrders: analysis.value?.work_order_drafts?.length || 0,
  confirmed: cases.value.filter((item) => item.source_run_id === analysis.value?.run_id).length,
}));
const highestRisk = computed(() => {
  const levels = { 高风险: 3, 中风险: 2, 低风险: 1 };
  return events.value.reduce((highest, event) =>
    (levels[event.severity] || 0) > (levels[highest] || 0) ? event.severity : highest,
  "正常");
});

const progressSteps = computed(() => [
  { id: "uploading", label: "接收数据", detail: "校验 CSV 并登记文件" },
  { id: "queued", label: "任务排队", detail: "建立可追溯分析任务" },
  { id: "running", label: "智能分析", detail: "检测、预测与根因研判" },
  { id: "finalizing", label: "整理结果", detail: "生成证据和运维工单" },
]);

onMounted(async () => {
  await refreshHistory();
  try {
    await health();
    apiStatus.value = "在线";
  } catch {
    apiStatus.value = "离线";
  }
  window.addEventListener("beforeunload", handleBeforeUnload);
  window.addEventListener("keydown", handleGlobalKeydown);
  window.addEventListener("focus", handleWindowFocus);
  document.addEventListener("visibilitychange", handleVisibilityChange);
  startAutoRefresh();
});

onBeforeUnmount(() => {
  stopProgressTimer();
  clearToastTimer();
  if (workOrderFilterTimer.value !== null) window.clearTimeout(workOrderFilterTimer.value);
  window.removeEventListener("beforeunload", handleBeforeUnload);
  window.removeEventListener("keydown", handleGlobalKeydown);
  window.removeEventListener("focus", handleWindowFocus);
  document.removeEventListener("visibilitychange", handleVisibilityChange);
  stopAutoRefresh();
});

function startAutoRefresh() {
  stopAutoRefresh();
  autoRefreshTimer.value = window.setInterval(() => {
    if (document.visibilityState === "visible" && !isAnalyzing.value && !refreshingHistory.value) {
      refreshOperationalData();
    }
  }, 20000);
}

function stopAutoRefresh() {
  if (autoRefreshTimer.value !== null) {
    window.clearInterval(autoRefreshTimer.value);
    autoRefreshTimer.value = null;
  }
}

function handleWindowFocus() {
  if (document.visibilityState === "visible" && !isAnalyzing.value && !refreshingHistory.value) {
    refreshOperationalData();
  }
}

function handleVisibilityChange() {
  if (document.visibilityState === "visible") handleWindowFocus();
}

function handleGlobalKeydown(event) {
  if (event.key === "Escape" && confirmDialog.value) {
    resolveConfirmation(false);
  }
}

function feedbackSnapshot() {
  return JSON.stringify({
    status: feedback.status,
    confirmed_cause: feedback.confirmed_cause,
    feedback_note: feedback.feedback_note,
    handled_by: feedback.handled_by,
  });
}

function handleBeforeUnload(event) {
  if (!feedbackDirty.value) return;
  event.preventDefault();
  event.returnValue = "当前工单有未保存修改。";
}

function requestConfirmation({ title, detail, confirmText = "继续", tone = "warning" }) {
  if (confirmResolver) return Promise.resolve(false);
  confirmDialog.value = { title, detail, confirmText, tone };
  return new Promise((resolve) => {
    confirmResolver = resolve;
  });
}

function resolveConfirmation(result) {
  if (confirmResolver) confirmResolver(result);
  confirmResolver = null;
  confirmDialog.value = null;
}

function confirmDiscardChanges() {
  if (!feedbackDirty.value) return true;
  return requestConfirmation({
    title: "当前工单有未保存修改",
    detail: "继续操作会丢失尚未保存的现场反馈，请确认是否放弃这些修改。",
    confirmText: "放弃修改",
    tone: "warning",
  });
}

async function changeTab(tabId) {
  if (!(await confirmDiscardChanges())) return;
  activeTab.value = tabId;
}

function scheduleWorkOrderRefresh() {
  workOrderPage.value = 1;
  if (workOrderFilterTimer.value !== null) window.clearTimeout(workOrderFilterTimer.value);
  workOrderFilterTimer.value = window.setTimeout(() => {
    refreshWorkOrders();
    workOrderFilterTimer.value = null;
  }, 280);
}

watch([workOrderStatusFilter, workOrderPriorityFilter], scheduleWorkOrderRefresh);
watch(workOrderSearch, scheduleWorkOrderRefresh);
watch(historySearch, () => { historyPage.value = 1; });
watch(historyStatus, async () => {
  historyPage.value = 1;
  await refreshHistory();
});

function clearToastTimer() {
  if (toastTimer.value !== null) {
    window.clearTimeout(toastTimer.value);
    toastTimer.value = null;
  }
}

function showToast(notice, duration = 6000) {
  clearToastTimer();
  toastNotice.value = notice;
  if (duration > 0) {
    toastTimer.value = window.setTimeout(() => {
      toastNotice.value = null;
      toastTimer.value = null;
    }, duration);
  }
}

function closeToast() {
  clearToastTimer();
  toastNotice.value = null;
}

async function chooseFile() {
  if (!(await confirmDiscardChanges())) return;
  fileInput.value?.click();
}

function onFileChange(event) {
  selectedFile.value = event.target.files?.[0] || null;
  filePreflight.value = null;
  showPreflight.value = false;
  preflightAccepted.value = false;
  errorMessage.value = "";
  successMessage.value = "";
  if (selectedFile.value) inspectCsvFile(selectedFile.value);
}

// 只在浏览器内读取 CSV 的前几千行进行预检，帮助用户在提交分析前发现明显的数据问题。
// 这里不把文件内容发送给大模型，也不会替代后端最终的数据校验。
async function inspectCsvFile(file) {
  if (!file.name.toLowerCase().endsWith(".csv")) {
    errorMessage.value = "当前只支持 CSV 文件。";
    return;
  }
  if (file.size > 200 * 1024 * 1024) {
    errorMessage.value = "文件超过 200 MB，建议先按设备或时间范围拆分后再分析。";
    return;
  }
  try {
    const text = await file.text();
    const lines = text.split(/\r?\n/).filter((line) => line.trim());
    const sampleLines = lines.slice(0, 2001);
    const header = parseCsvLine(sampleLines[0] || "");
    const delimiter = detectDelimiter(sampleLines[0] || "");
    const columns = header.map((item) => item.trim().replace(/^\"|\"$/g, ""));
    const datetimeColumn = columns.find((column) => /time|date|datetime|timestamp|时间|日期/i.test(column)) || "";
    const sensorColumns = columns.filter((column) => column && column !== datetimeColumn && !/label|target|class|状态|标签/i.test(column));
    const numericRows = sampleLines.slice(1).map((line) => parseCsvLine(line, delimiter));
    let missingCells = 0;
    let observedCells = 0;
    numericRows.forEach((row) => sensorColumns.forEach((column) => {
      const index = columns.indexOf(column);
      const value = row[index];
      observedCells += 1;
      if (value === undefined || value.trim() === "" || ["nan", "null", "none"].includes(value.trim().toLowerCase())) missingCells += 1;
    }));
    const sampledDataRows = Math.max(lines.length - 1, 0);
    const missingRate = observedCells ? missingCells / observedCells : 0;
    filePreflight.value = {
      fileName: file.name,
      sizeLabel: formatFileSize(file.size),
      rowCount: sampledDataRows,
      sampleCount: numericRows.length,
      delimiter: delimiter === "\t" ? "制表符" : delimiter,
      columns,
      datetimeColumn,
      sensorCount: sensorColumns.length,
      missingRate,
      warnings: [
        !datetimeColumn ? "未识别到时间列，后端将按数据行顺序处理。" : "",
        sensorColumns.length < 2 ? "可识别的传感器列少于 2 列，多变量关系诊断能力会受限。" : "",
        missingRate > 0.1 ? `抽样缺失率约 ${(missingRate * 100).toFixed(1)}%，建议先检查数据完整性。` : "",
        sampledDataRows < 100 ? "数据行数较少，趋势预测和工况分段结果可能不稳定。" : "",
      ].filter(Boolean),
    };
    showPreflight.value = true;
  } catch (error) {
    filePreflight.value = null;
    errorMessage.value = `CSV 预检失败：${error.message}`;
  }
}

function detectDelimiter(line) {
  const candidates = [",", ";", "\t"];
  return candidates.sort((a, b) => countDelimiter(line, b) - countDelimiter(line, a))[0];
}

function countDelimiter(line, delimiter) {
  return line.split(delimiter).length - 1;
}

function parseCsvLine(line, delimiter = detectDelimiter(line)) {
  const values = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === delimiter && !quoted) {
      values.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  values.push(current);
  return values;
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function startProgressTimer() {
  stopProgressTimer();
  const startedAt = Date.now();
  analysisElapsed.value = 0;
  progressTimer.value = window.setInterval(() => {
    analysisElapsed.value = Math.floor((Date.now() - startedAt) / 1000);
    // 后端没有把算法内部阶段伪装成精确百分比，前端只做有上限的执行进度提示。
    if (progressStage.value === "running") {
      progressPercent.value = Math.min(88, Math.max(progressPercent.value, 38 + Math.floor(analysisElapsed.value / 3)));
    }
  }, 1000);
}

function loadAnalysisResult(result, sourceRunId = "") {
  if (!result) return;
  analysis.value = result;
  runId.value = sourceRunId || result.run_id || "";
  selectedSensor.value = result.visualization?.sensor_columns?.[0] || "";
  selectedForecastSensor.value = Object.keys(result.forecast_results || {})[0] || "";
}

function stopProgressTimer() {
  if (progressTimer.value !== null) {
    window.clearInterval(progressTimer.value);
    progressTimer.value = null;
  }
}

function setProgress(stage, percent, detail) {
  progressStage.value = stage;
  progressPercent.value = percent;
  progressDetail.value = detail;
}

async function startAnalysis() {
  if (feedbackDirty.value && !(await confirmDiscardChanges())) return;
  if (!selectedFile.value) {
    errorMessage.value = "请先选择一份 CSV 文件。";
    return;
  }
  if (!filePreflight.value) {
    await inspectCsvFile(selectedFile.value);
  }
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
    const uploaded = await uploadCsv(selectedFile.value);
    setProgress("queued", 20, "文件已接收，正在建立分析任务...");
    jobStatus.value = "已提交";
    const accepted = await createJob(uploaded.file_id, { ...config });
    runId.value = accepted.run_id;
    activeJobId.value = accepted.run_id;
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
    stopProgressTimer();
  }
}

function confirmPreflightAndStart() {
  preflightAccepted.value = true;
  startAnalysis();
}

async function cancelActiveJob() {
  if (!activeJobId.value || cancellingJob.value) return;
  if (!(await requestConfirmation({
    title: "取消排队中的分析任务？",
    detail: "已上传文件和任务记录仍会保留，但本次分析不会继续执行。",
    confirmText: "确认取消",
    tone: "warning",
  }))) return;
  cancellingJob.value = true;
  cancelRequested.value = true;
  let cancelled = false;
  try {
    await cancelJob(activeJobId.value);
    cancelled = true;
    setProgress("failed", progressPercent.value, "任务已取消，原始文件和任务记录仍保留。");
    jobStatus.value = "已取消";
    successMessage.value = "分析任务已取消。";
  } catch (error) {
    cancelRequested.value = false;
    errorMessage.value = error.message;
  } finally {
    cancellingJob.value = false;
    if (cancelled) {
      isAnalyzing.value = false;
      activeJobId.value = "";
      stopProgressTimer();
    }
  }
}

async function pollJob(id) {
  const timeoutAt = Date.now() + 120000;
  while (Date.now() < timeoutAt) {
    if (cancelRequested.value) {
      throw new Error("任务已取消");
    }
    const status = await getJobStatus(id);
    // 取消请求可能与本次轮询同时返回，取消后不再继续处理任何成功结果。
    if (cancelRequested.value) {
      throw new Error("任务已取消");
    }
    jobStatus.value = status.job_status === "queued" ? "排队中" : status.job_status === "running" ? "执行中" : status.job_status;
    if (status.job_status === "queued") {
      setProgress("queued", Math.max(progressPercent.value, 20), "任务已进入队列，等待分析引擎调度...");
    } else if (status.job_status === "running") {
      setProgress("running", Math.max(progressPercent.value, 38), "正在执行异常检测、趋势预测和根因研判...");
    }
    if (status.job_status === "success") {
      const result = await getJobResult(id);
      loadAnalysisResult(result.result, id);
      setProgress("finalizing", 94, "分析结果已生成，正在加载到工作台...");
      return;
    }
    if (["failed", "cancelled"].includes(status.job_status)) {
      throw new Error(status.error || `任务${status.job_status}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  throw new Error("分析任务等待超时，请到历史记录查看任务状态。" );
}

async function refreshHistory() {
  if (refreshingHistory.value) return;
  refreshingHistory.value = true;
  try {
    const [runResponse, caseResponse, workOrderSummary] = await Promise.all([
      listRuns(historyStatus.value, showArchived.value, showArchived.value),
      listCases(showArchived.value, showArchived.value),
      listWorkOrders(showArchived.value, showArchived.value, { limit: 1, offset: 0 }),
    ]);
    runs.value = runResponse.runs || [];
    if (historyPage.value > historyPageCount.value) historyPage.value = historyPageCount.value;
    cases.value = caseResponse.cases || [];
    historyWorkOrderTotal.value = Number(workOrderSummary.work_order_count || 0);
    await refreshWorkOrders();
    lastDataSyncAt.value = new Date();
  } catch (error) {
    errorMessage.value = error.message;
  } finally {
    refreshingHistory.value = false;
  }
}

async function refreshOperationalData() {
  // 自动同步只刷新列表和案例，不重置当前分析结果；有未保存反馈时不会覆盖编辑草稿。
  try {
    const [runResponse, caseResponse, workOrderSummary] = await Promise.all([
      listRuns(historyStatus.value, showArchived.value, showArchived.value),
      listCases(showArchived.value, showArchived.value),
      listWorkOrders(showArchived.value, showArchived.value, { limit: 1, offset: 0 }),
    ]);
    runs.value = runResponse.runs || [];
    cases.value = caseResponse.cases || [];
    historyWorkOrderTotal.value = Number(workOrderSummary.work_order_count || 0);
    await refreshWorkOrders();
    apiStatus.value = "在线";
    lastDataSyncAt.value = new Date();
  } catch (error) {
    apiStatus.value = "离线";
    // 自动刷新失败不覆盖页面上的业务提示，顶部 API 状态会明确显示连接异常。
  }
}

function changeHistoryPage(page) {
  historyPage.value = Math.max(1, Math.min(historyPageCount.value, page));
}

async function refreshWorkOrders() {
  const token = ++workOrderRequestToken;
  const hadFeedbackDraft = feedbackDirty.value;
  workOrdersLoading.value = true;
  try {
    const response = await listWorkOrders(showArchived.value, showArchived.value, {
      limit: workOrderPageSize,
      offset: (workOrderPage.value - 1) * workOrderPageSize,
      search: workOrderSearch.value,
      status: workOrderStatusFilter.value,
      priority: workOrderPriorityFilter.value,
    });
    if (token !== workOrderRequestToken) return;
    workOrders.value = response.work_orders || [];
    workOrderTotal.value = Number(response.work_order_count || 0);
    const latestSelected = workOrders.value.find(
      (item) => item.record_id === selectedWorkOrder.value?.record_id,
    );
    if (latestSelected && !hadFeedbackDraft) {
      selectedWorkOrder.value = latestSelected;
      feedback.status = latestSelected.status;
      feedback.confirmed_cause = latestSelected.confirmed_cause || "";
      feedback.feedback_note = latestSelected.feedback_note || "";
      feedback.handled_by = latestSelected.handled_by || "";
      feedbackDraftSnapshot.value = feedbackSnapshot();
    }
    if (workOrderPage.value > workOrderPageCount.value) {
      workOrderPage.value = workOrderPageCount.value;
      await refreshWorkOrders();
    }
  } catch (error) {
    if (token === workOrderRequestToken) errorMessage.value = error.message;
  } finally {
    if (token === workOrderRequestToken) workOrdersLoading.value = false;
  }
}

async function changeWorkOrderPage(page) {
  if (feedbackDirty.value && !(await confirmDiscardChanges())) return;
  workOrderPage.value = Math.max(1, Math.min(workOrderPageCount.value, page));
  selectedWorkOrder.value = null;
  feedbackNotice.value = null;
  await refreshWorkOrders();
}

async function viewHistoryRun(run) {
  if (!run?.run_id || historyRunLoading.value) return;
  if (!(await confirmDiscardChanges())) return;
  historyRunLoading.value = run.run_id;
  errorMessage.value = "";
  try {
    const response = await getRun(run.run_id);
    loadAnalysisResult(response.run?.result, run.run_id);
    if (!response.run?.result) {
      throw new Error("该任务没有可查看的分析结果。" );
    }
    activeTab.value = "overview";
    successMessage.value = `已加载历史任务：${run.file_name}`;
  } catch (error) {
    errorMessage.value = error.message;
  } finally {
    historyRunLoading.value = "";
  }
}

async function retryHistoryRun(run) {
  if (!run?.file_id || retryingRunId.value || ["queued", "running"].includes(run.status) || run.archived_at) return;
  if (!(await confirmDiscardChanges())) return;
  retryingRunId.value = run.run_id;
  errorMessage.value = "";
  successMessage.value = "";
  try {
    const accepted = await createJob(run.file_id, run.config || { ...config });
    runId.value = accepted.run_id;
    activeJobId.value = accepted.run_id;
    isAnalyzing.value = true;
    cancelRequested.value = false;
    analysis.value = null;
    setProgress("queued", 20, "重试任务已提交，等待分析引擎调度...");
    startProgressTimer();
    await pollJob(accepted.run_id);
    setProgress("finalizing", 94, "重试分析完成，正在整理结果...");
    await refreshHistory();
    activeTab.value = "overview";
    setProgress("success", 100, "重试分析完成，结果已加载");
    jobStatus.value = "已完成";
    successMessage.value = "分析任务已重试并完成。";
  } catch (error) {
    setProgress("failed", progressPercent.value, "重试任务未完成，请查看错误信息。");
    jobStatus.value = "失败";
    errorMessage.value = error.message;
  } finally {
    retryingRunId.value = "";
    isAnalyzing.value = false;
    activeJobId.value = "";
    stopProgressTimer();
  }
}

function downloadBlob(content, fileName, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}

function exportAnalysisJson() {
  if (!analysis.value) return;
  downloadBlob(JSON.stringify(analysis.value, null, 2), `shicha-analysis-${runId.value || "result"}.json`, "application/json;charset=utf-8");
  successMessage.value = "分析结果 JSON 已导出。";
}

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function exportWorkOrdersCsv() {
  const rows = filteredWorkOrders.value;
  const headers = ["工单编号", "所属任务", "标题", "优先级", "状态", "责任角色", "候选根因", "证据摘要", "处置动作", "反馈备注"];
  const lines = [headers.map(csvCell).join(",")];
  rows.forEach((order) => lines.push([
    order.record_id, order.run_id, order.title, order.priority, order.status, order.assigned_role,
    order.confirmed_cause, (order.evidence_summary || []).join("；"), (order.actions || []).join("；"), order.feedback_note,
  ].map(csvCell).join(",")));
  downloadBlob("\uFEFF" + lines.join("\r\n"), `shicha-work-orders-${runId.value || "all"}.csv`, "text/csv;charset=utf-8");
  successMessage.value = `已导出当前页 ${rows.length} 条工单。`;
}

function exportSummaryMarkdown() {
  if (!analysis.value) return;
  const profile = analysis.value.data_profile || {};
  const quality = analysis.value.data_quality || {};
  const lines = [
    "# 时察千机工业时序分析摘要",
    "",
    `- 分析任务：${runId.value || analysis.value.run_id || "-"}`,
    `- 数据文件：${profile.source_name || "-"}`,
    `- 数据点数：${profile.row_count || 0}`,
    `- 传感器数量：${profile.sensor_columns?.length || 0}`,
    `- 缺失数据：${quality.missing_total || 0} 个（${((Number(quality.missing_rate) || 0) * 100).toFixed(2)}%）`,
    "",
    "## 风险事件",
    ...(events.value.length ? events.value.map((event, index) => `${index + 1}. ${event.severity || "未分级"}，${formatDate(event.start_time)} - ${formatDate(event.end_time)}，峰值 ${formatNumber(event.peak_score)}`) : ["暂无持续异常事件"]),
    "",
    "## 运维建议",
    ...(analysis.value.recommendations?.length ? analysis.value.recommendations.map((item) => `- ${item}`) : ["暂无额外处置建议"]),
    "",
    "## 结果边界",
    ...(analysis.value.limitations?.length ? analysis.value.limitations.map((item) => `- ${item}`) : ["本报告仅用于校赛验证和辅助研判，现场处置需结合人工复核。"]),
  ];
  downloadBlob(lines.join("\n"), `shicha-summary-${runId.value || "result"}.md`, "text/markdown;charset=utf-8");
  successMessage.value = "分析摘要 Markdown 已导出。";
}

async function toggleArchivedRecords() {
  if (!(await confirmDiscardChanges())) return;
  showArchived.value = !showArchived.value;
  workOrderPage.value = 1;
  selectedWorkOrder.value = null;
  feedbackDraftSnapshot.value = "";
  selectedCase.value = null;
  await refreshHistory();
}

async function selectCase(item) {
  if (!(await confirmDiscardChanges())) return;
  const requestToken = ++caseDetailRequestToken;
  selectedCase.value = item;
  selectedCaseAnalysis.value = null;
  caseLoading.value = true;
  try {
    if (analysis.value?.run_id === item.source_run_id) {
      selectedCaseAnalysis.value = analysis.value;
    } else {
      const response = await getRun(item.source_run_id);
      if (requestToken === caseDetailRequestToken) {
        selectedCaseAnalysis.value = response.run?.result || null;
      }
    }
  } catch (error) {
    if (requestToken === caseDetailRequestToken) {
      errorMessage.value = `案例证据加载失败：${error.message}`;
    }
  } finally {
    if (requestToken === caseDetailRequestToken) caseLoading.value = false;
  }
}

async function deleteConfirmedCase(item) {
  if (!item?.case_id) return;
  if (!(await requestConfirmation({
    title: "永久移除已确认案例？",
    detail: "案例记忆、确认根因和现场反馈将被删除，但来源分析任务和原始数据仍会保留。此操作不可恢复。",
    confirmText: "永久移除",
    tone: "danger",
  }))) return;
  try {
    await removeCase(item.case_id);
    if (selectedCase.value?.case_id === item.case_id) {
      selectedCase.value = null;
      selectedCaseAnalysis.value = null;
    }
    successMessage.value = "已确认案例已永久移除，来源分析证据仍保留。";
    await refreshHistory();
  } catch (error) {
    errorMessage.value = error.message;
  }
}

function clearWorkOrderFilters() {
  workOrderSearch.value = "";
  workOrderStatusFilter.value = "";
  workOrderPriorityFilter.value = "";
}

async function archiveSelectedWorkOrder() {
  const order = selectedWorkOrder.value;
  if (!order || order.archived_at || workOrderActionId.value) return;
  if (!["已完成", "已关闭"].includes(order.status)) {
    errorMessage.value = "工单完成或关闭后才能归档。";
    return;
  }
  if (!(await requestConfirmation({
    title: "归档当前工单？",
    detail: "工单会从默认队列隐藏，但数据库中的分析证据和历史案例仍会保留。",
    confirmText: "确认归档",
    tone: "warning",
  }))) return;
  workOrderActionId.value = order.record_id;
  try {
    await archiveWorkOrder(order.record_id);
    selectedWorkOrder.value = null;
    successMessage.value = "工单已归档，原始数据仍保留在数据库中。";
    await refreshHistory();
  } catch (error) {
    errorMessage.value = error.message;
  } finally {
    workOrderActionId.value = "";
  }
}

async function restoreSelectedWorkOrder(order) {
  if (!order?.archived_at || workOrderActionId.value) return;
  workOrderActionId.value = order.record_id;
  try {
    await restoreWorkOrder(order.record_id);
    successMessage.value = "工单已恢复到默认队列。";
    await refreshHistory();
  } catch (error) {
    errorMessage.value = error.message;
  } finally {
    workOrderActionId.value = "";
  }
}

async function archiveHistoryRun(run) {
  if (!run || run.archived_at || historyActionId.value) return;
  if (["queued", "running"].includes(run.status)) {
    errorMessage.value = "排队中或运行中的任务不能归档。";
    return;
  }
  if (!(await requestConfirmation({
    title: "归档当前分析任务？",
    detail: "任务会从默认历史记录隐藏，但完整分析结果仍会保留。",
    confirmText: "确认归档",
    tone: "warning",
  }))) return;
  historyActionId.value = run.run_id;
  try {
    await archiveRun(run.run_id);
    successMessage.value = "分析任务已归档，数据仍保留在数据库中。";
    await refreshHistory();
  } catch (error) {
    errorMessage.value = error.message;
  } finally {
    historyActionId.value = "";
  }
}

async function restoreHistoryRun(run) {
  if (!run?.archived_at || historyActionId.value) return;
  historyActionId.value = run.run_id;
  try {
    await restoreRun(run.run_id);
    successMessage.value = "分析任务已恢复到默认历史记录。";
    await refreshHistory();
  } catch (error) {
    errorMessage.value = error.message;
  } finally {
    historyActionId.value = "";
  }
}

async function selectWorkOrder(order) {
  if (selectedWorkOrder.value?.record_id !== order?.record_id && !(await confirmDiscardChanges())) return;
  const requestToken = ++workOrderDetailRequestToken;
  selectedWorkOrder.value = order;
  selectedWorkOrderAnalysis.value = null;
  workOrderLoading.value = true;
  feedback.status = order.status;
  feedback.confirmed_cause = order.confirmed_cause || "";
  feedback.feedback_note = order.feedback_note || "";
  feedback.handled_by = order.handled_by || "";
  feedbackDraftSnapshot.value = feedbackSnapshot();
  feedbackNotice.value = null;
  try {
    if (analysis.value?.run_id === order.run_id) {
      selectedWorkOrderAnalysis.value = analysis.value;
    } else {
      const response = await getRun(order.run_id);
      if (requestToken === workOrderDetailRequestToken) {
        selectedWorkOrderAnalysis.value = response.run?.result || null;
      }
    }
  } catch (error) {
    if (requestToken === workOrderDetailRequestToken) {
      errorMessage.value = `工单证据加载失败：${error.message}`;
    }
  } finally {
    if (requestToken === workOrderDetailRequestToken) workOrderLoading.value = false;
  }
}

async function saveFeedback() {
  if (!selectedWorkOrder.value || savingFeedback.value) return;
  const statusOrder = ["待确认", "已确认", "处理中", "已完成", "已关闭"];
  const currentIndex = statusOrder.indexOf(selectedWorkOrder.value.status);
  const targetIndex = statusOrder.indexOf(feedback.status);
  if (currentIndex >= 0 && targetIndex > currentIndex + 1) {
    const confirmed = await requestConfirmation({
      title: "跳过中间状态？",
      detail: `工单将从“${selectedWorkOrder.value.status}”直接变更为“${feedback.status}”，中间状态会被跳过。`,
      confirmText: "继续保存",
      tone: "warning",
    });
    if (!confirmed) return;
  }
  const requiresCause = ["已确认", "已完成", "已关闭"].includes(feedback.status);
  if (requiresCause && !feedback.confirmed_cause.trim()) {
    feedbackNotice.value = {
      type: "warning",
      title: "还不能生成历史案例",
      detail: "请先填写现场确认根因，再保存为已确认、已完成或已关闭。",
    };
    showToast(feedbackNotice.value, 0);
    errorMessage.value = "已确认状态必须填写现场确认根因。";
    return;
  }
  savingFeedback.value = true;
  try {
    const savedResponse = await updateWorkOrder(selectedWorkOrder.value.record_id, { ...feedback });
    const savedOrder = savedResponse.work_order;
    const savedRecordId = selectedWorkOrder.value.record_id;
    if (savedOrder) {
      selectedWorkOrder.value = savedOrder;
      feedback.status = savedOrder.status;
      feedback.confirmed_cause = savedOrder.confirmed_cause || "";
      feedback.feedback_note = savedOrder.feedback_note || "";
      feedback.handled_by = savedOrder.handled_by || "";
      feedbackDraftSnapshot.value = feedbackSnapshot();
    }
    await refreshHistory();
    const caseCreated = cases.value.some((item) => item.source_record_id === savedRecordId);
    feedbackNotice.value = caseCreated
      ? { type: "success", title: "现场确认已保存", detail: "该工单已生成历史案例，可在“历史记录”中查看。" }
      : { type: "success", title: "工单状态已保存", detail: "当前还未形成历史案例；填写确认根因并保存为已确认或已完成后即可沉淀。" };
    showToast(feedbackNotice.value);
    successMessage.value = caseCreated ? "现场确认已保存，历史案例已生成。" : "工单状态已保存。";
  } catch (error) {
    feedbackNotice.value = { type: "error", title: "保存失败", detail: error.message };
    showToast(feedbackNotice.value, 0);
    errorMessage.value = error.message;
  } finally {
    savingFeedback.value = false;
  }
}

function diagnosisForEvent(index) {
  return diagnoses.value.find((item) => item.event_number === index) || null;
}

function formatNumber(value, digits = 2) {
  return typeof value === "number" ? value.toFixed(digits) : "-";
}

function formatDate(value) {
  if (!value) return "-";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("zh-CN");
}

function formatClock(value) {
  if (!value) return "-";
  const parsed = value instanceof Date ? value : new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "-"
    : parsed.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function forecastEntries() {
  return Object.entries(forecasts.value);
}

function forecastDirection(item) {
  const direction = item?.[1]?.方向;
  return direction || "维持";
}

function forecastRisk(item) {
  return item?.[1]?.风险 || "待评估";
}

function relationshipForEvent(index) {
  return relationships.value.find((item) => Number(item["事件编号"]) === index) || null;
}

function finiteValues(values) {
  return (values || []).map((value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  });
}

function chartPoints(values, width = 920, height = 250) {
  const clean = finiteValues(values);
  if (!clean.length) return "";
  const usable = clean.filter((value) => value !== null);
  if (!usable.length) return "";
  const minimum = Math.min(...usable);
  const maximum = Math.max(...usable);
  const spread = Math.max(maximum - minimum, Math.abs(maximum) * 0.001, 1e-6);
  return clean.map((value, index) => {
    const fallback = index > 0 && clean[index - 1] !== null ? clean[index - 1] : usable[0];
    const normalized = ((value ?? fallback) - minimum) / spread;
    const x = clean.length === 1 ? width / 2 : (index / (clean.length - 1)) * width;
    const y = height - Math.max(0, Math.min(1, normalized)) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function chartY(value, values, height = 250) {
  const clean = finiteValues(values).filter((item) => item !== null);
  if (!clean.length || value === null || value === undefined) return height;
  const minimum = Math.min(...clean);
  const maximum = Math.max(...clean);
  const spread = Math.max(maximum - minimum, Math.abs(maximum) * 0.001, 1e-6);
  return height - Math.max(0, Math.min(1, (Number(value) - minimum) / spread)) * height;
}

function chartMin(values) {
  const clean = finiteValues(values).filter((value) => value !== null);
  return clean.length ? formatNumber(Math.min(...clean)) : "-";
}

function chartMax(values) {
  const clean = finiteValues(values).filter((value) => value !== null);
  return clean.length ? formatNumber(Math.max(...clean)) : "-";
}

function selectSensor(sensor) {
  selectedSensor.value = sensor;
}

function selectForecastSensor(sensor) {
  selectedForecastSensor.value = sensor;
}

function contributionWidth(item) {
  const contributions = visualization.value?.sensor_contributions || [];
  const maximum = Number(contributions[0]?.score || 0);
  const current = Number(item?.score || 0);
  if (maximum <= 0) return "5%";
  return `${Math.min(100, Math.max(5, (current / maximum) * 100))}%`;
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand-block">
        <div class="brand-mark">SQ</div>
        <div>
          <div class="brand-name">时察千机</div>
          <div class="brand-subtitle">工业时序异常诊断与运维决策平台</div>
        </div>
      </div>
      <div class="topbar-meta">
        <span class="api-dot" :class="{ offline: apiStatus === '离线' }"></span>
        API {{ apiStatus }}
        <span class="divider"></span>
        <span>校赛验证环境</span>
        <span v-if="lastDataSyncAt" class="sync-meta">同步于 {{ formatClock(lastDataSyncAt) }}</span>
      </div>
    </header>

    <div class="workspace">
      <aside class="sidebar">
        <div class="sidebar-label">工作台</div>
        <button
          v-for="tab in tabs"
          :key="tab.id"
          class="nav-item"
          :class="{ active: activeTab === tab.id }"
          @click="changeTab(tab.id)"
        >
          <span class="nav-index">{{ String(tabs.indexOf(tab) + 1).padStart(2, '0') }}</span>
          {{ tab.label }}
        </button>

        <div class="sidebar-divider"></div>
        <div class="sidebar-label">数据任务</div>
        <button class="outline-button" @click="chooseFile">选择 CSV 文件</button>
        <input ref="fileInput" type="file" accept=".csv" hidden @change="onFileChange" />
        <div class="selected-file" v-if="selectedFile">
          <span class="file-type">CSV</span>
          <span class="file-name">{{ selectedFile.name }}</span>
        </div>
        <div class="file-placeholder" v-else>尚未选择数据文件</div>

        <label class="field-label" for="detector">检测器</label>
        <select id="detector" v-model="config.detector" class="control-input">
          <option value="time_frequency_relation">时频关系多路径</option>
          <option value="window_autoencoder">滑动窗口 AutoEncoder</option>
          <option value="hybrid">时序-工况混合</option>
          <option value="pca_reconstruction">PCA 多变量重构</option>
          <option value="mad">稳健 MAD</option>
        </select>
        <label class="field-label" for="threshold">异常阈值 {{ config.threshold }}</label>
        <input id="threshold" v-model.number="config.threshold" type="range" min="2" max="10" step="0.1" />
        <button class="primary-button" :disabled="isAnalyzing || !selectedFile" @click="startAnalysis">
          {{ isAnalyzing ? "分析进行中..." : "开始智能分析" }}
        </button>
        <div v-if="filePreflight" class="sidebar-preflight">
          <div class="preflight-title"><b>文件预检</b><span :class="{ warn: filePreflight.warnings.length }">{{ filePreflight.warnings.length ? '需确认' : '通过' }}</span></div>
          <p>{{ filePreflight.rowCount }} 行 · {{ filePreflight.sensorCount }} 个测点 · 缺失率 {{ (filePreflight.missingRate * 100).toFixed(1) }}%</p>
          <button class="outline-button preflight-button" :disabled="isAnalyzing" @click="showPreflight = true">查看检查结果</button>
        </div>
      </aside>

      <main class="main-content">
        <section class="page-heading">
          <div>
            <div class="eyebrow">INDUSTRIAL TIME-SERIES AGENT</div>
            <h1>{{ tabs.find((tab) => tab.id === activeTab)?.label }}</h1>
            <p v-if="analysis">当前任务：{{ runId || analysis.run_id }} · {{ analysis.data_profile?.source_name }}</p>
            <p v-else>从一份工业时序数据开始，逐步形成可追溯的风险判断和处置动作。</p>
          </div>
          <div class="heading-status" :class="{ busy: isAnalyzing }">
            <span class="status-dot"></span>
            {{ isAnalyzing ? "分析中" : "分析引擎在线" }}
          </div>
        </section>

        <div v-if="errorMessage" class="alert error">{{ errorMessage }}</div>
        <div v-if="successMessage" class="alert success">{{ successMessage }}</div>
        <div v-if="toastNotice" class="operation-toast" :class="toastNotice.type" role="status" aria-live="polite">
          <div class="toast-copy"><strong>{{ toastNotice.title }}</strong><span>{{ toastNotice.detail }}</span></div>
          <button class="toast-close" title="关闭操作提示" @click="closeToast">×</button>
        </div>

        <ConfirmDialog
          :model-value="Boolean(confirmDialog)"
          :title="confirmDialog?.title || ''"
          :detail="confirmDialog?.detail || ''"
          :confirm-text="confirmDialog?.confirmText || '继续'"
          :tone="confirmDialog?.tone || 'warning'"
          @confirm="resolveConfirmation(true)"
          @cancel="resolveConfirmation(false)"
        />

        <AnalysisProgressPanel
          v-if="isAnalyzing || progressStage === 'failed'"
          :run-id="runId"
          :stage="progressStage"
          :status="jobStatus || '准备中'"
          :percent="progressPercent"
          :detail="progressDetail"
          :elapsed="analysisElapsed"
          :steps="progressSteps"
          :cancelling="cancellingJob"
          :active-job-id="activeJobId"
          @cancel="cancelActiveJob"
        />

        <PreflightModal
          v-if="showPreflight && filePreflight && !isAnalyzing"
          :file="filePreflight"
          @close="showPreflight = false"
          @confirm="confirmPreflightAndStart"
        />

        <section v-if="activeTab === 'overview'" class="content-stack">
          <div v-if="!analysis" class="empty-panel">
            <div class="empty-icon">TS</div>
            <h2>等待一份工业时序数据</h2>
            <p>选择左侧 CSV 文件后，系统将自动完成数据画像、异常检测、趋势研判和工单生成。</p>
          </div>
          <template v-else>
            <div class="metric-grid">
              <div class="metric-card accent"><span>当前风险</span><strong>{{ highestRisk }}</strong></div>
              <div class="metric-card"><span>异常事件</span><strong>{{ events.length }}</strong></div>
              <div class="metric-card"><span>传感器数量</span><strong>{{ analysis.data_profile?.sensor_columns?.length || 0 }}</strong></div>
              <div class="metric-card"><span>数据点数</span><strong>{{ analysis.data_profile?.row_count || 0 }}</strong></div>
              <div class="metric-card"><span>风险告警</span><strong>{{ riskAlertCount }}</strong></div>
            </div>
            <div class="process-line">
              <span>数据接入</span><b>→</b><span>异常发现</span><b>→</b><span>证据解释</span><b>→</b><span>工单闭环</span>
            </div>
            <div v-if="visualization" class="panel chart-panel">
              <div class="panel-header"><h2>设备风险曲线</h2><span>悬停查看采样点 · 红色区间为异常事件</span></div>
              <TimeSeriesChart
                :timestamps="visualization.timestamps"
                :values="visualization.risk_scores"
                :bands="visualization.event_ranges"
                :markers="visualization.risk_scores.map((value, index) => ({ index, value })).filter((item) => visualization.anomaly_labels[item.index])"
                line-color="#c65d59"
                title="设备风险分数"
                unit="风险分"
                :threshold="visualization.threshold"
              />
            </div>
            <div class="panel data-quality-panel">
              <div class="panel-header"><h2>数据质量与分析范围</h2><span>分析前置检查结果</span></div>
              <div class="quality-grid">
                <div class="quality-item"><span>时间范围</span><b>{{ formatDate(analysis.data_profile?.start_time) }} - {{ formatDate(analysis.data_profile?.end_time) }}</b></div>
                <div class="quality-item"><span>采样周期</span><b>{{ dataQuality.sampling_seconds ? `${formatNumber(dataQuality.sampling_seconds, 2)} 秒` : "未估计" }}</b></div>
                <div class="quality-item"><span>缺失数据</span><b :class="{ 'quality-warn': dataQuality.missing_total > 0 }">{{ dataQuality.missing_total || 0 }} 个 · {{ formatNumber(Number(dataQuality.missing_rate || 0) * 100, 2) }}%</b></div>
                <div class="quality-item"><span>标签状态</span><b>{{ dataQuality.label_columns?.length ? `含 ${dataQuality.label_columns.join("、")} 标签` : "无监督标签" }}</b></div>
              </div>
            </div>
            <div class="panel closure-panel">
              <div class="panel-header"><h2>工业分析闭环</h2><span>本次任务产出</span></div>
              <div class="closure-flow">
                <div class="closure-step"><strong>{{ closedLoop.dataPoints }}</strong><span>数据点</span><small>数据接入</small></div><b>→</b>
                <div class="closure-step"><strong>{{ closedLoop.events }}</strong><span>异常事件</span><small>异常发现</small></div><b>→</b>
                <div class="closure-step"><strong>{{ closedLoop.diagnoses }}</strong><span>诊断结果</span><small>证据解释</small></div><b>→</b>
                <div class="closure-step"><strong>{{ closedLoop.workOrders }}</strong><span>候选工单</span><small>处置生成</small></div><b>→</b>
                <div class="closure-step"><strong>{{ closedLoop.confirmed }}</strong><span>已确认案例</span><small>反馈沉淀</small></div>
              </div>
            </div>
            <div class="two-column">
              <div class="panel">
                <div class="panel-header"><h2>异常事件时间线</h2><span>{{ analysis.detector }}</span></div>
                <div v-if="events.length" class="event-list">
                  <button v-for="(event, index) in events" :key="index" class="event-row" @click="changeTab('evidence')">
                    <span class="event-number">{{ String(index + 1).padStart(2, '0') }}</span>
                    <span class="event-body"><b>{{ event.severity }} · {{ event.duration_points }} 个采样点</b><small>{{ formatDate(event.start_time) }} - {{ formatDate(event.end_time) }}</small></span>
                    <span class="event-score">{{ formatNumber(event.peak_score) }}</span>
                  </button>
                </div>
                <div v-else class="panel-empty">当前未形成持续异常事件</div>
              </div>
              <div class="panel evidence-panel">
                <div class="panel-header"><h2>运维建议</h2><span>结构化输出</span></div>
                <ol v-if="analysis.recommendations?.length">
                  <li v-for="recommendation in analysis.recommendations.slice(0, 5)" :key="recommendation">{{ recommendation }}</li>
                </ol>
                <div v-else class="panel-empty">暂无额外处置建议</div>
              </div>
            </div>
            <div class="two-column">
              <div class="panel">
                <div class="panel-header"><h2>工况上下文</h2><span>{{ regimes?.state_count || 1 }} 个状态</span></div>
                <div v-if="regimes?.segments?.length" class="regime-list">
                  <div v-for="segment in regimes.segments.slice(0, 6)" :key="`${segment['开始时间']}-${segment['结束时间']}`" class="regime-row">
                    <b>{{ segment["工况编号"] ? `工况 ${segment["工况编号"]}` : "工况" }}</b>
                    <span>{{ formatDate(segment["开始时间"]) }} - {{ formatDate(segment["结束时间"]) }}</span>
                    <small>{{ segment["持续点数"] }} 点 · 过渡占比 {{ formatNumber(Number(segment["过渡点占比"] || 0) * 100, 1) }}%</small>
                  </div>
                </div>
                <div v-else class="panel-empty">暂无稳定工况分段</div>
              </div>
              <div class="panel">
                <div class="panel-header"><h2>预测风险摘要</h2><span>{{ forecastEntries().length }} 个测点</span></div>
                <div v-if="forecastEntries().length" class="forecast-list">
                  <div v-for="item in forecastEntries().slice(0, 6)" :key="item[0]" class="forecast-row">
                    <b>{{ item[0] }}</b><span>{{ forecastDirection(item) }}</span><strong>{{ forecastRisk(item) }}</strong>
                  </div>
                </div>
                <div v-else class="panel-empty">暂无可用预测结果</div>
              </div>
            </div>
            <div v-if="visualization && chartSensors.length" class="two-column">
              <div class="panel chart-panel">
                <div class="panel-header"><h2>重点传感器趋势</h2><span>{{ selectedSensor }}</span></div>
                <div class="sensor-toolbar"><button v-for="sensor in chartSensors" :key="sensor" class="sensor-chip" :class="{ active: selectedSensor === sensor }" @click="selectSensor(sensor)">{{ sensor }}</button></div>
                <TimeSeriesChart
                  :timestamps="visualization.timestamps"
                  :values="selectedSensorValues"
                  :bands="visualization.event_ranges"
                  :markers="visualization.risk_scores.map((value, index) => ({ index })).filter((item) => visualization.anomaly_labels[item.index])"
                  line-color="#1d8583"
                  :title="`${selectedSensor} 时序曲线`"
                  :unit="selectedSensor"
                />
              </div>
              <div class="panel">
                <div class="panel-header"><h2>传感器贡献排序</h2><span>累计异常贡献</span></div>
                <div v-if="visualization.sensor_contributions?.length" class="contribution-list">
                  <div v-for="item in visualization.sensor_contributions" :key="item.sensor" class="contribution-row">
                    <div class="contribution-name"><b>{{ item.sensor }}</b><span>{{ formatNumber(item.score) }}</span></div>
                    <div class="contribution-track"><span :style="{ width: contributionWidth(item) }"></span></div>
                  </div>
                </div>
                <div v-else class="panel-empty">暂无传感器贡献数据</div>
              </div>
            </div>
          </template>
        </section>

        <section v-else-if="activeTab === 'evidence'" class="content-stack">
          <div v-if="!analysis" class="empty-panel compact"><h2>尚未生成分析结果</h2><p>先选择 CSV 并开始智能分析。</p></div>
          <template v-else>
            <div class="panel">
              <div class="panel-header"><h2>异常事件证据</h2><span>{{ events.length }} 个事件</span></div>
              <div v-for="(event, index) in events" :key="index" class="evidence-card">
                <div class="evidence-title"><span class="event-number">事件 {{ index + 1 }}</span><b :class="`risk-${event.severity}`">{{ event.severity }}</b><span class="event-score">峰值 {{ formatNumber(event.peak_score) }}</span></div>
                <div class="evidence-grid"><div><label>时间范围</label><p>{{ formatDate(event.start_time) }} - {{ formatDate(event.end_time) }}</p></div><div><label>主导传感器</label><p>{{ event.dominant_sensors?.join('、') || '待识别' }}</p></div><div><label>候选根因</label><p>{{ diagnosisForEvent(index + 1)?.primary_candidate?.name || '待现场确认' }}</p></div></div>
                <div v-if="diagnosisForEvent(index + 1)?.primary_candidate" class="evidence-columns"><div><label>支持证据</label><ul><li v-for="item in diagnosisForEvent(index + 1).primary_candidate.supporting_evidence" :key="item">{{ item }}</li></ul></div><div><label>证据缺口</label><ul><li v-for="item in diagnosisForEvent(index + 1).primary_candidate.missing_evidence" :key="item">{{ item }}</li></ul></div></div>
                <div v-if="relationshipForEvent(index + 1)" class="relationship-box">
                  <label>多传感器关系</label>
                  <p>{{ relationshipForEvent(index + 1)["关系结论"] }}</p>
                  <small>{{ relationshipForEvent(index + 1)["使用边界"] }}</small>
                </div>
              </div>
              <div v-if="!events.length" class="panel-empty">未发现持续异常事件</div>
            </div>
          </template>
        </section>

        <section v-else-if="activeTab === 'forecast'" class="content-stack">
          <div v-if="!analysis" class="empty-panel compact"><h2>尚未生成趋势研判</h2><p>先选择 CSV 并开始智能分析。</p></div>
          <template v-else>
            <div class="panel">
              <div class="panel-header"><h2>传感器未来趋势</h2><span>选择测点查看历史与预测区间</span></div>
              <div v-if="forecastSensors.length" class="sensor-toolbar forecast-sensor-toolbar"><button v-for="sensor in forecastSensors" :key="sensor" class="sensor-chip" :class="{ active: selectedForecastSensor === sensor }" @click="selectForecastSensor(sensor)">{{ sensor }}</button></div>
              <ForecastChart
                v-if="selectedForecast && visualization?.series?.[selectedForecastSensor]"
                :history-timestamps="visualization.timestamps"
                :history-values="visualization.series[selectedForecastSensor]"
                :future-timestamps="selectedForecast['预测时间'] || []"
                :predictions="selectedForecast['预测值'] || []"
                :lower="selectedForecast['下界'] || []"
                :upper="selectedForecast['上界'] || []"
                :title="`${selectedForecastSensor} 预测趋势`"
              />
              <div v-if="forecastEntries().length" class="forecast-detail-list">
                <div v-for="item in forecastEntries()" :key="item[0]" class="forecast-detail">
                  <div class="forecast-detail-header"><div><b>{{ item[0] }}</b><small>{{ item[1].模型名称 }} · {{ item[1].选择依据 }}</small></div><span :class="`forecast-risk-${item[1].风险}`">{{ item[1].风险 }}</span></div>
                  <div class="forecast-stats"><span>当前值 <b>{{ item[1].当前值 }}</b></span><span>预测末值 <b>{{ item[1].预测末值 }}</b></span><span>方向 <b>{{ item[1].方向 }}</b></span><span>末值偏移 <b>{{ item[1].预测末值偏移标准差 }}σ</b></span><span>回测 RMSE <b>{{ item[1].回测?.RMSE ?? '-' }}</b></span></div>
                  <div class="forecast-bar"><span :style="{ width: `${Math.min(100, Math.max(4, Math.abs(Number(item[1].预测末值偏移标准差 || 0)) * 10))}%` }"></span></div>
                </div>
              </div>
              <div v-else class="panel-empty">数据长度不足，暂未生成预测</div>
            </div>
            <div class="two-column">
              <div class="panel">
                <div class="panel-header"><h2>工况分段</h2><span>识别结果</span></div>
                <div v-if="regimes?.segments?.length" class="regime-list detailed">
                  <div v-for="segment in regimes.segments" :key="`${segment['开始时间']}-${segment['结束时间']}`" class="regime-row">
                    <b>工况 {{ segment["工况编号"] }}</b><span>{{ formatDate(segment["开始时间"]) }} - {{ formatDate(segment["结束时间"]) }}</span><small>{{ segment["持续点数"] }} 点</small>
                  </div>
                </div>
                <div v-else class="panel-empty">暂无工况分段</div>
              </div>
              <div class="panel">
                <div class="panel-header"><h2>关联诊断</h2><span>{{ relationships.length }} 个事件</span></div>
                <div v-if="relationships.length" class="relationship-list">
                  <div v-for="item in relationships" :key="item['事件编号']" class="relationship-row"><b>事件 {{ item["事件编号"] }}</b><span>{{ item["关系结论"] }}</span></div>
                </div>
                <div v-else class="panel-empty">暂无足够的多传感器关系证据</div>
              </div>
            </div>
          </template>
        </section>

        <WorkOrderPanel
          v-else-if="activeTab === 'work-orders'"
          :show-archived="showArchived"
          :selected-work-order="selectedWorkOrder"
          :selected-work-order-event="selectedWorkOrderEvent"
          :selected-work-order-diagnosis="selectedWorkOrderDiagnosis"
          :work-orders="filteredWorkOrders"
          :work-order-total="workOrderTotal"
          :work-order-page="workOrderPage"
          :work-order-page-count="workOrderPageCount"
          :work-orders-loading="workOrdersLoading"
          :work-order-loading="workOrderLoading"
          :work-order-action-id="workOrderActionId"
          :work-order-search="workOrderSearch"
          :work-order-status-filter="workOrderStatusFilter"
          :work-order-priority-filter="workOrderPriorityFilter"
          :feedback="feedback"
          :feedback-notice="feedbackNotice"
          :saving-feedback="savingFeedback"
          :feedback-dirty="feedbackDirty"
          :format-date="formatDate"
          :format-number="formatNumber"
          @update:work-order-search="workOrderSearch = $event"
          @update:work-order-status-filter="workOrderStatusFilter = $event"
          @update:work-order-priority-filter="workOrderPriorityFilter = $event"
          @select-order="selectWorkOrder"
          @restore-order="restoreSelectedWorkOrder"
          @change-page="changeWorkOrderPage"
          @refresh="refreshWorkOrders"
          @export="exportWorkOrdersCsv"
          @clear-filters="clearWorkOrderFilters"
          @save-feedback="saveFeedback"
          @archive-order="archiveSelectedWorkOrder"
        />
        <section v-else-if="false && activeTab === 'work-orders'" class="content-stack">
          <div v-if="selectedWorkOrder" class="work-order-live-status">
            <span>当前编辑工单状态</span>
            <b :class="`status-${feedback.status}`">{{ feedback.status }}</b>
          </div>
          <div v-if="feedbackNotice" class="feedback-notice" :class="feedbackNotice.type">
            <strong>{{ feedbackNotice.title }}</strong>
            <span>{{ feedbackNotice.detail }}</span>
          </div>
             <div class="work-export-toolbar">
              <span>当前筛选结果：{{ workOrderTotal }} 条 · 第 {{ workOrderPage }} / {{ workOrderPageCount }} 页</span>
              <div class="work-toolbar-actions"><button class="secondary-button" :disabled="workOrdersLoading" @click="refreshWorkOrders">{{ workOrdersLoading ? '刷新中...' : '刷新工单' }}</button><button v-if="filteredWorkOrders.length" class="secondary-button" @click="exportWorkOrdersCsv">导出当前页 CSV</button></div>
          </div>
          <div class="two-column work-layout">
            <div class="panel"><div class="panel-header"><h2>{{ showArchived ? '归档工单' : '工单队列' }}</h2><span>{{ workOrdersLoading ? '加载中...' : `${filteredWorkOrders.length} / ${workOrderTotal} 条` }}</span></div><div class="work-order-filters sticky-filters"><input v-model="workOrderSearch" class="control-input" placeholder="搜索工单编号、标题或责任角色" /><select v-model="workOrderStatusFilter" class="control-input"><option value="">全部状态</option><option>待确认</option><option>处理中</option><option>已确认</option><option>已完成</option><option>已关闭</option></select><select v-model="workOrderPriorityFilter" class="control-input"><option value="">全部优先级</option><option>P1</option><option>P2</option><option>P3</option></select><button class="filter-clear" @click="clearWorkOrderFilters">清除</button></div><div v-if="workOrdersLoading" class="panel-loading compact-loading">正在加载工单...</div><template v-else><div v-for="order in filteredWorkOrders" :key="order.record_id" class="work-order-row" :class="{ selected: selectedWorkOrder?.record_id === order.record_id }" @click="selectWorkOrder(order)"><span class="priority">{{ order.priority }}</span><span><b>{{ order.title }}</b><small>{{ order.status }} · {{ order.assigned_role }}</small></span><button v-if="showArchived" class="row-action" title="恢复工单" @click.stop="restoreSelectedWorkOrder(order)">恢复</button></div><div v-if="!filteredWorkOrders.length" class="panel-empty">{{ workOrderTotal ? '没有符合条件的工单' : (showArchived ? '暂无归档工单' : '暂无工单') }}</div></template><div class="pagination-bar"><button class="filter-clear" :disabled="workOrderPage <= 1 || workOrdersLoading" @click="changeWorkOrderPage(workOrderPage - 1)">上一页</button><span>第 {{ workOrderPage }} / {{ workOrderPageCount }} 页</span><button class="filter-clear" :disabled="workOrderPage >= workOrderPageCount || workOrdersLoading" @click="changeWorkOrderPage(workOrderPage + 1)">下一页</button></div></div>
            <div class="panel"><div v-if="selectedWorkOrder"><div class="panel-header"><h2>工单详情与现场反馈</h2><span>{{ selectedWorkOrder.record_id }}</span></div><div v-if="workOrderLoading" class="panel-loading">正在加载所属任务的异常证据...</div><div v-else class="work-order-detail"><div class="work-order-summary"><span class="priority large">{{ selectedWorkOrder.priority }}</span><div><h3>{{ selectedWorkOrder.title }}</h3><p>{{ selectedWorkOrder.assigned_role }} · 所属任务 {{ selectedWorkOrder.run_id }}</p></div></div><div class="detail-grid"><div><label>异常时间</label><b>{{ selectedWorkOrderEvent ? `${formatDate(selectedWorkOrderEvent.start_time)} - ${formatDate(selectedWorkOrderEvent.end_time)}` : '暂无' }}</b></div><div><label>风险峰值</label><b>{{ selectedWorkOrderEvent ? formatNumber(selectedWorkOrderEvent.peak_score) : '暂无' }}</b></div><div><label>主导传感器</label><b>{{ selectedWorkOrderEvent?.dominant_sensors?.join('、') || '暂无' }}</b></div></div><div class="evidence-box"><div><label>算法证据</label><ul><li v-for="item in selectedWorkOrder.evidence_summary" :key="item">{{ item }}</li><li v-if="!selectedWorkOrder.evidence_summary?.length">暂无结构化证据</li></ul></div><div><label>建议处置</label><ul><li v-for="item in selectedWorkOrder.actions" :key="item">{{ item }}</li><li v-if="!selectedWorkOrder.actions?.length">暂无处置动作</li></ul></div></div><div v-if="selectedWorkOrderDiagnosis?.primary_candidate" class="diagnosis-strip"><label>候选根因</label><b>{{ selectedWorkOrderDiagnosis.primary_candidate.name }}</b><span>{{ selectedWorkOrderDiagnosis.primary_candidate.confidence || '待现场确认' }}</span></div><div class="form-stack"><label>状态<select v-model="feedback.status" class="control-input" :disabled="showArchived"><option>待确认</option><option>已确认</option><option>处理中</option><option>已完成</option><option>已关闭</option></select></label><label>确认根因<input v-model="feedback.confirmed_cause" class="control-input" :disabled="showArchived" placeholder="填写现场确认结果" /></label><label>处置与复测<textarea v-model="feedback.feedback_note" class="control-input" rows="5" :disabled="showArchived" placeholder="填写处理动作和复测结果"></textarea></label><label>处理人员<input v-model="feedback.handled_by" class="control-input" :disabled="showArchived" /></label><div class="form-actions"><button v-if="!showArchived" class="primary-button" :disabled="savingFeedback || !feedbackDirty" @click="saveFeedback">{{ savingFeedback ? '保存中...' : feedbackDirty ? '保存反馈' : '已保存' }}</button><button v-if="!showArchived" class="archive-button" :disabled="![ '已完成', '已关闭' ].includes(selectedWorkOrder.status) || savingFeedback" @click="archiveSelectedWorkOrder">归档工单</button></div></div></div></div><div v-else class="panel-empty">选择一条工单查看详情</div></div>
          </div>
        </section>

        <HistoryPanel
          v-else
          :analysis="analysis"
          :runs="runs"
          :filtered-history-runs="filteredHistoryRuns"
          :paginated-history-runs="paginatedHistoryRuns"
          :cases="cases"
          :show-archived="showArchived"
          :history-search="historySearch"
          :history-status="historyStatus"
          :history-page="historyPage"
          :history-page-count="historyPageCount"
          :history-run-loading="historyRunLoading"
          :history-action-id="historyActionId"
          :refreshing-history="refreshingHistory"
          :retrying-run-id="retryingRunId"
          :selected-case="selectedCase"
          :selected-case-event="selectedCaseEvent"
          :selected-case-diagnosis="selectedCaseDiagnosis"
          :case-event-number="caseEventNumber"
          :case-loading="caseLoading"
          :format-date="formatDate"
          :format-number="formatNumber"
          @update:history-search="historySearch = $event"
          @update:history-status="historyStatus = $event"
          @view-run="viewHistoryRun"
          @retry-run="retryHistoryRun"
          @archive-run="archiveHistoryRun"
          @restore-run="restoreHistoryRun"
          @change-page="changeHistoryPage"
          @toggle-archived="toggleArchivedRecords"
          @refresh="refreshHistory"
          @export-json="exportAnalysisJson"
          @export-summary="exportSummaryMarkdown"
          @select-case="selectCase"
          @delete-case="deleteConfirmedCase"
          @close-case="selectedCase = null"
        />
      </main>
    </div>
  </div>
</template>
