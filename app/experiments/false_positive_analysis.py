"""SKAB 误报事件审计与 `other` 场景分析。

模型报告中的“平均误报事件”只能说明数量，不能说明误报为什么出现。本模块把每个未与
真实 anomaly 事件匹配的告警重新放回原始时序，结合 changepoint、无监督工况过渡和传感器
质量证据进行归因。归因是实验解释，不把“靠近变点”直接改写成故障结论。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.analysis.detection import (
    DETECTOR_RECOMMENDED_THRESHOLDS,
    recommended_event_policy,
)
from app.analysis.evaluation import (
    _changepoint_indexes,
    _event_near_indexes,
    _match_events,
    extract_binary_events,
)
from app.analysis.pipeline import analyze_file
from app.config import get_settings
from app.experiments.protocol import read_frozen_thresholds
from app.experiments.split import ExperimentSplit, build_skab_split
from app.models import AnalysisConfig, AnalysisResult

FALSE_POSITIVE_CATEGORIES = (
    "工况变点附近",
    "工况切换期",
    "传感器质量风险",
    "待解释误报",
)


@dataclass(frozen=True)
class FalsePositiveEvent:
    """一次未匹配真实异常的告警及其证据归因。"""

    detector: str
    detector_name: str
    scenario: str
    file_name: str
    event_number: int
    start_index: int
    end_index: int
    start_time: str
    end_time: str
    duration_points: int
    peak_score: float
    category: str
    confidence: str
    dominant_sensors: tuple[str, ...]
    changepoint_nearby: bool
    transition_overlap: float
    missing_rate: float
    flatline_sensors: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class FalsePositiveAnalysis:
    """一批文件的误报审计结果和固定输出位置。"""

    detector: str
    detector_name: str
    split_name: str
    file_count: int
    analyzed_file_count: int
    failed_files: dict[str, str]
    events: tuple[FalsePositiveEvent, ...]
    summary_rows: tuple[dict[str, Any], ...]
    csv_path: Path
    report_path: Path


def audit_result(
    result: AnalysisResult,
    *,
    scenario: str | None = None,
    merge_gap: int = 5,
    event_tolerance: int = 5,
    transition_threshold: float = 0.5,
    quality_missing_threshold: float = 0.20,
) -> list[FalsePositiveEvent]:
    """审计一份带 anomaly 标签的分析结果，返回所有误报事件。"""

    if "anomaly" not in result.dataframe.columns:
        raise ValueError("误报审计需要 anomaly 标签。")

    actual = result.dataframe["anomaly"].fillna(0).astype(int).clip(0, 1)
    predicted = result.predicted_labels.astype(int).clip(0, 1)
    actual_events = extract_binary_events(actual, merge_gap=0)
    predicted_events = extract_binary_events(predicted, merge_gap=merge_gap)
    _, matched_predicted, _ = _match_events(
        actual_events,
        predicted_events,
        tolerance=event_tolerance,
    )
    changepoints = _changepoint_indexes(result.dataframe)
    contexts = result.operating_regimes.event_contexts if result.operating_regimes else []
    output: list[FalsePositiveEvent] = []
    for event_index, event_range in enumerate(predicted_events):
        if event_index in matched_predicted:
            continue
        # 不能直接用预测事件下标索引工况上下文：事件可能在检测阶段被合并或抑制。
        # 先按区间匹配统一流水线中已经生成的事件，再读取对应的事件编号。
        context = _context_for_event(
            event_range,
            result.events,
            contexts,
            tolerance=event_tolerance,
        )
        output.append(
            _build_false_positive_event(
                result,
                event_range,
                event_index + 1,
                scenario or result.source_path.parent.name,
                changepoints,
                context,
                transition_threshold,
                quality_missing_threshold,
                event_tolerance,
            )
        )
    return output


def analyze_skab_false_positives(
    data_root: str | Path,
    output_dir: str | Path | None = None,
    *,
    detector: str = "time_frequency_relation",
    threshold: float | None = None,
    split: ExperimentSplit | None = None,
    files: tuple[Path, ...] | None = None,
) -> FalsePositiveAnalysis:
    """在固定独立测试文件上生成误报审计，重点保留 `other` 场景结果。"""

    settings = get_settings()
    root = Path(data_root).expanduser().resolve()
    target_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "competition"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    experiment_split = split or build_skab_split(root)
    selected_files = list(files) if files is not None else list(experiment_split.test_files)
    if not selected_files:
        raise ValueError("没有可用于误报审计的独立测试文件。")
    frozen_thresholds = read_frozen_thresholds(target_dir)
    min_event_length, merge_gap = recommended_event_policy(detector)

    config = AnalysisConfig(
        detector=detector,
        threshold=(
            threshold
            if threshold is not None
            else frozen_thresholds.get(
                detector,
                float(DETECTOR_RECOMMENDED_THRESHOLDS.get(detector, settings.anomaly_threshold)),
            )
        ),
        rolling_window=settings.rolling_window,
        min_event_length=min_event_length,
        merge_gap=merge_gap,
        contamination=settings.contamination,
    )
    events: list[FalsePositiveEvent] = []
    failed_files: dict[str, str] = {}
    analyzed_count = 0
    for file_path in selected_files:
        try:
            result = analyze_file(
                file_path,
                config=config,
                write_report=False,
                run_forecast=False,
                run_regime=True,
            )
            events.extend(
                audit_result(
                    result,
                    scenario=file_path.parent.name,
                    merge_gap=config.merge_gap,
                )
            )
            analyzed_count += 1
        except Exception as exc:  # noqa: BLE001
            failed_files[_safe_relative_path(file_path, root)] = str(exc)

    summary_rows = tuple(_summarize_events(events, selected_files, root))
    csv_path = target_dir / f"{detector}_false_positive_events.csv"
    report_path = target_dir / f"{detector}_false_positive_analysis.md"
    _write_events_csv(events, csv_path)
    report_path.write_text(
        _build_report(
            root,
            detector,
            config.threshold,
            len(selected_files),
            analyzed_count,
            failed_files,
            summary_rows,
            events,
        ),
        encoding="utf-8",
    )
    return FalsePositiveAnalysis(
        detector=detector,
        detector_name=_detector_name(events, detector),
        split_name="independent_test",
        file_count=len(selected_files),
        analyzed_file_count=analyzed_count,
        failed_files=failed_files,
        events=tuple(events),
        summary_rows=summary_rows,
        csv_path=csv_path,
        report_path=report_path,
    )


def _build_false_positive_event(
    result: AnalysisResult,
    event_range: tuple[int, int],
    event_number: int,
    scenario: str,
    changepoints: list[int],
    context: dict[str, Any],
    transition_threshold: float,
    quality_missing_threshold: float,
    event_tolerance: int,
) -> FalsePositiveEvent:
    """从一个告警区间提取风险、工况和数据质量证据。"""

    start_index, end_index = event_range
    dataframe = result.dataframe
    scores = result.combined_score.loc[start_index:end_index]
    peak_score = float(scores.max()) if not scores.empty else 0.0
    transition_overlap = float(context.get("过渡期重合率", 0.0))
    changepoint_nearby = _event_near_indexes(event_range, changepoints, event_tolerance)
    sensor_columns = result.profile.sensor_columns
    window = dataframe.loc[start_index:end_index, sensor_columns]
    missing_rate = float(window.isna().mean().mean()) if not window.empty else 0.0
    flatline_sensors = tuple(
        column
        for column in sensor_columns
        if len(window[column].dropna()) >= 3 and window[column].nunique(dropna=True) <= 1
    )
    dominant_sensors = _dominant_sensors(result, event_range)
    quality_risk = (
        missing_rate >= quality_missing_threshold
        or len(flatline_sensors) >= max(1, len(sensor_columns) // 2)
    )

    if changepoint_nearby:
        category = "工况变点附近"
        confidence = "高"
        evidence = ("预测告警未匹配 anomaly 事件，但与 changepoint 标签在容差范围内相邻。",)
    elif transition_overlap >= transition_threshold:
        category = "工况切换期"
        confidence = "中"
        evidence = (f"告警区间与无监督工况过渡区重合率为 {transition_overlap:.1%}。",)
    elif quality_risk:
        category = "传感器质量风险"
        confidence = "中"
        evidence = (f"告警区间测点缺失率为 {missing_rate:.1%}，或存在平直测点。",)
    else:
        category = "待解释误报"
        confidence = "低"
        evidence = ("未发现变点、工况过渡或明显传感器质量证据，需要结合现场记录复核。",)
    if dominant_sensors:
        evidence = evidence + (f"主导测点：{', '.join(dominant_sensors)}。",)
    return FalsePositiveEvent(
        detector=result.detector_name,
        detector_name=result.detector_name,
        scenario=scenario,
        file_name=result.profile.source_name,
        event_number=event_number,
        start_index=start_index,
        end_index=end_index,
        start_time=str(dataframe.at[start_index, "datetime"]),
        end_time=str(dataframe.at[end_index, "datetime"]),
        duration_points=end_index - start_index + 1,
        peak_score=round(peak_score, 6),
        category=category,
        confidence=confidence,
        dominant_sensors=dominant_sensors,
        changepoint_nearby=changepoint_nearby,
        transition_overlap=round(transition_overlap, 6),
        missing_rate=round(missing_rate, 6),
        flatline_sensors=flatline_sensors,
        evidence=evidence,
    )


def _summarize_events(
    events: list[FalsePositiveEvent],
    selected_files: list[Path],
    root: Path,
) -> list[dict[str, Any]]:
    """按场景汇总误报分类，包含零误报场景，方便直接放进 PPT。"""

    scenarios = sorted({path.parent.name for path in selected_files})
    output: list[dict[str, Any]] = []
    for scenario in scenarios:
        rows = [event for event in events if event.scenario == scenario]
        counts = {
            category: sum(event.category == category for event in rows)
            for category in FALSE_POSITIVE_CATEGORIES
        }
        output.append(
            {
                "scenario": scenario,
                "file_count": sum(path.parent.name == scenario for path in selected_files),
                "false_positive_events": len(rows),
                **counts,
                "unknown_rate": round(counts["待解释误报"] / max(len(rows), 1), 6),
                "relative_path_example": next(
                    (
                        _safe_relative_path(path, root)
                        for path in selected_files
                        if path.parent.name == scenario
                    ),
                    "",
                ),
            }
        )
    return output


def _write_events_csv(events: list[FalsePositiveEvent], path: Path) -> None:
    """保存逐事件审计表，使用 UTF-8 BOM 方便 Windows Excel 打开。"""

    fields = list(FalsePositiveEvent.__dataclass_fields__)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for event in events:
            row = event.__dict__.copy()
            row["dominant_sensors"] = ", ".join(event.dominant_sensors)
            row["flatline_sensors"] = ", ".join(event.flatline_sensors)
            row["evidence"] = "；".join(event.evidence)
            writer.writerow(row)


def _build_report(
    root: Path,
    detector: str,
    threshold: float,
    file_count: int,
    analyzed_count: int,
    failed_files: dict[str, str],
    summary_rows: tuple[dict[str, Any], ...],
    events: list[FalsePositiveEvent],
) -> str:
    """生成面向评委的误报解释报告，明确实验边界。"""

    counts = {
        category: sum(event.category == category for event in events)
        for category in FALSE_POSITIVE_CATEGORIES
    }
    lines = [
        "# SKAB 独立测试误报事件分析",
        "",
        "> 本报告用于解释模型为什么产生未匹配告警，不把误报线索包装成企业现场结论。",
        f"> 检测器：{_detector_name(events, detector)}；阈值：{threshold:.2f}；独立测试文件：{file_count}；成功分析：{analyzed_count}。",
        f"> 数据目录：`{root}`",
        "",
        "## 1. 误报分类总览",
        "",
        f"- 未匹配告警总数：{len(events)} 个。",
        *[f"- {category}：{counts[category]} 个。" for category in FALSE_POSITIVE_CATEGORIES],
        "",
        "## 2. 分场景统计",
        "",
        "| 场景 | 文件数 | 误报事件 | 变点附近 | 工况切换 | 传感器质量 | 待解释 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary_rows:
        lines.append(
            f"| {row['scenario']} | {row['file_count']} | {row['false_positive_events']} | "
            f"{row['工况变点附近']} | {row['工况切换期']} | {row['传感器质量风险']} | {row['待解释误报']} |"
        )
    lines.extend(
        [
            "",
            "## 3. other 场景的解释",
            "",
            "other 是 SKAB 中设备类型和故障模式更混杂的场景，不能只用一个总 F1 判断模型表现。",
            "本次分析把未匹配告警拆成可检查的来源：接近 changepoint 的告警优先作为工况切换干扰排查，",
            "处于无监督工况过渡区的告警保留为切换期线索，存在缺失或平直测点的告警进入传感器质量排查，",
            "其余才保留为待解释误报，后续应结合现场记录形成新案例。",
            "",
            "## 4. 典型未匹配告警",
            "",
            "| 场景 | 文件 | 事件 | 时间区间 | 分类 | 峰值风险 | 主导测点 |",
            "| --- | --- | ---: | --- | --- | ---: | --- |",
        ]
    )
    for event in events[:30]:
        lines.append(
            f"| {event.scenario} | {event.file_name} | {event.event_number} | "
            f"{event.start_time} 至 {event.end_time} | {event.category} | {event.peak_score:.2f} | "
            f"{', '.join(event.dominant_sensors) or '-'} |"
        )
    if not events:
        lines.append("| - | - | - | - | 未发现未匹配告警 | - | - |")
    lines.extend(
        [
            "",
            "## 5. 使用边界",
            "",
            "- “工况变点附近”只表示与 changepoint 标签相邻，不表示模型一定误报；工况变化本身可能诱发真实故障。",
            "- 无监督工况切换是算法根据传感器时序推断的上下文，必须结合设备运行日志复核。",
            "- 传感器质量风险只提示数据需要检查，不能替代仪表校验和现场检修。",
            "- 未接入企业真实数据前，本报告只用于 SKAB 校赛实验和模型改进，不用于宣称企业成效。",
        ]
    )
    if failed_files:
        lines.extend(["", "## 6. 失败文件", ""])
        lines.extend(f"- `{name}`：{reason}" for name, reason in failed_files.items())
    return "\n".join(lines) + "\n"


def _dominant_sensors(result: AnalysisResult, event_range: tuple[int, int]) -> tuple[str, ...]:
    """按事件区间内的传感器异常分数提取最多三个主导测点。"""

    start_index, end_index = event_range
    scores = result.anomaly_scores.loc[start_index:end_index]
    if scores.empty:
        return ()
    ranking = scores.mean().sort_values(ascending=False)
    return tuple(str(name) for name in ranking.head(3).index)


def _context_for_event(
    event_range: tuple[int, int],
    result_events: list[Any],
    contexts: list[dict[str, Any]],
    *,
    tolerance: int,
) -> dict[str, Any]:
    """按事件区间寻找工况上下文，避免告警合并后出现编号错位。"""

    for event_number, event in enumerate(result_events, start=1):
        candidate = (int(event.start_index), int(event.end_index))
        if _ranges_overlap(event_range, candidate, tolerance):
            return contexts[event_number - 1] if event_number <= len(contexts) else {}
    return {}


def _ranges_overlap(
    first: tuple[int, int],
    second: tuple[int, int],
    tolerance: int,
) -> bool:
    """判断两个闭区间是否代表同一个告警事件。"""

    return first[0] - tolerance <= second[1] and second[0] <= first[1] + tolerance


def _safe_relative_path(path: Path, root: Path) -> str:
    """生成稳定相对路径；测试或外部调用传入不同根目录时保留绝对路径。"""

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _detector_name(events: list[FalsePositiveEvent], detector: str) -> str:
    """没有误报事件时仍返回稳定的检测器展示名。"""

    return events[0].detector_name if events else detector
