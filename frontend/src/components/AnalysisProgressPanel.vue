<script setup>
import { computed } from "vue";

/**
 * 分析任务进度面板。
 *
 * 组件只负责展示任务状态，不直接请求后端。任务的上传、轮询、取消
 * 仍由 App.vue 统一管理，组件通过 cancel 事件把用户操作传回页面状态层。
 */
const props = defineProps({
  runId: { type: String, default: "" },
  stage: { type: String, default: "idle" },
  status: { type: String, default: "准备中" },
  percent: { type: Number, default: 0 },
  detail: { type: String, default: "等待提交任务" },
  elapsed: { type: Number, default: 0 },
  steps: { type: Array, default: () => [] },
  cancelling: { type: Boolean, default: false },
  activeJobId: { type: String, default: "" },
});

const emit = defineEmits(["cancel"]);

const isFailed = computed(() => props.stage === "failed");
const canCancel = computed(() => (
  props.activeJobId && ["排队中", "已提交"].includes(props.status)
));

function stepClass(stepId) {
  const order = ["uploading", "queued", "running", "finalizing"];
  const current = order.indexOf(props.stage);
  const step = order.indexOf(stepId);
  if (props.stage === "success") return "complete";
  if (isFailed.value) return step <= current ? "failed" : "pending";
  if (step < current) return "complete";
  if (step === current) return "active";
  return "pending";
}

function formatElapsed(seconds) {
  const value = Number(seconds) || 0;
  const minutes = Math.floor(value / 60);
  const remaining = String(value % 60).padStart(2, "0");
  return String(minutes).padStart(2, "0") + ":" + remaining;
}
</script>

<template>
  <section class="analysis-progress-panel" role="region" aria-live="polite" aria-labelledby="analysis-progress-title">
    <div class="progress-header">
      <div>
        <div class="progress-kicker">ANALYSIS TASK {{ runId || "PENDING" }}</div>
        <h2 id="analysis-progress-title">{{ isFailed ? "分析任务未完成" : "工业时序分析正在运行" }}</h2>
        <p>{{ detail }}</p>
      </div>
      <div class="progress-clock">
        <span>{{ status }}</span>
        <b>{{ formatElapsed(elapsed) }}</b>
        <small>已耗时</small>
      </div>
    </div>

    <div class="progress-track">
      <span
        role="progressbar"
        aria-label="分析任务进度"
        :aria-valuenow="Math.max(0, Math.min(100, percent))"
        aria-valuemin="0"
        aria-valuemax="100"
        :class="{ indeterminate: stage === 'running' }"
        :style="{ width: Math.max(0, Math.min(100, percent)) + '%' }"
      ></span>
    </div>
    <div class="progress-meta">
      <span>当前进度 {{ percent }}%</span>
      <span>任务状态：{{ status }}</span>
    </div>

    <div class="progress-steps">
      <div
        v-for="(step, index) in steps"
        :key="step.id"
        class="progress-step"
        :class="stepClass(step.id)"
      >
        <span class="step-marker">{{ stepClass(step.id) === "complete" ? "✓" : String(index + 1).padStart(2, "0") }}</span>
        <div>
          <b>{{ step.label }}</b>
          <small>{{ step.detail }}</small>
        </div>
      </div>
    </div>

    <div v-if="isFailed" class="progress-failure">
      任务已停止。可以检查后端终端日志，确认数据格式或分析参数。
    </div>
    <div v-if="canCancel" class="progress-actions">
      <button class="secondary-button" :disabled="cancelling" @click="emit('cancel')">
        {{ cancelling ? "正在取消..." : "取消排队任务" }}
      </button>
    </div>
  </section>
</template>
