<script setup>
import { computed, ref } from "vue";

const props = defineProps({
  historyTimestamps: { type: Array, default: () => [] },
  historyValues: { type: Array, default: () => [] },
  futureTimestamps: { type: Array, default: () => [] },
  predictions: { type: Array, default: () => [] },
  lower: { type: Array, default: () => [] },
  upper: { type: Array, default: () => [] },
  title: { type: String, default: "预测趋势" },
});

const width = 960;
const height = 300;
const padding = { top: 18, right: 22, bottom: 42, left: 58 };
const hoveredIndex = ref(null);
const allValues = computed(() => [...props.historyValues, ...props.predictions, ...props.lower, ...props.upper].map(Number).filter(Number.isFinite));
const minimum = computed(() => allValues.value.length ? Math.min(...allValues.value) : 0);
const maximum = computed(() => allValues.value.length ? Math.max(...allValues.value) : 1);
const spread = computed(() => Math.max(maximum.value - minimum.value, Math.abs(maximum.value) * 0.001, 1e-6));
const count = computed(() => Math.max(1, props.historyValues.length + props.predictions.length));
const plotWidth = width - padding.left - padding.right;
const plotHeight = height - padding.top - padding.bottom;

function point(value, index) {
  const number = Number(value);
  const safeValue = Number.isFinite(number) ? number : minimum.value;
  return {
    x: padding.left + (index / Math.max(1, count.value - 1)) * plotWidth,
    y: padding.top + plotHeight - ((safeValue - minimum.value) / spread.value) * plotHeight,
    value: safeValue,
    index,
  };
}

const historyPoints = computed(() => props.historyValues.map((value, index) => point(value, index)));
const forecastPoints = computed(() => props.predictions.map((value, index) => point(value, props.historyValues.length + index)));
const lowerPoints = computed(() => props.lower.map((value, index) => point(value, props.historyValues.length + index)));
const upperPoints = computed(() => props.upper.map((value, index) => point(value, props.historyValues.length + index)));
const labels = computed(() => {
  const timestamps = [...props.historyTimestamps, ...props.futureTimestamps];
  return [0, Math.floor((timestamps.length - 1) / 2), timestamps.length - 1].filter((value, index, array) => value >= 0 && array.indexOf(value) === index).map((index) => ({ index, x: point(0, index).x, label: formatTimestamp(timestamps[index]) }));
});

function linePoints(points) { return points.map((item) => `${item.x.toFixed(2)},${item.y.toFixed(2)}`).join(" "); }
function formatValue(value) { const number = Number(value); return Number.isFinite(number) ? number.toFixed(Math.abs(number) >= 100 ? 1 : 3) : "-"; }
function formatTimestamp(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value || "-") : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
function onPointerMove(event) { const rect = event.currentTarget.getBoundingClientRect(); hoveredIndex.value = Math.round(Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * (count.value - 1)); }
const hovered = computed(() => hoveredIndex.value === null ? null : [...historyPoints.value, ...forecastPoints.value][hoveredIndex.value]);
</script>

<template>
  <div class="timeseries-chart forecast-chart">
    <div class="chart-legend"><span class="legend-line history"></span><span>历史值</span><span class="legend-line prediction"></span><span>预测值</span><span class="legend-band"></span><span>预测区间</span></div>
    <div class="chart-canvas-wrap">
      <svg class="timeseries-svg" :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="none" role="img" :aria-label="title">
        <g class="chart-grid"><line v-for="index in 5" :key="index" :x1="padding.left" :x2="width - padding.right" :y1="padding.top + ((index - 1) / 4) * plotHeight" :y2="padding.top + ((index - 1) / 4) * plotHeight" /><line :x1="padding.left" :x2="padding.left" :y1="padding.top" :y2="height - padding.bottom" /><line :x1="padding.left" :x2="width - padding.right" :y1="height - padding.bottom" :y2="height - padding.bottom" /></g>
        <polygon v-if="upperPoints.length && lowerPoints.length" :points="`${linePoints(upperPoints)} ${linePoints([...lowerPoints].reverse())}`" class="forecast-area" />
        <polyline :points="linePoints(historyPoints)" class="timeseries-line history-line" />
        <polyline :points="linePoints(forecastPoints)" class="timeseries-line prediction-line" />
        <line v-if="forecastPoints.length" :x1="forecastPoints[0].x" :x2="forecastPoints[0].x" :y1="padding.top" :y2="height - padding.bottom" class="forecast-start" />
        <line v-if="hovered" :x1="hovered.x" :x2="hovered.x" :y1="padding.top" :y2="height - padding.bottom" class="chart-crosshair" />
        <circle v-if="hovered" :cx="hovered.x" :cy="hovered.y" r="5" class="chart-hover-point" />
        <rect class="chart-hit-area" :x="padding.left" :y="padding.top" :width="plotWidth" :height="plotHeight" @pointermove="onPointerMove" @pointerleave="hoveredIndex = null" />
        <g class="chart-labels"><text v-for="label in labels" :key="label.index" :x="label.x" :y="height - 13" text-anchor="middle">{{ label.label }}</text><text v-for="index in 5" :key="`y-${index}`" :x="padding.left - 10" :y="padding.top + ((index - 1) / 4) * plotHeight + 4" text-anchor="end">{{ formatValue(maximum - ((index - 1) / 4) * spread) }}</text></g>
      </svg>
      <div v-if="hovered" class="chart-tooltip" :style="{ left: `${Math.min(86, Math.max(5, (hovered.x / width) * 100 + 2))}%`, top: `${Math.max(8, (hovered.y / height) * 100 - 16)}%` }"><b>{{ formatTimestamp([...props.historyTimestamps, ...props.futureTimestamps][hovered.index]) }}</b><span>{{ formatValue(hovered.value) }}</span></div>
    </div>
    <div class="chart-summary"><span>历史 {{ historyValues.length }} 点</span><span>预测 {{ predictions.length }} 点</span><span>预测末值 {{ formatValue(predictions[predictions.length - 1]) }}</span></div>
  </div>
</template>
