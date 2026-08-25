#!/usr/bin/env python3
"""投放一份 SKAB 样本到无人值守监测目录。"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def numeric_name(path: Path) -> tuple[int, str]:
    try:
        return int(path.stem), path.name
    except ValueError:
        return sys.maxsize, path.name


def detect_delimiter(header: str) -> str:
    return ";" if header.count(";") > header.count(",") else ","


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def replay_sample(source: Path, destination: Path, offset: timedelta) -> None:
    text = source.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    delimiter = detect_delimiter(lines[0] if lines else "")
    rows = list(csv.DictReader(lines, delimiter=delimiter))
    if not rows:
        raise RuntimeError(f"重放 CSV 为空: {source}")

    fieldnames = list(rows[0])
    time_field = next(
        (name for name in fieldnames if name.lower() in {"datetime", "timestamp", "time"}),
        None,
    )
    if time_field is None:
        raise RuntimeError(f"重放 CSV 缺少时间列: {source}")
    for row in rows:
        row[time_field] = (parse_timestamp(row[time_field]) + offset).isoformat(sep=" ")

    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def read_state(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"模拟器状态文件无法读取: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"模拟器状态文件格式错误: {path}")
    return value


def write_state(path: Path, state: dict[str, object], staging: Path) -> None:
    temporary = staging / f".{path.name}.{os.getpid()}.partial"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def emit_sample(
    source: Path,
    target: Path,
    staging: Path,
    index: int,
    replay_offset: timedelta | None,
) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    destination = target / f"batch_{index:03d}_{timestamp}_{source.name}"
    temporary = staging / f".{destination.name}.partial"
    if replay_offset is None:
        shutil.copyfile(source, temporary)
    else:
        replay_sample(source, temporary, replay_offset)
    os.replace(temporary, destination)
    print(f"已投放 SKAB 样本 {source.name} -> {destination}")
    return destination


def build_parser() -> argparse.ArgumentParser:
    root = project_root()
    parser = argparse.ArgumentParser(
        description="向时察千机监测目录投放下一份 SKAB 样本"
    )
    parser.add_argument(
        "--source-directory",
        type=Path,
        default=root.parent / "SKAB" / "data" / "valve1",
    )
    parser.add_argument(
        "--target-directory",
        type=Path,
        default=root / "outputs" / "demo_feed" / "skab_valve1",
    )
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--run-once", action="store_true", help="只投放一份样本")
    parser.add_argument("--replay", action="store_true", help="重置进度并从第一份样本开始")
    parser.add_argument("--prepare-only", action="store_true", help="只创建目标目录")
    parser.add_argument(
        "--trigger-autonomous-workflow",
        action="store_true",
        help="投放后立即调用一次无人值守万悟工作流",
    )
    parser.add_argument(
        "--autonomous-workflow-config",
        type=Path,
        default=root / "outputs" / "wanwu_autonomous_workflow.local.json",
        help="无人值守工作流配置文件",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.interval_seconds < 10:
        raise SystemExit("--interval-seconds 不能小于 10 秒")
    if args.trigger_autonomous_workflow and not args.run_once:
        raise SystemExit(
            "--trigger-autonomous-workflow 只能与 --run-once 一起使用，避免连续重复通知"
        )
    root = project_root()
    source = args.source_directory.resolve()
    target = args.target_directory.resolve()
    # staging 与目标目录放在同一父目录，os.replace 才能跨平台保持原子移动。
    staging = target.parent / ".skab_feed_staging"
    state_path = target / ".feed_state.json"
    if not source.is_dir():
        raise SystemExit(f"SKAB 原始目录不存在: {source}")
    target.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    if args.prepare_only:
        print(f"演示监测目录已准备: {target}")
        return 0

    samples = sorted(source.glob("*.csv"), key=numeric_name)
    if not samples:
        raise SystemExit(f"SKAB 原始目录没有 CSV: {source}")
    state = {} if args.replay else read_state(state_path)
    if args.replay:
        print("已重置模拟器进度；历史 CSV 和数据库记录未删除。")
    try:
        next_index = int(state.get("next_index", 0))
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"模拟器 next_index 无效: {state.get('next_index')}") from exc

    replay_offset: timedelta | None = None
    if args.replay:
        replay_offset = timedelta(milliseconds=int(time.time() * 1000) % 3_600_000)
    elif state.get("replay_offset_milliseconds"):
        replay_offset = timedelta(milliseconds=int(state["replay_offset_milliseconds"]))

    while True:
        if next_index >= len(samples):
            print("SKAB 样本已经全部投放完毕；没有新的演示批次。")
            return 0
        sample = samples[next_index]
        emit_sample(sample, target, staging, next_index, replay_offset)
        next_index += 1
        write_state(
            state_path,
            {
                "next_index": next_index,
                "last_emitted": sample.name,
                "emitted_at": datetime.now().astimezone().isoformat(),
                "replay_offset_milliseconds": (
                    int(replay_offset.total_seconds() * 1000) if replay_offset else 0
                ),
            },
            staging,
        )
        if args.trigger_autonomous_workflow:
            config = args.autonomous_workflow_config.resolve()
            trigger_script = root / "wanwu" / "scripts" / "trigger_wanwu_workflow.sh"
            if not config.is_file():
                raise SystemExit(f"无人值守工作流配置不存在: {config}")
            if not trigger_script.is_file():
                raise SystemExit(f"万悟工作流触发脚本不存在: {trigger_script}")
            print("已投放新批次，立即调用万悟无人值守巡检工作流。")
            result = subprocess.run(
                [
                    "bash",
                    str(trigger_script),
                    "--config",
                    str(config),
                    "--run-once",
                ],
                check=False,
            )
            if result.returncode != 0:
                raise SystemExit(
                    f"万悟无人值守巡检触发失败，退出码: {result.returncode}"
                )
        if args.run_once:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
