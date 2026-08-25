"""生成面向竞赛材料的创新算法证据矩阵。

该模块只整理固定验证/独立测试实验产物，不读取待分析文件，也不会按测试标签为单个文件
挑选最优模型。证据分为三层：模型总体与分场景独立测试、按部署目标冻结的模型路由、
时域/频域/关系路径验证集消融。三层口径分开保存，避免把验证集结果写成测试集成效。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from app.analysis.model_selection import ANALYSIS_GOAL_LABELS, GOAL_PREFERENCES
from app.config import get_settings
from app.experiments.competition_report import CompetitionReport, build_competition_report
from app.experiments.protocol import PROTOCOL_VERSION
from app.experiments.tfr_ablation import TfrAblationResult, run_tfr_weight_ablation


@dataclass(frozen=True)
class InnovationEvidence:
    """一次创新证据生成后的固定产物位置。"""

    report_path: Path
    overall_csv_path: Path
    scenario_csv_path: Path
    routing_csv_path: Path
    ablation_csv_path: Path
    competition_report: CompetitionReport
    ablation_source_path: Path | None
    ablation_protocol_status: str


def build_innovation_evidence(
    data_root: str | Path,
    output_dir: str | Path | None = None,
    *,
    rerun_experiments: bool = False,
    rerun_ablation: bool = False,
) -> InnovationEvidence:
    """生成总体、分场景、目标路由和路径消融四组可复核证据。"""

    settings = get_settings()
    target = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "competition"
    )
    target.mkdir(parents=True, exist_ok=True)
    root = Path(data_root).expanduser().resolve()

    competition = build_competition_report(
        root,
        output_dir=target,
        rerun_experiments=rerun_experiments,
    )
    detail_records = _read_csv(competition.benchmark_path.with_suffix(".csv"))
    scenario_rows = _build_scenario_rows(detail_records)
    overall_rows = _build_overall_rows(detail_records)
    routing_rows = _build_routing_rows(overall_rows)

    ablation_result: TfrAblationResult | None = None
    if rerun_ablation:
        ablation_result = run_tfr_weight_ablation(root, output_dir=target)
    ablation_source = (
        ablation_result.csv_path
        if ablation_result
        else _latest_file(target, "tfr_weight_ablation_*.csv")
    )
    # 成果包目录首次生成时可能没有消融产物，再从标准实验目录读取历史或当前结果。
    if ablation_source is None:
        ablation_source = _latest_file(
            settings.output_dir / "experiments",
            "tfr_weight_ablation_*.csv",
        )
    ablation_rows, ablation_status = _build_ablation_rows(ablation_source)

    overall_path = target / "innovation_model_overall.csv"
    scenario_path = target / "innovation_model_scenario_matrix.csv"
    routing_path = target / "innovation_goal_routing_comparison.csv"
    ablation_path = target / "innovation_tfr_path_ablation.csv"
    report_path = target / "INNOVATION_EVIDENCE.md"
    _write_csv(overall_path, overall_rows)
    _write_csv(scenario_path, scenario_rows)
    _write_csv(routing_path, routing_rows)
    _write_csv(ablation_path, ablation_rows)
    report_path.write_text(
        _build_report(
            root=root,
            competition=competition,
            overall_rows=overall_rows,
            scenario_rows=scenario_rows,
            routing_rows=routing_rows,
            ablation_rows=ablation_rows,
            ablation_source=ablation_source,
            ablation_status=ablation_status,
        ),
        encoding="utf-8",
    )
    return InnovationEvidence(
        report_path=report_path,
        overall_csv_path=overall_path,
        scenario_csv_path=scenario_path,
        routing_csv_path=routing_path,
        ablation_csv_path=ablation_path,
        competition_report=competition,
        ablation_source_path=ablation_source,
        ablation_protocol_status=ablation_status,
    )


def _build_scenario_rows(records: list[dict[str, str]]) -> list[dict[str, Any]]:
    """按模型和场景聚合，并计算相对同场景 MAD 的差值。"""

    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for record in records:
        grouped.setdefault((record["detector"], record["scenario"]), []).append(record)

    base_by_scenario: dict[str, dict[str, float]] = {}
    for (detector, scenario), items in grouped.items():
        if detector == "mad":
            base_by_scenario[scenario] = _metrics(items)

    rows: list[dict[str, Any]] = []
    for (detector, scenario), items in sorted(grouped.items()):
        metrics = _metrics(items)
        baseline = base_by_scenario.get(scenario, {})
        rows.append(
            {
                "detector": detector,
                "detector_name": items[0]["detector_name"],
                "scenario": scenario,
                "file_count": len(items),
                **metrics,
                "event_f1_delta_vs_mad": round(
                    metrics["event_f1"] - baseline.get("event_f1", 0.0), 6
                ),
                "point_f1_delta_vs_mad": round(
                    metrics["point_f1"] - baseline.get("point_f1", 0.0), 6
                ),
                "false_events_delta_vs_mad": round(
                    metrics["false_positive_events"]
                    - baseline.get("false_positive_events", 0.0),
                    6,
                ),
            }
        )
    return rows


def _build_overall_rows(records: list[dict[str, str]]) -> list[dict[str, Any]]:
    """直接从逐文件独立测试记录计算总体均值，避免场景文件数不同造成二次平均偏差。"""

    grouped: dict[str, list[dict[str, str]]] = {}
    for record in records:
        grouped.setdefault(record["detector"], []).append(record)
    baseline = _metrics(grouped["mad"])
    rows = []
    for detector, items in sorted(grouped.items()):
        metrics = _metrics(items)
        rows.append(
            {
                "detector": detector,
                "detector_name": items[0]["detector_name"],
                "file_count": len(items),
                **metrics,
                "event_f1_delta_vs_mad": round(
                    metrics["event_f1"] - baseline["event_f1"], 6
                ),
                "event_recall_delta_vs_mad": round(
                    metrics["event_recall"] - baseline["event_recall"], 6
                ),
                "point_f1_delta_vs_mad": round(
                    metrics["point_f1"] - baseline["point_f1"], 6
                ),
                "false_events_delta_vs_mad": round(
                    metrics["false_positive_events"]
                    - baseline["false_positive_events"],
                    6,
                ),
            }
        )
    return rows


def _build_routing_rows(overall_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按冻结任务目标生成路由证据，不使用逐文件测试标签动态选模。"""

    by_detector = {str(row["detector"]): row for row in overall_rows}
    baseline = by_detector["mad"]
    objective_metrics = {
        "balanced": ("event_f1", "事件级 F1 越高越好"),
        "high_recall": ("event_recall", "事件召回越高越好"),
        "low_false_alarm": ("false_positive_events", "平均误报事件越低越好"),
        "relationship_fault": ("event_f1", "事件级 F1；关系证据另由模型路径输出"),
        "nonlinear_pattern": ("point_f1", "点级 F1；复杂模式能力仍需企业数据复核"),
        "fast_screening": ("inference_seconds", "单文件推理耗时越低越好"),
    }
    rows: list[dict[str, Any]] = []
    for goal, label in ANALYSIS_GOAL_LABELS.items():
        selected = next(
            detector for detector in GOAL_PREFERENCES[goal] if detector in by_detector
        )
        selected_row = by_detector[selected]
        metric, interpretation = objective_metrics[goal]
        rows.append(
            {
                "analysis_goal": goal,
                "analysis_goal_name": label,
                "selected_detector": selected,
                "selected_detector_name": selected_row["detector_name"],
                "selection_rule": "冻结目标优先级 + 数据最低适用条件",
                "objective_metric": metric,
                "objective_interpretation": interpretation,
                "selected_value": selected_row[metric],
                "mad_baseline_value": baseline[metric],
                "delta_vs_mad": round(selected_row[metric] - baseline[metric], 6),
                "label_leakage_control": "路由不读取当前文件 anomaly/changepoint 标签",
            }
        )
    return rows


def _build_ablation_rows(source: Path | None) -> tuple[list[dict[str, Any]], str]:
    """读取路径消融结果，并检查配套报告是否声明当前实验协议。"""

    if source is None:
        return [], "missing"
    rows = _read_csv(source)
    report_path = source.with_suffix(".md")
    report_text = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    current = PROTOCOL_VERSION in report_text
    status = "current_protocol" if current else "historical_unversioned"
    normalized = []
    for row in rows:
        normalized.append(
            {
                "candidate_id": row["candidate_id"],
                "time_weight": _number(row, "time_weight"),
                "frequency_weight": _number(row, "frequency_weight"),
                "relation_weight": _number(row, "relation_weight"),
                "threshold": _number(row, "threshold"),
                "validation_objective": _number(row, "objective"),
                "validation_point_f1": _number(row, "point_f1"),
                "validation_event_f1": _number(row, "event_f1"),
                "validation_event_recall": _number(row, "event_recall"),
                "validation_false_events": _number(row, "average_false_events"),
                "validation_healthy_false_event_rate": _number(
                    row, "healthy_false_event_rate"
                ),
                "protocol_status": status,
                "protocol_version": PROTOCOL_VERSION if current else "未记录",
            }
        )
    return normalized, status


def _metrics(records: list[dict[str, str]]) -> dict[str, float]:
    return {
        field: round(mean(_number(item, field) for item in records), 6)
        for field in (
            "point_f1",
            "pr_auc",
            "event_f1",
            "event_recall",
            "false_positive_events",
            "inference_seconds",
        )
    }


def _number(record: dict[str, str], field: str) -> float:
    value = record.get(field)
    return float(value) if value not in {None, ""} else 0.0


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"找不到创新证据数据源：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError(f"创新证据数据源为空：{path}")
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _latest_file(directory: Path, pattern: str) -> Path | None:
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _build_report(
    *,
    root: Path,
    competition: CompetitionReport,
    overall_rows: list[dict[str, Any]],
    scenario_rows: list[dict[str, Any]],
    routing_rows: list[dict[str, Any]],
    ablation_rows: list[dict[str, Any]],
    ablation_source: Path | None,
    ablation_status: str,
) -> str:
    """生成评委可读、同时保留实验边界的创新证据报告。"""

    baseline = next(row for row in overall_rows if row["detector"] == "mad")
    tfr = next(
        row for row in overall_rows if row["detector"] == "time_frequency_relation"
    )
    lines = [
        "# 时察千机创新算法证据",
        "",
        "> 数据来源：SKAB 公开工业时序数据；当前结果不代表联通企业现场实测成效。",
        f"> 当前独立测试协议：`{PROTOCOL_VERSION}`。",
        f"> 数据目录：`{root}`",
        "",
        "## 1. 核心结论",
        "",
        f"- 时频关系多路径模型独立测试事件级 F1 为 {tfr['event_f1']:.4f}，相对 MAD 基线 {baseline['event_f1']:.4f} 提升 {tfr['event_f1_delta_vs_mad']:+.4f}。",
        f"- 其事件召回为 {tfr['event_recall']:.4f}，点级 F1 为 {tfr['point_f1']:.4f}；平均误报事件为 {tfr['false_positive_events']:.2f} 个/文件。",
        "- 自动路由依据任务目标、设备配置和最低输入条件执行冻结策略，不读取当前文件异常标签。",
        "- 路径消融只用于验证时域、频域和关系路径的机制贡献，不与独立测试指标混写。",
        "",
        "## 2. 独立测试总体对照",
        "",
        "| 模型 | 文件 | 点级 F1 | 事件级 F1 | 事件召回 | 平均误报事件 | 相对 MAD 事件 F1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in sorted(overall_rows, key=lambda item: item["event_f1"], reverse=True):
        lines.append(
            f"| {row['detector_name']} | {row['file_count']} | {row['point_f1']:.4f} | "
            f"{row['event_f1']:.4f} | {row['event_recall']:.4f} | "
            f"{row['false_positive_events']:.2f} | {row['event_f1_delta_vs_mad']:+.4f} |"
        )
    lines.extend(
        [
            "",
            "## 3. 分场景证据",
            "",
            "| 场景 | 模型 | 文件 | 点级 F1 | 事件级 F1 | 事件召回 | 平均误报事件 |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in scenario_rows:
        if row["detector"] not in {"mad", "time_frequency_relation"}:
            continue
        lines.append(
            f"| {row['scenario']} | {row['detector_name']} | {row['file_count']} | "
            f"{row['point_f1']:.4f} | {row['event_f1']:.4f} | "
            f"{row['event_recall']:.4f} | {row['false_positive_events']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 4. 任务目标驱动的自动路由",
            "",
            "> 下表评价的是策略与任务目标是否对齐，不宣称路由在单一总体指标上必然优于所有固定模型。",
            "",
            "| 任务目标 | 冻结选择 | 评价指标 | 选择值 | MAD 基线 | 差值 |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for row in routing_rows:
        lines.append(
            f"| {row['analysis_goal_name']} | {row['selected_detector_name']} | "
            f"{row['objective_interpretation']} | {row['selected_value']:.4f} | "
            f"{row['mad_baseline_value']:.4f} | {row['delta_vs_mad']:+.4f} |"
        )

    status_text = {
        "current_protocol": f"已按当前协议 `{PROTOCOL_VERSION}` 记录",
        "historical_unversioned": "历史未版本化产物，仅作机制参考，不能与当前独立测试直接合并",
        "missing": "尚未生成，需运行 `--rerun-innovation-evidence`",
    }[ablation_status]
    lines.extend(
        [
            "",
            "## 5. 时频关系路径消融",
            "",
            f"> 状态：{status_text}。",
            f"> 数据源：`{ablation_source}`" if ablation_source else "> 数据源：无。",
            "",
            "| 路径组合 | 时域 | 频域 | 关系 | 验证事件 F1 | 验证事件召回 | 验证误报事件 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in sorted(
        ablation_rows, key=lambda item: item["validation_objective"], reverse=True
    ):
        lines.append(
            f"| {row['candidate_id']} | {row['time_weight']:.2f} | "
            f"{row['frequency_weight']:.2f} | {row['relation_weight']:.2f} | "
            f"{row['validation_event_f1']:.4f} | "
            f"{row['validation_event_recall']:.4f} | "
            f"{row['validation_false_events']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 6. 证据边界",
            "",
            f"- 独立测试逐文件来源：`{competition.benchmark_path}`。",
            "- 路由策略只证明系统能按任务目标编排模型，不等于企业现场自学习已经完成。",
            "- 分场景样本量不同，尤其 valve2 样本较少，不能只依据单个场景下结论。",
            "- 企业数据接入后需冻结新的时间划分，重新校准阈值并复核误报、漏报和检测延迟。",
            "",
        ]
    )
    return "\n".join(lines)
