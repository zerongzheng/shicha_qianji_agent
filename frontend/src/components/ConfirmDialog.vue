<script setup>
// 统一的危险操作确认弹窗。
// 业务页面只负责传入标题、说明和按钮文字，弹窗本身不包含任何业务逻辑，便于后续复用。
defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, required: true },
  detail: { type: String, required: true },
  confirmText: { type: String, default: "继续" },
  tone: { type: String, default: "warning" },
});

const emit = defineEmits(["confirm", "cancel"]);
</script>

<template>
  <section
    v-if="modelValue"
    class="confirm-backdrop"
    role="dialog"
    aria-modal="true"
    aria-labelledby="confirm-dialog-title"
    @click="emit('cancel')"
  >
    <div class="confirm-dialog" @click.stop>
      <div class="confirm-icon" :class="tone">!</div>
      <div class="confirm-copy">
        <h2 id="confirm-dialog-title">{{ title }}</h2>
        <p>{{ detail }}</p>
      </div>
      <div class="confirm-actions">
        <button class="secondary-button" @click="emit('cancel')">取消</button>
        <button
          class="primary-button"
          :class="{ 'danger-button': tone === 'danger' }"
          @click="emit('confirm')"
        >
          {{ confirmText }}
        </button>
      </div>
    </div>
  </section>
</template>
