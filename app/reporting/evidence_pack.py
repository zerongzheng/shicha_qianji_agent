"""生成校赛答辩用的可复现成果包。

成果包把实验结果和典型案例放在一个固定目录中，便于制作 PPT、现场演示和复核。
它只使用 SKAB 公开数据，不把结果包装成企业现场实测成效。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.experiments.competition_report import CompetitionReport, build_competition_report
from app.experiments.consensus_evaluation import (
    ConsensusEvaluation,
    evaluate_detector_consensus,
)
from app.experiments.false_positive_analysis import (
    FalsePositiveAnalysis,
    analyze_skab_false_positives,
)
from app.experiments.forecast_effectiveness import (
    ForecastEffectiveness,
    evaluate_forecast_effectiveness,
)
from app.experiments.optimization_effectiveness import (
    OptimizationEffectiveness,
    evaluate_optimization_effectiveness,
)
from app.experiments.system_effectiveness import (
    SystemEffectiveness,
    analyze_skab_system_effectiveness,
)
from app.reporting.case_package import CasePackage, build_case_package


@dataclass(frozen=True)
class EvidencePack:
    """一次成果包生成后的固定文件位置。"""

    output_dir: Path
    index_path: Path
    competition_report: CompetitionReport
    consensus_evaluation: ConsensusEvaluation
    forecast_effectiveness: ForecastEffectiveness
    optimization_effectiveness: OptimizationEffectiveness
    false_positive_analysis: FalsePositiveAnalysis
    system_effectiveness: SystemEffectiveness
    cases: tuple[CasePackage, ...]


def build_evidence_pack(
    data_root: str | Path,
    output_dir: str | Path | None = None,
    *,
    case_count: int = 3,
    rerun_experiments: bool = False,
) -> EvidencePack:
    """生成实验汇总、典型案例和答辩索引。"""

    settings = get_settings()
    target = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "evidence_pack"
    )
    target.mkdir(parents=True, exist_ok=True)
    competition = build_competition_report(
        data_root,
        output_dir=target / "experiments",
        rerun_experiments=rerun_experiments,
    )
    resolved_data_root = Path(data_root).expanduser().resolve()
    consensus_evaluation = evaluate_detector_consensus(
        resolved_data_root,
        output_dir=target / "experiments",
    )
    forecast_effectiveness = evaluate_forecast_effectiveness(
        resolved_data_root,
        output_dir=target / "experiments",
    )
    # 优化建议实验使用固定受控轨迹，只验证“小步、限幅、可回退”的执行机制。
    # 它不依赖 SKAB，也不能被表述为企业设备节能率或现场控制收益。
    optimization_effectiveness = evaluate_optimization_effectiveness(
        output_dir=target / "experiments",
    )
    false_positive_analysis = analyze_skab_false_positives(
        resolved_data_root,
        output_dir=target / "experiments",
        detector="time_frequency_relation",
    )
    system_effectiveness = analyze_skab_system_effectiveness(
        resolved_data_root,
        output_dir=target / "experiments",
        detector="time_frequency_relation",
    )
    case_files = _select_case_files(resolved_data_root, case_count)
    cases = tuple(
        build_case_package(file_path, output_dir=target / "cases")
        for file_path in case_files
    )
    index_path = target / "EVIDENCE_PACK_INDEX.md"
    index_path.write_text(
        _build_index(
            resolved_data_root,
            competition,
            consensus_evaluation,
            forecast_effectiveness,
            optimization_effectiveness,
            false_positive_analysis,
            system_effectiveness,
            cases,
        ),
        encoding="utf-8",
    )
    return EvidencePack(
        target,
        index_path,
        competition,
        consensus_evaluation,
        forecast_effectiveness,
        optimization_effectiveness,
        false_positive_analysis,
        system_effectiveness,
        cases,
    )


def _select_case_files(data_root: Path, count: int) -> list[Path]:
    """按父目录稳定选择案例，尽量让案例覆盖不同数据场景。"""

    candidates = sorted(
        path
        for path in data_root.rglob("*.csv")
        if path.is_file() and path.parent.name.lower() != "anomaly-free"
    )
    if not candidates:
        raise FileNotFoundError(f"在 SKAB 数据目录中没有找到异常样例：{data_root}")
    selected: list[Path] = []
    selected_set: set[Path] = set()
    scenario_order = ("other", "valve1", "valve2")
    # 典型案例优先覆盖混合场景和两类阀门场景，便于答辩展示模型的泛化能力。
    for scenario in scenario_order:
        scenario_files = [path for path in candidates if path.parent.name.lower() == scenario]
        if scenario_files:
            selected.append(scenario_files[0])
            selected_set.add(scenario_files[0])
        if len(selected) >= max(1, count):
            return selected

    # 数据集目录可能只有部分场景，按父目录稳定补齐，不因为缺少某一场景而失败。
    seen_parents = {path.parent.name for path in selected}
    for path in candidates:
        if path.parent.name not in seen_parents:
            selected.append(path)
            selected_set.add(path)
            seen_parents.add(path.parent.name)
        if len(selected) >= max(1, count):
            return selected
    for path in candidates:
        if path not in selected_set:
            selected.append(path)
            selected_set.add(path)
        if len(selected) >= max(1, count):
            break
    return selected


def _build_index(
    data_root: Path,
    competition: CompetitionReport,
    consensus_evaluation: ConsensusEvaluation,
    forecast_effectiveness: ForecastEffectiveness,
    optimization_effectiveness: OptimizationEffectiveness,
    false_positive_analysis: FalsePositiveAnalysis,
    system_effectiveness: SystemEffectiveness,
    cases: tuple[CasePackage, ...],
) -> str:
    """将成果包内容和答辩讲解顺序写成一页索引。"""

    lines = [
        "# 时察千机校赛成果包索引",
        "",
        "> 数据来源：SKAB 公开工业时序数据集；本成果包不代表联通企业现场实测效果。",
        f"> 数据目录：`{data_root}`",
        "",
        "## 推荐讲解顺序",
        "",
        "1. 先展示 `experiments/skab_competition_summary.md`，说明数据划分和模型对比。",
        "2. 展示多模型共识实验，说明为什么交叉验证只增强可信度、不直接覆盖主告警。",
        "3. 展示趋势预测与提前预警实验，区分 SKAB 时间尾段结果和受控退化模拟。",
        "4. 展示受约束优化建议实验，说明建议如何限幅、观察、回退并保留人工确认。",
        "5. 展示 `experiments/time_frequency_relation_false_positive_analysis.md`，说明 other 场景的误报来源。",
        "6. 再展示典型案例中的风险图，说明异常如何从数据中被发现。",
        "7. 打开案例摘要，沿着“异常事件 - 主导传感器 - 候选原因 - 排查动作”讲解。",
        "8. 回到 Vue3 的“运维闭环”，演示工单确认、现场反馈和历史案例沉淀。",
        "",
        "## 实验材料",
        "",
        f"- Markdown 汇总：`{competition.report_path}`",
        f"- CSV 汇总：`{competition.csv_path}`",
        f"- 数据划分：`{competition.split_path}`",
        f"- 最终评估说明：`{competition.report_path.parent / 'FINAL_EVALUATION.md'}`",
        f"- 能力对比表：`{competition.report_path.parent / 'CAPABILITY_COMPARISON.md'}`",
        f"- 多模型共识报告：`{consensus_evaluation.report_path}`",
        f"- 多模型共识逐文件记录：`{consensus_evaluation.csv_path}`",
        f"- 共识实验成功记录：{len(consensus_evaluation.records)}；失败文件：{len(consensus_evaluation.failed_files)}",
        f"- 趋势预测与预警报告：`{forecast_effectiveness.report_path}`",
        f"- SKAB 时间尾段逐序列记录：`{forecast_effectiveness.real_csv_path}`",
        f"- 受控退化场景记录：`{forecast_effectiveness.warning_csv_path}`",
        f"- 预测成功记录：{len(forecast_effectiveness.real_records)}；失败任务：{len(forecast_effectiveness.failed_tasks)}",
        f"- 受约束优化建议报告：`{optimization_effectiveness.report_path}`",
        f"- 受约束优化建议逐场景记录：`{optimization_effectiveness.csv_path}`",
        f"- 优化机制场景数：{len(optimization_effectiveness.records)}（含稳定无风险对照）",
        f"- 误报解释报告：`{false_positive_analysis.report_path}`",
        f"- 逐事件误报审计表：`{false_positive_analysis.csv_path}`",
        f"- 误报事件数：{len(false_positive_analysis.events)}；成功分析文件：{false_positive_analysis.analyzed_file_count}/{false_positive_analysis.file_count}",
        f"- 系统成效报告：`{system_effectiveness.report_path}`",
        f"- 系统成效逐文件统计：`{system_effectiveness.csv_path}`",
        f"- 证据覆盖率：{system_effectiveness.evidence_coverage:.2%}；诊断覆盖率：{system_effectiveness.diagnosis_coverage:.2%}；工单草案覆盖率：{system_effectiveness.work_order_coverage:.2%}",
        "",
        "## 典型案例",
        "",
        "| 案例 | 场景 | 数据文件 | 材料目录 |",
        "| --- | --- | --- | --- |",
    ]
    for index, package in enumerate(cases, start=1):
        lines.append(
            f"| 案例 {index} | {package.case_dir.parent.name} | "
            f"`{package.result.profile.source_name}` | `{package.case_dir}` |"
        )
    lines.extend(
        [
            "",
            "## 结果边界",
            "",
            "- 算法指标只在固定划分的 SKAB 验证/测试数据上成立。",
            "- 候选根因用于安排现场排查顺序，不等于设备故障确诊。",
            "- 企业数据接入后必须重新建立健康基线、校准阈值并独立评估。",
            "",
        ]
    )
    return "\n".join(lines)
