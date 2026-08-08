<script setup>
import { computed, ref } from "vue";

const props = defineProps({
  timestamps: { type: Array, default: () => [] },
  values: { type: Array, default: () => [] },
  lineColor: { type: String, default: "#c65d59" },
  bands: { type: Array, default: () => [] },
  markers: { type: Array, default: () => [] },
  unit: { type: String, default: "" },
  title: { type: String, default: "" },
  minLabel: { type: String, default: "低" },
  maxLabel: { type: String, default: "高" },
  threshold: { type: Number, default: null },
});

const width = 960;
const height = 300;
const padding = { top: 18, right: 22, bottom: 42, left: 58 };
const hoveredIndex = ref(null);

const cleanValues = computed(() => props.values.map((value) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}));

const usableValues = computed(() => cleanValues.value.filter((value) => value !== null));
const minimum = computed(() => usableValues.value.length ? Math.min(...usableValues.value) : 0);
const maximum = computed(() => usableValues.value.length ? Math.max(...usableValues.value) : 1);
const spread = computed(() => Math.max(maximum.value - minimum.value, Math.abs(maximum.value) * 0.001, 1e-6));
const plotWidth = width - padding.left - padding.right;
const plotHeight = height - padding.top - padding.bottom;

const points = computed(() => cleanValues.value.map((value, index) => {
  const fallback = index > 0 && cleanValues.value[index - 1] !== null
    ? cleanValues.value[index - 1]
    : (usableValues.value[0] ?? minimum.value);
  const normalized = ((value ?? fallback) - minimum.value) / spread.value;
  const x = cleanValues.value.length <= 1
    ? padding.left + plotWidth / 2
    : padding.left + (index / (cleanValues.value.length - 1)) * plotWidth;
  const y = padding.top + plotHeight - Math.max(0, Math.min(1, normalized)) * plotHeight;
  return { x, y, value: value ?? fallback, index };
}));

const linePoints = computed(() => points.value.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" "));
const yTicks = computed(() => Array.from({ length: 5 }, (_, index) => {
  const ratio = index / 4;
  return {
    y: padding.top + ratio * plotHeight,
    value: maximum.value - ratio * spread.value,
  };
}));
const xTicks = computed(() => {
  const count = props.timestamps.length;
  if (!count) return [];
  const indexes = [...new Set([0, Math.floor((count - 1) / 2), count - 1])];
  return indexes.map((index) => ({ index, x: points.value[index]?.x || padding.left }));
});
const hoveredPoint = computed(() => hoveredIndex.value === null ? null : points.value[hoveredIndex.value]);
const thresholdY = computed(() => {
  if (props.threshold === null || props.threshold === undefined || !usableValues.value.length) return null;
  const normalized = (Number(props.threshold) - minimum.value) / spread.value;
  return padding.top + plotHeight - Math.max(0, Math.min(1, normalized)) * plotHeight;
});
const tooltipStyle = computed(() => {
  if (!hoveredPoint.value) return {};
  const left = Math.min(Math.max(hoveredPoint.value.x + 14, 8), width - 180);
  const top = Math.max(8, hoveredPoint.value.y - 70);
  return { left: `${(left / width) * 100}%`, top: `${(top / height) * 100}%` };
});

function formatValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toFixed(Math.abs(number) >= 100 ? 1 : 3);
}

function formatTimestamp(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function indexFromPointer(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  const relative = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  return Math.round(relative * Math.max(0, points.value.length - 1));
}

function onPointerMove(event) {
  hoveredIndex.value = points.value.length ? indexFromPointer(event) : null;
}
</script>

<template>
  <div class="timeseries-chart" :aria-label="title || '工业时序图表'">
    <div class="chart-legend">
      <span class="legend-line" :style="{ background: lineColor }"></span>
      <span>{{ title || "时序曲线" }}</span>
      <span v-if="bands.length" class="legend-band"></span><span v-if="bands.length">异常区间</span>
    </div>
    <div class="chart-canvas-wrap">
      <svg class="timeseries-svg" :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="none" role="img">
        <g class="chart-grid">
          <line v-for="tick in yTicks" :key="tick.y" :x1="padding.left" :x2="width - padding.right" :y1="tick.y" :y2="tick.y" />
          <line :x1="padding.left" :x2="padding.left" :y1="padding.top" :y2="height - padding.bottom" />
          <line :x1="padding.left" :x2="width - padding.right" :y1="height - padding.bottom" :y2="height - padding.bottom" />
        </g>
        <g class="chart-bands">
          <rect v-for="band in bands" :key="`${band.start_ratio}-${band.end_ratio}`" :x="padding.left + band.start_ratio * plotWidth" :y="padding.top" :width="Math.max(4, (band.end_ratio - band.start_ratio) * plotWidth)" :height="plotHeight" />
          <text v-for="band in bands" :key="`label-${band.event_number}`" :x="padding.left + band.start_ratio * plotWidth + 5" :y="padding.top + 16" class="event-band-label">事件 {{ band.event_number }}</text>
        </g>
        <g v-if="thresholdY !== null" class="threshold-line">
          <line :x1="padding.left" :x2="width - padding.right" :y1="thresholdY" :y2="thresholdY" />
          <text :x="width - padding.right - 4" :y="thresholdY - 5" text-anchor="end">阈值 {{ formatValue(threshold) }}</text>
        </g>
        <polyline :points="linePoints" :style="{ stroke: lineColor }" class="timeseries-line" />
        <template v-for="marker in markers" :key="marker.index">
          <circle v-if="points[marker.index]" :cx="points[marker.index].x" :cy="points[marker.index].y" r="4" class="chart-marker" />
        </template>
        <line v-if="hoveredPoint" :x1="hoveredPoint.x" :x2="hoveredPoint.x" :y1="padding.top" :y2="height - padding.bottom" class="chart-crosshair" />
        <circle v-if="hoveredPoint" :cx="hoveredPoint.x" :cy="hoveredPoint.y" r="5" :style="{ stroke: lineColor }" class="chart-hover-point" />
        <rect class="chart-hit-area" :x="padding.left" :y="padding.top" :width="plotWidth" :height="plotHeight" @pointermove="onPointerMove" @pointerleave="hoveredIndex = null" />
        <g class="chart-labels">
          <text v-for="tick in yTicks" :key="`y-${tick.y}`" :x="padding.left - 10" :y="tick.y + 4" text-anchor="end">{{ formatValue(tick.value) }}</text>
          <text v-for="tick in xTicks" :key="`x-${tick.index}`" :x="tick.x" :y="height - 13" text-anchor="middle">{{ formatTimestamp(timestamps[tick.index]) }}</text>
        </g>
      </svg>
      <div v-if="hoveredPoint" class="chart-tooltip" :style="tooltipStyle">
        <b>{{ formatTimestamp(timestamps[hoveredPoint.index]) }}</b>
        <span>{{ formatValue(hoveredPoint.value) }} {{ unit }}</span>
      </div>
    </div>
    <div class="chart-summary"><span>最低 {{ formatValue(minimum) }}</span><span>最高 {{ formatValue(maximum) }}</span><span>采样点 {{ values.length }}</span></div>
  </div>
</template>
