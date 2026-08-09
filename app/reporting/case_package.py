"""生成校赛典型案例材料包。

案例材料包复用统一分析流水线的 ``AnalysisResult``，不会重新计算一套指标。它把评委最
容易理解的四类证据单独导出：数据概况、异常事件、主导传感器和设备风险曲线。输出可以
直接用于答辩截图，也可以作为后续企业案例替换时的固定模板。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import plotly.graph_objects as go

from app.analysis.pipeline import analyze_file
from app.config import get_settings
from app.models import AnalysisConfig, AnalysisResult


@dataclass(frozen=True)
class CasePackage:
    """一次典型案例导出的文件集合。"""

    case_dir: Path
    markdown_path: Path
    events_csv_path: Path
    chart_html_path: Path
    summary_json_path: Path
    result: AnalysisResult


def build_case_package(
    file_path: str | Path,
    config: AnalysisConfig | None = None,
    output_dir: str | Path | None = None,
) -> CasePackage:
    """分析一份 CSV 并导出校赛阶段可复用的典型案例材料包。"""

    source_path = Path(file_path).expanduser().resolve()
    result = analyze_file(source_path, config=config, write_report=False)
    return build_case_package_from_result(result, output_dir=output_dir)


def build_case_package_from_result(
    result: AnalysisResult,
    output_dir: str | Path | None = None,
) -> CasePackage:
    """直接从已经完成的分析结果导出材料，避免页面重复计算。"""

    source_path = result.source_path
    root = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else get_settings().output_dir / "cases"
    )
    case_dir = root / source_path.parent.name / source_path.stem
    case_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = case_dir / "case_summary.md"
    events_csv_path = case_dir / "anomaly_events.csv"
    chart_html_path = case_dir / "risk_evidence.html"
    summary_json_path = case_dir / "case_summary.json"

    markdown_path.write_text(_build_case_markdown(result), encoding="utf-8")
    _write_events_csv(result, events_csv_path)
    _write_risk_chart(result, chart_html_path)
    summary_json_path.write_text(
        json.dumps(result.to_summary(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return CasePackage(
        case_dir=case_dir,
        markdown_path=markdown_path,
        events_csv_path=events_csv_path,
        chart_html_path=chart_html_path,
        summary_json_path=summary_json_path,
        result=result,
    )


def _build_case_markdown(result: AnalysisResult) -> str:
    """生成面向评委和答辩讲解的案例摘要。"""

    profile = result.profile
    metrics = result.metrics
    lines = [
        "# 时察千机典型案例分析",
        "",
        f"> 数据文件：`{profile.source_name}`",
        "> 数据来源：SKAB 公开工业时序数据集（校赛阶段验证数据）",
        "",
        "## 1. 数据进入系统后形成的判断",
        "",
        f"- 数据规模：{profile.row_count} 个采样点，{len(profile.sensor_columns)} 个传感器。",
        f"- 时间范围：{profile.start_time} 至 {profile.end_time}。",
        f"- 当前检测器：{result.detector_name}。",
        f"- 识别到异常事件：{len(result.events)} 个。",
        f"- 最高风险等级：{result.events[0].severity if result.events else '正常'}。",
        "",
        "## 2. 异常事件证据",
        "",
        "| 事件 | 时间区间 | 持续点数 | 峰值风险 | 主导传感器 | 候选根因 |",
        "| ---: | --- | ---: | ---: | --- | --- |",
    ]
    diagnoses = {item.event_number: item for item in result.event_diagnoses}
    for index, event in enumerate(result.events[:10], start=1):
        diagnosis = diagnoses.get(index)
        cause = (
            diagnosis.primary_candidate.name
            if diagnosis and diagnosis.primary_candidate
            else "待现场确认"
        )
        lines.append(
            f"| {index} | {event.start_time} 至 {event.end_time} | {event.duration_points} | "
            f"{event.peak_score:.2f} | {', '.join(event.dominant_sensors)} | {cause} |"
        )
    if not result.events:
        lines.append("| - | 当前未形成持续异常事件 | - | - | - | - |")

    lines.extend(["", "## 3. 结果如何支持处置", ""])
    for index, recommendation in enumerate(result.recommendations[:5], start=1):
        lines.append(f"{index}. {recommendation}")
    if not result.recommendations:
        lines.append("1. 当前没有生成额外处置建议，建议保持监测并核对设备边界。")

    lines.extend(["", "## 4. 可量化结果", ""])
    if metrics is None:
        lines.append("当前数据没有 anomaly 标签，无法计算监督指标；系统仍可完成无监督分析。")
    else:
        lines.extend(
            [
                f"- 点级 F1：{metrics.f1_score:.4f}；PR-AUC：{metrics.pr_auc:.4f}。",
                f"- 事件级 F1：{metrics.event_f1_score:.4f}；事件召回：{metrics.event_recall:.4f}。",
                f"- 平均检测延迟：{_format_optional(metrics.mean_detection_delay)} 个采样点。",
                f"- 误报事件：{metrics.false_positive_event_count} 个。",
            ]
        )

    lines.extend(
        [
            "",
            "## 5. 使用边界",
            "",
            "本案例展示的是公开数据上的算法验证。候选根因用于安排现场排查顺序，不等于设备故障确诊；",
            "企业数据接入后仍需重新建立健康基线、校准阈值，并结合设备资料和维修记录验证。",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_events_csv(result: AnalysisResult, path: Path) -> None:
    """导出事件明细，便于 Excel 制表和 PPT 统计。"""

    fields = [
        "event_number",
        "start_time",
        "end_time",
        "duration_points",
        "peak_score",
        "severity",
        "dominant_sensors",
        "primary_candidate",
    ]
    diagnoses = {item.event_number: item for item in result.event_diagnoses}
    rows: list[dict[str, Any]] = []
    for index, event in enumerate(result.events, start=1):
        diagnosis = diagnoses.get(index)
        rows.append(
            {
                "event_number": index,
                "start_time": event.start_time,
                "end_time": event.end_time,
                "duration_points": event.duration_points,
                "peak_score": round(event.peak_score, 6),
                "severity": event.severity,
                "dominant_sensors": ", ".join(event.dominant_sensors),
                "primary_candidate": (
                    diagnosis.primary_candidate.name
                    if diagnosis and diagnosis.primary_candidate
                    else "待现场确认"
                ),
            }
        )
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_risk_chart(result: AnalysisResult, path: Path) -> None:
    """导出独立 HTML 风险图，打开浏览器即可查看和缩放。"""

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=result.dataframe["datetime"],
            y=result.combined_score,
            name="设备风险分数",
            line={"color": "#D14D3F", "width": 2},
        )
    )
    predicted = result.predicted_labels.astype(bool)
    figure.add_trace(
        go.Scatter(
            x=result.dataframe.loc[predicted, "datetime"],
            y=result.combined_score.loc[predicted],
            name="检测异常",
            mode="markers",
            marker={"color": "#161B22", "size": 6},
        )
    )
    if "anomaly" in result.dataframe:
        actual = result.dataframe["anomaly"].fillna(0).astype(bool)
        figure.add_trace(
            go.Scatter(
                x=result.dataframe.loc[actual, "datetime"],
                y=result.combined_score.loc[actual],
                name="真实标签",
                mode="markers",
                marker={"color": "#197278", "size": 5, "symbol": "x"},
            )
        )
    figure.update_layout(
        title="异常事件与风险分数",
        height=560,
        template="plotly_white",
        hovermode="x unified",
        xaxis_title="时间",
        yaxis_title="稳健异常分数",
        legend={"orientation": "h", "y": 1.08},
        margin={"l": 50, "r": 30, "t": 70, "b": 50},
    )
    path.write_text(
        figure.to_html(full_html=True, include_plotlyjs=True, config={"displaylogo": False}),
        encoding="utf-8",
    )


def _format_optional(value: float | None) -> str:
    """格式化可能为空的延迟指标。"""

    return f"{value:.2f}" if value is not None else "未知"
