<script setup>
/**
 * 智能体执行链展示。
 *
 * 组件只读取后端返回的可审计工具轨迹，不展示大模型思维过程。每一步聚焦“系统调用了
 * 什么模块、产生了什么结果、是否自动完成”，用于竞赛演示和工程排错。
 */
defineProps({
  steps: { type: Array, default: () => [] },
});

const statusText = (status) => ({
  completed: "已完成",
  skipped: "已跳过",
  failed: "失败",
}[status] || status || "未知");

const outputLabels = {
  row_count: "数据点",
  column_count: "字段",
  time_column: "时间列",
  profile_id: "配置",
  display_name: "设备",
  match_mode: "匹配方式",
  match_score: "匹配度",
  sensor_count: "传感器",
  missing_total: "缺失值",
  sampling_seconds: "采样间隔",
  detector: "检测器",
  anomaly_point_count: "异常点",
  event_count: "异常事件",
  state_count: "工况",
  transition_point_count: "切换点",
  suppressed_event_count: "抑制事件",
  event_evidence_count: "证据事件",
  forecast_sensor_count: "预测测点",
  diagnosis_count: "诊断",
  candidate_count: "候选根因",
  historical_case_match_count: "历史案例命中",
  work_order_draft_count: "工单草案",
  feature_count: "特征数",
  features_per_sensor: "每测点特征",
  feature_types: "特征类型",
  additional_feature_types: "附加特征",
  detector_scope: "适用范围",
  window_size: "窗口长度",
  window_count: "窗口数",
  window_layout: "窗口布局",
  window_stride: "步长",
  methods: "缩放方法",
  fit_scope: "拟合范围",
  future_leakage_guard: "防泄漏",
  reason: "原因",
};

function formatOutput(summary = {}) {
  const entries = Object.entries(summary);
  if (!entries.length) return "无新增结果";
  return entries
    .map(([key, value]) => {
      const unit = key === "sampling_seconds" && value !== null ? " 秒" : "";
      const displayValue = Array.isArray(value)
        ? value.join("、")
        : value === null || value === undefined
          ? "未知"
          : value === true
            ? "是"
            : value === false
              ? "否"
              : value;
      return `${outputLabels[key] || key} ${displayValue}${unit}`;
    })
    .join(" · ");
}

function formatDuration(seconds, status) {
  if (seconds === null || seconds === undefined) {
    return status === "completed" ? "已记录" : "未执行";
  }
  if (seconds < 0.001) return "< 1 ms";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${Number(seconds).toFixed(2)} s`;
}
</script>

<template>
  <div class="panel execution-trace-panel">
    <div class="panel-header">
      <div>
        <h2>自动分析链路</h2>
        <span>确定性模块自动编排 · 全过程可追溯</span>
      </div>
      <strong>{{ steps.filter((step) => step.status === "completed").length }}/{{ steps.length }} 完成</strong>
    </div>

    <div v-if="steps.length" class="execution-trace-list">
      <details
        v-for="(step, index) in steps"
        :key="step.step_id || index"
        class="execution-trace-step"
        :class="`trace-${step.status || 'unknown'}`"
      >
        <summary>
          <span class="trace-index">{{ String(index + 1).padStart(2, "0") }}</span>
          <span class="trace-main">
            <b>{{ step.title }}</b>
            <small>{{ formatOutput(step.output_summary) }}</small>
          </span>
          <span class="trace-duration">{{ formatDuration(step.duration_seconds, step.status) }}</span>
          <span class="trace-status">{{ statusText(step.status) }}</span>
        </summary>
        <div class="trace-detail">
          <div><span>执行模块</span><code>{{ step.module }}</code></div>
          <div><span>使用边界</span><p>{{ step.limitation || "当前步骤未声明额外使用边界。" }}</p></div>
        </div>
      </details>
    </div>
    <div v-else class="panel-empty">该历史任务未保存执行轨迹，可重新运行分析生成。</div>
  </div>
</template>
