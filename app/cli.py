"""命令行入口，覆盖单文件、批量分析和全量基准实验。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.analysis import analyze_file, analyze_folder
from app.analysis.detection import DETECTOR_RECOMMENDED_THRESHOLDS
from app.config import get_settings
from app.experiments import (
    analyze_skab_system_effectiveness,
    build_competition_report,
    evaluate_detector_consensus,
    evaluate_forecast_effectiveness,
    evaluate_regime_strategy,
    run_hybrid_weight_ablation,
    run_skab_benchmark,
    run_tfr_weight_ablation,
    tune_and_evaluate,
)
from app.models import AnalysisConfig
from app.reporting.case_package import build_case_package
from app.reporting.evidence_pack import build_evidence_pack


def main() -> None:
    """解析命令行参数并调用统一分析流程。"""

    settings = get_settings()
    parser = argparse.ArgumentParser(description="时察千机工业时序分析与实验工具")
    parser.add_argument("--file", default=str(settings.default_skab_file), help="单个 CSV 路径")
    parser.add_argument("--dir", default="", help="批量分析目录；填写后优先于 --file")
    parser.add_argument("--max-files", type=int, default=0, help="批量文件上限，0 表示全部")
    parser.add_argument(
        "--detector",
        choices=[
            "mad",
            "isolation_forest",
            "pca_reconstruction",
            "window_autoencoder",
            "time_frequency_relation",
            "hybrid",
        ],
        default=settings.anomaly_detector,
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="告警阈值；不填写时使用所选检测器在验证集冻结的推荐阈值",
    )
    parser.add_argument("--window", type=int, default=settings.rolling_window)
    parser.add_argument("--min-event-length", type=int, default=settings.min_event_length)
    parser.add_argument("--contamination", type=float, default=settings.contamination)
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="运行 SKAB 全场景 MAD、Isolation Forest、PCA 重构和混合检测器对比实验",
    )
    parser.add_argument(
        "--tune",
        action="store_true",
        help="在验证集调优阈值，并在独立测试集生成最终实验报告",
    )
    parser.add_argument(
        "--ablate-hybrid",
        action="store_true",
        help="在验证集比较 Hybrid 融合权重，并用冻结配置运行独立测试",
    )
    parser.add_argument(
        "--ablate-tfr",
        action="store_true",
        help="比较时域、频域和关系路径组合，并用冻结配置运行独立测试",
    )
    parser.add_argument(
        "--evaluate-consensus",
        action="store_true",
        help="在固定独立测试集比较单模型与四模型严格多数共识",
    )
    parser.add_argument(
        "--evaluate-forecast",
        action="store_true",
        help="评价 SKAB 时间尾段预测和受控退化场景提前预警成效",
    )
    parser.add_argument(
        "--evaluate-regimes",
        action="store_true",
        help="在固定验证/测试划分上评价工况识别和过渡期弱告警抑制",
    )
    parser.add_argument(
        "--competition-report",
        action="store_true",
        help="生成校赛阶段 SKAB 全量实验汇总；默认复用最近产物",
    )
    parser.add_argument(
        "--rerun-competition-report",
        action="store_true",
        help="重新运行固定划分实验后生成校赛汇总，耗时较长",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="执行不依赖大模型和万悟的本地项目自检",
    )
    parser.add_argument(
        "--case-package",
        action="store_true",
        help="为 --file 生成典型案例 Markdown、CSV 和交互式风险图",
    )
    parser.add_argument(
        "--evidence-pack",
        action="store_true",
        help="生成校赛成果包：固定实验汇总、典型案例和答辩索引",
    )
    parser.add_argument(
        "--system-effectiveness",
        action="store_true",
        help="统计 SKAB 独立测试集上的证据、诊断和工单覆盖率",
    )
    parser.add_argument(
        "--case-count",
        type=int,
        default=3,
        help="--evidence-pack 生成的典型案例数量",
    )
    parser.add_argument(
        "--threshold-grid",
        default="2.0,2.5,3.0,3.5,4.0,4.5,5.0,5.5,6.0,7.0,8.0,9.0,10.0",
        help="--tune 使用的逗号分隔候选阈值",
    )
    parser.add_argument(
        "--data-root",
        default=str(settings.default_skab_dir.parent),
        help="基准实验扫描的 SKAB data 根目录",
    )
    args = parser.parse_args()

    threshold = (
        args.threshold
        if args.threshold is not None
        else DETECTOR_RECOMMENDED_THRESHOLDS.get(args.detector, settings.anomaly_threshold)
    )
    config = AnalysisConfig(
        detector=args.detector,
        threshold=threshold,
        rolling_window=_ensure_odd_window(args.window),
        min_event_length=args.min_event_length,
        merge_gap=settings.merge_gap,
        contamination=args.contamination,
    )

    if args.evaluate_forecast:
        evaluation = evaluate_forecast_effectiveness(
            args.data_root,
            max_files=args.max_files or None,
        )
        summary = {
            "real_record_count": len(evaluation.real_records),
            "warning_scenario_count": len(evaluation.warning_records),
            "failed_tasks": evaluation.failed_tasks,
            "real_csv_path": str(evaluation.real_csv_path),
            "warning_csv_path": str(evaluation.warning_csv_path),
            "report_path": str(evaluation.report_path),
        }
    elif args.evaluate_consensus:
        evaluation = evaluate_detector_consensus(
            args.data_root,
            max_files=args.max_files or None,
        )
        summary = {
            "record_count": len(evaluation.records),
            "failed_files": evaluation.failed_files,
            "csv_path": str(evaluation.csv_path),
            "report_path": str(evaluation.report_path),
        }
    elif args.system_effectiveness:
        effectiveness = analyze_skab_system_effectiveness(args.data_root)
        summary = {
            "detector": effectiveness.detector,
            "detector_name": effectiveness.detector_name,
            "file_count": effectiveness.file_count,
            "analyzed_file_count": effectiveness.analyzed_file_count,
            "total_rows": effectiveness.total_rows,
            "total_events": effectiveness.total_events,
            "evidence_coverage": effectiveness.evidence_coverage,
            "diagnosis_coverage": effectiveness.diagnosis_coverage,
            "work_order_coverage": effectiveness.work_order_coverage,
            "average_inference_seconds": effectiveness.average_inference_seconds,
            "csv_path": str(effectiveness.csv_path),
            "report_path": str(effectiveness.report_path),
            "failed_files": effectiveness.failed_files,
        }
    elif args.evidence_pack:
        pack = build_evidence_pack(
            args.data_root,
            case_count=max(1, min(10, args.case_count)),
            rerun_experiments=args.rerun_competition_report,
        )
        summary = {
            "evidence_pack_dir": str(pack.output_dir),
            "index_path": str(pack.index_path),
            "experiment_report_path": str(pack.competition_report.report_path),
            "consensus_report_path": str(pack.consensus_evaluation.report_path),
            "consensus_csv_path": str(pack.consensus_evaluation.csv_path),
            "forecast_report_path": str(pack.forecast_effectiveness.report_path),
            "forecast_csv_path": str(pack.forecast_effectiveness.real_csv_path),
            "controlled_warning_csv_path": str(pack.forecast_effectiveness.warning_csv_path),
            "false_positive_report_path": str(pack.false_positive_analysis.report_path),
            "false_positive_csv_path": str(pack.false_positive_analysis.csv_path),
            "system_effectiveness_report_path": str(pack.system_effectiveness.report_path),
            "system_effectiveness_csv_path": str(pack.system_effectiveness.csv_path),
            "case_count": len(pack.cases),
            "case_dirs": [str(item.case_dir) for item in pack.cases],
        }
    elif args.case_package:
        package = build_case_package(args.file, config=config)
        summary = {
            "case_dir": str(package.case_dir),
            "markdown_path": str(package.markdown_path),
            "events_csv_path": str(package.events_csv_path),
            "chart_html_path": str(package.chart_html_path),
            "summary_json_path": str(package.summary_json_path),
            "event_count": len(package.result.events),
        }
    elif args.check:
        summary = _run_basic_check(settings)
    elif args.competition_report or args.rerun_competition_report:
        report = build_competition_report(
            args.data_root,
            rerun_experiments=args.rerun_competition_report,
        )
        summary = {
            "report_path": str(report.report_path),
            "summary_csv_path": str(report.csv_path),
            "benchmark_path": str(report.benchmark_path),
            "split_path": str(report.split_path),
            "protocol_json_path": str(report.protocol_json_path),
            "protocol_markdown_path": str(report.protocol_markdown_path),
            "effectiveness_csv_path": str(report.effectiveness_csv_path),
        }
    elif args.evaluate_regimes:
        evaluation = evaluate_regime_strategy(args.data_root)
        summary = {
            "recommended": evaluation.recommended,
            "record_count": len(evaluation.records),
            "csv_path": str(evaluation.csv_path),
            "report_path": str(evaluation.report_path),
        }
    elif args.ablate_tfr:
        ablation = run_tfr_weight_ablation(args.data_root)
        summary = {
            "selected_weights": {
                "time": ablation.selected.time_weight,
                "frequency": ablation.selected.frequency_weight,
                "relation": ablation.selected.relation_weight,
            },
            "selected_threshold": ablation.selected.threshold,
            "validation_objective": ablation.selected.objective,
            "ablation_csv_path": str(ablation.csv_path),
            "ablation_report_path": str(ablation.report_path),
            "test_report_path": str(ablation.test_benchmark.report_path),
        }
    elif args.ablate_hybrid:
        ablation = run_hybrid_weight_ablation(args.data_root)
        summary = {
            "selected_weights": {
                "mad": ablation.selected.mad_weight,
                "isolation_forest": ablation.selected.forest_weight,
                "pca": ablation.selected.pca_weight,
            },
            "selected_threshold": ablation.selected.threshold,
            "validation_objective": ablation.selected.objective,
            "ablation_csv_path": str(ablation.csv_path),
            "ablation_report_path": str(ablation.report_path),
            "test_report_path": str(ablation.test_benchmark.report_path),
        }
    elif args.tune:
        tuning = tune_and_evaluate(
            args.data_root,
            thresholds=_parse_threshold_grid(args.threshold_grid),
        )
        summary = {
            "validation_files": len(tuning.split.validation_files),
            "test_files": len(tuning.split.test_files),
            "selected_thresholds": tuning.selected_thresholds,
            "trials_csv_path": str(tuning.trials_csv_path),
            "split_csv_path": str(tuning.split_csv_path),
            "tuning_report_path": str(tuning.report_path),
            "test_report_path": str(tuning.test_benchmark.report_path),
        }
    elif args.benchmark:
        benchmark = run_skab_benchmark(
            args.data_root,
            max_files=args.max_files or None,
        )
        summary = {
            "records": len(benchmark.records),
            "failed_tasks": benchmark.failed_tasks,
            "csv_path": str(benchmark.csv_path),
            "report_path": str(benchmark.report_path),
        }
    elif args.dir:
        batch = analyze_folder(args.dir, config=config, max_files=args.max_files or None)
        summary = {
            "source_dir": str(batch.source_dir),
            "detector": config.detector,
            "success_files": len(batch.results),
            "failed_files": batch.failed_files,
            "total_rows": batch.total_rows,
            "total_events": batch.total_events,
            "average_f1": batch.average_f1,
            "average_event_f1": batch.average_event_f1,
        }
    else:
        result = analyze_file(args.file, config=config)
        summary = result.to_summary()

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def _ensure_odd_window(window: int) -> int:
    """滚动窗口至少为 5 且使用奇数。"""

    window = max(5, window)
    return window if window % 2 == 1 else window + 1


def _parse_threshold_grid(raw_grid: str) -> tuple[float, ...]:
    """解析候选阈值并去重排序。"""

    try:
        thresholds = tuple(sorted({float(value.strip()) for value in raw_grid.split(",")}))
    except ValueError as exc:
        raise ValueError("--threshold-grid 只能包含逗号分隔的数字。") from exc
    if not thresholds or any(value <= 0 for value in thresholds):
        raise ValueError("--threshold-grid 至少包含一个大于 0 的阈值。")
    return thresholds


def _run_basic_check(settings: object) -> dict[str, object]:
    """检查校赛阶段最基本的本地运行条件，不访问外部模型接口。"""

    default_file = Path(settings.default_skab_file)
    default_dir = Path(settings.default_skab_dir)
    output_dir = Path(settings.output_dir)
    checks: dict[str, bool] = {
        "默认 SKAB 文件存在": default_file.is_file(),
        "默认 SKAB 目录存在": default_dir.is_dir(),
        "输出目录可创建": _ensure_directory(output_dir),
        "数据库目录可创建": _ensure_directory(Path(settings.database_path).parent),
    }
    errors = [name for name, passed in checks.items() if not passed]
    if not errors:
        # 只有基础条件通过后才执行一次轻量完整分析，避免给错误路径制造大量异常日志。
        result = analyze_file(
            default_file,
            write_report=False,
            run_forecast=False,
            run_regime=True,
        )
        checks["默认样例可完成核心分析"] = bool(result.profile.sensor_columns)
        checks["结果可以转换为结构化摘要"] = bool(result.to_summary())
    return {
        "status": "ok" if all(checks.values()) else "failed",
        "checks": checks,
        "errors": errors,
        "llm_enabled": bool(getattr(settings, "llm_enabled", False)),
        "message": (
            "本地核心分析可以运行；大模型和万悟属于可选外部能力。"
            if not errors
            else "请先修复失败的路径或目录权限，再运行项目。"
        ),
    }


def _ensure_directory(path: Path) -> bool:
    """创建并测试项目运行目录；不删除目录中的任何已有数据。"""

    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


if __name__ == "__main__":
    main()
