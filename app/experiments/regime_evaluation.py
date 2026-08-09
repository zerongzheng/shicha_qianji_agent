"""SKAB 无监督工况识别与过渡期抑制专项评测。

`changepoint` 和 `anomaly` 只在本模块中作为事后评价标签，绝不会进入工况识别算法。实验先
比较验证集默认告警与过渡期弱告警抑制，再在独立测试集报告冻结策略，避免测试集调参。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

from app.analysis.pipeline import analyze_file
from app.config import get_settings
from app.experiments.split import ExperimentSplit, build_skab_split
from app.models import AnalysisConfig


@dataclass(frozen=True)
class RegimeEvaluationRecord:
    """一个文件在一种策略下的工况与告警指标。"""

    split: str
    scenario: str
    file_name: str
    strategy: str
    state_count: int
    transition_points: int
    changepoint_count: int
    changepoint_recall: float
    transition_precision: float
    event_recall: float
    event_f1: float
    false_positive_events: int
    suppressed_events: int


@dataclass(frozen=True)
class RegimeEvaluationResult:
    """专项实验输出。"""

    split: ExperimentSplit
    records: list[RegimeEvaluationRecord]
    recommended: bool
    csv_path: Path
    report_path: Path


def evaluate_regime_strategy(
    data_root: str | Path,
    output_dir: str | Path | None = None,
) -> RegimeEvaluationResult:
    """在固定验证/测试划分上评价工况识别与可选抑制。"""

    root = Path(data_root).expanduser().resolve()
    split = build_skab_split(root)
    settings = get_settings()
    target_dir = Path(output_dir).resolve() if output_dir else settings.output_dir / "experiments"
    target_dir.mkdir(parents=True, exist_ok=True)

    records: list[RegimeEvaluationRecord] = []
    for split_name, files in (
        ("validation", split.validation_files),
        ("test", split.test_files),
    ):
        for file_path in files:
            records.extend(_evaluate_file(file_path, split_name))

    recommended = _strategy_is_recommended(records, split_name="validation")
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    csv_path = target_dir / f"regime_evaluation_{timestamp}.csv"
    report_path = target_dir / f"regime_evaluation_{timestamp}.md"
    _write_csv(records, csv_path)
    report_path.write_text(
        _build_report(records, recommended, root),
        encoding="utf-8",
    )
    return RegimeEvaluationResult(split, records, recommended, csv_path, report_path)


def _evaluate_file(file_path: Path, split_name: str) -> list[RegimeEvaluationRecord]:
    """同一文件分别运行仅解释模式和抑制模式。"""

    records: list[RegimeEvaluationRecord] = []
    for strategy, suppress in (("context_only", False), ("transition_suppression", True)):
        config = AnalysisConfig(
            detector="window_autoencoder",
            threshold=5.5,
            suppress_transition_events=suppress,
        )
        result = analyze_file(
            file_path,
            config=config,
            write_report=False,
            run_forecast=False,
            run_regime=True,
        )
        metrics = result.metrics
        regimes = result.operating_regimes
        if metrics is None or regimes is None:
            raise ValueError(f"工况评测缺少标签或工况结果：{file_path}")
        changepoint_recall, transition_precision, changepoint_count = _transition_metrics(
            result.dataframe,
            regimes.transition_mask,
        )
        records.append(
            RegimeEvaluationRecord(
                split=split_name,
                scenario=file_path.parent.name,
                file_name=file_path.name,
                strategy=strategy,
                state_count=regimes.state_count,
                transition_points=int(regimes.transition_mask.sum()),
                changepoint_count=changepoint_count,
                changepoint_recall=changepoint_recall,
                transition_precision=transition_precision,
                event_recall=metrics.event_recall,
                event_f1=metrics.event_f1_score,
                false_positive_events=metrics.false_positive_event_count,
                suppressed_events=regimes.suppressed_event_count,
            )
        )
    return records


def _transition_metrics(
    dataframe,
    transition_mask,
    tolerance: int = 5,
) -> tuple[float, float, int]:
    """评价无监督过渡区对 changepoint 的覆盖和点级纯度。"""

    changepoints = (
        list(
            dataframe.index[
                dataframe["changepoint"].fillna(0).astype(float) > 0
            ]
        )
        if "changepoint" in dataframe
        else []
    )
    predicted = list(transition_mask.index[transition_mask.astype(bool)])
    if not changepoints:
        return 1.0 if not predicted else 0.0, 0.0, 0
    matched = sum(
        any(abs(int(predicted_index) - int(changepoint)) <= tolerance for predicted_index in predicted)
        for changepoint in changepoints
    )
    near_changepoint = sum(
        any(abs(int(predicted_index) - int(changepoint)) <= tolerance for changepoint in changepoints)
        for predicted_index in predicted
    )
    recall = matched / len(changepoints)
    precision = near_changepoint / len(predicted) if predicted else 0.0
    return recall, precision, len(changepoints)


def _strategy_is_recommended(
    records: list[RegimeEvaluationRecord],
    split_name: str,
) -> bool:
    """工业策略必须保持事件召回，同时改善事件级 F1 并减少误报。"""

    base = [item for item in records if item.split == split_name and item.strategy == "context_only"]
    candidate = [
        item
        for item in records
        if item.split == split_name and item.strategy == "transition_suppression"
    ]
    if not base or len(base) != len(candidate):
        return False
    return (
        _average(candidate, "event_recall") >= _average(base, "event_recall")
        and _average(candidate, "event_f1") >= _average(base, "event_f1")
        and _average(candidate, "false_positive_events")
        < _average(base, "false_positive_events")
    )


def _build_report(
    records: list[RegimeEvaluationRecord],
    recommended: bool,
    data_root: Path,
) -> str:
    """生成验证集决策和独立测试结果。"""

    lines = [
        "# SKAB 工况识别与过渡期抑制评测",
        "",
        f"> 数据目录：`{data_root}`",
        "> changepoint 仅用于事后评价，未进入无监督工况算法。",
        "",
    ]
    for split_name, title in (("validation", "验证集"), ("test", "独立测试集")):
        lines.extend(
            [
                f"## {title}",
                "",
                "| 策略 | 文件数 | 变点召回 | 过渡点精度 | 事件召回 | 事件级 F1 | 平均误报事件 | 平均抑制事件 |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        split_records = [item for item in records if item.split == split_name]
        for strategy in ("context_only", "transition_suppression"):
            selected = [item for item in split_records if item.strategy == strategy]
            lines.append(
                f"| {strategy} | {len(selected)} | "
                f"{_average(selected, 'changepoint_recall'):.4f} | "
                f"{_average(selected, 'transition_precision'):.4f} | "
                f"{_average(selected, 'event_recall'):.4f} | "
                f"{_average(selected, 'event_f1'):.4f} | "
                f"{_average(selected, 'false_positive_events'):.2f} | "
                f"{_average(selected, 'suppressed_events'):.2f} |"
            )
        lines.append("")

    decision = (
        "验证集满足召回不下降、事件级 F1 不下降且误报减少，可进入独立测试观察；"
        "仍需人工确认后才能修改产品默认配置。"
        if recommended
        else "验证集未同时满足召回、事件级 F1 和误报约束，默认保持仅解释模式，不启用抑制。"
    )
    lines.extend(["## 决策", "", decision, ""])
    return "\n".join(lines)


def _write_csv(records: list[RegimeEvaluationRecord], path: Path) -> None:
    """保存逐文件实验数据。"""

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(RegimeEvaluationRecord.__dataclass_fields__))
        writer.writeheader()
        for record in records:
            writer.writerow(record.__dict__)


def _average(records: list[RegimeEvaluationRecord], field: str) -> float:
    """计算记录字段平均值。"""

    values = [float(getattr(record, field)) for record in records]
    return mean(values) if values else 0.0
