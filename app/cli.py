"""命令行入口，覆盖单文件、批量分析和全量基准实验。"""

from __future__ import annotations

import argparse
import json

from app.analysis import analyze_file, analyze_folder
from app.config import get_settings
from app.experiments import run_skab_benchmark, tune_and_evaluate
from app.models import AnalysisConfig


def main() -> None:
    """解析命令行参数并调用统一分析流程。"""

    settings = get_settings()
    parser = argparse.ArgumentParser(description="时察千机工业时序分析与实验工具")
    parser.add_argument("--file", default=str(settings.default_skab_file), help="单个 CSV 路径")
    parser.add_argument("--dir", default="", help="批量分析目录；填写后优先于 --file")
    parser.add_argument("--max-files", type=int, default=0, help="批量文件上限，0 表示全部")
    parser.add_argument(
        "--detector",
        choices=["mad", "isolation_forest", "hybrid"],
        default=settings.anomaly_detector,
    )
    parser.add_argument("--threshold", type=float, default=settings.anomaly_threshold)
    parser.add_argument("--window", type=int, default=settings.rolling_window)
    parser.add_argument("--min-event-length", type=int, default=settings.min_event_length)
    parser.add_argument("--contamination", type=float, default=settings.contamination)
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="运行 SKAB 全场景 MAD、Isolation Forest、混合检测器对比实验",
    )
    parser.add_argument(
        "--tune",
        action="store_true",
        help="在验证集调优阈值，并在独立测试集生成最终实验报告",
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

    config = AnalysisConfig(
        detector=args.detector,
        threshold=args.threshold,
        rolling_window=_ensure_odd_window(args.window),
        min_event_length=args.min_event_length,
        merge_gap=settings.merge_gap,
        contamination=args.contamination,
    )

    if args.tune:
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


if __name__ == "__main__":
    main()
