<script setup>
import { ref } from "vue";

defineProps({
  selectedFile: { type: Object, default: null },
  filePreflight: { type: Object, default: null },
  config: { type: Object, required: true },
  deviceProfiles: { type: Array, default: () => [] },
  deviceProfilesLoading: { type: Boolean, default: false },
  sampleLoading: { type: Boolean, default: false },
  analyzing: { type: Boolean, default: false },
});

const emit = defineEmits([
  "select-file",
  "load-sample",
  "start-analysis",
  "show-preflight",
  "update-config",
]);
const fileInput = ref(null);

function chooseFile() {
  fileInput.value?.click();
}

function onFileChange(event) {
  emit("select-file", event.target.files?.[0] || null);
  event.target.value = "";
}
</script>

<template>
  <section class="debug-workspace">
    <div class="debug-banner">
      <div>
        <span>MANUAL VALIDATION</span>
        <h2>单文件调试分析</h2>
        <p>用于算法验证、参数对照和比赛现场备选演示，不参与自动监测主链路。</p>
      </div>
      <b>调试入口</b>
    </div>

    <div class="debug-grid">
      <section class="debug-section file-section">
        <div class="section-number">01</div>
        <div class="section-copy">
          <h3>选择验证数据</h3>
          <p>使用本地 CSV，或载入项目配置的 SKAB 默认样例。</p>
          <div class="file-actions">
            <button class="debug-primary" type="button" @click="chooseFile">选择 CSV</button>
            <button type="button" :disabled="sampleLoading || analyzing" @click="emit('load-sample')">
              {{ sampleLoading ? "正在准备..." : "载入 SKAB 样例" }}
            </button>
          </div>
          <input ref="fileInput" type="file" accept=".csv" hidden @change="onFileChange" />
          <div class="debug-file" :class="{ empty: !selectedFile }">
            <span>{{ selectedFile ? "CSV" : "--" }}</span>
            <div>
              <strong>{{ selectedFile?.name || "尚未选择文件" }}</strong>
              <small v-if="selectedFile">{{ selectedFile.isSample ? "项目内置公开样例" : "本机临时验证文件" }}</small>
              <small v-else>选择文件后先进行结构预检，再提交后台分析。</small>
            </div>
          </div>
          <div v-if="filePreflight" class="preflight-summary">
            <div><span>数据行</span><b>{{ filePreflight.rowCount }}</b></div>
            <div><span>测点数</span><b>{{ filePreflight.sensorCount }}</b></div>
            <div><span>缺失率</span><b>{{ (filePreflight.missingRate * 100).toFixed(1) }}%</b></div>
            <button type="button" @click="emit('show-preflight')">查看预检</button>
          </div>
        </div>
      </section>

      <section class="debug-section config-section">
        <div class="section-number">02</div>
        <div class="section-copy">
          <h3>设置对照参数</h3>
          <p>自动监测使用数据源自身配置；这里的参数只影响本次手动验证。</p>
          <div class="debug-form">
            <label>
              设备数据配置
              <select
                :value="config.device_profile_id"
                :disabled="deviceProfilesLoading || analyzing"
                @change="emit('update-config', 'device_profile_id', $event.target.value || null)"
              >
                <option value="">自动识别</option>
                <option value="generic">通用模式</option>
                <option v-for="profile in deviceProfiles" :key="profile.profile_id" :value="profile.profile_id">
                  {{ profile.display_name }} · {{ profile.profile_id }}
                </option>
              </select>
            </label>
            <label>
              异常检测器
              <select :value="config.detector" @change="emit('update-config', 'detector', $event.target.value)">
                <option value="time_frequency_relation">时频关系多路径</option>
                <option value="window_autoencoder">滑动窗口 AutoEncoder</option>
                <option value="hybrid">时序-工况混合</option>
                <option value="pca_reconstruction">PCA 多变量重构</option>
                <option value="mad">稳健 MAD</option>
              </select>
            </label>
            <label class="threshold-control">
              <span>异常阈值 <b>{{ config.threshold }}</b></span>
              <input
                :value="config.threshold"
                type="range"
                min="2"
                max="10"
                step="0.1"
                @input="emit('update-config', 'threshold', Number($event.target.value))"
              />
            </label>
          </div>
        </div>
      </section>
    </div>

    <div class="debug-submit">
      <div><strong>提交单次验证任务</strong><span>结果会进入风险总览、工单与历史记录，并标记为手动调试来源。</span></div>
      <button :disabled="analyzing || !selectedFile" @click="emit('start-analysis')">
        {{ analyzing ? "分析进行中..." : "开始调试分析" }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.debug-workspace{display:grid;gap:18px}.debug-banner{display:flex;align-items:center;justify-content:space-between;padding:22px 24px;border-left:4px solid #28837d;background:#edf6f4}.debug-banner span{color:#3e837d;font-size:10px;font-weight:800}.debug-banner h2{margin:5px 0 4px;color:#1c383b;font-size:21px}.debug-banner p{margin:0;color:#637a7c;font-size:12px}.debug-banner>b{padding:6px 10px;background:#fff;color:#44716f;font-size:11px}.debug-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.debug-section{display:grid;grid-template-columns:44px minmax(0,1fr);gap:16px;padding:22px;border:1px solid #dce5e5;background:#fff}.section-number{display:grid;width:36px;height:36px;place-items:center;background:#e6f1ef;color:#2a7772;font-size:11px;font-weight:800}.section-copy h3{margin:0 0 5px;color:#243b40;font-size:16px}.section-copy>p{margin:0 0 18px;color:#78898c;font-size:12px}.file-actions{display:flex;gap:8px}.file-actions button,.preflight-summary button{min-height:36px;padding:0 13px;border:1px solid #bfcfce;background:#fff;color:#3c5d5c;font-weight:700;cursor:pointer}.file-actions .debug-primary{border-color:#217d76;background:#217d76;color:#fff}.debug-file{display:flex;align-items:center;gap:12px;margin-top:14px;padding:13px;border:1px solid #d9e3e2;background:#f8fbfa}.debug-file.empty{border-style:dashed}.debug-file>span{display:grid;width:38px;height:32px;place-items:center;background:#dcecea;color:#246f6b;font-size:10px;font-weight:800}.debug-file strong,.debug-file small{display:block}.debug-file strong{color:#31484c;font-size:12px}.debug-file small{margin-top:4px;color:#879597;font-size:10px}.preflight-summary{display:grid;grid-template-columns:repeat(3,1fr) auto;align-items:end;gap:9px;margin-top:12px}.preflight-summary div{padding:8px 9px;background:#f0f5f4}.preflight-summary span,.preflight-summary b{display:block}.preflight-summary span{color:#829092;font-size:9px}.preflight-summary b{margin-top:3px;color:#34595a;font-size:12px}.debug-form{display:grid;gap:14px}.debug-form label{display:grid;gap:6px;color:#637578;font-size:11px}.debug-form select{width:100%;min-height:38px;padding:7px 9px;border:1px solid #cbd7d6;background:#fff;color:#263d41}.threshold-control span{display:flex;justify-content:space-between}.threshold-control input{width:100%;accent-color:#2d8a82}.debug-submit{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:18px 22px;border:1px solid #d7e2e1;background:#fff}.debug-submit strong,.debug-submit span{display:block}.debug-submit strong{color:#263e42;font-size:14px}.debug-submit span{margin-top:4px;color:#819093;font-size:11px}.debug-submit button{min-width:150px;min-height:42px;border:0;background:#176f6c;color:#fff;font-weight:800;cursor:pointer}.debug-submit button:disabled{background:#94aaa8;cursor:not-allowed}@media(max-width:980px){.debug-grid{grid-template-columns:1fr}}@media(max-width:650px){.debug-banner,.debug-submit{align-items:flex-start;flex-direction:column}.debug-section{grid-template-columns:1fr}.preflight-summary{grid-template-columns:repeat(3,1fr)}.preflight-summary button{grid-column:1/-1}.debug-submit button{width:100%}}
</style>
