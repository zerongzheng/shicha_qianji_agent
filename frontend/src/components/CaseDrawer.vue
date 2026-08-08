<script setup>
/**
 * 已确认案例详情抽屉。
 *
 * 抽屉只展示案例、异常证据和现场反馈，删除与关闭动作通过事件交给
 * HistoryPanel，再由 App.vue 负责确认弹窗和后端请求。
 */
defineProps({
  caseItem: { type: Object, required: true },
  event: { type: Object, default: null },
  diagnosis: { type: Object, default: null },
  eventNumber: { type: Number, default: 0 },
  loading: { type: Boolean, default: false },
  formatDate: { type: Function, required: true },
  formatNumber: { type: Function, required: true },
});

const emit = defineEmits(["close", "delete"]);
</script>

<template>
  <div class="case-drawer-backdrop" @click="emit('close')">
    <aside class="case-drawer" role="dialog" aria-modal="true" aria-labelledby="case-drawer-title" @click.stop>
      <div class="drawer-header">
        <div><span class="eyebrow">TRACEABLE CASE</span><h2 id="case-drawer-title">{{ caseItem.case_id }}</h2></div>
        <button class="drawer-close" title="关闭案例详情" aria-label="关闭案例详情" @click="emit('close')">×</button>
      </div>
      <div v-if="loading" class="panel-loading">正在加载案例来源任务...</div>
      <div v-else class="drawer-body">
        <div class="case-hero"><span>现场确认根因</span><strong>{{ caseItem.confirmed_cause }}</strong><small>{{ caseItem.handled_by || "未记录处理人员" }} · {{ formatDate(caseItem.closed_at) }}</small></div>
        <div class="drawer-grid"><div><label>来源分析任务</label><b>{{ caseItem.source_run_id }}</b></div><div><label>异常事件</label><b>事件 {{ eventNumber || "-" }}</b></div><div><label>工况上下文</label><b>{{ caseItem.regime_context || "未记录" }}</b></div></div>
        <div class="drawer-section"><h3>异常现象</h3><p v-if="event">{{ formatDate(event.start_time) }} - {{ formatDate(event.end_time) }}，峰值风险 {{ formatNumber(event.peak_score) }}；主导传感器：{{ event.dominant_sensors?.join("、") || "未识别" }}。</p><p v-else>原始任务中没有可展示的异常事件详情。</p></div>
        <div class="drawer-section"><h3>证据与诊断</h3><ul><li v-for="item in caseItem.evidence_summary" :key="item">{{ item }}</li><li v-for="item in diagnosis?.primary_candidate?.supporting_evidence || []" :key="`support-${item}`">{{ item }}</li><li v-if="!caseItem.evidence_summary?.length && !diagnosis?.primary_candidate?.supporting_evidence?.length">暂无结构化证据</li></ul></div>
        <div class="drawer-section"><h3>现场处置与复测</h3><p>{{ caseItem.feedback_note || "未填写现场处置反馈" }}</p></div>
        <div class="drawer-footer"><span>{{ caseItem.archived_at ? "已归档，原始数据仍保留" : "当前案例可用于后续相似案例检索" }}</span><button v-if="!caseItem.archived_at" class="delete-action drawer-delete" @click="emit('delete', caseItem)">永久移除案例</button><button class="secondary-button" @click="emit('close')">关闭</button></div>
      </div>
    </aside>
  </div>
</template>
