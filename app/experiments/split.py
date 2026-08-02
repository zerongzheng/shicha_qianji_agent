"""SKAB 实验数据划分。

健康基线仅用于无监督检测器的标定，不参与阈值选择和最终排名。有异常标签的文件按场景
分别排序后交错划分，确保 valve1、valve2、other 都同时出现在验证集和测试集中。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExperimentSplit:
    """一次可复现实验使用的文件清单。"""

    healthy_files: tuple[Path, ...]
    validation_files: tuple[Path, ...]
    test_files: tuple[Path, ...]


def build_skab_split(data_root: str | Path) -> ExperimentSplit:
    """按场景生成确定性的健康集、验证集和测试集。

    规则说明：
    - `anomaly-free` 目录只作为健康参考；
    - 每个有标签场景自然排序后，偶数位置进入验证集，奇数位置进入测试集；
    - 单文件场景进入测试集，避免用唯一文件调参后再汇报同一文件。

    这里按文件划分而非随机切分采样点，可以避免同一条连续时序同时出现在验证和测试中。
    """

    root = Path(data_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"找不到 SKAB 数据目录：{root}")

    healthy_files: list[Path] = []
    validation_files: list[Path] = []
    test_files: list[Path] = []

    scenario_dirs = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda p: p.name)
    for scenario_dir in scenario_dirs:
        files = sorted(scenario_dir.glob("*.csv"), key=_natural_key)
        if scenario_dir.name.lower() == "anomaly-free":
            healthy_files.extend(files)
            continue
        if len(files) == 1:
            test_files.extend(files)
            continue
        validation_files.extend(files[::2])
        test_files.extend(files[1::2])

    if not validation_files:
        raise ValueError("没有可用于阈值调优的验证文件。")
    if not test_files:
        raise ValueError("没有可用于独立评估的测试文件。")
    return ExperimentSplit(
        healthy_files=tuple(healthy_files),
        validation_files=tuple(validation_files),
        test_files=tuple(test_files),
    )


def describe_split(split: ExperimentSplit, data_root: str | Path) -> list[dict[str, str]]:
    """把划分结果转换为便于写入 CSV 的明细。"""

    root = Path(data_root).expanduser().resolve()
    rows: list[dict[str, str]] = []
    for split_name, files in (
        ("healthy", split.healthy_files),
        ("validation", split.validation_files),
        ("test", split.test_files),
    ):
        for file_path in files:
            rows.append(
                {
                    "split": split_name,
                    "scenario": file_path.parent.name,
                    "file": file_path.relative_to(root).as_posix(),
                }
            )
    return rows


def _natural_key(path: Path) -> tuple[int, str]:
    """让数字文件名按数值顺序排列。"""

    return (int(path.stem), path.name) if path.stem.isdigit() else (10**9, path.name)
