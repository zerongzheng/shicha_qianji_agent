<script setup>
/**
 * 运维工单工作台。
 *
 * 工单列表、筛选、分页、证据详情和现场反馈表单集中在这里展示。
 * 接口请求仍由 App.vue 负责，组件只通过事件通知父组件执行具体动作。
 */
defineProps({
  showArchived: { type: Boolean, default: false },
  selectedWorkOrder: { type: Object, default: null },
  selectedWorkOrderEvent: { type: Object, default: null },
  selectedWorkOrderDiagnosis: { type: Object, default: null },
  workOrders: { type: Array, default: () => [] },
  workOrderTotal: { type: Number, default: 0 },
  workOrderPage: { type: Number, default: 1 },
  workOrderPageCount: { type: Number, default: 1 },
  workOrdersLoading: { type: Boolean, default: false },
  workOrderLoading: { type: Boolean, default: false },
  workOrderActionId: { type: String, default: "" },
  workOrderSearch: { type: String, default: "" },
  workOrderStatusFilter: { type: String, default: "" },
  workOrderPriorityFilter: { type: String, default: "" },
  currentWorkOrderOnly: { type: Boolean, default: false },
  currentRunId: { type: String, default: "" },
  currentSourceFile: { type: String, default: "" },
  feedback: { type: Object, required: true },
  feedbackNotice: { type: Object, default: null },
  savingFeedback: { type: Boolean, default: false },
  feedbackDirty: { type: Boolean, default: false },
  formatDate: { type: Function, required: true },
  formatNumber: { type: Function, required: true },
});

// 时间线必须和后端允许的状态集合保持一致，尤其要保留“待验证”这一现场复测节点。
// 这样工单从处置完成到最终关闭之间有明确的验收环节，不会把两个业务状态压成一步。
const statusSteps = ["待确认", "已确认", "处理中", "待验证", "已完成", "已关闭"];

const emit = defineEmits([
  "update:workOrderSearch",
  "update:workOrderStatusFilter",
  "update:workOrderPriorityFilter",
  "toggle-current-scope",
  "select-order",
  "restore-order",
  "change-page",
  "refresh",
  "export",
  "clear-filters",
  "save-feedback",
  "archive-order",
]);
</script>

<template>
  <section class="content-stack">
    <div v-if="selectedWorkOrder" class="work-order-live-status">
      <span>当前编辑工单状态</span>
      <b :class="`status-${feedback.status}`">{{ feedback.status }}</b>
    </div>
    <div v-if="feedbackNotice" class="feedback-notice" :class="feedbackNotice.type">
      <strong>{{ feedbackNotice.title }}</strong>
      <span>{{ feedbackNotice.detail }}</span>
    </div>

    <div class="work-export-toolbar">
      <span>{{ currentWorkOrderOnly ? "本次分析工单" : "全部历史工单" }}：{{ workOrderTotal }} 条 · 第 {{ workOrderPage }} / {{ workOrderPageCount }} 页</span>
      <div class="work-toolbar-actions">
        <button class="secondary-button" :disabled="workOrdersLoading" @click="emit('refresh')">
          {{ workOrdersLoading ? "刷新中..." : "刷新工单" }}
        </button>
        <button v-if="workOrders.length" class="secondary-button" @click="emit('export')">导出当前页 CSV</button>
      </div>
    </div>

    <div class="two-column work-layout">
      <div class="panel">
        <div class="panel-header work-list-header"><div><h2>{{ showArchived ? "归档工单" : "工单队列" }}</h2><span>{{ workOrdersLoading ? "加载中..." : `${workOrders.length} / ${workOrderTotal} 条` }}</span></div></div>
        <div class="work-order-scope-bar">
          <div class="scope-copy">
            <strong>工单范围</strong>
            <span v-if="currentWorkOrderOnly">当前数据：{{ currentSourceFile || "本次上传数据" }}</span>
            <span v-else>包含此前分析任务产生的历史工单</span>
          </div>
          <div class="scope-switch" role="group" aria-label="工单范围">
            <button
              class="scope-option"
              :class="{ active: !currentWorkOrderOnly }"
              @click="currentWorkOrderOnly && emit('toggle-current-scope')"
            >全部工单</button>
            <button
              class="scope-option current"
              :class="{ active: currentWorkOrderOnly }"
              :disabled="!currentRunId"
              :title="currentRunId ? `仅显示任务 ${currentRunId} 的工单` : '请先完成一次分析'"
              @click="!currentWorkOrderOnly && currentRunId && emit('toggle-current-scope')"
            >仅看本次分析</button>
          </div>
        </div>
        <div class="work-order-filters sticky-filters">
          <input :value="workOrderSearch" class="control-input" placeholder="搜索工单编号、标题或责任角色" @input="emit('update:workOrderSearch', $event.target.value)" />
          <select :value="workOrderStatusFilter" class="control-input" @change="emit('update:workOrderStatusFilter', $event.target.value)"><option value="">全部状态</option><option>待确认</option><option>已确认</option><option>处理中</option><option>待验证</option><option>已完成</option><option>已关闭</option></select>
          <select :value="workOrderPriorityFilter" class="control-input" @change="emit('update:workOrderPriorityFilter', $event.target.value)"><option value="">全部优先级</option><option>P1</option><option>P2</option><option>P3</option></select>
          <button class="filter-clear" @click="emit('clear-filters')">清除</button>
        </div>
        <div v-if="workOrdersLoading" class="panel-loading compact-loading">正在加载工单...</div>
        <template v-else>
          <div v-for="order in workOrders" :key="order.record_id" class="work-order-row" :class="{ selected: selectedWorkOrder?.record_id === order.record_id }" tabindex="0" role="button" @click="emit('select-order', order)" @keydown.enter="emit('select-order', order)">
            <span class="priority">{{ order.priority }}</span>
            <span class="work-order-row-main">
              <b>{{ order.title }}</b>
              <small>{{ order.status }} · {{ order.assigned_role }}</small>
              <em :title="order.run_id">来源：{{ order.source_file_name || "历史分析任务" }}</em>
            </span>
            <button v-if="showArchived" class="row-action" title="恢复工单" @click.stop="emit('restore-order', order)">恢复</button>
          </div>
          <div v-if="!workOrders.length" class="panel-empty">{{ workOrderTotal ? "没有符合条件的工单" : (showArchived ? "暂无归档工单" : "暂无工单") }}</div>
        </template>
        <div class="pagination-bar"><button class="filter-clear" :disabled="workOrderPage <= 1 || workOrdersLoading" @click="emit('change-page', workOrderPage - 1)">上一页</button><span>第 {{ workOrderPage }} / {{ workOrderPageCount }} 页</span><button class="filter-clear" :disabled="workOrderPage >= workOrderPageCount || workOrdersLoading" @click="emit('change-page', workOrderPage + 1)">下一页</button></div>
      </div>

      <div class="panel">
        <div v-if="selectedWorkOrder">
          <div class="panel-header"><h2>工单详情与现场反馈</h2><span>{{ selectedWorkOrder.record_id }}</span></div>
          <div v-if="workOrderLoading" class="panel-loading">正在加载所属任务的异常证据...</div>
          <div v-else class="work-order-detail">
            <div class="work-order-summary"><span class="priority large">{{ selectedWorkOrder.priority }}</span><div><h3>{{ selectedWorkOrder.title }}</h3><p>{{ selectedWorkOrder.assigned_role }} · 来源文件 {{ selectedWorkOrder.source_file_name || "历史分析任务" }}</p><small class="source-run-detail">分析任务：{{ selectedWorkOrder.run_id }}</small></div></div>
            <div class="status-timeline" aria-label="工单处理进度">
              <div v-for="(step, index) in statusSteps" :key="step" class="status-step" :class="{ current: selectedWorkOrder.status === step, complete: statusSteps.indexOf(selectedWorkOrder.status) > index }">
                <span class="status-step-marker">{{ statusSteps.indexOf(selectedWorkOrder.status) > index ? "✓" : index + 1 }}</span>
                <span>{{ step }}</span>
              </div>
            </div>
            <div class="detail-grid"><div><label>异常时间</label><b>{{ selectedWorkOrderEvent ? `${formatDate(selectedWorkOrderEvent.start_time)} - ${formatDate(selectedWorkOrderEvent.end_time)}` : "暂无" }}</b></div><div><label>风险峰值</label><b>{{ selectedWorkOrderEvent ? formatNumber(selectedWorkOrderEvent.peak_score) : "暂无" }}</b></div><div><label>主导传感器</label><b>{{ selectedWorkOrderEvent?.dominant_sensors?.join("、") || "暂无" }}</b></div></div>
            <div class="evidence-box"><div><label>算法证据</label><ul><li v-for="item in selectedWorkOrder.evidence_summary" :key="item">{{ item }}</li><li v-if="!selectedWorkOrder.evidence_summary?.length">暂无结构化证据</li></ul></div><div><label>建议处置</label><ul><li v-for="item in selectedWorkOrder.actions" :key="item">{{ item }}</li><li v-if="!selectedWorkOrder.actions?.length">暂无处置动作</li></ul></div></div>
            <div v-if="selectedWorkOrderDiagnosis?.primary_candidate" class="diagnosis-strip"><label>候选根因</label><b>{{ selectedWorkOrderDiagnosis.primary_candidate.name }}</b><span>{{ selectedWorkOrderDiagnosis.primary_candidate.confidence || "待现场确认" }}</span></div>
            <div class="form-stack">
              <label>状态<select v-model="feedback.status" class="control-input" :disabled="showArchived"><option>待确认</option><option>已确认</option><option>处理中</option><option>待验证</option><option>已完成</option><option>已关闭</option></select></label>
              <label>确认根因<input v-model="feedback.confirmed_cause" class="control-input" :disabled="showArchived" placeholder="填写现场确认结果" /></label>
              <label>处置与复测<textarea v-model="feedback.feedback_note" class="control-input" rows="5" :disabled="showArchived" placeholder="填写处理动作和复测结果"></textarea></label>
              <label>处理人员<input v-model="feedback.handled_by" class="control-input" :disabled="showArchived" /></label>
              <div class="form-actions">
                <button v-if="!showArchived" class="primary-button" :disabled="savingFeedback || !feedbackDirty" @click="emit('save-feedback')">{{ savingFeedback ? "保存中..." : feedbackDirty ? "保存反馈" : "已保存" }}</button>
                <button v-if="!showArchived" class="archive-button" :disabled="!['已完成', '已关闭'].includes(selectedWorkOrder.status) || savingFeedback || workOrderActionId" @click="emit('archive-order')">归档工单</button>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="panel-empty">选择一条工单查看详情</div>
      </div>
    </div>
  </section>
</template>
