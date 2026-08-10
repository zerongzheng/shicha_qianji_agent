"""模型基准测试与竞赛实验管理。"""

from app.experiments.benchmark import run_skab_benchmark
from app.experiments.competition_report import build_competition_report
from app.experiments.false_positive_analysis import analyze_skab_false_positives, audit_result
from app.experiments.hybrid_ablation import run_hybrid_weight_ablation
from app.experiments.protocol import build_protocol_manifest, write_protocol_artifacts
from app.experiments.regime_evaluation import evaluate_regime_strategy
from app.experiments.split import build_skab_split
from app.experiments.system_effectiveness import (
    SystemEffectiveness,
    analyze_skab_system_effectiveness,
)
from app.experiments.tfr_ablation import run_tfr_weight_ablation
from app.experiments.tuning import tune_and_evaluate

__all__ = [
    "SystemEffectiveness",
    "analyze_skab_false_positives",
    "analyze_skab_system_effectiveness",
    "audit_result",
    "build_competition_report",
    "build_protocol_manifest",
    "build_skab_split",
    "evaluate_regime_strategy",
    "run_hybrid_weight_ablation",
    "run_skab_benchmark",
    "run_tfr_weight_ablation",
    "tune_and_evaluate",
    "write_protocol_artifacts",
]
