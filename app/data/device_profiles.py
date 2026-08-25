"""设备数据契约与字段标准化。

公开数据集和企业现场导出的 CSV 往往使用不同的列名、单位和采样约定。本模块把这些
差异收敛到可版本化的 JSON 设备配置中，使异常检测、预测和诊断模块始终面对统一字段。
未匹配到任何配置时，系统会回退到原有通用 CSV 模式，不阻断临时数据分析。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import get_settings


@dataclass(frozen=True)
class SensorDefinition:
    """单个标准测点的业务含义，用于页面解释和后续设备专属诊断。"""

    name: str
    display_name: str
    unit: str | None = None
    category: str = "其他"
    safe_range: tuple[float | None, float | None] | None = None
    description: str = ""


@dataclass(frozen=True)
class DeviceProfile:
    """一类设备的数据契约，不保存原始数据或企业敏感信息。"""

    profile_id: str
    version: str
    display_name: str
    device_type: str
    enabled: bool
    auto_match: bool
    time_column: str
    label_columns: tuple[str, ...]
    required_columns: tuple[str, ...]
    optional_columns: tuple[str, ...]
    match_columns: tuple[str, ...]
    minimum_match_count: int
    column_aliases: dict[str, str]
    sensors: dict[str, SensorDefinition]
    expected_sampling_seconds: float | None = None
    healthy_baseline_path: str | None = None
    recommended_analysis: dict[str, Any] = field(default_factory=dict)
    scope: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def canonical_name(self, column: str) -> str:
        """将一个原始列名转换为配置约定的标准列名。"""

        aliases = {
            _normalize_column_name(source): target
            for source, target in self.column_aliases.items()
        }
        normalized = _normalize_column_name(column)
        return aliases.get(normalized, str(column).strip())

    def resolve_healthy_baseline(self, project_root: Path) -> Path | None:
        """把配置中的相对基线路径解释为相对于项目根目录的路径。"""

        if not self.healthy_baseline_path:
            return None
        path = Path(self.healthy_baseline_path).expanduser()
        return path.resolve() if path.is_absolute() else (project_root / path).resolve()

    def public_summary(self) -> dict[str, Any]:
        """生成可安全返回给前端和系统诊断接口的配置摘要。"""

        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "display_name": self.display_name,
            "device_type": self.device_type,
            "enabled": self.enabled,
            "auto_match": self.auto_match,
            "required_columns": list(self.required_columns),
            "optional_columns": list(self.optional_columns),
            "expected_sampling_seconds": self.expected_sampling_seconds,
            "recommended_analysis": self.recommended_analysis,
            "scope": list(self.scope),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class DeviceProfileSelection:
    """某份 CSV 与设备配置的匹配结果。"""

    profile: DeviceProfile | None
    match_mode: str
    match_score: float


@dataclass(frozen=True)
class LoadedTimeSeries:
    """标准化数据、设备配置和可追溯上下文的组合结果。"""

    dataframe: pd.DataFrame
    profile: DeviceProfile | None
    context: dict[str, Any]


def load_device_profiles(directory: Path | None = None) -> dict[str, DeviceProfile]:
    """读取目录中的全部设备 JSON，并检查编号是否重复。"""

    profile_dir = directory or get_settings().device_profiles_dir
    if not profile_dir.exists():
        return {}

    profiles: dict[str, DeviceProfile] = {}
    for path in sorted(profile_dir.glob("*.json")):
        profile = _parse_device_profile(path)
        if profile.profile_id in profiles:
            raise ValueError(f"设备配置编号重复：{profile.profile_id}")
        profiles[profile.profile_id] = profile
    return profiles


def get_device_profile(
    profile_id: str,
    directory: Path | None = None,
) -> DeviceProfile:
    """按编号读取设备配置；显式指定错误时立即报错，避免套用错误设备规则。"""

    profiles = load_device_profiles(directory)
    profile = profiles.get(profile_id)
    if profile is None:
        available = "、".join(sorted(profiles)) or "无"
        raise ValueError(f"找不到设备配置 {profile_id!r}，当前可用配置：{available}")
    if not profile.enabled:
        raise ValueError(f"设备配置 {profile_id!r} 是模板或已停用，不能用于正式分析")
    return profile


def match_device_profile(
    columns: list[str] | pd.Index,
    directory: Path | None = None,
) -> DeviceProfileSelection:
    """按特征列自动选择最匹配的启用配置，未命中时返回通用模式。"""

    best_profile: DeviceProfile | None = None
    best_score = 0.0
    for profile in load_device_profiles(directory).values():
        if not profile.enabled or not profile.auto_match or not profile.match_columns:
            continue
        canonical_columns = {
            _normalize_column_name(profile.canonical_name(column)) for column in columns
        }
        matched = sum(
            _normalize_column_name(column) in canonical_columns
            for column in profile.match_columns
        )
        score = matched / len(profile.match_columns)
        if matched >= profile.minimum_match_count and score > best_score:
            best_profile = profile
            best_score = score

    if best_profile is None:
        return DeviceProfileSelection(profile=None, match_mode="generic", match_score=0.0)
    return DeviceProfileSelection(
        profile=best_profile,
        match_mode="automatic",
        match_score=best_score,
    )


def select_device_profile(
    columns: list[str] | pd.Index,
    profile_id: str | None = None,
    directory: Path | None = None,
) -> DeviceProfileSelection:
    """优先使用显式配置，否则根据 CSV 表头自动匹配。"""

    if profile_id == "generic":
        return DeviceProfileSelection(
            profile=None,
            match_mode="explicit_generic",
            match_score=0.0,
        )
    if profile_id:
        return DeviceProfileSelection(
            profile=get_device_profile(profile_id, directory),
            match_mode="explicit",
            match_score=1.0,
        )
    return match_device_profile(columns, directory)


def apply_device_profile(
    dataframe: pd.DataFrame,
    profile: DeviceProfile,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """执行字段别名映射并校验必需字段，返回标准化副本和重命名记录。"""

    renamed: dict[str, str] = {}
    target_sources: dict[str, str] = {}
    for source in dataframe.columns:
        target = profile.canonical_name(str(source))
        normalized_target = _normalize_column_name(target)
        previous = target_sources.get(normalized_target)
        if previous is not None and previous != source:
            raise ValueError(
                f"字段 {previous!r} 和 {source!r} 都映射为 {target!r}，请检查设备配置别名"
            )
        target_sources[normalized_target] = str(source)
        if str(source) != target:
            renamed[str(source)] = target

    standardized = dataframe.rename(columns=renamed).copy()
    available = {_normalize_column_name(column) for column in standardized.columns}
    missing = [
        column
        for column in profile.required_columns
        if _normalize_column_name(column) not in available
    ]
    if missing:
        raise ValueError(
            f"设备配置 {profile.profile_id!r} 缺少必需字段：{'、'.join(missing)}"
        )
    return standardized, renamed


def build_device_context(
    profile: DeviceProfile | None,
    match_mode: str,
    match_score: float,
    renamed_columns: dict[str, str] | None = None,
) -> dict[str, Any]:
    """构造分析结果中的设备配置追踪信息，不暴露本地绝对路径。"""

    if profile is None:
        return {
            "profile_id": None,
            "version": None,
            "display_name": "通用工业时序数据",
            "device_type": "未指定设备",
            "match_mode": match_mode,
            "match_score": 0.0,
            "renamed_columns": {},
            "expected_sampling_seconds": None,
            "sensor_metadata": {},
            "recommended_analysis": {},
            "healthy_baseline_configured": False,
            "scope": ["通用多变量 CSV 初步分析"],
            "limitations": ["尚未绑定设备专属字段、单位、安全边界和健康基线。"],
        }

    return {
        "profile_id": profile.profile_id,
        "version": profile.version,
        "display_name": profile.display_name,
        "device_type": profile.device_type,
        "match_mode": match_mode,
        "match_score": round(match_score, 4),
        "renamed_columns": renamed_columns or {},
        "expected_sampling_seconds": profile.expected_sampling_seconds,
        "sensor_metadata": {
            name: {
                "display_name": sensor.display_name,
                "unit": sensor.unit,
                "category": sensor.category,
                "safe_range": list(sensor.safe_range) if sensor.safe_range else None,
                "description": sensor.description,
            }
            for name, sensor in profile.sensors.items()
        },
        "recommended_analysis": profile.recommended_analysis,
        "healthy_baseline_configured": bool(profile.healthy_baseline_path),
        "scope": list(profile.scope),
        "limitations": list(profile.limitations),
    }


def _parse_device_profile(path: Path) -> DeviceProfile:
    """把一份 JSON 转换为强类型配置，并在启动阶段暴露格式错误。"""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取设备配置 {path.name}：{exc}") from exc

    required_keys = {"profile_id", "version", "display_name", "device_type", "schema"}
    missing_keys = required_keys - payload.keys()
    if missing_keys:
        raise ValueError(f"设备配置 {path.name} 缺少字段：{'、'.join(sorted(missing_keys))}")

    schema = payload["schema"]
    match = payload.get("match", {})
    sensors = {
        name: SensorDefinition(
            name=name,
            display_name=str(detail.get("display_name", name)),
            unit=detail.get("unit"),
            category=str(detail.get("category", "其他")),
            safe_range=_parse_safe_range(detail.get("safe_range")),
            description=str(detail.get("description", "")),
        )
        for name, detail in payload.get("sensors", {}).items()
    }
    match_columns = tuple(match.get("columns", schema.get("required_columns", [])))
    minimum_match_count = int(match.get("minimum_match_count", len(match_columns)))
    if match_columns and not 1 <= minimum_match_count <= len(match_columns):
        raise ValueError(f"设备配置 {path.name} 的 minimum_match_count 超出有效范围")

    return DeviceProfile(
        profile_id=str(payload["profile_id"]),
        version=str(payload["version"]),
        display_name=str(payload["display_name"]),
        device_type=str(payload["device_type"]),
        enabled=bool(payload.get("enabled", True)),
        auto_match=bool(payload.get("auto_match", True)),
        time_column=str(schema.get("time_column", "datetime")),
        label_columns=tuple(schema.get("label_columns", [])),
        required_columns=tuple(schema.get("required_columns", [])),
        optional_columns=tuple(schema.get("optional_columns", [])),
        match_columns=match_columns,
        minimum_match_count=minimum_match_count,
        column_aliases={
            str(source): str(target)
            for source, target in payload.get("column_aliases", {}).items()
        },
        sensors=sensors,
        expected_sampling_seconds=payload.get("expected_sampling_seconds"),
        healthy_baseline_path=payload.get("healthy_baseline_path"),
        recommended_analysis=dict(payload.get("recommended_analysis", {})),
        scope=tuple(payload.get("scope", [])),
        limitations=tuple(payload.get("limitations", [])),
    )


def _parse_safe_range(value: Any) -> tuple[float | None, float | None] | None:
    """校验可选安全范围；未知边界使用 null，而不是编造设备阈值。"""

    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("传感器 safe_range 必须是 [下限, 上限] 或 null")
    lower = float(value[0]) if value[0] is not None else None
    upper = float(value[1]) if value[1] is not None else None
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("传感器 safe_range 下限不能大于上限")
    return lower, upper


def _normalize_column_name(value: str) -> str:
    """忽略大小写、空格、下划线和连字符比较字段名。"""

    return "".join(character for character in str(value).casefold() if character.isalnum())
