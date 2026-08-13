<script setup>
/**
 * 历史任务与已确认案例页面。
 *
 * 组件负责历史数据的展示和案例抽屉布局；任务查看、重试、归档、删除等
 * 行为由 App.vue 通过事件处理，避免在展示组件中重复维护接口逻辑。
 */
defineProps({
  analysis: { type: Object, default: null },
  runs: { type: Array, default: () => [] },
  filteredHistoryRuns: { type: Array, default: () => [] },
  paginatedHistoryRuns: { type: Array, default: () => [] },
  cases: { type: Array, default: () => [] },
  showArchived: { type: Boolean, default: false },
  historySearch: { type: String, default: "" },
  historyStatus: { type: String, default: "" },
  historyPage: { type: Number, default: 1 },
  historyPageCount: { type: Number, default: 1 },
  historyRunLoading: { type: String, default: "" },
  historyActionId: { type: String, default: "" },
  refreshingHistory: { type: Boolean, default: false },
  retryingRunId: { type: String, default: "" },
  selectedCase: { type: Object, default: null },
  selectedCaseEvent: { type: Object, default: null },
  selectedCaseDiagnosis: { type: Object, default: null },
  caseEventNumber: { type: Number, default: 0 },
  caseLoading: { type: Boolean, default: false },
  formatDate: { type: Function, required: true },
  formatNumber: { type: Function, required: true },
});

const emit = defineEmits([
  "update:historySearch",
  "update:historyStatus",
  "view-run",
  "retry-run",
  "archive-run",
  "restore-run",
  "delete-run",
  "change-page",
  "toggle-archived",
 "refresh",
  "export-json",
  "export-summary",
 "select-case",
  "delete-case",
  "close-case",
]);
</script>

<template>
  <section class="content-stack">
    <div class="history-toolbar">
      <div><h2>历史任务与案例</h2><p>{{ showArchived ? "查看已归档记录，可恢复到日常工作台。" : "对历次分析进行追踪，复核工单处置结果。" }}</p></div>
      <div class="history-actions">
        <button v-if="analysis" class="secondary-button" @click="emit('export-json')">导出分析 JSON</button>
        <button v-if="analysis" class="secondary-button" @click="emit('export-summary')">导出摘要</button>
        <button class="secondary-button" @click="emit('toggle-archived')">{{ showArchived ? "返回当前记录" : "查看归档记录" }}</button>
        <button class="secondary-button" :disabled="refreshingHistory" @click="emit('refresh')">{{ refreshingHistory ? "刷新中..." : "刷新记录" }}</button>
      </div>
    </div>

    <div class="two-column">
      <div class="panel">
        <div class="panel-header"><div><h2>分析任务</h2><span>当前显示 {{ filteredHistoryRuns.length }} / {{ runs.length }} 条</span></div><span>最近 50 条</span></div>
        <div class="history-filters">
          <input :value="historySearch" class="control-input" placeholder="搜索文件名、任务编号或检测器" @input="emit('update:historySearch', $event.target.value)" />
          <select :value="historyStatus" class="control-input" @change="emit('update:historyStatus', $event.target.value)"><option value="">全部任务状态</option><option value="success">成功</option><option value="failed">失败</option><option value="running">运行中</option><option value="queued">排队中</option><option value="cancelled">已取消</option></select>
        </div>
        <div v-for="item in paginatedHistoryRuns" :key="item.run_id" class="history-row">
          <span class="history-status" :class="item.status">{{ item.status }}</span>
          <span>
            <b>{{ item.file_name }} <em class="run-source" :class="{ automatic: item.source_id }">{{ item.source_id ? "自动监测" : "手动调试" }}</em></b>
            <small>{{ item.detector }} · {{ formatDate(item.started_at) }}{{ item.archived_at ? " · 已归档" : "" }}</small>
          </span>
          <span>{{ item.duration_ms ? `${formatNumber(item.duration_ms, 0)} ms` : "-" }}</span>
          <button class="row-action view-action" :disabled="historyRunLoading === item.run_id" title="查看完整分析结果" @click="emit('view-run', item)">{{ historyRunLoading === item.run_id ? "加载中" : "查看" }}</button>
          <button v-if="item.status === 'failed' && !showArchived" class="row-action retry-action" :disabled="retryingRunId === item.run_id" title="使用原文件重试" @click="emit('retry-run', item)">{{ retryingRunId === item.run_id ? "重试中" : "重试" }}</button>
          <button class="row-action" :disabled="historyActionId === item.run_id || refreshingHistory" :title="showArchived ? '恢复任务' : '归档任务'" @click="emit(showArchived ? 'restore-run' : 'archive-run', item)">{{ historyActionId === item.run_id ? (showArchived ? "处理中" : "归档中") : (showArchived ? "恢复" : "归档") }}</button>
          <button v-if="showArchived" class="row-action delete-action" :disabled="historyActionId === item.run_id || refreshingHistory" title="永久删除任务及关联工单" @click="emit('delete-run', item)">彻底删除</button>
        </div>
        <div v-if="!paginatedHistoryRuns.length" class="panel-empty">{{ filteredHistoryRuns.length ? "当前页没有任务" : (showArchived ? "暂无归档任务" : "暂无历史任务") }}</div>
        <div class="pagination-bar history-pagination">
          <button class="filter-clear" :disabled="historyPage <= 1 || refreshingHistory" @click="emit('change-page', historyPage - 1)">上一页</button>
          <span>第 {{ historyPage }} / {{ historyPageCount }} 页</span>
          <button class="filter-clear" :disabled="historyPage >= historyPageCount || refreshingHistory" @click="emit('change-page', historyPage + 1)">下一页</button>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header"><h2>已确认案例</h2><span>{{ cases.length }} 条</span></div>
        <div v-for="item in cases" :key="item.case_id" class="case-row case-button">
          <button class="case-row-main" @click="emit('select-case', item)"><span class="case-id">{{ item.case_id }}</span><span><b>{{ item.confirmed_cause }}</b><small>{{ item.feedback_note || "未填写处置反馈" }}{{ item.archived_at ? " · 已归档" : "" }}</small></span></button>
          <button class="row-action delete-action" title="永久移除案例" @click="emit('delete-case', item)">移除</button>
        </div>
        <div v-if="!cases.length" class="panel-empty">{{ showArchived ? "暂无归档案例" : "完成工单反馈后，案例会显示在这里" }}</div>
      </div>
    </div>

    <CaseDrawer
      v-if="selectedCase"
      :case-item="selectedCase"
      :event="selectedCaseEvent"
      :diagnosis="selectedCaseDiagnosis"
      :event-number="caseEventNumber"
      :loading="caseLoading"
      :format-date="formatDate"
      :format-number="formatNumber"
      @close="emit('close-case')"
      @delete="emit('delete-case', $event)"
    />
 </section>
</template>
import CaseDrawer from "./CaseDrawer.vue";

<style scoped>
.run-source{display:inline-block;margin-left:6px;padding:2px 5px;background:#f0f2f2;color:#7b898b;font-size:9px;font-style:normal;font-weight:700;vertical-align:2px}.run-source.automatic{background:#e2f1ed;color:#2d7569}
</style>
