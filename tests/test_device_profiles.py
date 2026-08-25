"""设备配置和企业字段契约测试。

这些测试不依赖 SKAB 实体文件，保证配置层在数据集未随 GitHub 仓库提交时也能回归。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app.data.device_profiles import (
    apply_device_profile,
    get_device_profile,
    load_device_profiles,
    match_device_profile,
    select_device_profile,
)
from app.data.loader import load_time_series_with_context


def test_repository_profiles_include_skab_and_disabled_enterprise_template() -> None:
    """仓库应同时提供可运行的 SKAB 配置和不应误用的企业模板。"""

    profiles = load_device_profiles()
    assert profiles["skab_valve"].enabled is True
    assert profiles["enterprise_template"].enabled is False
    recommended = profiles["skab_valve"].recommended_analysis
    assert recommended["threshold"] == 3.5
    assert recommended["min_event_length"] == 12
    assert recommended["merge_gap"] == 30


def test_skab_header_is_matched_automatically() -> None:
    """SKAB 特征字段足够时应自动匹配阀门测试台配置。"""

    selection = match_device_profile(
        [
            "datetime",
            "Accelerometer1RMS",
            "Accelerometer2RMS",
            "Current",
            "Pressure",
            "Temperature",
            "Thermocouple",
            "Voltage",
            "Volume Flow RateRMS",
            "anomaly",
        ]
    )
    assert selection.profile is not None
    assert selection.profile.profile_id == "skab_valve"
    assert selection.match_mode == "automatic"
    assert selection.match_score == 1.0


def test_generic_mode_disables_automatic_matching() -> None:
    """用户明确选择通用模式后，即使表头像 SKAB 也不能自动套用设备配置。"""

    selection = select_device_profile(
        [
            "datetime",
            "Accelerometer1RMS",
            "Accelerometer2RMS",
            "Current",
            "Pressure",
            "Thermocouple",
            "Volume Flow RateRMS",
        ],
        "generic",
    )
    assert selection.profile is None
    assert selection.match_mode == "explicit_generic"


def test_explicit_profile_maps_enterprise_aliases_and_rejects_missing_fields(tmp_path: Path) -> None:
    """显式企业配置应完成别名映射，并对缺失必需字段给出明确错误。"""

    profile_path = Path("resources/device_profiles/enterprise_template.json")
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["enabled"] = True
    profile_file = tmp_path / "enterprise.json"
    profile_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    profile = get_device_profile("enterprise_template", tmp_path)
    dataframe = pd.DataFrame(
        {"采集时间": ["2026-01-01 00:00:00"], "压力值": [1.2], "流量值": [3.4]}
    )
    standardized, renamed = apply_device_profile(dataframe, profile)
    assert {"datetime", "Pressure", "FlowRate"}.issubset(standardized.columns)
    assert renamed["采集时间"] == "datetime"

    with pytest.raises(ValueError, match="缺少必需字段"):
        apply_device_profile(dataframe.drop(columns=["流量值"]), profile)


def test_unmatched_csv_uses_generic_context_and_explicit_profile_is_traceable(tmp_path: Path) -> None:
    """未知 CSV 继续可分析，标准配置分析则返回可追溯上下文。"""

    generic = tmp_path / "generic.csv"
    generic.write_text(
        "time,SensorA,SensorB\n2026-01-01 00:00:00,1,2\n2026-01-01 00:00:01,2,3\n",
        encoding="utf-8",
    )
    generic_loaded = load_time_series_with_context(generic)
    assert generic_loaded.profile is None
    assert generic_loaded.context["match_mode"] == "generic"

    skab = tmp_path / "skab.csv"
    skab.write_text(
        "datetime;Accelerometer1RMS;Accelerometer2RMS;Current;Pressure;Temperature;Thermocouple;Voltage;Volume Flow RateRMS\n"
        "2026-01-01 00:00:00;1;1;1;1;1;1;1;1\n"
        "2026-01-01 00:00:01;1;1;1;1;1;1;1;1\n",
        encoding="utf-8",
    )
    configured = load_time_series_with_context(skab, "skab_valve")
    assert configured.context["profile_id"] == "skab_valve"
    assert configured.context["match_mode"] == "explicit"
