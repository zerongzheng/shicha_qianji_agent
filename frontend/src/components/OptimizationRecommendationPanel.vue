<script setup>
/**
 * 运行优化建议证据面板。
 *
 * 建议只作为受约束的人工确认草案展示，证据和回退条件默认收起，避免把风险总览
 * 变成一面文字墙，也避免页面暗示系统已经直接控制设备。
 */
defineProps({
  recommendations: { type: Array, default: () => [] },
});
</script>

<template>
  <section class="panel optimization-panel">
    <div class="panel-header optimization-header">
      <div>
        <h2>运行优化建议</h2>
        <span>预测、根因与历史证据形成的待确认草案</span>
      </div>
      <strong>{{ recommendations.length }} 条建议</strong>
    </div>

    <div v-if="recommendations.length" class="optimization-list">
      <article
        v-for="(item, index) in recommendations"
        :key="item.recommendation_id || index"
        class="optimization-item"
      >
        <div class="optimization-index">{{ String(index + 1).padStart(2, "0") }}</div>
        <div class="optimization-main">
          <div class="optimization-title">
            <span>{{ item.category }}</span>
            <h3>{{ item.target }}</h3>
            <b>{{ item.status || "待人工确认" }}</b>
          </div>
          <p class="optimization-action">{{ item.action }}</p>
          <div class="optimization-meta">
            <span>调整方向：<b>{{ item.adjustment_direction }}</b></span>
            <span>建议范围：<b>{{ item.suggested_range }}</b></span>
            <span>置信度：<b>{{ item.confidence }}</b></span>
          </div>
          <details>
            <summary>查看证据与验证条件</summary>
            <dl>
              <div><dt>证据</dt><dd><ul><li v-for="evidence in item.evidence || []" :key="evidence">{{ evidence }}</li></ul></dd></div>
              <div><dt>验证指标</dt><dd>{{ (item.validation_metrics || []).join("、") || "待定义" }}</dd></div>
              <div><dt>观察窗口</dt><dd>{{ item.observation_window || "待工艺人员确认" }}</dd></div>
              <div><dt>约束</dt><dd><ul><li v-for="constraint in item.constraints || []" :key="constraint">{{ constraint }}</li></ul></dd></div>
              <div><dt>回退条件</dt><dd>{{ item.rollback_condition || "出现风险恶化时停止调整并恢复原策略" }}</dd></div>
              <div><dt>人工闸门</dt><dd>仅提供建议，不自动下发控制指令；由工艺负责人确认后执行。</dd></div>
            </dl>
          </details>
        </div>
      </article>
    </div>
    <div v-else class="panel-empty">暂无优化建议，系统不会在缺少证据时生成数值调参。</div>
  </section>
</template>

<style scoped>
.optimization-panel{border-top:3px solid #b57b35;overflow:hidden}.optimization-header strong{padding:6px 9px;background:#fff4df;color:#8a601f;font-size:11px;white-space:nowrap}.optimization-list{display:grid}.optimization-item{display:grid;grid-template-columns:42px minmax(0,1fr);gap:14px;padding:17px 20px;border-top:1px solid #e8eeee}.optimization-index{display:grid;width:34px;height:34px;place-items:center;background:#39474a;color:#fff;font-size:10px;font-weight:800}.optimization-main{min-width:0}.optimization-title{display:flex;align-items:center;gap:10px}.optimization-title span{color:#9a6a27;font-size:9px;font-weight:800}.optimization-title h3{min-width:0;margin:0;color:#294346;font-size:13px}.optimization-title b{margin-left:auto;padding:3px 6px;background:#fff1d8;color:#8a621d;font-size:9px;white-space:nowrap}.optimization-action{margin:8px 0;color:#354d50;font-size:12px;font-weight:700;line-height:1.55}.optimization-meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;color:#7b8a89;font-size:10px;line-height:1.5}.optimization-meta b{color:#456363;font-weight:700}.optimization-main details{margin-top:10px}.optimization-main summary{width:max-content;color:#8b6527;font-size:10px;font-weight:700;cursor:pointer}.optimization-main dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 18px;margin:10px 0 0;padding:12px;background:#fbfaf6}.optimization-main dl div{display:grid;grid-template-columns:64px minmax(0,1fr);gap:8px}.optimization-main dt{color:#958d7d;font-size:9px}.optimization-main dd{margin:0;color:#5d655e;font-size:10px;line-height:1.55;overflow-wrap:anywhere}.optimization-main ul{margin:0;padding-left:15px}.optimization-main li+li{margin-top:4px}@media(max-width:700px){.optimization-item{grid-template-columns:1fr}.optimization-index{display:none}.optimization-title{align-items:flex-start;flex-wrap:wrap}.optimization-title b{margin-left:0}.optimization-meta{grid-template-columns:1fr}.optimization-main dl{grid-template-columns:1fr}}
</style>
