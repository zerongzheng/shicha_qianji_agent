<script setup>
/** 展示确定性模型验证、RAG 来源和脱敏模型调用审计。 */
import { computed } from "vue";

const props = defineProps({
  analysis: { type: Object, required: true },
  explainLoading: { type: Boolean, default: false },
});

const emit = defineEmits(["explain"]);

const validation = computed(() => props.analysis?.detector_validation || {});
const models = computed(() => validation.value.models || []);
const diagnosis = computed(() => props.analysis?.automatic_diagnosis || {});
const evidence = computed(() => diagnosis.value.evidence || {});
const knowledge = computed(() => evidence.value.knowledge || []);
const audit = computed(() => props.analysis?.model_audit || null);

function formatScore(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(3) : "-";
}
</script>

<template>
  <section class="panel model-evidence-panel">
    <div class="panel-header model-evidence-header">
      <div>
        <h2>模型验证与知识证据</h2>
        <span>算法负责判断，RAG 负责补充解释，调用记录可追溯</span>
      </div>
      <div class="model-evidence-actions">
        <button
          type="button"
          class="explain-button"
          :disabled="props.explainLoading"
          @click="emit('explain')"
        >
          {{ props.explainLoading ? "解释中..." : "生成辅助解释" }}
        </button>
        <strong>{{ validation.agreement?.level || "未形成" }} 一致性</strong>
      </div>
    </div>

    <div class="model-evidence-section">
      <div class="model-evidence-section-title">
        <h3>多模型交叉验证</h3>
        <span>主模型：{{ validation.primary_detector_name || props.analysis.detector || "未记录" }}</span>
      </div>
      <div v-if="models.length" class="model-evidence-table-wrap">
        <table class="model-evidence-table">
          <thead><tr><th>模型</th><th>类型</th><th>事件</th><th>异常点</th><th>与主模型一致性</th></tr></thead>
          <tbody>
            <tr v-for="model in models" :key="model.detector">
              <td><b>{{ model.detector_name || model.detector }}</b><small v-if="model.is_primary">主模型</small></td>
              <td>{{ model.model_family || "-" }}</td>
              <td>{{ model.event_count ?? 0 }}</td>
              <td>{{ model.anomaly_point_count ?? 0 }}</td>
              <td>{{ formatScore(model.agreement_with_primary?.anomaly_jaccard) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="model-evidence-empty">当前任务没有保存多模型验证明细。</p>
      <p class="model-evidence-note">{{ validation.conclusion || "交叉验证结论尚未生成。" }}</p>
    </div>

    <div class="model-evidence-grid">
      <div class="model-evidence-section">
        <div class="model-evidence-section-title"><h3>RAG 检索证据</h3><span>{{ knowledge.length }} 条命中</span></div>
        <p class="model-evidence-query">{{ evidence.retrieval_query || "本次任务未执行自然语言诊断检索。" }}</p>
        <ul v-if="knowledge.length" class="model-evidence-sources">
          <li v-for="item in knowledge" :key="`${item.source}-${item.score}`">
            <span>{{ item.source }}</span>
            <small>{{ item.retrieval_mode || "keyword" }} · {{ formatScore(item.score) }}</small>
          </li>
        </ul>
        <p v-else class="model-evidence-empty">确定性巡检主链不依赖 RAG；需要解释时按需检索。</p>
      </div>

      <div class="model-evidence-section">
        <div class="model-evidence-section-title"><h3>模型调用审计</h3><span>{{ audit?.call_count ?? 0 }} 次</span></div>
        <div v-if="audit?.calls?.length" class="model-audit-list">
          <div v-for="call in audit.calls" :key="call.call_id" class="model-audit-row">
            <b>{{ call.operation }}</b><span>{{ call.model }}</span><span>{{ call.status }}</span><small>{{ call.duration_ms }} ms</small>
          </div>
        </div>
        <p v-else class="model-evidence-empty">本次结果没有关联外部模型调用，或调用审计尚未写入。</p>
        <p class="model-evidence-note">不保存提示词、回答正文、API Key 或原始 CSV。</p>
      </div>
    </div>

    <div v-if="diagnosis.diagnosis" class="model-evidence-section model-explanation-section">
      <div class="model-evidence-section-title"><h3>辅助解释结果</h3><span>{{ diagnosis.status || "已生成" }}</span></div>
      <p class="model-explanation-text">{{ diagnosis.diagnosis }}</p>
      <p class="model-evidence-note">解释只读取当前任务的结构化证据，并通过知识来源和使用边界约束回答。</p>
    </div>
  </section>
</template>

<style scoped>
.model-evidence-panel{border-top:3px solid #3c6f84}.model-evidence-header{align-items:flex-start}.model-evidence-header strong{padding:6px 9px;background:#edf4f7;color:#35657a;font-size:11px}.model-evidence-actions{display:flex;align-items:center;gap:8px}.explain-button{border:1px solid #3c6f84;background:#fff;color:#35657a;padding:6px 9px;border-radius:4px;font-size:11px;cursor:pointer}.explain-button:disabled{opacity:.55;cursor:wait}.model-evidence-section{padding:16px 20px;border-top:1px solid #e4ebe9}.model-evidence-section-title{display:flex;align-items:baseline;justify-content:space-between;gap:12px}.model-evidence-section-title h3{margin:0;color:#294346;font-size:13px}.model-evidence-section-title span{color:#7b8b8a;font-size:10px}.model-evidence-table-wrap{overflow-x:auto;margin-top:10px}.model-evidence-table{width:100%;border-collapse:collapse;font-size:10px}.model-evidence-table th,.model-evidence-table td{padding:8px;text-align:left;border-bottom:1px solid #edf1ef;white-space:nowrap}.model-evidence-table th{color:#82908e;font-weight:700}.model-evidence-table th{color:#82908e;font-weight:700}.model-evidence-table td{color:#536562}.model-evidence-table td b{display:block;color:#294346}.model-evidence-table td small{display:inline-block;margin-top:3px;color:#398074;font-size:9px}.model-evidence-note,.model-evidence-query{margin:10px 0 0;color:#687976;font-size:11px;line-height:1.6}.model-evidence-grid{display:grid;grid-template-columns:1fr 1fr;border-top:1px solid #e4ebe9}.model-evidence-grid .model-evidence-section{border-top:0}.model-evidence-sources{display:grid;gap:7px;margin:11px 0 0;padding:0;list-style:none}.model-evidence-sources li{display:flex;justify-content:space-between;gap:10px;padding:7px 8px;background:#f6f9f8;color:#486260;font-size:10px}.model-evidence-sources small{color:#78908c}.model-audit-list{display:grid;gap:7px;margin-top:11px}.model-audit-row{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr) auto auto;gap:8px;align-items:center;padding:7px 8px;background:#f6f9f8;color:#536562;font-size:10px}.model-audit-row b{overflow-wrap:anywhere}.model-audit-row small{color:#78908c}.model-evidence-empty{margin:10px 0 0;color:#8a9895;font-size:11px}.model-explanation-section{background:#fbfcfc}.model-explanation-text{margin:12px 0 0;color:#405a57;font-size:12px;line-height:1.75;white-space:pre-wrap}@media(max-width:700px){.model-evidence-grid{grid-template-columns:1fr}.model-audit-row{grid-template-columns:1fr 1fr}.model-audit-row small{grid-column:2}.model-evidence-actions{flex-wrap:wrap}}
</style>
