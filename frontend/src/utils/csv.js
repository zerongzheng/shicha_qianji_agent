/**
 * 浏览器端 CSV 预检工具。
 *
 * 这些函数只负责识别分隔符、解析带引号的 CSV 行和估计数据质量，
 * 不会把文件内容发送给大模型，也不会替代后端的最终数据校验。
 * 将它们独立出来后，既方便 App.vue 使用，也方便用单元测试验证边界情况。
 */

export function countDelimiter(line, delimiter) {
  return String(line || "").split(delimiter).length - 1;
}

export function detectDelimiter(line) {
  const candidates = [",", ";", "\t"];
  return candidates.sort((left, right) => (
    countDelimiter(line, right) - countDelimiter(line, left)
  ))[0];
}

/** 解析一行 CSV，支持双引号包裹字段和用两个双引号表示字段内引号。 */
export function parseCsvLine(line, delimiter = detectDelimiter(line)) {
  const values = [];
  let current = "";
  let quoted = false;

  for (let index = 0; index < String(line || "").length; index += 1) {
    const char = line[index];
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === delimiter && !quoted) {
      values.push(current);
      current = "";
    } else {
      current += char;
    }
  }

  values.push(current);
  return values;
}

export function formatFileSize(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}

/**
 * 对 CSV 前若干行做轻量预检，供上传前提示使用。
 * 只读取文本样本，因此不会因为大文件预检而阻塞页面太久。
 */
export function inspectCsvText(text, fileName = "data.csv", fileSize = 0, maxRows = 2000) {
  const lines = String(text || "").split(/\r?\n/).filter((line) => line.trim());
  const sampleLines = lines.slice(0, maxRows + 1);
  const header = parseCsvLine(sampleLines[0] || "");
  const delimiter = detectDelimiter(sampleLines[0] || "");
  const columns = header.map((item) => item.trim().replace(/^"|"$/g, ""));
  const datetimeColumn = columns.find((column) => (
    /time|date|datetime|timestamp|时间|日期/i.test(column)
  )) || "";
  const sensorColumns = columns.filter((column) => (
    column && column !== datetimeColumn && !/label|target|class|状态|标签/i.test(column)
  ));
  const numericRows = sampleLines.slice(1).map((line) => parseCsvLine(line, delimiter));
  let missingCells = 0;
  let observedCells = 0;

  numericRows.forEach((row) => sensorColumns.forEach((column) => {
    const index = columns.indexOf(column);
    const value = row[index];
    observedCells += 1;
    if (value === undefined || value.trim() === "" || ["nan", "null", "none"].includes(value.trim().toLowerCase())) {
      missingCells += 1;
    }
  }));

  const rowCount = Math.max(lines.length - 1, 0);
  const missingRate = observedCells ? missingCells / observedCells : 0;
  return {
    fileName,
    sizeLabel: formatFileSize(fileSize),
    rowCount,
    sampleCount: numericRows.length,
    delimiter: delimiter === "\t" ? "制表符" : delimiter,
    columns,
    datetimeColumn,
    sensorCount: sensorColumns.length,
    missingRate,
    warnings: [
      !datetimeColumn ? "未识别到时间列，后端将按数据行顺序处理。" : "",
      sensorColumns.length < 2 ? "可识别的传感器列少于 2 列，多变量关系诊断能力会受限。" : "",
      missingRate > 0.1 ? `抽样缺失率约 ${(missingRate * 100).toFixed(1)}%，建议先检查数据完整性。` : "",
      rowCount < 100 ? "数据行数较少，趋势预测和工况分段结果可能不稳定。" : "",
    ].filter(Boolean),
  };
}
