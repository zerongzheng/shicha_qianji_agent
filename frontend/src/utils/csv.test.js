import { describe, expect, it } from "vitest";
import {
  detectDelimiter,
  formatFileSize,
  inspectCsvText,
  parseCsvLine,
} from "./csv";

describe("CSV 预检工具", () => {
  it("能识别常见分隔符并解析带引号的字段", () => {
    expect(detectDelimiter("time;pressure;note")).toBe(";");
    expect(parseCsvLine('2026-01-01,"12,5","阀门内压"')).toEqual([
      "2026-01-01",
      "12,5",
      "阀门内压",
    ]);
    expect(parseCsvLine('a,"含""引号""",c')).toEqual(["a", '含"引号"', "c"]);
  });

  it("能给出时间列、测点数量、缺失率和数据量提示", () => {
    const result = inspectCsvText(
      "timestamp,pressure,flow,label\n2026-01-01T00:00:00,1,,0\n2026-01-01T00:01:00,2,3,1",
      "sample.csv",
      2048,
    );
    expect(result.fileName).toBe("sample.csv");
    expect(result.sizeLabel).toBe("2.0 KB");
    expect(result.datetimeColumn).toBe("timestamp");
    expect(result.sensorCount).toBe(2);
    expect(result.rowCount).toBe(2);
    expect(result.missingRate).toBeCloseTo(0.25);
    expect(result.warnings).toContain("数据行数较少，趋势预测和工况分段结果可能不稳定。");
  });

  it("空文件不会抛出异常，并返回可展示的空预检结果", () => {
    const result = inspectCsvText("", "empty.csv", 0);
    expect(result.columns).toEqual([""]);
    expect(result.rowCount).toBe(0);
    expect(result.sensorCount).toBe(0);
    expect(formatFileSize(0)).toBe("0 B");
  });
});
