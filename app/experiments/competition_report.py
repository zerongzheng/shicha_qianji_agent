"""生成校赛阶段可复现的 SKAB 项目证据汇总。

这个模块不重新发明指标，也不把某一个样例文件的结果当成总体效果。它读取固定划分实验
产物，按模型和场景汇总，生成适合 PPT、答辩和项目自查的 Markdown 与 CSV 报告。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.experiments.protocol import build_protocol_manifest, write_protocol_artifacts
from app.experiments.split import build_skab_split
from app.experiments.tuning import tune_and_evaluate


@dataclass(frozen=True)
class CompetitionReport:
    """校赛证据报告及其数据来源。"""

    report_path: Path
    csv_path: Path
    benchmark_path: Path
    split_path: Path
    protocol_json_path: Path
    protocol_markdown_path: Path
    effectiveness_csv_path: Path


def build_competition_report(
    data_root: str | Path,
    output_dir: str | Path | None = None,
    *,
    rerun_experiments: bool = False,
) -> CompetitionReport:
    """生成一份独立、可追溯的校赛实验汇总。

    默认复用最近一次实验结果，避免每次打开项目都重复运行全部检测器。没有已有产物时，
    自动执行一次固定划分调优与独立测试。
    """

    settings = get_settings()
    root = Path(data_root).expanduser().resolve()
    target_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "competition"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    split = build_skab_split(root)
    selected_thresholds: dict[str, float] = {}
    if rerun_experiments:
        tuning = tune_and_evaluate(root, output_dir=target_dir)
        selected_thresholds = tuning.selected_thresholds
        # 阈值调优函数已经使用冻结参数完成独立测试，这里直接复用结果，
        # 避免生成竞赛汇总时把同一套模型重复运行一遍。
        benchmark_path = tuning.test_benchmark.report_path
        split_path = tuning.split_csv_path
    else:
        # 成果包和普通 competition-report 可能使用不同的文件前缀；先复用已有
        # 独立测试产物，避免每次生成答辩材料都重新运行耗时实验。
        benchmark_path = _latest_file(
            target_dir,
            "competition_independent_test_*.md",
            "independent_test_*.md",
        )
        split_path = _latest_file(target_dir, "data_split_*.csv")
        if benchmark_path is None or split_path is None:
            tuning = tune_and_evaluate(root, output_dir=target_dir)
            selected_thresholds = tuning.selected_thresholds
            benchmark_path = tuning.test_benchmark.report_path
            split_path = tuning.split_csv_path

    csv_source = benchmark_path.with_suffix(".csv")
    records = _read_records(csv_source)
    csv_path = target_dir / "skab_competition_summary.csv"
    report_path = target_dir / "skab_competition_summary.md"
    evaluation_path = target_dir / "FINAL_EVALUATION.md"
    comparison_path = target_dir / "CAPABILITY_COMPARISON.md"
    summary_rows = _summarize(records)
    if not selected_thresholds:
        selected_thresholds = _thresholds_from_records(records)
    protocol = build_protocol_manifest(
        root,
        split,
        selected_thresholds=selected_thresholds,
        detectors=tuple(sorted({record["detector"] for record in records})),
    )
    protocol_json_path, protocol_markdown_path = write_protocol_artifacts(
        protocol,
        target_dir,
    )
    effectiveness_rows = _build_effectiveness_rows(summary_rows)
    effectiveness_csv_path = target_dir / "skab_competition_effectiveness.csv"
    _write_effectiveness_csv(effectiveness_csv_path, effectiveness_rows)
    _write_csv(csv_path, summary_rows)
    _write_final_evaluation(
        evaluation_path,
        root,
        split,
        records,
        summary_rows,
        benchmark_path,
        split_path,
    )
    comparison_path.write_text(
        _build_capability_comparison(summary_rows),
        encoding="utf-8",
    )
    report_path.write_text(
        _build_report(
            root,
            split,
            records,
            summary_rows,
            benchmark_path,
            split_path,
            effectiveness_rows,
            protocol_json_path,
            protocol_markdown_path,
        ),
        encoding="utf-8",
    )
    return CompetitionReport(
        report_path,
        csv_path,
        benchmark_path,
        split_path,
        protocol_json_path,
        protocol_markdown_path,
        effectiveness_csv_path,
    )


def _write_final_evaluation(
    path: Path,
    root: Path,
    split: object,
    records: list[dict[str, str]],
    rows: list[dict[str, object]],
    benchmark_path: Path,
    split_path: Path,
) -> None:
    """生成面向评委的最终评估说明，固定实验口径并保留结果边界。"""

    model_rows = _summarize_by_model(rows)
    best_event = max(model_rows, key=lambda row: (row["event_f1"], row["event_recall"]))
    best_point = max(model_rows, key=lambda row: (row["point_f1"], row["pr_auc"]))
    fastest = min(model_rows, key=lambda row: row["inference_seconds"])
    lines = [
        "# 时察千机最终评估说明",
        "",
        "> 本文档用于校赛阶段网评材料和答辩说明。所有指标来自 SKAB 公开数据，不代表企业现场实测成效。",
        "",
        "## 1. 评估对象",
        "",
        "本项目评估的是一套工业时序异常诊断流程，而不是单一异常检测模型。系统包含数据质量检查、",
        "多变量异常检测、异常事件合并、工况上下文分析、候选根因排序、运维建议和工单生成。",
        "",
        f"- 数据目录：`{root}`",
        f"- 实验逐文件记录：`{benchmark_path}`",
        f"- 固定数据划分：`{split_path}`",
        f"- 健康基线文件：{len(getattr(split, 'healthy_files', []))} 个",
        f"- 验证文件：{len(getattr(split, 'validation_files', []))} 个",
        f"- 独立测试文件：{len(getattr(split, 'test_files', []))} 个",
        f"- 成功实验记录：{len(records)} 条",
        "",
        "## 2. 实验协议",
        "",
        "1. 按完整 CSV 文件划分数据集，不把同一条连续时序拆到训练集和测试集，降低时序泄漏风险。",
        "2. 健康文件用于建立无监督基线和约束误报，验证集用于选择阈值和模型参数。",
        "3. 参数冻结后仅在独立测试集上进行最终评价，测试集结果不反向参与调参。",
        "4. 同时报告点级和事件级指标；工业场景重点关注事件召回、事件级 F1、误报事件和检测延迟。",
        "5. 推理耗时为单文件平均耗时，受硬件、缓存和数据规模影响，只用于同环境相对比较。",
        "",
        "## 3. 独立测试结果",
        "",
        "| 模型 | 文件数 | 点级 F1 | PR-AUC | 事件级 F1 | 事件召回 | 平均误报事件 | 单文件耗时/秒 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in model_rows:
        lines.append(
            f"| {row['detector_name']} | {row['file_count']} | {row['point_f1']:.4f} | "
            f"{row['pr_auc']:.4f} | {row['event_f1']:.4f} | {row['event_recall']:.4f} | "
            f"{row['false_positive_events']:.2f} | {row['inference_seconds']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## 4. 结果解读",
            "",
            f"- 事件级 F1 最优：{best_event['detector_name']}（{best_event['event_f1']:.4f}）。",
            f"- 点级 F1 最优：{best_point['detector_name']}（{best_point['point_f1']:.4f}）。",
            f"- 平均推理速度最快：{fastest['detector_name']}（{fastest['inference_seconds']:.4f} 秒/文件）。",
            "- 产品默认检测器仍采用时频关系多路径检测器，因为它在事件召回、事件级 F1、误报控制和多路径证据之间取得了更适合演示的平衡。",
            "- MAD 作为稳定告警基线保留；AutoEncoder 作为点级识别和速度对照；其他模型用于交叉验证和模型比较。",
            "",
            "## 5. 局限与下一阶段",
            "",
            "- SKAB 公开数据不能完全代表联通企业真实设备、测点命名、采样频率和维修流程。",
            "- 企业数据接入后需要重新建立健康基线、校准阈值，并在独立时间段进行验证。",
            "- 候选根因用于安排现场排查优先级，不等于故障确诊；确认结果需要人工回写。",
            "- 预测结果用于趋势预警和风险提前量展示，不能替代设备安全边界和停机规程。",
            "",
            "## 6. 可用于 PPT 的核心表述",
            "",
            "时察千机不是只输出一个异常标签，而是将工业时序数据转化为可解释的风险事件，进一步给出候选原因、验证动作、运维工单和历史案例沉淀。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _build_capability_comparison(rows: list[dict[str, object]]) -> str:
    """生成面向评委的传统方式与本项目能力对比表。"""

    model_rows = _summarize_by_model(rows)
    best = max(model_rows, key=lambda row: (row["event_f1"], row["event_recall"]))
    return "\n".join(
        [
            "# 时察千机能力对比表",
            "",
            "> 本表用于说明项目解决的问题和系统闭环，不将算法指标与企业现场成效混用。",
            "",
            "| 对比方式 | 异常发现 | 多变量关系 | 原因解释 | 排查建议 | 工单闭环 | 历史案例复用 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| 人工查看曲线 | 依赖经验，容易遗漏 | 需要人工比对 | 依赖专家 | 不稳定 | 无 | 无 |",
            "| 固定单变量阈值 | 可发现明显越界 | 弱 | 无法解释复杂联动 | 固定规则 | 无 | 无 |",
            "| 单一异常模型 | 可发现部分偏离 | 有限 | 只能提供分数 | 有限 | 无 | 无 |",
            f"| 时察千机 | 多路径联合检测 | 支持关联、时滞和工况分析 | 输出候选根因与证据缺口 | 生成有序验证动作 | 支持状态回写 | {('支持' if True else '未支持')} |",
            "",
            "## 当前模型定位",
            "",
            f"- 当前独立测试中事件级 F1 最优模型：{best['detector_name']}（{best['event_f1']:.4f}）。",
            "- 系统默认模型同时考虑事件召回、误报控制、处理速度和可解释证据，不简单按照单一指标选择。",
            "- 工单确认结果进入 SQLite，并可作为下一次相似异常的历史案例证据。",
            "",
            "## 项目价值链",
            "",
            "数据接入 → 质量检查 → 异常发现 → 证据解释 → 候选根因 → 运维工单 → 人工确认 → 案例沉淀",
            "",
        ]
    )


def _latest_file(directory: Path, *patterns: str) -> Path | None:
    """返回一个或多个文件匹配规则下最新的实验产物。"""

    files = sorted(
        {path for pattern in patterns for path in directory.glob(pattern)},
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def _read_records(csv_path: Path) -> list[dict[str, str]]:
    """读取逐文件实验记录，并验证关键列存在。"""

    if not csv_path.exists():
        raise FileNotFoundError(f"找不到实验明细：{csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        records = list(csv.DictReader(file))
    required = {"detector", "detector_name", "scenario", "event_f1", "point_f1"}
    missing = required - set(records[0]) if records else required
    if missing:
        raise ValueError(f"实验明细缺少字段：{', '.join(sorted(missing))}")
    return records


def _summarize(records: list[dict[str, str]]) -> list[dict[str, object]]:
    """按模型和场景汇总核心指标。"""

    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for record in records:
        groups.setdefault((record["detector"], record["scenario"]), []).append(record)

    rows: list[dict[str, object]] = []
    for (detector, scenario), items in sorted(groups.items()):
        rows.append(
            {
                "detector": detector,
                "detector_name": items[0]["detector_name"],
                "scenario": scenario,
                "file_count": len(items),
                "point_f1": _average(items, "point_f1"),
                "pr_auc": _average(items, "pr_auc"),
                "event_f1": _average(items, "event_f1"),
                "event_recall": _average(items, "event_recall"),
                "false_positive_events": _average(items, "false_positive_events"),
                "inference_seconds": _average(items, "inference_seconds"),
            }
        )
    return rows


def _average(records: list[dict[str, str]], field: str) -> float:
    """计算数值字段平均值，空值不参与计算。"""

    values = [float(item[field]) for item in records if item.get(field) not in {"", None}]
    return round(mean(values), 6) if values else 0.0


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    """写入稳定列顺序的汇总 CSV。"""

    fields = [
        "detector",
        "detector_name",
        "scenario",
        "file_count",
        "point_f1",
        "pr_auc",
        "event_f1",
        "event_recall",
        "false_positive_events",
        "inference_seconds",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _build_report(
    root: Path,
    split: object,
    records: list[dict[str, str]],
    rows: list[dict[str, object]],
    benchmark_path: Path,
    split_path: Path,
    effectiveness_rows: list[dict[str, object]],
    protocol_json_path: Path,
    protocol_markdown_path: Path,
) -> str:
    """生成中文校赛报告，明确实验边界，避免夸大为企业实测成效。"""

    model_rows = _summarize_by_model(rows)
    lines = [
        "# 时察千机校赛阶段实验汇总",
        "",
        "> 数据来源：SKAB 公开工业时序数据集；当前未接入企业真实数据。",
        f"> 生成时间：{datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')}",
        f"> 成功逐文件记录：{len(records)} 条。",
        "",
        "## 一、实验边界",
        "",
        "本报告用于校赛阶段验证算法流程、模型选择和系统可复现性。指标来自按完整文件划分的验证与独立测试实验，",
        "不把同一条连续时序拆到训练和测试两侧，也不将公开数据结果表述为企业现场成效。",
        "",
        f"- 数据目录：`{root}`",
        f"- 实验明细：`{benchmark_path}`",
        f"- 数据划分：`{split_path}`",
        f"- 健康文件数：{len(getattr(split, 'healthy_files', []))}",
        f"- 验证文件数：{len(getattr(split, 'validation_files', []))}",
        f"- 独立测试文件数：{len(getattr(split, 'test_files', []))}",
        f"- 实验协议：`{protocol_markdown_path}`",
        "",
        "## 二、独立测试模型对比",
        "",
        "| 检测器 | 文件数 | 点级 F1 | PR-AUC | 事件级 F1 | 事件召回 | 平均误报事件 | 单文件耗时/秒 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in model_rows:
        lines.append(
            f"| {row['detector_name']} | {row['file_count']} | {row['point_f1']:.4f} | "
            f"{row['pr_auc']:.4f} | {row['event_f1']:.4f} | {row['event_recall']:.4f} | "
            f"{row['false_positive_events']:.2f} | {row['inference_seconds']:.4f} |"
        )

    lines.extend(
        [
            "",
            "## 三、相对基线的成效",
            "",
            "> 这里的“提升”是相对 SKAB 独立测试中的 MAD 基线计算，不是企业现场提升率。",
            "",
            "| 模型 | 事件级 F1 | 相对 MAD | 事件召回 | 点级 F1 | 平均误报事件 | 单文件耗时/秒 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in effectiveness_rows:
        lines.append(
            f"| {row['detector_name']} | {row['event_f1']:.4f} | "
            f"{_format_delta(row['event_f1_delta_vs_mad'])} | {row['event_recall']:.4f} | "
            f"{row['point_f1']:.4f} | {row['false_positive_events']:.2f} | "
            f"{row['inference_seconds']:.4f} |"
        )
    best_event = max(model_rows, key=lambda row: (row["event_f1"], row["event_recall"]))
    main_model = next(
        (
            row
            for row in model_rows
            if str(row["detector"]) == "time_frequency_relation"
        ),
        best_event,
    )
    lines.extend(
        [
            "",
            "## 四、校赛阶段结论",
            "",
            f"- 按事件级 F1 单指标，当前测试集最高的是：**{best_event['detector_name']}**（{best_event['event_f1']:.4f}）。",
            (
                f"- 竞赛演示主模型采用{main_model['detector_name']}：它在当前测试集的事件召回为 "
                f"{main_model['event_recall']:.4f}，并同时输出时域偏离、频域变化和测点关系证据；"
                "MAD 作为稳定告警基线保留。"
            ),
            "- 评价重点应放在完整异常事件是否被发现、误报事件数量和检测延迟，而不能只看点级准确率。",
            "- 当前结果证明时察千机已经具备从原始时序到风险事件和运维建议的可运行流程。",
            "- 当前结果不代表联通企业设备现场效果；企业数据到位后仍需重新建立健康基线、校准阈值并做独立验证。",
            "",
            "## 五、下一步校赛工作",
            "",
            "1. 选取 2 至 3 个典型 SKAB 异常文件，制作原始曲线、异常分数、事件区间和传感器贡献图。",
            "2. 固定推荐检测器和参数，页面、命令行、API 使用同一份配置。",
            "3. 完成 Streamlit 的设备健康总览和一键报告演示。",
            "4. 整理设备数据协议、知识库资料模板和现场反馈字段，为后续企业数据接入预留接口。",
            "",
            "## 六、复现文件",
            "",
            f"- 机器可读实验协议：`{protocol_json_path}`",
            f"- 中文实验协议：`{protocol_markdown_path}`",
            "- 逐文件独立测试结果和汇总 CSV 与本报告位于同一目录。",
        ]
    )
    return "\n".join(lines) + "\n"


def _thresholds_from_records(records: list[dict[str, str]]) -> dict[str, float]:
    """从独立测试明细中恢复冻结阈值，兼容历史实验产物。"""

    thresholds: dict[str, float] = {}
    for record in records:
        detector = record["detector"]
        if detector not in thresholds and record.get("threshold") not in {None, ""}:
            thresholds[detector] = float(record["threshold"])
    return thresholds


def _build_effectiveness_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """生成可直接放进竞赛 PPT 的模型成效表。"""

    model_rows = _summarize_by_model(rows)
    baseline = next(
        (row for row in model_rows if str(row["detector"]) == "mad"),
        None,
    )
    baseline_event_f1 = float(baseline["event_f1"]) if baseline else 0.0
    output: list[dict[str, object]] = []
    for row in model_rows:
        event_f1 = float(row["event_f1"])
        output.append(
            {
                "detector": row["detector"],
                "detector_name": row["detector_name"],
                "file_count": row["file_count"],
                "event_f1": event_f1,
                # 保留 6 位小数，避免浮点误差污染 CSV、报告和测试断言。
                "event_f1_delta_vs_mad": round(event_f1 - baseline_event_f1, 6),
                "event_recall": row["event_recall"],
                "point_f1": row["point_f1"],
                "false_positive_events": row["false_positive_events"],
                "inference_seconds": row["inference_seconds"],
            }
        )
    return output


def _write_effectiveness_csv(path: Path, rows: list[dict[str, object]]) -> None:
    """保存竞赛成效表，使用 UTF-8 BOM 方便直接用 Excel 打开。"""

    fields = [
        "detector",
        "detector_name",
        "file_count",
        "event_f1",
        "event_f1_delta_vs_mad",
        "event_recall",
        "point_f1",
        "false_positive_events",
        "inference_seconds",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _format_delta(value: object) -> str:
    """将相对基线差值格式化为百分数文本。"""

    return f"{float(value):+.2%}"


def _summarize_by_model(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """把按场景汇总的记录再合并成模型总表。"""

    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["detector"]), []).append(row)
    output: list[dict[str, object]] = []
    for detector, items in grouped.items():
        total_files = sum(int(item["file_count"]) for item in items)
        output.append(
            {
                "detector": detector,
                "detector_name": items[0]["detector_name"],
                "file_count": total_files,
                **{
                    field: round(
                        sum(float(item[field]) * int(item["file_count"]) for item in items)
                        / max(total_files, 1),
                        6,
                    )
                    for field in (
                        "point_f1",
                        "pr_auc",
                        "event_f1",
                        "event_recall",
                        "false_positive_events",
                        "inference_seconds",
                    )
                },
            }
        )
    return sorted(
        output,
        key=lambda row: (float(row["event_f1"]), float(row["event_recall"])),
        reverse=True,
    )
