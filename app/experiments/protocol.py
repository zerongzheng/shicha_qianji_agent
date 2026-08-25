"""生成可追溯的 SKAB 实验协议。

实验结果只有在“数据怎么划分、参数怎么冻结、文件有没有被替换”都能说明白时，
才适合写进竞赛材料。本模块不运行模型，只负责把一次实验使用的数据清单、文件
哈希、评价口径和冻结参数保存下来，便于复现和答辩核验。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.experiments.split import ExperimentSplit, build_skab_split

PROTOCOL_VERSION = "skab-competition-v6-joint-parameter-tuning"


@dataclass(frozen=True)
class ProtocolFile:
    """实验清单中的一个数据文件。"""

    split: str
    scenario: str
    relative_path: str
    size_bytes: int
    sha256: str


def build_protocol_manifest(
    data_root: str | Path,
    split: ExperimentSplit | None = None,
    *,
    selected_thresholds: dict[str, float] | None = None,
    selected_event_policies: dict[str, dict[str, int]] | None = None,
    detectors: tuple[str, ...] = (),
) -> dict[str, Any]:
    """创建一次 SKAB 实验的机器可读协议清单。"""

    root = Path(data_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"找不到 SKAB 数据目录：{root}")
    split = split or build_skab_split(root)

    files: list[ProtocolFile] = []
    for split_name, paths in (
        ("healthy", split.healthy_files),
        ("validation", split.validation_files),
        ("test", split.test_files),
    ):
        for path in paths:
            resolved = path.expanduser().resolve()
            if not resolved.is_file():
                raise FileNotFoundError(f"实验文件不存在：{resolved}")
            files.append(
                ProtocolFile(
                    split=split_name,
                    scenario=resolved.parent.name,
                    relative_path=resolved.relative_to(root).as_posix(),
                    size_bytes=resolved.stat().st_size,
                    sha256=_sha256(resolved),
                )
            )

    return {
        "protocol_version": PROTOCOL_VERSION,
        "dataset": "SKAB",
        "dataset_root_label": "外部 SKAB/data 目录",
        "split_rule": {
            "healthy": "anomaly-free 目录仅用于无监督健康基线和健康误报约束",
            "validation": "有标签场景按自然排序取偶数位置文件",
            "test": "有标签场景按自然排序取奇数位置文件；单文件场景直接进入测试集",
            "unit": "按完整 CSV 文件划分，不拆分连续时序采样点",
        },
        "label_semantics": {
            "point_metrics": "anomaly 列按采样点评价",
            "event_metrics": "连续异常点按 merge_gap 合并后，按 event_tolerance 匹配",
            "changepoint": "仅用于统计变点附近误报，不作为删除告警的依据",
        },
        "preprocessing_policy": {
            "time_axis": "检测明显不规则采样或设备配置周期不符时，按固定时间网格对齐",
            "sensor_aggregation": "同一时间网格内传感器值取均值",
            "label_aggregation": (
                "同一时间网格内标签取最大值；anomaly/label/target 等持续状态标签对重采样新增点"
                "继承前一观测状态，changepoint 等瞬时标签新增点填 0"
            ),
            "missing_values": "短缺口线性插值，长缺口局部延续，剩余边界使用历史中位数兜底",
            "spike_policy": "保留原始尖峰，不执行无条件平滑",
            "normalization": "缩放器只由对应模型在训练或历史窗口内拟合，避免未来信息泄漏",
        },
        "event_policy": {
            "selection": "阈值、最短事件长度和合并间隔在验证集联合选择，独立测试集不参与调参",
            "deployment_boundary": "参数以采样点计，企业接入后必须按真实采样周期重新标定",
        },
        "files": [asdict(item) for item in files],
        "counts": {
            "healthy": len(split.healthy_files),
            "validation": len(split.validation_files),
            "test": len(split.test_files),
            "total": len(files),
        },
        "detectors": list(detectors),
        "frozen_thresholds": {
            key: float(value) for key, value in (selected_thresholds or {}).items()
        },
        "frozen_event_policies": {
            detector: {
                "min_event_length": int(policy["min_event_length"]),
                "merge_gap": int(policy["merge_gap"]),
            }
            for detector, policy in (selected_event_policies or {}).items()
        },
        "disclosure": [
            "当前为校赛阶段公开数据验证，不等同于联通企业现场实测成效。",
            "企业数据接入后需要重新建立健康基线、校准阈值并使用独立时间段复测。",
        ],
    }


def write_protocol_artifacts(
    manifest: dict[str, Any],
    output_dir: str | Path,
    *,
    json_name: str = "SKAB_EXPERIMENT_PROTOCOL.json",
    markdown_name: str = "SKAB_EXPERIMENT_PROTOCOL.md",
) -> tuple[Path, Path]:
    """把协议同时写成 JSON 和中文 Markdown，方便程序读取及竞赛材料引用。"""

    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / json_name
    markdown_path = target / markdown_name
    json_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(_build_markdown(manifest), encoding="utf-8")
    return json_path, markdown_path


def read_frozen_thresholds(output_dir: str | Path) -> dict[str, float]:
    """读取当前协议冻结阈值；无协议、旧协议或损坏文件均返回空字典。"""

    path = Path(output_dir).expanduser().resolve() / "SKAB_EXPERIMENT_PROTOCOL.json"
    if not path.is_file():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        return {}
    thresholds = manifest.get("frozen_thresholds", {})
    if not isinstance(thresholds, dict):
        return {}
    parsed: dict[str, float] = {}
    for detector, value in thresholds.items():
        try:
            parsed[str(detector)] = float(value)
        except (TypeError, ValueError):
            continue
    return parsed


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """分块计算文件哈希，避免把较大的 CSV 一次性读入内存。"""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _build_markdown(manifest: dict[str, Any]) -> str:
    """生成适合放入实验附件的协议说明。"""

    counts = manifest["counts"]
    lines = [
        "# SKAB 实验协议",
        "",
        f"> 协议版本：`{manifest['protocol_version']}`",
        "> 数据集：SKAB 公开工业时序数据",
        "> 当前用途：校赛阶段算法流程和工程闭环验证",
        "",
        "## 数据划分",
        "",
        f"- 健康基线：{counts['healthy']} 个文件",
        f"- 验证集：{counts['validation']} 个文件",
        f"- 独立测试集：{counts['test']} 个文件",
        f"- 文件总数：{counts['total']} 个",
        "- 按完整 CSV 文件划分，不把同一条连续时序拆到不同集合。",
        "- 健康文件不参与最终模型排名，只用于健康误报约束。",
        "",
        "## 评价口径",
        "",
        "- 点级 F1、PR-AUC：评价采样点识别和异常排序能力。",
        "- 事件级 F1、事件召回：评价完整故障事件是否被发现，以及告警是否碎片化。",
        "- 平均误报事件、变点附近误报占比：评价告警可用性。",
        "- 单文件推理耗时：只做同一环境下的相对工程效率比较。",
        "",
        "## 固定预处理口径",
        "",
        *[f"- {value}" for value in manifest["preprocessing_policy"].values()],
        "",
        "## 参数冻结",
        "",
    ]
    thresholds = manifest.get("frozen_thresholds") or {}
    if thresholds:
        lines.extend(f"- `{name}`：阈值 `{value:.2f}`" for name, value in thresholds.items())
    else:
        lines.append("- 本协议尚未写入冻结阈值，请先运行阈值调优实验。")
    lines.extend(
        [
            "",
            "## 事件策略",
            "",
            *[f"- {value}" for value in manifest["event_policy"].values()],
        ]
    )
    policies = manifest.get("frozen_event_policies") or {}
    if policies:
        lines.extend(
            f"- `{detector}`：最短事件 `{policy['min_event_length']}`，"
            f"合并间隔 `{policy['merge_gap']}`"
            for detector, policy in policies.items()
        )
    lines.extend(["", "## 文件校验清单", "", "| 集合 | 场景 | 文件 | SHA-256（前 16 位） |", "| --- | --- | --- | --- |"])
    for item in manifest["files"]:
        lines.append(
            f"| {item['split']} | {item['scenario']} | `{item['relative_path']}` | "
            f"`{item['sha256'][:16]}` |"
        )
    lines.extend(["", "## 结果边界", "", *[f"- {item}" for item in manifest["disclosure"]], ""])
    return "\n".join(lines)
