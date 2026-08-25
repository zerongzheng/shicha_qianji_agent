<script setup>
import { computed, reactive, ref } from "vue";

const props = defineProps({
  monitoring: { type: Object, required: true },
  loading: { type: Boolean, default: false },
  actionId: { type: String, default: "" },
  notificationActionId: { type: String, default: "" },
  currentUser: { type: Object, default: null },
  formatDate: { type: Function, required: true },
  analysis: { type: Object, default: null },
  currentRunId: { type: String, default: "" },
  runLoading: { type: String, default: "" },
});

const emit = defineEmits([
  "refresh", "save-source", "poll-source", "delete-source", "view-run",
  "acknowledge-notification",
]);
const showForm = ref(false);
const form = reactive({
  source_id: null,
  name: "SKAB 自动监测目录",
  source_type: "directory",
  endpoint: "../SKAB/data/valve1",
  interval_seconds: 60,
  enabled: true,
  timeout_seconds: 15,
  initial_scan_mode: "latest",
  p1_name: "生产值班负责人",
  p2_name: "设备工程师",
  p3_name: "运行值班员",
});

const sources = computed(() => props.monitoring?.sources || []);
// 主界面只突出当前真正参与无人值守任务的数据源；停用配置仍保留在审计区中。
const activeSources = computed(() => sources.value.filter((source) => source.enabled));
const inactiveSources = computed(() => sources.value.filter((source) => !source.enabled));
const ready = computed(() => props.monitoring?.status === "success");
const ingestions = computed(() => (props.monitoring?.ingestions || []).slice(0, 12));
const notifications = computed(() => (props.monitoring?.notifications || []).slice(0, 12));
const completedCount = computed(() => ingestions.value.filter((item) => item.status === "completed").length);
const abnormalNoticeCount = computed(() => notifications.value.filter((item) => item.priority === "P1").length);
const wecomStatus = computed(() => props.monitoring?.notification_channels?.wecom || {});
const latestIngestion = computed(() => ingestions.value.find((item) => item.run_id) || null);
const agentAnalysis = computed(() => (
  latestIngestion.value?.run_id === props.currentRunId ? props.analysis : null
));
const latestNotifications = computed(() => {
  const runId = latestIngestion.value?.run_id;
  return runId ? notifications.value.filter((item) => item.run_id === runId) : [];
});
const completedTraceCount = computed(() => (agentAnalysis.value?.execution_trace || [])
  .filter((step) => step.status === "completed").length);
const eventCount = computed(() => agentAnalysis.value?.anomaly_events?.length || 0);
const workOrderCount = computed(() => agentAnalysis.value?.work_order_drafts?.length || 0);
const selectedModel = computed(() => agentAnalysis.value?.model_selection?.selected_detector_name
  || agentAnalysis.value?.detector
  || "等待任务");
const primaryCause = computed(() => agentAnalysis.value?.root_cause_diagnoses?.[0]?.primary_candidate?.name
  || (eventCount.value ? "候选根因正在形成" : "当前批次未形成持续异常"));
const agentState = computed(() => {
  if (!ready.value) return "正在同步运行状态";
  if (latestIngestion.value?.status === "submitted") return "已发现新数据，正在自主分析";
  if (latestIngestion.value?.status === "completed") return "最近一次自主任务已闭环";
  return props.monitoring.monitor?.running ? "持续值守，等待新数据" : "等待启用数据源";
});
const notifiedRoles = computed(() => [...new Set(
  latestNotifications.value.map((item) => item.recipient_role).filter(Boolean),
)].join("、") || "无待通知岗位");

function statusLabel(status) {
  return {
    detected: "已发现",
    submitted: "排队分析",
    completed: "分析完成",
    failed: "处理失败",
    sent: "已送达",
    pending: "待发送",
  }[status] || status || "未知";
}

function submitSource() {
  const route = (name, role) => [{ recipient_name: name.trim(), recipient_role: role }];
  emit("save-source", {
    source_id: form.source_id || undefined,
    name: form.name.trim(),
    source_type: form.source_type,
    endpoint: form.endpoint.trim(),
    interval_seconds: Number(form.interval_seconds),
    enabled: form.enabled,
    timeout_seconds: Number(form.timeout_seconds),
    initial_scan_mode: form.initial_scan_mode,
    request_headers: {},
    analysis_config: {
      detector_selection_mode: "auto",
      analysis_goal: "balanced",
      threshold: 3.5,
      min_event_length: 12,
      merge_gap: 30,
    },
    routing: {
      priority_routes: {
        P1: route(form.p1_name, "生产负责人"),
        P2: route(form.p2_name, "设备运维"),
        P3: route(form.p3_name, "运行监控"),
      },
    },
  });
}

function editSource(source) {
  const routes = source.routing?.priority_routes || {};
  Object.assign(form, {
    source_id: source.source_id,
    name: source.name,
    source_type: source.source_type,
    endpoint: source.endpoint,
    interval_seconds: source.interval_seconds,
    enabled: source.enabled,
    timeout_seconds: source.timeout_seconds || 15,
    initial_scan_mode: source.initial_scan_mode || "latest",
    p1_name: routes.P1?.[0]?.recipient_name || "生产值班负责人",
    p2_name: routes.P2?.[0]?.recipient_name || "设备工程师",
    p3_name: routes.P3?.[0]?.recipient_name || "运行值班员",
  });
  showForm.value = true;
}

function createSource() {
  form.source_id = null;
  form.enabled = true;
  showForm.value = true;
}

function toggleSource(source) {
  emit("save-source", {
    source_id: source.source_id,
    name: source.name,
    source_type: source.source_type,
    endpoint: source.endpoint,
    interval_seconds: source.interval_seconds,
    enabled: !source.enabled,
    timeout_seconds: source.timeout_seconds || 15,
    initial_scan_mode: source.initial_scan_mode || "latest",
    request_headers: {},
    analysis_config: source.analysis_config || {},
    routing: source.routing || {},
  });
}
</script>

<template>
  <section class="monitoring-stack">
    <div class="monitor-hero">
      <div>
        <span class="section-kicker">UNATTENDED OPERATION</span>
        <h2>新数据进入后，分析链路自动启动</h2>
        <p>采集、去重、诊断、工单生成和分级通知在后台连续完成。</p>
      </div>
      <div class="monitor-live" :class="{ stopped: ready && !monitoring.monitor?.running }">
        <span></span>{{ !ready ? "正在同步状态" : monitoring.monitor?.running ? "监测服务运行中" : "暂无启用数据源" }}
      </div>
    </div>

    <section class="agent-console">
      <div class="agent-console-head">
        <div>
          <span class="agent-label">自主运行状态</span>
          <h3>{{ agentState }}</h3>
          <p v-if="latestIngestion">任务 {{ latestIngestion.run_id || '正在创建' }} · {{ latestIngestion.file_name }}</p>
          <p v-else>智能体将持续监听已启用数据源。</p>
        </div>
        <button
          v-if="latestIngestion?.status === 'completed' && latestIngestion.run_id"
          class="agent-result-button"
          :disabled="runLoading === latestIngestion.run_id"
          @click="emit('view-run', latestIngestion.run_id)"
        >
          {{ runLoading === latestIngestion.run_id ? '加载中...' : '进入分析结果' }}
        </button>
      </div>

      <div class="agent-cycle">
        <article class="agent-stage" :class="{ complete: latestIngestion }">
          <span class="stage-index">01</span>
          <div><small>自主感知</small><strong>{{ latestIngestion ? '发现并锁定新批次' : '持续监听数据源' }}</strong><p>{{ latestIngestion?.file_name || sources.find((item) => item.enabled)?.endpoint || '等待数据源配置' }}</p></div>
        </article>
        <span class="stage-arrow">→</span>
        <article class="agent-stage" :class="{ complete: completedTraceCount }">
          <span class="stage-index">02</span>
          <div><small>自主编排</small><strong>{{ completedTraceCount ? `${completedTraceCount} 个模块自动完成` : '等待触发分析工具链' }}</strong><p>主模型：{{ selectedModel }}</p></div>
        </article>
        <span class="stage-arrow">→</span>
        <article class="agent-stage" :class="{ complete: agentAnalysis }">
          <span class="stage-index">03</span>
          <div><small>自主研判</small><strong>{{ agentAnalysis ? `识别 ${eventCount} 个异常事件` : '等待风险判断' }}</strong><p>{{ primaryCause }}</p></div>
        </article>
        <span class="stage-arrow">→</span>
        <article class="agent-stage" :class="{ complete: latestNotifications.length || (agentAnalysis && !workOrderCount) }">
          <span class="stage-index">04</span>
          <div><small>主动执行</small><strong>{{ agentAnalysis ? `生成 ${workOrderCount} 张工单` : '等待处置决策' }}</strong><p>{{ latestNotifications.length ? `已通知：${notifiedRoles}` : '按风险等级自动路由岗位' }}</p></div>
        </article>
      </div>

      <div v-if="agentAnalysis" class="agent-decision">
        <span>本次决策依据</span>
        <p>{{ agentAnalysis.model_selection?.reason || '依据设备配置、数据画像和冻结规则完成模型路由。' }}</p>
        <b>{{ agentAnalysis.model_selection?.analysis_goal_name || '综合平衡' }}</b>
      </div>
    </section>

    <div class="metric-strip">
      <div><span>启用数据源</span><strong>{{ ready ? monitoring.enabled_source_count || 0 : '--' }}</strong></div>
      <div><span>近期采集批次</span><strong>{{ ready ? ingestions.length : '--' }}</strong></div>
      <div><span>自动完成分析</span><strong>{{ ready ? completedCount : '--' }}</strong></div>
      <div><span>P1 主动通知</span><strong>{{ ready ? abnormalNoticeCount : '--' }}</strong></div>
    </div>

    <div class="panel-toolbar">
      <div><h3>数据源</h3><p>配置一次，服务按周期持续检测新增批次。</p></div>
      <div class="toolbar-actions">
        <span class="channel-state" :class="{ active: wecomStatus.enabled && wecomStatus.configured }">
          <i></i>{{ wecomStatus.enabled && wecomStatus.configured ? "企业微信机器人已启用" : wecomStatus.configured ? "企业微信机器人未启用" : "企业微信机器人未配置" }}
        </span>
        <button class="quiet-button" :disabled="loading" @click="emit('refresh')">刷新状态</button>
        <button class="command-button" @click="createSource">新增数据源</button>
      </div>
    </div>

    <form v-if="showForm" class="source-form" @submit.prevent="submitSource">
      <div class="form-heading"><strong>{{ form.source_id ? "编辑数据源" : "接入数据源" }}</strong><button type="button" @click="showForm = false">关闭</button></div>
      <label>名称<input v-model="form.name" required /></label>
      <label>接入方式<select v-model="form.source_type"><option value="directory">监控目录</option><option value="http_csv">HTTP CSV 接口</option></select></label>
      <label class="wide">{{ form.source_type === 'directory' ? '目录路径' : '接口地址' }}<input v-model="form.endpoint" required /></label>
      <label>检测周期（秒）<input v-model.number="form.interval_seconds" type="number" min="1" max="86400" required /></label>
      <label>首次接入<select v-model="form.initial_scan_mode"><option value="latest">验证最新一批</option><option value="new_only">只等待新批次</option><option value="all">处理全部历史批次</option></select></label>
      <label>P1 接收人<input v-model="form.p1_name" required /></label>
      <label>P2 接收人<input v-model="form.p2_name" required /></label>
      <label>P3 接收人<input v-model="form.p3_name" required /></label>
      <div class="wide secure-channel-note"><strong>主动通知由部署环境统一管理</strong><span>机器人密钥不会发送到浏览器；此处只配置 P1/P2/P3 的责任人路由。</span></div>
      <div class="form-footer"><label class="check"><input v-model="form.enabled" type="checkbox" /> 保存后立即启用</label><button class="command-button" :disabled="actionId === 'save'">{{ actionId === 'save' ? '保存中...' : '保存配置' }}</button></div>
    </form>

    <div v-if="!ready" class="monitor-empty"><strong>正在读取数据源</strong><span>正在同步监测服务、采集批次和通知状态。</span></div>
    <div v-else-if="!sources.length" class="monitor-empty"><strong>尚未接入数据源</strong><span>新增目录或 HTTP 接口后，系统将开始无人值守监测。</span></div>
    <div v-else-if="activeSources.length" class="source-list">
      <article v-for="source in activeSources" :key="source.source_id" class="source-row">
        <div class="source-state" :class="{ disabled: !source.enabled }"></div>
        <div class="source-main"><div><strong>{{ source.name }}</strong><span class="type-tag">{{ source.source_type === 'directory' ? '目录' : 'HTTP' }}</span></div><code>{{ source.endpoint }}</code><small>每 {{ source.interval_seconds }} 秒检测 · 最近成功 {{ formatDate(source.last_success_at) }}</small><em v-if="source.last_error">{{ source.last_error }}</em></div>
        <div class="source-actions"><button :disabled="actionId === source.source_id" @click="emit('poll-source', source.source_id)">立即检测</button><button @click="editSource(source)">编辑</button><button @click="toggleSource(source)">{{ source.enabled ? '停用' : '启用' }}</button><button class="danger" @click="emit('delete-source', source.source_id)">删除</button></div>
      </article>
    </div>
    <div v-else class="monitor-empty"><strong>暂无启用的数据源</strong><span>启用已有配置或新增数据源后，智能体将恢复自主值守。</span></div>

    <details v-if="inactiveSources.length" class="inactive-sources">
      <summary>
        <span>已停用数据源</span>
        <small>{{ inactiveSources.length }} 项历史配置，仅用于审计与恢复</small>
      </summary>
      <div class="source-list archived">
        <article v-for="source in inactiveSources" :key="source.source_id" class="source-row">
          <div class="source-state disabled"></div>
          <div class="source-main"><div><strong>{{ source.name }}</strong><span class="type-tag muted">已停用</span></div><code>{{ source.endpoint }}</code><small>最后成功 {{ formatDate(source.last_success_at) }}</small><em v-if="source.last_error">{{ source.last_error }}</em></div>
          <div class="source-actions"><button @click="editSource(source)">编辑</button><button class="restore" @click="toggleSource(source)">重新启用</button><button class="danger" @click="emit('delete-source', source.source_id)">删除</button></div>
        </article>
      </div>
    </details>

    <div class="monitor-grid">
      <section class="data-panel">
        <div class="panel-title"><div><h3>自动处理记录</h3><p>每批数据都有内容指纹、快照和任务编号。</p></div></div>
        <div v-if="!ready" class="table-empty">正在同步处理记录</div>
        <div v-else-if="!ingestions.length" class="table-empty">等待新数据</div>
        <div v-for="item in ingestions" :key="item.ingestion_id" class="timeline-row">
          <span class="status-badge" :class="item.status">{{ statusLabel(item.status) }}</span>
          <div><strong>{{ item.file_name }}</strong><small>{{ formatDate(item.detected_at) }} · {{ item.run_id || '尚未生成任务' }}</small><em v-if="item.error">{{ item.error }}</em></div>
          <button v-if="item.status === 'completed' && item.run_id" class="view-result" @click="emit('view-run', item.run_id)">查看结果</button>
        </div>
      </section>
      <section class="data-panel">
        <div class="panel-title"><div><h3>分级通知</h3><p>异常工单按风险等级送达对应岗位。</p></div></div>
        <div v-if="!ready" class="table-empty">正在同步通知记录</div>
        <div v-else-if="!notifications.length" class="table-empty">暂无异常通知</div>
        <div v-for="item in notifications" :key="item.notification_id" class="notification-row">
          <b :class="`priority-${item.priority}`">{{ item.priority }}</b>
          <div><strong>{{ item.title }}</strong><small>{{ item.recipient_name }} · {{ item.recipient_role }} · {{ statusLabel(item.status) }}</small><em v-if="item.acknowledged_at" class="notification-acknowledged">已签收 · {{ formatDate(item.acknowledged_at) }}</em></div>
          <button v-if="!item.acknowledged_at && item.recipient_user_id === currentUser?.user_id" class="acknowledge-button" :disabled="notificationActionId === item.notification_id" @click="emit('acknowledge-notification', item.notification_id)">{{ notificationActionId === item.notification_id ? "签收中..." : "确认收到" }}</button>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.agent-console{border:1px solid #d7e3e1;background:#fff}.agent-console-head{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:16px 20px;border-bottom:1px solid #e3eae8}.agent-label{color:#2f8179;font-size:10px;font-weight:800}.agent-console-head h3{margin:4px 0;color:#233f42;font-size:17px}.agent-console-head p{margin:0;color:#7b8c8e;font-size:11px}.agent-result-button{min-height:36px;padding:0 13px;border:1px solid #247d75;background:#247d75;color:#fff;font-weight:750;cursor:pointer}.agent-result-button:disabled{opacity:.6;cursor:wait}.agent-cycle{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr) auto minmax(0,1fr) auto minmax(0,1fr);align-items:stretch;padding:18px 20px}.agent-stage{display:grid;grid-template-columns:30px minmax(0,1fr);gap:10px;padding:13px;border:1px solid #e2e8e7;background:#f8faf9}.stage-index{display:grid;width:27px;height:27px;place-items:center;background:#e7eceb;color:#778684;font-size:9px;font-weight:800}.agent-stage.complete{border-color:#c8dfd9;background:#f1f8f5}.agent-stage.complete .stage-index{background:#dcefe8;color:#267668}.agent-stage small,.agent-stage strong,.agent-stage p{display:block}.agent-stage small{color:#3d837b;font-size:9px;font-weight:800}.agent-stage strong{margin-top:5px;color:#284447;font-size:12px}.agent-stage p{margin:5px 0 0;color:#78898b;font-size:10px;line-height:1.45;overflow-wrap:anywhere}.stage-arrow{align-self:center;padding:0 8px;color:#86a09c;font-weight:800}.agent-decision{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:14px;margin:0 20px 18px;padding:11px 13px;border-left:3px solid #c38a2f;background:#fff8ea}.agent-decision span{color:#8e681e;font-size:10px;font-weight:800}.agent-decision p{margin:0;color:#685e49;font-size:11px;line-height:1.5}.agent-decision b{padding:4px 7px;background:#fff;color:#8c6926;font-size:10px;white-space:nowrap}
.monitoring-stack{display:grid;gap:18px}.monitor-hero{display:flex;justify-content:space-between;align-items:center;padding:18px 22px;background:#edf6f4;border-left:4px solid #267f78;color:#254543}.section-kicker{font-size:10px;color:#4d8b85}.monitor-hero h2{margin:5px 0 4px;font-size:20px}.monitor-hero p{margin:0;color:#6c8281;font-size:12px}.monitor-live{display:flex;align-items:center;gap:8px;padding:7px 10px;background:#fff;font-size:12px;font-weight:700}.monitor-live span,.source-state{width:9px;height:9px;border-radius:50%;background:#38a96a;box-shadow:0 0 0 4px #38b66d22}.monitor-live.stopped span,.source-state.disabled{background:#9ba6a4;box-shadow:none}.metric-strip{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid #dbe3e1;background:#fff}.metric-strip div{padding:16px 20px;border-right:1px solid #e3e9e7}.metric-strip div:last-child{border:0}.metric-strip span,.metric-strip strong{display:block}.metric-strip span{font-size:11px;color:#687673}.metric-strip strong{font-size:23px;margin-top:5px}.panel-toolbar,.form-heading,.form-footer,.panel-title{display:flex;align-items:center;justify-content:space-between}.panel-toolbar h3,.panel-title h3{margin:0}.panel-toolbar p,.panel-title p{margin:4px 0 0;color:#71807d;font-size:12px}.toolbar-actions,.source-actions{display:flex;gap:8px}.quiet-button,.command-button,.source-actions button,.form-heading button,.view-result{border:1px solid #c8d4d1;background:#fff;padding:8px 12px;cursor:pointer}.command-button{background:#147d72;color:#fff;border-color:#147d72}.source-form,.data-panel,.source-list{background:#fff;border:1px solid #dbe3e1}.source-form{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;padding:20px}.source-form label{display:grid;gap:6px;font-size:12px;color:#586763}.source-form input,.source-form select{min-width:0;padding:9px 10px;border:1px solid #c8d4d1}.source-form .wide,.form-heading,.form-footer{grid-column:1/-1}.form-heading button{border:0}.check{display:flex!important;grid-template-columns:auto 1fr;align-items:center}.monitor-empty,.table-empty{display:flex;flex-direction:column;align-items:center;padding:28px;color:#7a8784}.source-row{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:14px;align-items:center;padding:16px 18px;border-bottom:1px solid #e6ecea}.source-row:last-child{border:0}.source-main{min-width:0}.source-main strong,.source-main code,.source-main small,.source-main em{display:block}.source-main code{margin:6px 0;overflow:hidden;text-overflow:ellipsis;color:#36514d}.source-main small{color:#71807d}.source-main em,.timeline-row em{color:#b7423b;font-size:12px}.type-tag{margin-left:8px;padding:2px 6px;background:#e8f3f1;color:#236d65;font-size:11px}.type-tag.muted{background:#edf0ef;color:#727d7b}.source-actions button{padding:6px 9px}.source-actions .danger{color:#b33b35}.source-actions .restore{color:#176f64;font-weight:700}.inactive-sources{border:1px solid #dbe3e1;background:#f8faf9}.inactive-sources summary{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 16px;color:#536461;cursor:pointer;list-style-position:inside}.inactive-sources summary span{font-size:12px;font-weight:750}.inactive-sources summary small{color:#84918f;font-size:11px}.inactive-sources .source-list{border-width:1px 0 0}.source-list.archived .source-row{background:#fbfcfc}.monitor-grid{display:grid;grid-template-columns:1.1fr .9fr;gap:18px}.data-panel{padding:18px}.timeline-row{grid-template-columns:auto minmax(0,1fr) auto}.timeline-row,.notification-row{display:grid;gap:12px;align-items:start;padding:13px 0;border-top:1px solid #e9eeec}.notification-row{grid-template-columns:auto 1fr}.timeline-row strong,.timeline-row small,.notification-row strong,.notification-row small{display:block}.timeline-row small,.notification-row small{margin-top:4px;color:#71807d}.view-result{align-self:center;padding:6px 9px;color:#176f6c;font-size:11px;font-weight:700}.status-badge{min-width:65px;text-align:center;padding:4px 6px;font-size:11px;background:#eef2f1}.status-badge.completed,.status-badge.sent{background:#e3f4eb;color:#26754b}.status-badge.failed{background:#fbe9e7;color:#a73a34}.notification-row b{padding:5px 7px}.priority-P1{background:#f9dedb;color:#a9342f}.priority-P2{background:#fff0cf;color:#986417}.priority-P3{background:#e4efee;color:#2d6963}@media(max-width:1000px){.metric-strip{grid-template-columns:repeat(2,1fr)}.monitor-grid{grid-template-columns:1fr}.source-form{grid-template-columns:repeat(2,1fr)}.source-row{grid-template-columns:auto 1fr}.source-actions{grid-column:2;flex-wrap:wrap}}@media(max-width:650px){.monitor-hero,.panel-toolbar{align-items:flex-start;flex-direction:column;gap:14px}.metric-strip,.source-form{grid-template-columns:1fr}.metric-strip div{border-right:0;border-bottom:1px solid #e3e9e7}.source-row{grid-template-columns:1fr}.source-state{display:none}.source-actions{grid-column:1}.source-form label,.source-form .wide{grid-column:1}.timeline-row{grid-template-columns:1fr}.view-result{justify-self:start}.inactive-sources summary{align-items:flex-start;flex-direction:column;gap:4px}}
@media(max-width:1100px){.agent-cycle{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.stage-arrow{display:none}.agent-decision{grid-template-columns:1fr}.agent-decision b{justify-self:start}}
@media(max-width:650px){.agent-console-head{align-items:flex-start;flex-direction:column}.agent-cycle{grid-template-columns:1fr}.agent-decision{margin-left:12px;margin-right:12px}.agent-result-button{width:100%}}
.notification-row{grid-template-columns:auto minmax(0,1fr) auto}.notification-acknowledged{display:block;margin-top:5px;color:#2f7958;font-size:10px;font-style:normal}.acknowledge-button{align-self:center;padding:6px 9px;border:1px solid #82b6a8;background:#f1faf6;color:#26745c;font-size:10px;font-weight:750;cursor:pointer}.acknowledge-button:disabled{opacity:.6;cursor:wait}@media(max-width:650px){.notification-row{grid-template-columns:1fr}.acknowledge-button{justify-self:start}}
.channel-state{display:flex;align-items:center;gap:7px;padding:7px 10px;border:1px solid #d4dddb;background:#f6f8f7;color:#6d7a78;font-size:11px;font-weight:700;white-space:nowrap}.channel-state i{width:7px;height:7px;border-radius:50%;background:#9aa5a3}.channel-state.active{border-color:#b9dace;background:#edf8f3;color:#276e58}.channel-state.active i{background:#2fa36c;box-shadow:0 0 0 3px #2fa36c20}.secure-channel-note{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:10px 12px;border-left:3px solid #2c8177;background:#f1f7f5;color:#536b67}.secure-channel-note strong{font-size:11px;color:#285f58}.secure-channel-note span{font-size:10px;text-align:right}@media(max-width:650px){.toolbar-actions{align-items:flex-start;flex-wrap:wrap}.channel-state{width:100%}.secure-channel-note{align-items:flex-start;flex-direction:column}.secure-channel-note span{text-align:left}}
</style>
