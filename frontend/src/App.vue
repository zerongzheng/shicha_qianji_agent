<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import {
  createJob,
  archiveRun,
  archiveWorkOrder,
  cancelJob,
  getJobResult,
  getFilePreflight,
  getJobStatus,
  getRun,
  health,
  listCases,
  listRuns,
  listWorkOrders,
  registerDefaultSkabSample,
  removeCase,
  restoreRun,
  restoreWorkOrder,
  updateWorkOrder,
  uploadCsv,
} from "./api";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import AnalysisProgressPanel from "./components/AnalysisProgressPanel.vue";
import PreflightModal from "./components/PreflightModal.vue";
import WorkOrderPanel from "./components/WorkOrderPanel.vue";
import HistoryPanel from "./components/HistoryPanel.vue";
import OverviewPanel from "./components/OverviewPanel.vue";
import EvidencePanel from "./components/EvidencePanel.vue";
import ForecastPanel from "./components/ForecastPanel.vue";

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
const sampleLoading = ref(false);
const selectedSampleFileId = ref("");
const activeJobStorageKey = "shichi_qianji_active_job";
// 证据页使用筛选和折叠，事件较多时仍然可以按风险快速定位。
const evidenceRiskFilter = ref("");
const expandedEvidenceEvent = ref(0);

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
const highestRiskEvent = computed(() => {
  const levels = { 高风险: 3, 中风险: 2, 低风险: 1 };
  return events.value.reduce((current, event, index) => {
    if (!current) return { event, index };
    return (levels[event.severity] || 0) > (levels[current.event.severity] || 0)
      ? { event, index }
      : current;
  }, null);
});
const overviewDiagnosis = computed(() => {
  const item = highestRiskEvent.value;
  return item ? diagnosisForEvent(item.index + 1) : null;
});
const overviewWorkOrder = computed(() => {
  const item = highestRiskEvent.value;
  if (!item) return null;
  return workOrders.value.find((order) => Number(order.event_number) === item.index + 1) || null;
});
const overviewAction = computed(() => {
  const actions = overviewWorkOrder.value?.actions || analysis.value?.recommendations || [];
  return actions[0] || "请先复核工况、数据质量和现场测点。";
});
const visibleEvidenceEvents = computed(() => events.value
  .map((event, index) => ({ event, index }))
  .filter(({ event }) => !evidenceRiskFilter.value || event.severity === evidenceRiskFilter.value)
  .sort((left, right) => Number(right.event.peak_score || 0) - Number(left.event.peak_score || 0)));
const evidenceCharts = computed(() => new Map(
  visibleEvidenceEvents.value.map((item) => [item.index, evidenceChartFor(item)]),
));
const analysisScope = computed(() => {
  const sourceName = String(analysis.value?.data_profile?.source_name || selectedFile.value?.name || "");
  const isSkab = Boolean(selectedFile.value?.isSample) || /skab|valve|anomaly-free/i.test(sourceName);
  return {
    label: isSkab ? "SKAB 校赛样例" : "用户上传数据",
    detail: isSkab
      ? "用于验证分析流程和工程闭环，不代表联通现场设备成效。"
      : "结果基于当前上传文件生成，需结合设备台账和现场记录复核。",
  };
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
  await resumeActiveJob();
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

function openEvidenceEvent(index) {
  expandedEvidenceEvent.value = index;
  activeTab.value = "evidence";
  window.setTimeout(() => {
    document.querySelector(`[data-evidence-event="${index}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 0);
}

function toggleEvidenceEvent(index) {
  expandedEvidenceEvent.value = expandedEvidenceEvent.value === index ? -1 : index;
}

function openConfirmedCases() {
  activeTab.value = "history";
  showArchived.value = false;
  refreshHistory();
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

async function loadDefaultSkab() {
  if (sampleLoading.value || isAnalyzing.value || !(await confirmDiscardChanges())) return;
  sampleLoading.value = true;
  errorMessage.value = "";
  successMessage.value = "";
  try {
    const sample = await registerDefaultSkabSample();
    selectedSampleFileId.value = sample.file_id;
    selectedFile.value = { name: sample.file_name, size: sample.size_bytes, isSample: true };
    const preflight = await getFilePreflight(sample.file_id);
    filePreflight.value = normalizePreflight(preflight);
    preflightAccepted.value = true;
    showPreflight.value = false;
    successMessage.value = "默认 SKAB 样例已准备，可以直接开始分析。";
  } catch (error) {
    errorMessage.value = `加载 SKAB 样例失败：${error.message}`;
  } finally {
    sampleLoading.value = false;
  }
}

function onFileChange(event) {
  selectedFile.value = event.target.files?.[0] || null;
  // 用户改选真实 CSV 后，必须清除默认样例标记，避免分析时继续使用旧的 sample file_id。
  selectedSampleFileId.value = "";
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

function normalizePreflight(payload) {
  return {
    ...payload,
    fileName: payload.file_name,
    sizeLabel: formatFileSize(payload.size_bytes),
    datetimeColumn: payload.datetime_column,
  };
}

function persistActiveJob(jobId) {
  if (jobId) window.localStorage.setItem(activeJobStorageKey, jobId);
  else window.localStorage.removeItem(activeJobStorageKey);
}

async function resumeActiveJob() {
  const savedJobId = window.localStorage.getItem(activeJobStorageKey);
  if (!savedJobId || isAnalyzing.value) return;
  try {
    const status = await getJobStatus(savedJobId);
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
    const uploaded = selectedSampleFileId.value
      ? { file_id: selectedSampleFileId.value }
      : await uploadCsv(selectedFile.value);
    setProgress("queued", 20, "文件已接收，正在建立分析任务...");
    jobStatus.value = "已提交";
    const accepted = await createJob(uploaded.file_id, { ...config });
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
    persistActiveJob(accepted.run_id);
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
    persistActiveJob("");
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

async function openRelatedWorkOrder(eventNumber) {
  if (!analysis.value?.run_id) return;
  try {
    const response = await listWorkOrders(showArchived.value, showArchived.value, {
      limit: 200,
      offset: 0,
      run_id: analysis.value.run_id,
    });
    const order = (response.work_orders || []).find(
      (item) => Number(item.event_number) === Number(eventNumber),
    );
    activeTab.value = "work-orders";
    if (order) {
      await selectWorkOrder(order);
    } else {
      showToast({ type: "warning", title: "暂未找到关联工单", detail: `事件 ${eventNumber} 当前没有对应的运维工单。` });
    }
  } catch (error) {
    errorMessage.value = `关联工单加载失败：${error.message}`;
  }
}

async function saveFeedback() {
  if (!selectedWorkOrder.value || savingFeedback.value) return;
  const statusOrder = ["待确认", "已确认", "处理中", "待验证", "已完成", "已关闭"];
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
    // 保存按钮可能位于长页面下方，保存后把用户带回状态提示位置。
    window.setTimeout(() => {
      document.querySelector(".work-order-live-status")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
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

function relationshipForEvent(index) {
  return relationships.value.find((item) => Number(item["事件编号"]) === index) || null;
}

function evidenceChartFor(item) {
  const visualizationPayload = visualization.value;
  const event = item?.event;
  if (!visualizationPayload || !event) return null;
  const sampleIndexes = visualizationPayload.sample_indexes || [];
  const sensor = event.dominant_sensors?.[0] || visualizationPayload.sensor_columns?.[0];
  const values = visualizationPayload.series?.[sensor] || [];
  if (!sampleIndexes.length || !values.length || !sensor) return null;

  // 按事件原始索引截取前后各一段上下文，让评委看到异常发生前后的变化，而不是只看结论。
  const eventLength = Math.max(1, Number(event.end_index || 0) - Number(event.start_index || 0) + 1);
  const context = Math.max(12, Math.min(80, eventLength * 2));
  const lower = Math.max(0, Number(event.start_index || 0) - context);
  const upper = Math.min(Number(event.end_index || 0) + context, sampleIndexes[sampleIndexes.length - 1]);
  const positions = sampleIndexes
    .map((originalIndex, position) => ({ originalIndex, position }))
    .filter((point) => point.originalIndex >= lower && point.originalIndex <= upper);
  if (!positions.length) return null;
  const first = positions[0].position;
  const last = positions[positions.length - 1].position;
  const span = Math.max(1, sampleIndexes[last] - sampleIndexes[first]);
  return {
    sensor,
    timestamps: (visualizationPayload.timestamps || []).slice(first, last + 1),
    values: values.slice(first, last + 1),
    bands: [{
      start_ratio: Math.max(0, (Number(event.start_index) - sampleIndexes[first]) / span),
      end_ratio: Math.min(1, (Number(event.end_index) - sampleIndexes[first]) / span),
      event_number: item.index + 1,
      severity: event.severity,
    }],
  };
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
        <button class="sample-button" :disabled="sampleLoading || isAnalyzing" @click="loadDefaultSkab">
          {{ sampleLoading ? "准备样例中..." : "加载默认 SKAB 样例" }}
        </button>
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
          <OverviewPanel
            v-else
            :analysis="analysis"
            :events="events"
            :visualization="visualization"
            :chart-sensors="chartSensors"
            :selected-sensor="selectedSensor"
            :selected-sensor-values="selectedSensorValues"
            :highest-risk="highestRisk"
            :risk-alert-count="riskAlertCount"
            :analysis-scope="analysisScope"
            :highest-risk-event="highestRiskEvent"
            :overview-diagnosis="overviewDiagnosis"
            :overview-work-order="overviewWorkOrder"
            :overview-action="overviewAction"
            :data-quality="dataQuality"
            :closed-loop="closedLoop"
            :regimes="regimes"
            :forecast-entries="forecastEntries()"
            :format-date="formatDate"
            :format-number="formatNumber"
            :contribution-width="contributionWidth"
            @open-evidence="openEvidenceEvent"
            @select-sensor="selectSensor"
          />
        </section>

        <section v-else-if="activeTab === 'evidence'" class="content-stack">
          <div v-if="!analysis" class="empty-panel compact"><h2>尚未生成分析结果</h2><p>先选择 CSV 并开始智能分析。</p></div>
          <EvidencePanel
            v-else
            :analysis="analysis"
            :events="events"
            :visible-evidence-events="visibleEvidenceEvents"
            :evidence-risk-filter="evidenceRiskFilter"
            :expanded-evidence-event="expandedEvidenceEvent"
            :evidence-charts="evidenceCharts"
            :format-date="formatDate"
            :format-number="formatNumber"
            :diagnosis-for-event="diagnosisForEvent"
            :relationship-for-event="relationshipForEvent"
            @filter-risk="evidenceRiskFilter = $event"
            @toggle-event="toggleEvidenceEvent"
            @open-work-order="openRelatedWorkOrder"
          />
        </section>

        <section v-else-if="activeTab === 'forecast'" class="content-stack">
          <div v-if="!analysis" class="empty-panel compact"><h2>尚未生成趋势研判</h2><p>先选择 CSV 并开始智能分析。</p></div>
          <ForecastPanel
            v-else
            :analysis="analysis"
            :forecast-sensors="forecastSensors"
            :selected-forecast-sensor="selectedForecastSensor"
            :selected-forecast="selectedForecast"
            :visualization="visualization"
            :forecast-entries="forecastEntries()"
            :regimes="regimes"
            :relationships="relationships"
            :format-date="formatDate"
            :format-number="formatNumber"
            @select-sensor="selectForecastSensor"
          />
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
