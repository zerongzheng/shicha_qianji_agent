<script setup>
/** 异常证据面板：展示事件窗口、传感器变化、候选根因和关联工单。 */
import TimeSeriesChart from "./TimeSeriesChart.vue";

defineProps({
  analysis: { type: Object, required: true },
  events: { type: Array, default: () => [] },
  visibleEvidenceEvents: { type: Array, default: () => [] },
  evidenceRiskFilter: { type: String, default: "" },
  expandedEvidenceEvent: { type: Number, default: 0 },
  evidenceCharts: { type: Map, default: () => new Map() },
  formatDate: { type: Function, required: true },
  formatNumber: { type: Function, required: true },
  diagnosisForEvent: { type: Function, required: true },
  relationshipForEvent: { type: Function, required: true },
});

const emit = defineEmits(["filter-risk", "toggle-event", "open-work-order"]);
</script>

<template>
  <section class="content-stack">
    <div class="panel">
      <div class="panel-header evidence-toolbar-header">
        <div><h2>异常事件证据</h2><span class="panel-subtitle">按峰值风险排序，点击事件展开证据链</span></div>
        <div class="evidence-filters">
          <button class="filter-clear" :class="{ active: !evidenceRiskFilter }" @click="emit('filter-risk', '')">全部 {{ events.length }}</button>
          <button class="filter-clear" :class="{ active: evidenceRiskFilter === '高风险' }" @click="emit('filter-risk', '高风险')">高风险</button>
          <button class="filter-clear" :class="{ active: evidenceRiskFilter === '中风险' }" @click="emit('filter-risk', '中风险')">中风险</button>
        </div>
      </div>
      <div v-for="item in visibleEvidenceEvents" :key="item.index" :data-evidence-event="item.index" class="evidence-card" :class="{ expanded: expandedEvidenceEvent === item.index }">
        <button class="evidence-title evidence-title-button" @click="emit('toggle-event', item.index)">
          <span class="event-number">事件 {{ item.index + 1 }}</span><b :class="`risk-${item.event.severity}`">{{ item.event.severity }}</b><span class="event-score">峰值 {{ formatNumber(item.event.peak_score) }}</span><span class="evidence-expand">{{ expandedEvidenceEvent === item.index ? "收起" : "展开" }}</span>
        </button>
        <template v-if="expandedEvidenceEvent === item.index">
          <div v-if="evidenceCharts.get(item.index)" class="evidence-chart-wrap">
            <div class="evidence-chart-label">局部证据 · {{ evidenceCharts.get(item.index).sensor }} · 红色区间为事件窗口</div>
            <TimeSeriesChart :timestamps="evidenceCharts.get(item.index).timestamps" :values="evidenceCharts.get(item.index).values" :bands="evidenceCharts.get(item.index).bands" line-color="#1d8583" :title="`${evidenceCharts.get(item.index).sensor} 异常前后变化`" :unit="evidenceCharts.get(item.index).sensor" />
          </div>
          <div class="evidence-grid"><div><label>时间范围</label><p>{{ formatDate(item.event.start_time) }} - {{ formatDate(item.event.end_time) }}</p></div><div><label>主导传感器</label><p>{{ item.event.dominant_sensors?.join('、') || '待识别' }}</p></div><div><label>候选根因</label><p>{{ diagnosisForEvent(item.index + 1)?.primary_candidate?.name || '待现场确认' }}</p></div></div>
          <div v-if="diagnosisForEvent(item.index + 1)?.primary_candidate" class="evidence-columns"><div><label>支持证据</label><ul><li v-for="evidence in diagnosisForEvent(item.index + 1).primary_candidate.supporting_evidence" :key="evidence">{{ evidence }}</li></ul></div><div><label>证据缺口</label><ul><li v-for="evidence in diagnosisForEvent(item.index + 1).primary_candidate.missing_evidence" :key="evidence">{{ evidence }}</li></ul></div></div>
          <div v-if="relationshipForEvent(item.index + 1)" class="relationship-box"><label>多传感器关系</label><p>{{ relationshipForEvent(item.index + 1)["关系结论"] }}</p><small>{{ relationshipForEvent(item.index + 1)["使用边界"] }}</small></div>
          <div class="evidence-card-actions"><button class="secondary-button" @click="emit('open-work-order', item.index + 1)">查看关联工单</button></div>
        </template>
      </div>
      <div v-if="!visibleEvidenceEvents.length" class="panel-empty">{{ events.length ? "没有符合当前筛选条件的事件" : "未发现持续异常事件" }}</div>
    </div>
  </section>
</template>
