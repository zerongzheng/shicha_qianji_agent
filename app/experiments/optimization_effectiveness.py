"""受控场景中的优化建议成效实验。

实验不模拟具体企业设备，只验证建议执行框架的三个性质：风险出现后是否按上限分级干预、
稳定场景是否保持不动作、干预后是否减少标准化阈值越界暴露。所有结果均为受控机制验证，
不能写成企业节能率、故障率下降或现场收益。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

import numpy as np

from app.config import get_settings


@dataclass(frozen=True)
class OptimizationScenarioRecord:
    """一个受控场景中不干预与受约束干预的对比结果。"""

    scenario: str
    scenario_name: str
    baseline_peak_risk: float
    controlled_peak_risk: float
    baseline_exceedance_points: int
    controlled_exceedance_points: int
    exceedance_reduction: float
    baseline_cumulative_deviation: float
    controlled_cumulative_deviation: float
    deviation_reduction: float
    intervention_count: int
    maximum_adjustment: float
    maximum_cumulative_adjustment: float
    saturation_count: int
    constraint_violations: int
    rollback_count: int


@dataclass(frozen=True)
class OptimizationEffectiveness:
    """受控建议实验产物。"""

    records: list[OptimizationScenarioRecord]
    csv_path: Path
    report_path: Path


def evaluate_optimization_effectiveness(
    output_dir: str | Path | None = None,
    *,
    seed: int = 20260812,
) -> OptimizationEffectiveness:
    """运行固定退化与稳定场景并导出 CSV、Markdown。"""

    settings = get_settings()
    target = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.output_dir / "competition"
    )
    target.mkdir(parents=True, exist_ok=True)
    records = [_evaluate_scenario(*item) for item in _build_scenarios(seed)]
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    csv_path = target / f"optimization_effectiveness_{timestamp}.csv"
    report_path = target / f"optimization_effectiveness_{timestamp}.md"
    _write_csv(csv_path, records)
    report_path.write_text(build_optimization_report(records), encoding="utf-8")
    return OptimizationEffectiveness(records, csv_path, report_path)


def apply_bounded_recommendation(
    disturbance: np.ndarray,
    *,
    warning_threshold: float = 0.65,
    risk_threshold: float = 1.0,
    maximum_adjustment: float = 0.08,
    maximum_cumulative_adjustment: float = 0.45,
    gain: float = 0.55,
) -> tuple[np.ndarray, dict[str, int | float]]:
    """模拟分级、小步、可回退的建议执行，不代表真实控制器。

    `disturbance` 是未经干预的标准化偏移。每一步只能观察当前及历史状态；调整量受硬上限
    约束。若调整后风险反而连续恶化，则撤回上一步调整并进入五步观察期。
    """

    controlled = np.zeros_like(disturbance, dtype=float)
    accumulated_adjustment = 0.0
    interventions = 0
    saturations = 0
    violations = 0
    rollbacks = 0
    cooldown = 0
    previous_abs = 0.0
    worsening_steps = 0

    for index, external_state in enumerate(np.asarray(disturbance, dtype=float)):
        observed = float(external_state + accumulated_adjustment)
        if cooldown:
            cooldown -= 1
        elif abs(observed) >= warning_threshold:
            requested = -gain * observed
            adjustment = float(np.clip(requested, -maximum_adjustment, maximum_adjustment))
            violations += int(abs(adjustment) > maximum_adjustment + 1e-12)
            requested_total = accumulated_adjustment + adjustment
            bounded_total = float(
                np.clip(
                    requested_total,
                    -maximum_cumulative_adjustment,
                    maximum_cumulative_adjustment,
                )
            )
            saturations += int(abs(requested_total - bounded_total) > 1e-12)
            accumulated_adjustment = bounded_total
            interventions += 1
            observed = float(external_state + accumulated_adjustment)

        current_abs = abs(observed)
        worsening_steps = worsening_steps + 1 if current_abs > previous_abs + 0.04 else 0
        if worsening_steps >= 3 and interventions:
            # 撤回最近一个最大允许步长，随后暂停干预观察，体现建议中的回退条件。
            accumulated_adjustment *= 0.5
            observed = float(external_state + accumulated_adjustment)
            rollbacks += 1
            cooldown = 5
            worsening_steps = 0
        controlled[index] = observed
        previous_abs = abs(observed)

    return controlled, {
        "intervention_count": interventions,
        "maximum_adjustment": maximum_adjustment,
        "maximum_cumulative_adjustment": maximum_cumulative_adjustment,
        "saturation_count": saturations,
        "constraint_violations": violations,
        "rollback_count": rollbacks,
        "risk_threshold": risk_threshold,
    }


def build_optimization_report(records: list[OptimizationScenarioRecord]) -> str:
    """生成竞赛材料可引用且边界明确的机制实验报告。"""

    risk_records = [item for item in records if item.baseline_exceedance_points > 0]
    stable = [item for item in records if item.baseline_exceedance_points == 0]
    lines = [
        "# 受约束优化建议机制实验",
        "",
        "> 本实验使用标准化受控轨迹验证建议执行框架，不代表企业设备控制效果、节能率或经济收益。",
        "",
        "## 实验规则",
        "",
        "- 每一步只使用当前及历史状态，不读取未来轨迹。",
        "- 风险进入观察区后采用小步调整，单次调整绝对值不超过 0.08，累计调整绝对值不超过 0.45。",
        "- 连续恶化时撤回部分调整并进入观察期，不持续盲目加码。",
        "- 稳定对照用于检查系统是否在没有风险时产生不必要动作。",
        "",
        "| 场景 | 基线越界点 | 干预后越界点 | 越界减少 | 累计偏移减少 | 干预次数 | 累计边界触发 | 回退次数 | 约束违规 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in records:
        lines.append(
            f"| {item.scenario_name} | {item.baseline_exceedance_points} | "
            f"{item.controlled_exceedance_points} | {item.exceedance_reduction:.2%} | "
            f"{item.deviation_reduction:.2%} | {item.intervention_count} | "
            f"{item.saturation_count} | {item.rollback_count} | {item.constraint_violations} |"
        )
    lines.extend(
        [
            "",
            "## 汇总结论",
            "",
            f"- 风险场景平均越界暴露减少：{mean(item.exceedance_reduction for item in risk_records):.2%}。",
            f"- 风险场景平均累计偏移减少：{mean(item.deviation_reduction for item in risk_records):.2%}。",
            f"- 稳定对照干预次数：{sum(item.intervention_count for item in stable)}。",
            f"- 全部场景调整约束违规：{sum(item.constraint_violations for item in records)} 次。",
            "",
            "## 使用边界",
            "",
            "- 本实验只证明受约束建议框架在已知模拟轨迹上的机制完整性。",
            "- 企业设备的控制变量、联锁逻辑、允许步长和观察周期必须由企业确认。",
            "- 项目当前不会向真实设备直接下发控制指令，所有建议均为人工确认草案。",
            "- 企业数据到位后应开展同工况 A/B 或前后对照，评价真实风险、能耗、质量和节拍。",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_scenarios(seed: int) -> list[tuple[str, str, np.ndarray]]:
    rng = np.random.default_rng(seed)
    length = 260
    time_axis = np.arange(length, dtype=float)
    start = 100
    progress = np.clip((time_axis - start) / (length - start - 1), 0.0, 1.0)
    return [
        ("gradual_up", "渐进上升偏移", 1.55 * progress + rng.normal(0, 0.025, length)),
        ("gradual_down", "渐进下降偏移", -1.55 * progress + rng.normal(0, 0.025, length)),
        ("accelerated", "加速退化", 1.75 * progress**2 + rng.normal(0, 0.025, length)),
        (
            "cyclic_drift",
            "周期扰动叠加漂移",
            1.45 * progress + 0.08 * np.sin(2 * np.pi * time_axis / 25) + rng.normal(0, 0.02, length),
        ),
        ("stable_control", "稳定无风险对照", rng.normal(0, 0.035, length)),
    ]


def _evaluate_scenario(
    scenario: str,
    scenario_name: str,
    baseline: np.ndarray,
) -> OptimizationScenarioRecord:
    threshold = 1.0
    controlled, audit = apply_bounded_recommendation(baseline, risk_threshold=threshold)
    baseline_exceedance = int(np.sum(np.abs(baseline) >= threshold))
    controlled_exceedance = int(np.sum(np.abs(controlled) >= threshold))
    baseline_deviation = float(np.sum(np.abs(baseline)))
    controlled_deviation = float(np.sum(np.abs(controlled)))
    return OptimizationScenarioRecord(
        scenario=scenario,
        scenario_name=scenario_name,
        baseline_peak_risk=round(float(np.max(np.abs(baseline))), 6),
        controlled_peak_risk=round(float(np.max(np.abs(controlled))), 6),
        baseline_exceedance_points=baseline_exceedance,
        controlled_exceedance_points=controlled_exceedance,
        exceedance_reduction=_reduction(baseline_exceedance, controlled_exceedance),
        baseline_cumulative_deviation=round(baseline_deviation, 6),
        controlled_cumulative_deviation=round(controlled_deviation, 6),
        deviation_reduction=_reduction(baseline_deviation, controlled_deviation),
        intervention_count=int(audit["intervention_count"]),
        maximum_adjustment=float(audit["maximum_adjustment"]),
        maximum_cumulative_adjustment=float(audit["maximum_cumulative_adjustment"]),
        saturation_count=int(audit["saturation_count"]),
        constraint_violations=int(audit["constraint_violations"]),
        rollback_count=int(audit["rollback_count"]),
    )


def _reduction(baseline: float, controlled: float) -> float:
    if baseline <= 0:
        return 0.0
    return round((baseline - controlled) / baseline, 6)


def _write_csv(path: Path, records: list[OptimizationScenarioRecord]) -> None:
    fields = list(OptimizationScenarioRecord.__dataclass_fields__)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(record.__dict__)
