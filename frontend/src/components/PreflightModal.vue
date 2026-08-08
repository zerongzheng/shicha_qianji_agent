<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
/**
 * CSV 分析前预检弹窗。
 *
 * 预检结果来自浏览器端的轻量检查，后端仍会执行最终校验。组件本身不保存
 * 文件内容，只展示列名、数据规模和风险提示，确认后由父组件开始分析。
 */
defineProps({
  file: { type: Object, required: true },
});

const emit = defineEmits(["close", "confirm"]);
const closeButton = ref(null);

function handleKeydown(event) {
  if (event.key === "Escape") emit("close");
}

onMounted(async () => {
  window.addEventListener("keydown", handleKeydown);
  await nextTick();
  closeButton.value?.focus();
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleKeydown);
});
</script>

<template>
  <section class="preflight-backdrop" @click="emit('close')">
    <div class="preflight-modal" role="dialog" aria-modal="true" aria-labelledby="preflight-title" @click.stop>
      <div class="panel-header">
        <div>
          <span class="eyebrow">DATA PREFLIGHT</span>
          <h2 id="preflight-title">分析前文件检查</h2>
        </div>
        <button ref="closeButton" class="drawer-close" title="关闭检查结果" aria-label="关闭检查结果" @click="emit('close')">×</button>
      </div>

      <div class="preflight-grid">
        <div><span>文件</span><b>{{ file.fileName }}</b></div>
        <div><span>大小</span><b>{{ file.sizeLabel }}</b></div>
        <div><span>数据行数</span><b>{{ file.rowCount }}</b></div>
        <div><span>解析分隔符</span><b>{{ file.delimiter }}</b></div>
        <div><span>时间列</span><b>{{ file.datetimeColumn || "未识别" }}</b></div>
        <div><span>测点列</span><b>{{ file.sensorCount }} 个</b></div>
      </div>

      <div class="preflight-columns">
        <span>识别到的列</span>
        <p>{{ file.columns?.join("、") || "未识别到表头" }}</p>
      </div>
      <div v-if="file.warnings?.length" class="preflight-warnings">
        <b>提交前请确认</b>
        <ul><li v-for="warning in file.warnings" :key="warning">{{ warning }}</li></ul>
      </div>
      <div v-else class="preflight-pass">文件结构满足当前校赛分析流程的基本要求。</div>

      <div class="modal-actions">
        <button type="button" class="secondary-button" @click="emit('close')">返回修改</button>
        <button type="button" class="primary-button modal-primary" @click="emit('confirm')">确认并开始分析</button>
      </div>
    </div>
  </section>
</template>
