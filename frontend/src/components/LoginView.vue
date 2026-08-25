<script setup>
/** 本地部署的人员登录入口；账号由管理员预置，不提供公开注册。 */
import { reactive } from "vue";

defineProps({ loading: { type: Boolean, default: false }, error: { type: String, default: "" } });
const emit = defineEmits(["login"]);
const form = reactive({ username: "", password: "" });
</script>

<template>
  <main class="login-page">
    <section class="login-brand">
      <img class="login-mark" src="/brand/shicha-mark.svg" alt="时察千机" />
      <span>工业多变量时序智能运维平台</span>
      <h1>时察千机</h1>
      <p>异常由系统主动发现，责任由工单清晰承接，处置结果持续沉淀为可追溯经验。</p>
      <div class="login-flow"><b>自主感知</b><i></i><b>智能研判</b><i></i><b>分级推送</b><i></i><b>人员闭环</b></div>
    </section>
    <form class="login-form" @submit.prevent="emit('login', { ...form })">
      <div><span class="eyebrow">OPERATIONS CONSOLE</span><h2>进入运维工作台</h2><p>使用管理员预置的校赛演示账号登录。</p></div>
      <label>账号<input v-model.trim="form.username" class="control-input" autocomplete="username" required /></label>
      <label>密码<input v-model="form.password" type="password" class="control-input" autocomplete="current-password" required /></label>
      <div v-if="error" class="login-error">{{ error }}</div>
      <button class="primary-button" :disabled="loading">{{ loading ? "正在验证..." : "登录" }}</button>
      <small>账号不支持自行注册，由系统管理员统一配置人员与岗位。</small>
    </form>
  </main>
</template>
