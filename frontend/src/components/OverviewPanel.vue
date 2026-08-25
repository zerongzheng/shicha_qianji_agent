<script setup>
/**
 * 风险总览面板。
 *
 * 这里只负责把一次分析结果组织成评委和运维人员能快速浏览的界面。
 * 文件上传、任务轮询和工单请求仍由 App.vue 负责，面板通过事件调用父组件动作。
 */
import TimeSeriesChart from "./TimeSeriesChart.vue";
import ExecutionTracePanel from "./ExecutionTracePanel.vue";
import AgentDecisionPanel from "./AgentDecisionPanel.vue";
import OptimizationRecommendationPanel from "./OptimizationRecommendationPanel.vue";

defineProps({
  analysis: { type: Object, required: true },
  events: { type: Array, default: () => [] },
  visualization: { type: Object, default: null },
  chartSensors: { type: Array, default: () => [] },
  selectedSensor: { type: String, default: "" },
  selectedSensorValues: { type: Array, default: () => [] },
  highestRisk: { type: String, default: "正常" },
  riskAlertCount: { type: Number, default: 0 },
  analysisScope: { type: Object, default: () => ({ label: "当前数据", detail: "" }) },
  highestRiskEvent: { type: Object, default: null },
  overviewDiagnosis: { type: Object, default: null },
  overviewWorkOrder: { type: Object, default: null },
  overviewAction: { type: String, default: "" },
  dataQuality: { type: Object, default: () => ({}) },
  closedLoop: { type: Object, default: () => ({}) },
  regimes: { type: Object, default: null },
  forecastEntries: { type: Array, default: () => [] },
  formatDate: { type: Function, required: true },
  formatNumber: { type: Function, required: true },
  contributionWidth: { type: Function, required: true },
});

const emit = defineEmits(["open-evidence", "select-sensor"]);
</script>

<template>
  <section class="content-stack">
    <div class="metric-grid">
      <div class="metric-card accent"><span>当前风险</span><strong>{{ highestRisk }}</strong></div>
      <div class="metric-card"><span>异常事件</span><strong>{{ events.length }}</strong></div>
      <div class="metric-card"><span>传感器数量</span><strong>{{ analysis.data_profile?.sensor_columns?.length || 0 }}</strong></div>
      <div class="metric-card"><span>数据点数</span><strong>{{ analysis.data_profile?.row_count || 0 }}</strong></div>
      <div class="metric-card"><span>风险告警</span><strong>{{ riskAlertCount }}</strong></div>
    </div>

    <div class="process-line">
      <span>数据接入</span><b>→</b><span>异常发现</span><b>→</b><span>证据解释</span><b>→</b><span>工单闭环</span>
    </div>
    <div class="analysis-scope-bar">
      <div><span class="section-kicker">当前数据范围</span><strong>{{ analysisScope.label }}</strong></div>
      <p>{{ analysisScope.detail }}</p>
    </div>

    <ExecutionTracePanel :steps="analysis.execution_trace || []" />
    <AgentDecisionPanel :decisions="analysis.agent_decisions || []" />
    <OptimizationRecommendationPanel
      :recommendations="analysis.optimization_recommendations || []"
    />

    <div v-if="visualization" class="panel chart-panel">
      <div class="panel-header"><h2>设备风险曲线</h2><span>悬停查看采样点 · 红色区间为异常事件</span></div>
      <TimeSeriesChart
        :timestamps="visualization.timestamps"
        :values="visualization.risk_scores"
        :bands="visualization.event_ranges"
        :markers="visualization.risk_scores.map((value, index) => ({ index, value })).filter((item) => visualization.anomaly_labels[item.index])"
        line-color="#c65d59"
        title="设备风险分数"
        unit="风险分"
        :threshold="visualization.threshold"
      />
    </div>

    <div class="decision-summary">
      <div class="decision-summary-header">
        <div><span class="section-kicker">重点事件</span><h2>从异常发现到处置动作</h2></div>
        <button v-if="highestRiskEvent" class="text-link-button" @click="emit('open-evidence', highestRiskEvent.index)">查看完整证据</button>
      </div>
      <div v-if="highestRiskEvent" class="decision-summary-grid">
        <div><span>观测到什么</span><strong>{{ highestRiskEvent.event.severity }} · {{ highestRiskEvent.event.dominant_sensors?.join("、") || "多测点" }}</strong><p>{{ formatDate(highestRiskEvent.event.start_time) }} - {{ formatDate(highestRiskEvent.event.end_time) }}</p></div>
        <div><span>候选原因</span><strong>{{ overviewDiagnosis?.primary_candidate?.name || "待现场确认" }}</strong><p>{{ overviewDiagnosis?.primary_candidate?.confidence || "当前仅作为排查方向" }}</p></div>
        <div><span>建议动作</span><strong>{{ overviewAction }}</strong><p>{{ overviewWorkOrder ? `工单 ${overviewWorkOrder.source_work_order_id}` : "分析完成后可生成运维工单" }}</p></div>
      </div>
      <div v-else class="decision-summary-empty">当前没有可供处置的持续异常事件。</div>
    </div>

    <div class="panel data-quality-panel">
      <div class="panel-header"><h2>数据质量与分析范围</h2><span>分析前置检查结果</span></div>
      <div class="quality-grid">
        <div class="quality-item"><span>时间范围</span><b>{{ formatDate(analysis.data_profile?.start_time) }} - {{ formatDate(analysis.data_profile?.end_time) }}</b></div>
        <div class="quality-item"><span>采样周期</span><b>{{ dataQuality.sampling_seconds ? `${formatNumber(dataQuality.sampling_seconds, 2)} 秒` : "未估计" }}</b></div>
        <div class="quality-item"><span>缺失数据</span><b :class="{ 'quality-warn': dataQuality.missing_total > 0 }">{{ dataQuality.missing_total || 0 }} 个 · {{ formatNumber(Number(dataQuality.missing_rate || 0) * 100, 2) }}%</b></div>
        <div class="quality-item"><span>标签状态</span><b>{{ dataQuality.label_columns?.length ? `含 ${dataQuality.label_columns.join("、")} 标签` : "无监督标签" }}</b></div>
      </div>
    </div>

    <div class="panel closure-panel">
      <div class="panel-header"><h2>工业分析闭环</h2><span>本次任务产出</span></div>
      <div class="closure-flow">
        <div class="closure-step"><strong>{{ closedLoop.dataPoints }}</strong><span>数据点</span><small>数据接入</small></div><b>→</b>
        <div class="closure-step"><strong>{{ closedLoop.events }}</strong><span>异常事件</span><small>异常发现</small></div><b>→</b>
        <div class="closure-step"><strong>{{ closedLoop.diagnoses }}</strong><span>诊断结果</span><small>证据解释</small></div><b>→</b>
        <div class="closure-step"><strong>{{ closedLoop.workOrders }}</strong><span>候选工单</span><small>处置生成</small></div><b>→</b>
        <div class="closure-step"><strong>{{ closedLoop.confirmed }}</strong><span>已确认案例</span><small>反馈沉淀</small></div>
      </div>
    </div>

    <div class="two-column">
      <div class="panel">
        <div class="panel-header"><h2>异常事件时间线</h2><span>{{ analysis.detector }}</span></div>
        <div v-if="events.length" class="event-list">
          <button v-for="(event, index) in events" :key="index" class="event-row" @click="emit('open-evidence', index)">
            <span class="event-number">{{ String(index + 1).padStart(2, '0') }}</span>
            <span class="event-body"><b>{{ event.severity }} · {{ event.duration_points }} 个采样点</b><small>{{ formatDate(event.start_time) }} - {{ formatDate(event.end_time) }}</small></span>
            <span class="event-score">{{ formatNumber(event.peak_score) }}</span>
          </button>
        </div>
        <div v-else class="panel-empty">当前未形成持续异常事件</div>
      </div>
      <div class="panel evidence-panel">
        <div class="panel-header"><h2>运维建议</h2><span>结构化输出</span></div>
        <ol v-if="analysis.recommendations?.length">
          <li v-for="recommendation in analysis.recommendations.slice(0, 5)" :key="recommendation">{{ recommendation }}</li>
        </ol>
        <div v-else class="panel-empty">暂无额外处置建议</div>
      </div>
    </div>

    <div class="two-column">
      <div class="panel">
        <div class="panel-header"><h2>工况上下文</h2><span>{{ regimes?.state_count || 1 }} 个状态</span></div>
        <div v-if="regimes?.segments?.length" class="regime-list">
          <div v-for="segment in regimes.segments.slice(0, 6)" :key="`${segment['开始时间']}-${segment['结束时间']}`" class="regime-row">
            <b>{{ segment["工况编号"] ? `工况 ${segment["工况编号"]}` : "工况" }}</b>
            <span>{{ formatDate(segment["开始时间"]) }} - {{ formatDate(segment["结束时间"]) }}</span>
            <small>{{ segment["持续点数"] }} 点 · 过渡占比 {{ formatNumber(Number(segment["过渡点占比"] || 0) * 100, 1) }}%</small>
          </div>
        </div>
        <div v-else class="panel-empty">暂无稳定工况分段</div>
      </div>
      <div class="panel">
        <div class="panel-header"><h2>预测风险摘要</h2><span>{{ forecastEntries.length }} 个测点</span></div>
        <div v-if="forecastEntries.length" class="forecast-list">
          <div v-for="item in forecastEntries.slice(0, 6)" :key="item[0]" class="forecast-row">
            <b>{{ item[0] }}</b><span>{{ item[1].方向 || "维持" }}</span><strong>{{ item[1].风险 || "待评估" }}</strong>
          </div>
        </div>
        <div v-else class="panel-empty">暂无可用预测结果</div>
      </div>
    </div>

    <div v-if="visualization && chartSensors.length" class="two-column">
      <div class="panel chart-panel">
        <div class="panel-header"><h2>重点传感器趋势</h2><span>{{ selectedSensor }}</span></div>
        <div class="sensor-toolbar"><button v-for="sensor in chartSensors" :key="sensor" class="sensor-chip" :class="{ active: selectedSensor === sensor }" @click="emit('select-sensor', sensor)">{{ sensor }}</button></div>
        <TimeSeriesChart
          :timestamps="visualization.timestamps"
          :values="selectedSensorValues"
          :bands="visualization.event_ranges"
          :markers="visualization.risk_scores.map((value, index) => ({ index })).filter((item) => visualization.anomaly_labels[item.index])"
          line-color="#1d8583"
          :title="`${selectedSensor} 时序曲线`"
          :unit="selectedSensor"
        />
      </div>
      <div class="panel">
        <div class="panel-header"><h2>传感器贡献排序</h2><span>累计异常贡献</span></div>
        <div v-if="visualization.sensor_contributions?.length" class="contribution-list">
          <div v-for="item in visualization.sensor_contributions" :key="item.sensor" class="contribution-row">
            <div class="contribution-name"><b>{{ item.sensor }}</b><span>{{ formatNumber(item.score) }}</span></div>
            <div class="contribution-track"><span :style="{ width: contributionWidth(item) }"></span></div>
          </div>
        </div>
        <div v-else class="panel-empty">暂无传感器贡献数据</div>
      </div>
    </div>
  </section>
</template>
