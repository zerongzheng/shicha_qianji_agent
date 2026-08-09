"""生成校赛答辩用的可复现成果包。

成果包把实验结果和典型案例放在一个固定目录中，便于制作 PPT、现场演示和复核。
它只使用 SKAB 公开数据，不把结果包装成企业现场实测成效。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.experiments.competition_report import CompetitionReport, build_competition_report
from app.reporting.case_package import CasePackage, build_case_package


@dataclass(frozen=True)
class EvidencePack:
    """一次成果包生成后的固定文件位置。"""

    output_dir: Path
    index_path: Path
    competition_report: CompetitionReport
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
    case_files = _select_case_files(Path(data_root).expanduser().resolve(), case_count)
    cases = tuple(
        build_case_package(file_path, output_dir=target / "cases")
        for file_path in case_files
    )
    index_path = target / "EVIDENCE_PACK_INDEX.md"
    index_path.write_text(
        _build_index(Path(data_root).expanduser().resolve(), competition, cases),
        encoding="utf-8",
    )
    return EvidencePack(target, index_path, competition, cases)


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
    seen_parents: set[str] = set()
    for path in candidates:
        if path.parent.name not in seen_parents:
            selected.append(path)
            seen_parents.add(path.parent.name)
        if len(selected) >= max(1, count):
            return selected
    for path in candidates:
        if path not in selected:
            selected.append(path)
        if len(selected) >= max(1, count):
            break
    return selected


def _build_index(
    data_root: Path,
    competition: CompetitionReport,
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
        "2. 再展示典型案例中的风险图，说明异常如何从数据中被发现。",
        "3. 打开案例摘要，沿着“异常事件 - 主导传感器 - 候选原因 - 排查动作”讲解。",
        "4. 回到 Streamlit 的“运维闭环”，演示工单确认、现场反馈和历史案例沉淀。",
        "",
        "## 实验材料",
        "",
        f"- Markdown 汇总：`{competition.report_path}`",
        f"- CSV 汇总：`{competition.csv_path}`",
        f"- 数据划分：`{competition.split_path}`",
        f"- 最终评估说明：`{competition.report_path.parent / 'FINAL_EVALUATION.md'}`",
        f"- 能力对比表：`{competition.report_path.parent / 'CAPABILITY_COMPARISON.md'}`",
        "",
        "## 典型案例",
        "",
        "| 案例 | 数据文件 | 材料目录 |",
        "| --- | --- | --- |",
    ]
    for index, package in enumerate(cases, start=1):
        lines.append(
            f"| 案例 {index} | `{package.result.profile.source_name}` | `{package.case_dir}` |"
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
