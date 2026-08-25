<script setup>
/** 趋势研判面板：展示预测区间、回测指标、工况分段和多传感器关联诊断。 */
import ForecastChart from "./ForecastChart.vue";

defineProps({
  analysis: { type: Object, required: true },
  forecastSensors: { type: Array, default: () => [] },
  selectedForecastSensor: { type: String, default: "" },
  selectedForecast: { type: Object, default: null },
  visualization: { type: Object, default: null },
  forecastEntries: { type: Array, default: () => [] },
  regimes: { type: Object, default: null },
  relationships: { type: Array, default: () => [] },
  formatDate: { type: Function, required: true },
  formatNumber: { type: Function, required: true },
});

const emit = defineEmits(["select-sensor"]);
</script>

<template>
  <section class="content-stack">
    <div class="panel">
      <div class="panel-header"><h2>传感器未来趋势</h2><span>选择测点查看历史与预测区间</span></div>
      <div v-if="forecastSensors.length" class="sensor-toolbar forecast-sensor-toolbar"><button v-for="sensor in forecastSensors" :key="sensor" class="sensor-chip" :class="{ active: selectedForecastSensor === sensor }" @click="emit('select-sensor', sensor)">{{ sensor }}</button></div>
      <ForecastChart v-if="selectedForecast && visualization?.series?.[selectedForecastSensor]" :history-timestamps="visualization.timestamps" :history-values="visualization.series[selectedForecastSensor]" :future-timestamps="selectedForecast['预测时间'] || []" :predictions="selectedForecast['预测值'] || []" :lower="selectedForecast['下界'] || []" :upper="selectedForecast['上界'] || []" :title="`${selectedForecastSensor} 预测趋势`" />
      <div v-if="forecastEntries.length" class="forecast-detail-list">
        <div v-for="item in forecastEntries" :key="item[0]" class="forecast-detail">
          <div class="forecast-detail-header"><div><b>{{ item[0] }}</b><small>{{ item[1].模型名称 }} · {{ item[1].选择依据 }}</small></div><span :class="`forecast-risk-${item[1].风险}`">{{ item[1].风险 }}</span></div>
          <div class="forecast-stats"><span>当前值 <b>{{ item[1].当前值 }}</b></span><span>预测末值 <b>{{ item[1].预测末值 }}</b></span><span>方向 <b>{{ item[1].方向 }}</b></span><span>末值偏移 <b>{{ item[1].预测末值偏移标准差 }}σ</b></span><span>回测 RMSE <b>{{ item[1].回测?.RMSE ?? '-' }}</b></span></div>
          <div class="forecast-bar"><span :style="{ width: `${Math.min(100, Math.max(4, Math.abs(Number(item[1].预测末值偏移标准差 || 0)) * 10))}%` }"></span></div>
        </div>
      </div>
      <div v-else class="panel-empty">数据长度不足，暂未生成预测</div>
    </div>
    <div class="two-column">
      <div class="panel"><div class="panel-header"><h2>工况分段</h2><span>识别结果</span></div><div v-if="regimes?.segments?.length" class="regime-list detailed"><div v-for="segment in regimes.segments" :key="`${segment['开始时间']}-${segment['结束时间']}`" class="regime-row"><b>工况 {{ segment["工况编号"] }}</b><span>{{ formatDate(segment["开始时间"]) }} - {{ formatDate(segment["结束时间"]) }}</span><small>{{ segment["持续点数"] }} 点</small></div></div><div v-else class="panel-empty">暂无工况分段</div></div>
      <div class="panel"><div class="panel-header"><h2>关联诊断</h2><span>{{ relationships.length }} 个事件</span></div><div v-if="relationships.length" class="relationship-list"><div v-for="item in relationships" :key="item['事件编号']" class="relationship-row"><b>事件 {{ item["事件编号"] }}</b><span>{{ item["关系结论"] }}</span></div></div><div v-else class="panel-empty">暂无足够的多传感器关系证据</div></div>
    </div>
  </section>
</template>
