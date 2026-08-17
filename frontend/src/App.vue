<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { inspectCsvText, formatFileSize } from "./utils/csv";
import { useAnalysisJob } from "./composables/useAnalysisJob";
import {
  createJob,
  archiveRun,
  archiveWorkOrder,
  deleteArchivedRun,
  deleteArchivedWorkOrder,
  cancelJob,
  getJobResult,
  getFilePreflight,
  getJobStatus,
  getRun,
  getMonitoringStatus,
  health,
  listCases,
  listDeviceProfiles,
  listRuns,
  listWorkOrders,
  pollMonitoringSource,
  saveMonitoringSource,
  deleteMonitoringSource,
  registerDefaultSkabSample,
  removeCase,
  restoreRun,
  restoreWorkOrder,
  updateWorkOrder,
  uploadCsv,
  acceptWorkOrder,
  acknowledgeNotification,
  getAuthConfig,
  getCurrentUser,
  login,
  logout,
  setSessionToken,
} from "./api";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import AnalysisProgressPanel from "./components/AnalysisProgressPanel.vue";
import PreflightModal from "./components/PreflightModal.vue";
import WorkOrderPanel from "./components/WorkOrderPanel.vue";
import HistoryPanel from "./components/HistoryPanel.vue";
import OverviewPanel from "./components/OverviewPanel.vue";
import EvidencePanel from "./components/EvidencePanel.vue";
import ModelEvidencePanel from "./components/ModelEvidencePanel.vue";
import ForecastPanel from "./components/ForecastPanel.vue";
import MonitoringPanel from "./components/MonitoringPanel.vue";
import DebugAnalysisPanel from "./components/DebugAnalysisPanel.vue";
import LoginView from "./components/LoginView.vue";

const tabs = [
  { id: "monitoring", label: "自动监测" },
  { id: "overview", label: "风险总览" },
  { id: "evidence", label: "异常证据" },
  { id: "forecast", label: "趋势研判" },
  { id: "work-orders", label: "运维工单" },
  { id: "history", label: "历史记录" },
  { id: "debug", label: "调试分析" },
];

const activeTab = ref("monitoring");
const authReady = ref(false);
const authEnabled = ref(false);
const authLoading = ref(false);
const authError = ref("");
const currentUser = ref(null);
const monitoring = ref({ monitor: {}, sources: [], ingestions: [], notifications: [] });
const monitoringLoading = ref(false);
const monitoringActionId = ref("");
const notificationActionId = ref("");
const automaticRunLoading = ref("");
const selectedFile = ref(null);
const filePreflight = ref(null);
const showPreflight = ref(false);
const preflightAccepted = ref(false);
const apiStatus = ref("检查中");
const errorMessage = ref("");
const successMessage = ref("");
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
// 默认保留全部历史工单；打开该开关后只显示当前分析任务产生的工单。
const currentWorkOrderOnly = ref(false);
const myWorkOrdersOnly = ref(false);
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
const retryingRunId = ref("");
const sampleLoading = ref(false);
const selectedSampleFileId = ref("");
const deviceProfiles = ref([]);
const deviceProfilesLoading = ref(false);
// 证据页使用筛选和折叠，事件较多时仍然可以按风险快速定位。
const evidenceRiskFilter = ref("");
const expandedEvidenceEvent = ref(0);

const config = reactive({
  // null 表示自动识别；空字符串表示强制使用通用模式。
  device_profile_id: null,
  detector: "time_frequency_relation",
  threshold: 3.5,
  rolling_window: 61,
  min_event_length: 12,
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

// 分析任务的状态机独立管理，App.vue 只负责把它接入当前页面状态。
const {
  isAnalyzing,
  jobStatus,
  progressStage,
  progressPercent,
  progressDetail,
  analysisElapsed,
  runId,
  analysis,
  activeJobId,
  cancellingJob,
  cancelRequested,
  progressSteps,
  setProgress,
  startProgressTimer,
  stopProgressTimer,
  persistActiveJob,
  pollJob,
  loadAnalysisResult,
  startAnalysis: runAnalysisJob,
  cancelActiveJob: cancelAnalysisJob,
  resumeActiveJob,
} = useAnalysisJob({
  api: { uploadCsv, createJob, getJobStatus, getJobResult, cancelJob },
  config,
  selectedFile,
  selectedSampleFileId,
  filePreflight,
  preflightAccepted,
  showPreflight,
  inspectCsvFile: async (file) => inspectCsvFile(file),
  confirmDiscardChanges,
  refreshHistory: async () => refreshHistory(),
  activeTab,
  successMessage,
  errorMessage,
});

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
const currentAnalysisRunId = computed(() => analysis.value?.run_id || runId.value || "");
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
  const currentRun = runs.value.find((item) => item.run_id === currentAnalysisRunId.value);
  const isAutomatic = Boolean(currentRun?.source_id || currentRun?.ingestion_id);
  return {
    label: isAutomatic ? "自动监测任务" : isSkab ? "SKAB 校赛样例" : "手动调试数据",
    detail: isAutomatic
      ? "系统检测到新批次后自动完成分析，当前结果可追溯至对应数据源和采集记录。"
      : isSkab
      ? "用于验证分析流程和工程闭环，不代表联通现场设备成效。"
      : "结果来自单文件调试入口，需结合设备台账和现场记录复核。",
  };
});

onMounted(async () => {
  try {
    const configResponse = await getAuthConfig();
    authEnabled.value = Boolean(configResponse.auth_enabled);
    if (authEnabled.value) {
      try {
        const userResponse = await getCurrentUser();
        currentUser.value = userResponse.user;
        myWorkOrdersOnly.value = true;
      } catch {
        setSessionToken("");
      }
    }
  } catch (error) {
    authError.value = error.message;
  } finally {
    authReady.value = true;
  }
  if (!authEnabled.value || currentUser.value) await initializeWorkspace();
  window.addEventListener("beforeunload", handleBeforeUnload);
  window.addEventListener("keydown", handleGlobalKeydown);
  window.addEventListener("focus", handleWindowFocus);
  document.addEventListener("visibilitychange", handleVisibilityChange);
});

async function initializeWorkspace() {
  await refreshHistory();
  await refreshMonitoring();
  await loadLatestAutomaticRun();
  await loadDeviceProfiles();
  try {
    await health();
    apiStatus.value = "在线";
  } catch {
    apiStatus.value = "离线";
  }
  await resumeActiveJob();
  startAutoRefresh();
}

async function handleLogin(credentials) {
  authLoading.value = true;
  authError.value = "";
  try {
    const response = await login(credentials.username, credentials.password);
    setSessionToken(response.token);
    currentUser.value = response.user;
    myWorkOrdersOnly.value = true;
    await initializeWorkspace();
  } catch (error) {
    setSessionToken("");
    authError.value = error.message;
  } finally {
    authLoading.value = false;
  }
}

async function handleLogout() {
  if (feedbackDirty.value && !(await confirmDiscardChanges())) return;
  await logout();
  stopAutoRefresh();
  currentUser.value = null;
  analysis.value = null;
  runs.value = [];
  workOrders.value = [];
  selectedWorkOrder.value = null;
}

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

watch(analysis, (result) => {
  if (!result) return;
  // 每次载入新任务或历史结果时，默认选中第一条可视化测点。
  selectedSensor.value = result.visualization?.sensor_columns?.[0] || "";
  selectedForecastSensor.value = Object.keys(result.forecast_results || {})[0] || "";
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

function restoreFeedbackDraft() {
  // 放弃修改后恢复到当前工单最后一次从后端加载或保存的值，并同步快照。
  // 只关闭弹窗而不恢复这四个字段，会让下一次切换仍被判断为“有未保存修改”。
  if (!selectedWorkOrder.value) {
    feedbackDraftSnapshot.value = feedbackSnapshot();
    return;
  }
  feedback.status = selectedWorkOrder.value.status || "待确认";
  feedback.confirmed_cause = selectedWorkOrder.value.confirmed_cause || "";
  feedback.feedback_note = selectedWorkOrder.value.feedback_note || "";
  feedback.handled_by = selectedWorkOrder.value.handled_by || "";
  feedbackDraftSnapshot.value = feedbackSnapshot();
  feedbackNotice.value = null;
}

async function confirmDiscardChanges() {
  if (!feedbackDirty.value) return true;
  const discarded = await requestConfirmation({
    title: "当前工单有未保存修改",
    detail: "继续操作会丢失尚未保存的现场反馈，请确认是否放弃这些修改。",
    confirmText: "放弃修改",
    tone: "warning",
  });
  if (discarded) restoreFeedbackDraft();
  return discarded;
}

async function changeTab(tabId) {
  if (!(await confirmDiscardChanges())) return;
  activeTab.value = tabId;
  if (tabId === "monitoring") await refreshMonitoring();
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
    filePreflight.value = inspectCsvText(text, file.name, file.size);
    showPreflight.value = true;
  } catch (error) {
    filePreflight.value = null;
    errorMessage.value = `CSV 预检失败：${error.message}`;
  }
}

function normalizePreflight(payload) {
  return {
    ...payload,
    fileName: payload.file_name,
    sizeLabel: formatFileSize(payload.size_bytes),
    datetimeColumn: payload.datetime_column,
  };
}

async function loadDeviceProfiles() {
  deviceProfilesLoading.value = true;
  try {
    const response = await listDeviceProfiles();
    deviceProfiles.value = response.profiles || [];
  } catch (error) {
    // 配置接口不可用时不阻塞通用 CSV 分析，后端仍会按通用模式运行。
    deviceProfiles.value = [];
    errorMessage.value = `设备配置读取失败，将使用自动通用模式：${error.message}`;
  } finally {
    deviceProfilesLoading.value = false;
  }
}

async function startAnalysis() {
  try {
    await runAnalysisJob();
  } catch (error) {
    errorMessage.value = error.message;
  }
}

function confirmPreflightAndStart() {
  preflightAccepted.value = true;
  startAnalysis();
}

async function cancelActiveJob() {
  await cancelAnalysisJob(() => requestConfirmation({
    title: "取消排队中的分析任务？",
    detail: "已上传文件和任务记录仍会保留，但本次分析不会继续执行。",
    confirmText: "确认取消",
    tone: "warning",
  }));
}

async function refreshHistory() {
  if (refreshingHistory.value) return;
  refreshingHistory.value = true;
  try {
    const [runResponse, caseResponse, workOrderSummary] = await Promise.all([
      listRuns(historyStatus.value, showArchived.value, showArchived.value),
      listCases(showArchived.value, showArchived.value),
      listWorkOrders(showArchived.value, showArchived.value, {
        limit: 1,
        offset: 0,
        run_id: currentWorkOrderOnly.value ? currentAnalysisRunId.value : "",
      }),
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
      listWorkOrders(showArchived.value, showArchived.value, {
        limit: 1,
        offset: 0,
        run_id: currentWorkOrderOnly.value ? currentAnalysisRunId.value : "",
      }),
    ]);
    runs.value = runResponse.runs || [];
    cases.value = caseResponse.cases || [];
    historyWorkOrderTotal.value = Number(workOrderSummary.work_order_count || 0);
    await refreshWorkOrders();
    if (activeTab.value === "monitoring") await refreshMonitoring();
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
      run_id: currentWorkOrderOnly.value ? currentAnalysisRunId.value : "",
      mine: myWorkOrdersOnly.value,
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

async function toggleCurrentWorkOrderScope() {
  if (feedbackDirty.value && !(await confirmDiscardChanges())) return;
  currentWorkOrderOnly.value = !currentWorkOrderOnly.value;
  workOrderPage.value = 1;
  selectedWorkOrder.value = null;
  selectedWorkOrderAnalysis.value = null;
  feedbackDraftSnapshot.value = "";
  feedbackNotice.value = null;
  await refreshHistory();
}

async function toggleMyWorkOrders() {
  if (feedbackDirty.value && !(await confirmDiscardChanges())) return;
  myWorkOrdersOnly.value = !myWorkOrdersOnly.value;
  workOrderPage.value = 1;
  selectedWorkOrder.value = null;
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

async function loadRunById(runIdToLoad, { navigate = true, announce = true } = {}) {
  if (!runIdToLoad || automaticRunLoading.value) return;
  automaticRunLoading.value = runIdToLoad;
  errorMessage.value = "";
  try {
    const response = await getRun(runIdToLoad);
    if (!response.run?.result) throw new Error("该自动任务尚未生成可查看的分析结果。");
    loadAnalysisResult(response.run.result, runIdToLoad);
    if (navigate) activeTab.value = "overview";
    if (announce) {
      showToast({
        type: "success",
        title: "已加载自动分析结果",
        detail: response.run.file_name || runIdToLoad,
      });
    }
  } catch (error) {
    errorMessage.value = error.message;
  } finally {
    automaticRunLoading.value = "";
  }
}

async function loadLatestAutomaticRun() {
  if (analysis.value) return;
  const latest = (monitoring.value.ingestions || []).find(
    (item) => item.status === "completed" && item.run_id,
  );
  if (latest) await loadRunById(latest.run_id, { navigate: false, announce: false });
}

function selectDebugFile(file) {
  selectedFile.value = file;
  selectedSampleFileId.value = "";
  filePreflight.value = null;
  showPreflight.value = false;
  preflightAccepted.value = false;
  errorMessage.value = "";
  successMessage.value = "";
  if (file) inspectCsvFile(file);
}

function updateDebugConfig(key, value) {
  config[key] = value;
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

async function deleteSelectedWorkOrder(order) {
  if (!order?.archived_at || workOrderActionId.value) return;
  if (!(await requestConfirmation({
    title: "彻底删除这张工单？",
    detail: "工单、现场反馈、案例记忆和对应通知将永久删除；来源分析任务仍会保留。此操作不可恢复。",
    confirmText: "彻底删除",
    tone: "danger",
  }))) return;
  workOrderActionId.value = order.record_id;
  try {
    await deleteArchivedWorkOrder(order.record_id);
    if (selectedWorkOrder.value?.record_id === order.record_id) selectedWorkOrder.value = null;
    showToast({ type: "success", title: "工单已彻底删除", detail: "来源分析任务和原始分析证据仍保留。" });
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

async function deleteHistoryRun(run) {
  if (!run?.archived_at || historyActionId.value) return;
  if (!(await requestConfirmation({
    title: "彻底删除这次分析任务？",
    detail: "分析结果、关联工单、案例记忆、通知和模型调用记录将永久删除。自动采集指纹会保留，以避免同一文件被再次分析。",
    confirmText: "彻底删除",
    tone: "danger",
  }))) return;
  historyActionId.value = run.run_id;
  try {
    await deleteArchivedRun(run.run_id);
    if (currentAnalysisRunId.value === run.run_id) {
      analysis.value = null;
      runId.value = "";
    }
    showToast({ type: "success", title: "历史任务已彻底删除", detail: "关联工单和通知已同步清理。" });
    await refreshHistory();
    await refreshMonitoring();
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
  const requiresCause = ["已确认", "待验证", "已完成", "已关闭"].includes(feedback.status);
  if (requiresCause && !feedback.confirmed_cause.trim()) {
    feedbackNotice.value = {
      type: "warning",
      title: "还不能生成历史案例",
      detail: "请先填写现场确认根因，再保存为已确认、待验证、已完成或已关闭。",
    };
    showToast(feedbackNotice.value, 0);
    errorMessage.value = "该状态必须填写现场确认根因。";
    return;
  }
  if (feedback.status === "待验证" && !feedback.feedback_note.trim()) {
    feedbackNotice.value = {
      type: "warning",
      title: "还不能开始自动复检",
      detail: "请先填写已经执行的现场处置动作，再进入待验证。系统会等待同一数据源的新批次自动复检。",
    };
    showToast(feedbackNotice.value, 0);
    errorMessage.value = "进入待验证前必须填写现场处置反馈。";
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
    feedbackNotice.value = savedOrder?.reinspection_status === "pending"
      ? {
          type: "success",
          title: "已进入自动复检队列",
          detail: "系统将等待同一数据源产生新的成功分析任务，并自动判断原异常主导测点是否仍然出现。",
        }
      : caseCreated
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

async function acceptSelectedWorkOrder() {
  if (!selectedWorkOrder.value || !currentUser.value?.user_id) return;
  workOrderActionId.value = selectedWorkOrder.value.record_id;
  try {
    const response = await acceptWorkOrder(selectedWorkOrder.value.record_id);
    selectedWorkOrder.value = response.work_order;
    showToast({
      type: "success",
      title: "工单已接收",
      detail: `责任人已登记为 ${currentUser.value.display_name}，接单时间已写入审计记录。`,
    });
    await refreshWorkOrders();
  } catch (error) {
    showToast({ type: "error", title: "接单失败", detail: error.message }, 0);
  } finally {
    workOrderActionId.value = "";
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

async function refreshMonitoring() {
  monitoringLoading.value = true;
  try {
    const response = await getMonitoringStatus();
    monitoring.value = response;
    const latestCompleted = (response.ingestions || []).find(
      (item) => item.status === "completed" && item.run_id,
    );
    // 用户停留在自动监测页时，新任务完成后静默同步到结果工作区；不强制跳转，
    // 也不覆盖用户正在查看的证据、工单或历史任务。
    if (
      latestCompleted
      && latestCompleted.run_id !== currentAnalysisRunId.value
      && activeTab.value === "monitoring"
    ) {
      await loadRunById(latestCompleted.run_id, { navigate: false, announce: Boolean(analysis.value) });
    }
  } catch (error) {
    errorMessage.value = error.message || "自动监测状态读取失败";
  } finally {
    monitoringLoading.value = false;
  }
}

async function configureMonitoring(payload) {
  monitoringActionId.value = "save";
  try {
    await saveMonitoringSource(payload);
    await refreshMonitoring();
    showToast({ type: "success", title: "数据源已保存", detail: "服务会按配置周期自动检测新批次。" });
  } catch (error) {
    errorMessage.value = error.message || "数据源保存失败";
  } finally {
    monitoringActionId.value = "";
  }
}

async function runMonitoringPoll(sourceId) {
  monitoringActionId.value = sourceId;
  try {
    const response = await pollMonitoringSource(sourceId);
    await refreshMonitoring();
    showToast({
      type: "success",
      title: "已完成一次检测",
      detail: `发现 ${response.poll.detected} 批，提交 ${response.poll.submitted} 个自动分析任务。`,
    });
  } catch (error) {
    errorMessage.value = error.message || "立即检测失败";
  } finally {
    monitoringActionId.value = "";
  }
}

async function removeMonitoringSource(sourceId) {
  monitoringActionId.value = sourceId;
  try {
    await deleteMonitoringSource(sourceId);
    await refreshMonitoring();
    showToast({ type: "success", title: "数据源已删除", detail: "没有采集历史的数据源才允许删除。" });
  } catch (error) {
    errorMessage.value = error.message || "数据源无法删除，请先停用并保留审计记录";
  } finally {
    monitoringActionId.value = "";
  }
}

async function acknowledgeMonitoringNotification(notificationId) {
  if (!notificationId || notificationActionId.value) return;
  notificationActionId.value = notificationId;
  try {
    await acknowledgeNotification(notificationId);
    showToast({
      type: "success",
      title: "异常通知已签收",
      detail: "签收人员和时间已写入 PostgreSQL 审计记录。",
    });
    await refreshMonitoring();
  } catch (error) {
    showToast({ type: "error", title: "通知签收失败", detail: error.message }, 0);
  } finally {
    notificationActionId.value = "";
  }
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
  <div v-if="!authReady" class="app-starting">正在连接时察千机服务...</div>
  <LoginView
    v-else-if="authEnabled && !currentUser"
    :loading="authLoading"
    :error="authError"
    @login="handleLogin"
  />
  <div v-else class="app-shell">
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
        <span class="topbar-environment"><span class="divider"></span>校赛验证环境</span>
        <span v-if="lastDataSyncAt" class="sync-meta">同步于 {{ formatClock(lastDataSyncAt) }}</span>
        <div v-if="currentUser" class="user-session">
          <span><b>{{ currentUser.display_name }}</b><small>{{ currentUser.role }}</small></span>
          <button title="退出登录" @click="handleLogout">退出</button>
        </div>
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
        <div class="sidebar-runtime">
          <span :class="{ active: monitoring.monitor?.running }"></span>
          <div>
            <strong>{{ monitoring.status !== 'success' ? '正在同步监测状态' : monitoring.monitor?.running ? '自动监测运行中' : '自动监测未启用' }}</strong>
            <small>{{ monitoring.status === 'success' ? `${monitoring.enabled_source_count || 0} 个数据源 · ${monitoring.ingestions?.length || 0} 个采集批次` : '连接后台监测服务...' }}</small>
          </div>
        </div>
      </aside>

      <main class="main-content">
        <section class="page-heading">
          <div>
            <div class="eyebrow">INDUSTRIAL TIME-SERIES AGENT</div>
            <h1>{{ tabs.find((tab) => tab.id === activeTab)?.label }}</h1>
            <p v-if="analysis">当前任务：{{ runId || analysis.run_id }} · {{ analysis.data_profile?.source_name }}</p>
            <p v-else-if="activeTab === 'monitoring'">数据接入后，系统持续检测新批次并自动完成分析、工单和分级通知。</p>
            <p v-else-if="activeTab === 'debug'">单文件上传仅用于调试、对照实验和临时数据验证。</p>
            <p v-else>结果由自动监测任务产生，也可从历史记录中选择任务查看。</p>
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

        <MonitoringPanel
          v-if="activeTab === 'monitoring'"
          :monitoring="monitoring"
          :loading="monitoringLoading"
          :action-id="monitoringActionId"
          :notification-action-id="notificationActionId"
          :current-user="currentUser"
          :format-date="formatDate"
          :analysis="analysis"
          :current-run-id="currentAnalysisRunId"
          :run-loading="automaticRunLoading"
          @refresh="refreshMonitoring"
          @save-source="configureMonitoring"
          @poll-source="runMonitoringPoll"
          @delete-source="removeMonitoringSource"
          @view-run="loadRunById"
          @acknowledge-notification="acknowledgeMonitoringNotification"
        />

        <section v-else-if="activeTab === 'overview'" class="content-stack">
          <div v-if="!analysis" class="empty-panel">
            <div class="empty-icon">TS</div>
            <h2>等待自动分析结果</h2>
            <p>系统检测到新批次后会自动加载结果；也可以在自动监测或历史记录中选择任务。</p>
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
          <div v-if="!analysis" class="empty-panel compact"><h2>尚未加载分析结果</h2><p>从自动监测记录或历史记录选择一次已完成任务。</p></div>
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
          <ModelEvidencePanel v-if="analysis" :analysis="analysis" />
        </section>

        <section v-else-if="activeTab === 'forecast'" class="content-stack">
          <div v-if="!analysis" class="empty-panel compact"><h2>尚未加载趋势研判</h2><p>从自动监测记录或历史记录选择一次已完成任务。</p></div>
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
          :current-work-order-only="currentWorkOrderOnly"
          :my-work-orders-only="myWorkOrdersOnly"
          :current-user="currentUser"
          :current-run-id="currentAnalysisRunId"
          :current-source-file="analysis?.data_profile?.source_name || selectedFile?.name || ''"
          :feedback="feedback"
          :feedback-notice="feedbackNotice"
          :saving-feedback="savingFeedback"
          :feedback-dirty="feedbackDirty"
          :format-date="formatDate"
          :format-number="formatNumber"
          @update:work-order-search="workOrderSearch = $event"
          @update:work-order-status-filter="workOrderStatusFilter = $event"
          @update:work-order-priority-filter="workOrderPriorityFilter = $event"
          @toggle-current-scope="toggleCurrentWorkOrderScope"
          @toggle-my-scope="toggleMyWorkOrders"
          @select-order="selectWorkOrder"
          @restore-order="restoreSelectedWorkOrder"
          @delete-order="deleteSelectedWorkOrder"
          @toggle-archived="toggleArchivedRecords"
          @change-page="changeWorkOrderPage"
          @refresh="refreshWorkOrders"
          @export="exportWorkOrdersCsv"
          @clear-filters="clearWorkOrderFilters"
          @save-feedback="saveFeedback"
          @accept-order="acceptSelectedWorkOrder"
          @archive-order="archiveSelectedWorkOrder"
        />

        <HistoryPanel
          v-else-if="activeTab === 'history'"
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
          @delete-run="deleteHistoryRun"
          @change-page="changeHistoryPage"
          @toggle-archived="toggleArchivedRecords"
          @refresh="refreshHistory"
          @export-json="exportAnalysisJson"
          @export-summary="exportSummaryMarkdown"
          @select-case="selectCase"
          @delete-case="deleteConfirmedCase"
          @close-case="selectedCase = null"
        />

        <DebugAnalysisPanel
          v-else-if="activeTab === 'debug'"
          :selected-file="selectedFile"
          :file-preflight="filePreflight"
          :config="config"
          :device-profiles="deviceProfiles"
          :device-profiles-loading="deviceProfilesLoading"
          :sample-loading="sampleLoading"
          :analyzing="isAnalyzing"
          @select-file="selectDebugFile"
          @load-sample="loadDefaultSkab"
          @start-analysis="startAnalysis"
          @show-preflight="showPreflight = true"
          @update-config="updateDebugConfig"
        />
      </main>
    </div>
  </div>
</template>
