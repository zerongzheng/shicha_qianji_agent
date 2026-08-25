<script setup>
/**
 * 智能体业务决策账本。
 *
 * 执行链展示工具调用事实，本面板展示可复核的决策依据、动作、责任对象与人工闸门。
 * 所有内容均由后端结构化生成，不展示或伪造大模型思维过程。
 */
defineProps({
  decisions: { type: Array, default: () => [] },
});
</script>

<template>
  <section class="panel decision-panel">
    <div class="panel-header decision-header">
      <div>
        <h2>智能体决策账本</h2>
        <span>证据、规则、动作与人工闸门全程可审计</span>
      </div>
      <strong>{{ decisions.length }} 项决策</strong>
    </div>

    <div v-if="decisions.length" class="decision-list">
      <article v-for="(item, index) in decisions" :key="item.decision_id || index" class="decision-item">
        <div class="decision-index">{{ String(index + 1).padStart(2, "0") }}</div>
        <div class="decision-main">
          <div class="decision-title">
            <span>{{ item.stage }}</span>
            <h3>{{ item.title }}</h3>
            <b>{{ item.status }}</b>
          </div>
          <p class="decision-action">{{ item.action }}</p>
          <div class="decision-evidence">
            <small v-for="evidence in item.evidence || []" :key="evidence">{{ evidence }}</small>
          </div>
          <details>
            <summary>查看决策约束</summary>
            <dl>
              <div><dt>触发条件</dt><dd>{{ item.trigger }}</dd></div>
              <div><dt>冻结规则</dt><dd>{{ item.rule }}</dd></div>
              <div><dt>责任对象</dt><dd>{{ item.target }}</dd></div>
              <div><dt>可信说明</dt><dd>{{ item.confidence }}</dd></div>
              <div><dt>人工闸门</dt><dd>{{ item.human_gate }}</dd></div>
              <div><dt>回退条件</dt><dd>{{ item.rollback_condition }}</dd></div>
            </dl>
          </details>
        </div>
      </article>
    </div>
    <div v-else class="panel-empty">该历史任务未保存决策账本，重新分析后可生成。</div>
  </section>
</template>

<style scoped>
.decision-panel{border-top:3px solid #237b73}.decision-header strong{padding:6px 9px;background:#e8f4f1;color:#246d65;font-size:11px}.decision-list{display:grid}.decision-item{display:grid;grid-template-columns:42px minmax(0,1fr);gap:14px;padding:17px 20px;border-top:1px solid #e4ebe9}.decision-index{display:grid;width:34px;height:34px;place-items:center;background:#263e41;color:#fff;font-size:10px;font-weight:800}.decision-main{min-width:0}.decision-title{display:flex;align-items:center;gap:10px}.decision-title span{color:#33827a;font-size:9px;font-weight:800}.decision-title h3{min-width:0;margin:0;color:#294346;font-size:13px}.decision-title b{margin-left:auto;padding:3px 6px;background:#fff1d8;color:#8a621d;font-size:9px;white-space:nowrap}.decision-action{margin:8px 0;color:#354d50;font-size:12px;font-weight:700;line-height:1.55}.decision-evidence{display:flex;flex-wrap:wrap;gap:6px}.decision-evidence small{padding:4px 7px;background:#f0f5f4;color:#60726f;font-size:9px}.decision-main details{margin-top:10px}.decision-main summary{width:max-content;color:#2f776f;font-size:10px;font-weight:700;cursor:pointer}.decision-main dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 18px;margin:10px 0 0;padding:12px;background:#f8faf9}.decision-main dl div{display:grid;grid-template-columns:64px minmax(0,1fr);gap:8px}.decision-main dt{color:#84918f;font-size:9px}.decision-main dd{margin:0;color:#536562;font-size:10px;line-height:1.55;overflow-wrap:anywhere}@media(max-width:700px){.decision-item{grid-template-columns:1fr}.decision-index{display:none}.decision-title{align-items:flex-start;flex-wrap:wrap}.decision-title b{margin-left:0}.decision-main dl{grid-template-columns:1fr}}
</style>
