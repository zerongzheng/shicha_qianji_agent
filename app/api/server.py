"""时察千机 REST API。

接口使用受控 `file_id`，不接受任意服务器路径。万悟可通过 API 节点调用这些接口；本地
Streamlit 仍然直接调用分析核心，方便开发阶段离线运行。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import psycopg

from app.analysis.detection import (
    DETECTOR_RECOMMENDED_THRESHOLDS,
    recommended_event_policy,
)
from app.analysis.forecast import MODEL_LABELS, forecast_sensors
from app.analysis.pipeline import analyze_file
from app.analysis.profiling import build_profile
from app.api.jobs import JobQueueFullError, get_job_manager
from app.api.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    ArchiveRequest,
    DataSourceRequest,
    ErrorResponse,
    FilePreflightResponse,
    FileUploadResponse,
    ForecastCompareRequest,
    JobAcceptedResponse,
    JobCancelledResponse,
    JobCreateRequest,
    JobResultResponse,
    JobStatusResponse,
    LoginRequest,
    LoginResponse,
    ModelCompareRequest,
    NotificationAcknowledgeRequest,
    RunIdRequest,
    WanwuCaseListRequest,
    WanwuJobAcceptedResponse,
    WanwuJobCreateRequest,
    WanwuQuickDiagnosisRequest,
    WanwuQuickDiagnosisResponse,
    WanwuWorkOrderListRequest,
    WanwuWorkOrderUpdateRequest,
    WorkOrderAssignmentRequest,
    WorkOrderUpdateRequest,
)
from app.api.wanwu_openapi import build_wanwu_openapi
from app.automation import MonitoringService, dispatch_run_notifications
from app.config import get_settings
from app.data.device_profiles import load_device_profiles
from app.data.loader import (
    _detect_delimiter,
    load_time_series,
    load_time_series_with_context,
)
from app.diagnosis import AutomaticDiagnosisService, diagnosis_to_dict
from app.integrations import receive_wanwu_csv
from app.model_store import list_autoencoder_models
from app.models import (
    TFR_RECOMMENDED_FREQUENCY_WEIGHT,
    TFR_RECOMMENDED_RELATION_WEIGHT,
    TFR_RECOMMENDED_TIME_WEIGHT,
    AnalysisConfig,
)
from app.observability import RunLogger
from app.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from app.storage import get_repository

try:
    from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
except ImportError:  # 让未安装 API 依赖时，核心分析和 Streamlit 仍可用。
    FastAPI = None
    Depends = File = Header = UploadFile = Any
    CORSMiddleware = None


settings = get_settings()
UPLOAD_DIR = settings.output_dir / "api_uploads"
# 快速诊断会按文件哈希复用历史结果。每当分析管线或返回证据发生实质升级时必须递增
# 此版本，防止同一份演示 CSV 长期命中旧结果而看不到新能力。
QUICK_DIAGNOSIS_VERSION = "0.8.0"
_monitoring_service: MonitoringService | None = None


@asynccontextmanager
async def _lifespan(_app: Any):
    """启动时清理中断任务，关闭时等待后台线程完成。"""

    global _monitoring_service
    repository = get_repository()
    _ensure_bootstrap_users(repository)
    repository.fail_incomplete_runs("服务重启导致任务中断，请重新提交")
    monitor = get_monitoring_service()
    repository = get_repository()
    enabled_sources = (
        repository.list_data_sources(enabled_only=True)
        if hasattr(repository, "list_data_sources")
        else []
    )
    if getattr(settings, "automatic_monitor_enabled", False) or enabled_sources:
        monitor.start()
    try:
        yield
    finally:
        monitor.stop()
        _monitoring_service = None
        cancelled_run_ids = get_job_manager().shutdown()
        get_job_manager.cache_clear()
        repository = get_repository()
        for run_id in cancelled_run_ids:
            repository.cancel_run(run_id, "服务关闭取消了尚未执行的排队任务")
        repository.fail_incomplete_runs("服务关闭导致任务中断，请重新提交")


def get_monitoring_service() -> MonitoringService:
    """返回当前进程唯一的自动监测调度器。"""

    global _monitoring_service
    if _monitoring_service is None:
        default_storage = settings.output_dir / "auto_ingestion"
        _monitoring_service = MonitoringService(
            get_repository(),
            getattr(
                settings,
                "automatic_monitor_storage_dir",
                default_storage,
            ),
            _submit_automatic_ingestion,
            max_bytes=getattr(settings, "max_upload_bytes", 25 * 1024 * 1024),
            tick_seconds=getattr(settings, "automatic_monitor_tick_seconds", 1.0),
        )
    return _monitoring_service


app = (
    FastAPI(
        title="时察千机工业时序分析服务",
        version="0.5.0",
        description="工业多变量时序异常检测、预测、根因诊断与运维工单服务。",
        servers=[
            {
                "url": settings.api_public_base_url,
                "description": "时察千机工业分析服务",
            }
        ],
        lifespan=_lifespan,
    )
    if FastAPI
    else None
)

if app is not None and CORSMiddleware is not None:
    # Vue 开发服务器与 FastAPI 端口不同，需要显式允许本地前端访问 API。
    # 生产环境应通过环境变量限制为实际部署域名，而不是长期放开所有来源。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in settings.frontend_allowed_origins.split(",")
            if origin.strip()
        ],
        # Vite 在 5173 被占用时会自动选择 5174、5175 等端口。本地开发只允许回环主机，
        # 但不固定端口，避免前端页面能打开却被浏览器 CORS 拦截 API 请求。
        allow_origin_regex=r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?$",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )


def _check_api_key(api_key: str | None) -> None:
    """只有配置了服务密钥时才启用鉴权，便于本地开发。"""

    expected = getattr(settings, "industrial_api_key", "")
    if expected and not secrets.compare_digest(api_key or "", expected):
        raise HTTPException(status_code=401, detail="工业分析服务鉴权失败")


def _ensure_bootstrap_users(repository: Any) -> None:
    """用环境变量首次初始化校赛演示人员，永不把明文密码写入仓库。"""

    password = settings.auth_bootstrap_password
    if not password:
        return
    accounts = (
        ("admin", "系统管理员", "系统管理员"),
        ("production", "生产负责人", "生产负责人"),
        ("engineer", "设备工程师", "设备工程师"),
        ("operator", "运行值班员", "运行值班员"),
        ("observer", "观察人员", "观察人员"),
    )
    for username, display_name, role in accounts:
        if repository.get_user_by_username(username) is None:
            repository.upsert_user(
                username=username,
                display_name=display_name,
                role=role,
                password_hash=hash_password(password),
            )


def _bearer_token(authorization: str | None) -> str:
    """严格解析 Bearer 会话令牌，避免把其他认证头误当作登录态。"""

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="请先登录时察千机工作台")
    return token.strip()


def _current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """解析当前人员；关闭本地认证时返回兼容身份，方便算法与万悟独立联调。"""

    if not settings.auth_enabled:
        return {
            "user_id": None,
            "username": "local",
            "display_name": "本地调试用户",
            "role": "系统管理员",
            "active": True,
        }
    token = _bearer_token(authorization)
    user = get_repository().get_user_by_session(hash_session_token(token))
    if user is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return user


def _optional_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any] | None:
    """读取可选登录态，供同时支持浏览器人员和万悟 API Key 的接口使用。"""

    if not settings.auth_enabled:
        return _current_user(authorization)
    if not authorization:
        return None
    return _current_user(authorization)


def _check_user_or_api_key(user: dict[str, Any] | None, api_key: str | None) -> None:
    """人工操作必须来自已登录人员，或来自配置了有效密钥的万悟工作流。"""

    if user is not None:
        return
    expected = getattr(settings, "industrial_api_key", "")
    if expected and secrets.compare_digest(api_key or "", expected):
        return
    raise HTTPException(status_code=401, detail="请先登录，或使用有效的工业服务 API Key")


def _require_roles(*roles: str):
    """生成角色权限依赖，当前只保护人员管理和工单指派等人工操作。"""

    def dependency(user: Annotated[dict[str, Any], Depends(_current_user)]) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="当前账号没有执行此操作的权限")
        return user

    return dependency


def _parse_config(payload: dict[str, Any] | None) -> AnalysisConfig:
    """将万悟工作流传入的可选参数限制在项目支持范围内。"""

    payload = payload or {}
    selection_mode = str(
        payload.get(
            "detector_selection_mode",
            "manual" if "detector" in payload else "auto",
        )
    ).strip().lower()
    if selection_mode not in {"auto", "manual"}:
        raise ValueError("detector_selection_mode 只能是 auto 或 manual")
    detector = str(payload.get("detector", settings.anomaly_detector))
    default_threshold = DETECTOR_RECOMMENDED_THRESHOLDS.get(
        detector,
        settings.anomaly_threshold,
    )
    default_min_event_length, default_merge_gap = recommended_event_policy(detector)
    return AnalysisConfig(
        device_profile_id=(
            None
            if not str(payload.get("device_profile_id", "")).strip()
            else str(payload["device_profile_id"]).strip()
        ),
        detector_selection_mode=selection_mode,
        analysis_goal=str(payload.get("analysis_goal", "balanced")),
        detector=detector,
        threshold=float(payload.get("threshold", default_threshold)),
        rolling_window=max(5, int(payload.get("rolling_window", settings.rolling_window))),
        min_event_length=max(
            1,
            int(payload.get("min_event_length", default_min_event_length)),
        ),
        merge_gap=max(0, int(payload.get("merge_gap", default_merge_gap))),
        contamination=float(payload.get("contamination", settings.contamination)),
        hybrid_mad_weight=float(payload.get("hybrid_mad_weight", 0.50)),
        hybrid_forest_weight=float(payload.get("hybrid_forest_weight", 0.30)),
        hybrid_pca_weight=float(payload.get("hybrid_pca_weight", 0.20)),
        autoencoder_window=max(4, int(payload.get("autoencoder_window", 16))),
        autoencoder_hidden=max(8, int(payload.get("autoencoder_hidden", 24))),
        autoencoder_bottleneck=max(2, int(payload.get("autoencoder_bottleneck", 6))),
        autoencoder_max_iter=max(50, int(payload.get("autoencoder_max_iter", 250))),
        autoencoder_max_training_windows=max(
            100,
            int(payload.get("autoencoder_max_training_windows", 3000)),
        ),
        tfr_time_weight=float(
            payload.get("tfr_time_weight", TFR_RECOMMENDED_TIME_WEIGHT)
        ),
        tfr_frequency_weight=float(
            payload.get("tfr_frequency_weight", TFR_RECOMMENDED_FREQUENCY_WEIGHT)
        ),
        tfr_relation_weight=float(
            payload.get("tfr_relation_weight", TFR_RECOMMENDED_RELATION_WEIGHT)
        ),
        tfr_frequency_components=max(1, int(payload.get("tfr_frequency_components", 8))),
        tfr_relation_components=max(1, int(payload.get("tfr_relation_components", 4))),
        suppress_transition_events=_parse_bool(
            payload.get("suppress_transition_events", False)
        ),
        regime_window=max(5, int(payload.get("regime_window", 31))),
        regime_max_states=max(1, min(6, int(payload.get("regime_max_states", 4)))),
        regime_transition_quantile=float(payload.get("regime_transition_quantile", 0.98)),
        regime_suppression_overlap=float(payload.get("regime_suppression_overlap", 0.75)),
        regime_suppression_peak_ratio=float(
            payload.get("regime_suppression_peak_ratio", 1.35)
        ),
    )


def _parse_request_config(payload: dict[str, Any] | None) -> AnalysisConfig:
    """把万悟参数格式错误转换为明确的 400 响应。"""

    try:
        return _parse_config(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"分析参数不合法：{exc}") from exc


def _parse_bool(value: Any) -> bool:
    """避免字符串 "false" 被 Python 误判为 True。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    raise ValueError("布尔配置只能使用 true/false、1/0 或 yes/no。")


def _validate_data_source(payload: DataSourceRequest) -> None:
    """在保存前验证数据源端点，避免轮询线程长期重复报告固定配置错误。"""

    if payload.source_type == "directory":
        directory = Path(payload.endpoint).expanduser().resolve()
        if not directory.is_dir():
            raise HTTPException(status_code=400, detail=f"监控目录不存在：{directory}")
        return
    parsed = urlparse(payload.endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="HTTP 数据源必须是有效的 http/https 地址")


def _public_data_source(source: dict[str, Any]) -> dict[str, Any]:
    """返回前端需要的配置和状态，不回显请求头或通知密钥。"""

    public_source = {
        key: value
        for key, value in source.items()
        if key not in {"request_headers"}
    }
    # 兼容旧数据：历史 routing 中可能保存过 webhook_url，任何 API 都不得将其回传浏览器。
    routing = dict(public_source.get("routing") or {})
    routing.pop("webhook_url", None)
    public_source["routing"] = routing
    public_source["request_header_count"] = len(source.get("request_headers") or {})
    return public_source


def _result_payload(run_id: str, result: Any) -> dict[str, Any]:
    """返回稳定的机器可读协议，避免万悟依赖 Markdown 文本解析。"""

    summary = result.to_summary()
    stored_run = get_repository().get_run(run_id)
    stored_config = stored_run.get("config", {}) if stored_run else {}
    threshold = float(
        result.model_selection.get(
            "selected_threshold",
            stored_config.get("threshold", 3.5),
        )
    )
    return {
        "run_id": run_id,
        "status": "success",
        "device_profile": result.device_context,
        "data_profile": {
            "source_name": result.profile.source_name,
            "row_count": result.profile.row_count,
            "sensor_columns": result.profile.sensor_columns,
            "missing_total": result.profile.missing_total,
            "sampling_seconds": result.profile.sampling_seconds,
            "label_columns": result.profile.label_columns,
            "start_time": result.profile.start_time,
            "end_time": result.profile.end_time,
        },
        "data_quality": {
            "missing_total": result.profile.missing_total,
            "missing_rate": round(
                result.profile.missing_total
                / max(result.profile.row_count * len(result.profile.sensor_columns), 1),
                6,
            ),
            "sampling_seconds": result.profile.sampling_seconds,
            "label_columns": result.profile.label_columns,
            "sensor_profiles": [
                {
                    "sensor": item.name,
                    "missing_count": item.missing_count,
                    "missing_rate": round(item.missing_rate, 6),
                    "minimum": round(item.min_value, 6),
                    "maximum": round(item.max_value, 6),
                    "mean": round(item.mean_value, 6),
                    "std": round(item.std_value, 6),
                }
                for item in result.profile.sensors[:12]
            ],
        },
        "raw_data_profile": (
            {
                "row_count": result.raw_profile.row_count,
                "missing_total": result.raw_profile.missing_total,
                "sampling_seconds": result.raw_profile.sampling_seconds,
            }
            if result.raw_profile
            else None
        ),
        "preprocessing": result.preprocessing,
        "detector": result.detector_name,
        "visualization": _visualization_payload(result, threshold=threshold),
        "anomaly_events": [event.__dict__ for event in result.events],
        "model_selection": result.model_selection,
        "detector_validation": result.detector_validation,
        "operating_regimes": (
            {
                "state_count": result.operating_regimes.state_count,
                "segments": result.operating_regimes.segments,
                "event_contexts": result.operating_regimes.event_contexts,
                "suppression_applied": result.operating_regimes.suppression_applied,
                "suppressed_event_count": result.operating_regimes.suppressed_event_count,
            }
            if result.operating_regimes
            else None
        ),
        "relationship_diagnostics": result.relationship_diagnostics,
        "root_cause_diagnoses": [
            diagnosis_to_dict(item) for item in result.event_diagnoses
        ],
        "historical_case_matches": {
            str(event_number): [asdict(item) for item in matches]
            for event_number, matches in result.historical_case_matches.items()
        },
        "work_order_drafts": [
            {
                **asdict(item),
                # 算法工单编号在不同任务中可能重复，数据库记录编号加入 run_id 命名空间。
                "record_id": f"{run_id}:{item.work_order_id}",
            }
            for item in result.work_order_drafts
        ],
        "forecast_results": result.forecast_results,
        "risk_alerts": result.risk_alerts,
        "recommendations": result.recommendations,
        "optimization_recommendations": [
            asdict(item) for item in result.optimization_recommendations
        ],
        "execution_trace": [asdict(item) for item in result.execution_trace],
        "summary": summary,
        "limitations": [
            "预测模型由滚动回测自动选择，仍需结合现场工况、设备边界和人工复核确认。",
            "候选根因来自内置通用故障模式，不是企业设备专属知识，不能替代现场确诊。",
            "无 anomaly 标签的企业数据不计算监督指标。",
        ],
    }


def _visualization_payload(
    result: Any,
    max_points: int = 360,
    threshold: float | None = None,
) -> dict[str, Any]:
    """生成前端图表需要的有限采样数据。

    分析结果内部保留完整 DataFrame，但 HTTP 响应不应直接返回整份原始数据。这里按
    时间顺序均匀抽样，同时保留异常区间的相对位置，让 Vue 可以直接绘制风险曲线和
    传感器趋势图；完整 CSV 仍保存在后端受控目录中。
    """

    dataframe = result.dataframe
    row_count = len(dataframe)
    if row_count == 0:
        return {
            "timestamps": [],
            "series": {},
            "risk_scores": [],
            "anomaly_labels": [],
            "event_ranges": [],
            "threshold": threshold,
        }

    point_count = min(max(60, int(max_points)), row_count)
    if point_count == 1:
        sample_indexes = [0]
    else:
        sample_indexes = sorted(
            {
                round(index * (row_count - 1) / (point_count - 1))
                for index in range(point_count)
            }
        )

    def json_number(value: Any) -> float | None:
        """把 NumPy 标量和缺失值转换成浏览器可安全读取的数字。"""

        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return round(number, 6) if isfinite(number) else None

    timestamps = [
        item.isoformat() if hasattr(item, "isoformat") else str(item)
        for item in dataframe.iloc[sample_indexes]["datetime"]
    ]
    series = {
        sensor: [
            json_number(value)
            for value in dataframe.iloc[sample_indexes][sensor]
        ]
        for sensor in result.profile.sensor_columns[:8]
    }
    risk_scores = [json_number(result.combined_score.iloc[index]) for index in sample_indexes]
    anomaly_labels = [int(bool(result.predicted_labels.iloc[index])) for index in sample_indexes]

    event_ranges = [
        {
            "event_number": index,
            "start_ratio": round(event.start_index / max(row_count - 1, 1), 6),
            "end_ratio": round(event.end_index / max(row_count - 1, 1), 6),
            "severity": event.severity,
        }
        for index, event in enumerate(result.events[:12], start=1)
    ]

    contribution_scores: dict[str, float] = {}
    for event in result.events:
        for sensor, score in event.sensor_scores.items():
            contribution_scores[sensor] = contribution_scores.get(sensor, 0.0) + float(score)
    if not contribution_scores and not result.anomaly_scores.empty:
        contribution_scores = {
            str(sensor): float(result.anomaly_scores[sensor].max())
            for sensor in result.anomaly_scores.columns
        }
    sensor_contributions = [
        {"sensor": sensor, "score": round(score, 4)}
        for sensor, score in sorted(
            contribution_scores.items(), key=lambda item: item[1], reverse=True
        )[:8]
    ]
    return {
        "sample_indexes": sample_indexes,
        "row_count": row_count,
        "sensor_columns": list(series),
        "timestamps": timestamps,
        "series": series,
        "risk_scores": risk_scores,
        "anomaly_labels": anomaly_labels,
        "threshold": threshold,
        "event_ranges": event_ranges,
        "sensor_contributions": sensor_contributions,
    }


def _find_uploaded_file(file_id: str) -> Path:
    """校验 file_id、定位 CSV，并把历史上传目录补登记到数据库。"""

    if not file_id or Path(file_id).name != file_id:
        raise HTTPException(status_code=400, detail="必须提供合法 file_id")
    # 默认 SKAB 样例不复制到上传目录，只允许解析这一枚由样例接口签发的固定 ID。
    sample_file_id = f"sample_{settings.default_skab_file.stem}"
    if file_id == sample_file_id and settings.default_skab_file.exists():
        get_repository().register_file(
            file_id,
            settings.default_skab_file.name,
            settings.default_skab_file,
        )
        return settings.default_skab_file
    matches = list((UPLOAD_DIR / file_id).glob("*.csv"))
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail="找不到对应上传文件")
    get_repository().register_file(file_id, matches[0].name, matches[0])
    return matches[0]


def _store_uploaded_csv(file_name: str, content: bytes) -> tuple[str, Path, dict[str, Any]]:
    """把已校验 CSV 写入受控目录并登记哈希，供所有上传入口复用。"""

    if not content:
        raise HTTPException(status_code=400, detail="CSV 文件内容不能为空")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"CSV 文件超过 {settings.max_upload_bytes} 字节限制",
        )
    safe_name = Path(file_name).name
    if not safe_name.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="当前只允许上传 CSV 文件")
    file_id = uuid.uuid4().hex
    target_dir = UPLOAD_DIR / file_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name
    target_path.write_bytes(content)
    metadata = get_repository().register_file(file_id, safe_name, target_path)
    return file_id, target_path, metadata


def _register_sample_file(source_path: Path) -> dict[str, Any]:
    """登记项目内置样例并返回与上传接口一致的文件元数据。"""

    if not source_path.exists() or source_path.suffix.lower() != ".csv":
        raise HTTPException(status_code=404, detail="找不到默认 SKAB 样例文件")
    file_id = f"sample_{source_path.stem}"
    metadata = get_repository().register_file(file_id, source_path.name, source_path)
    return {
        "file_id": file_id,
        "file_name": source_path.name,
        "sha256": metadata["sha256"],
        "size_bytes": metadata["size_bytes"],
    }


def _build_file_preflight(file_id: str, source_path: Path) -> dict[str, Any]:
    """读取受控 CSV 的真实画像，统一服务于默认样例和已上传文件。"""

    try:
        loaded = load_time_series_with_context(source_path)
        dataframe = loaded.dataframe
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    profile = build_profile(dataframe, source_path.name)
    missing_rate = profile.missing_total / max(profile.row_count * len(profile.sensor_columns), 1)
    warnings: list[str] = []
    if len(profile.sensor_columns) < 2:
        warnings.append("可识别的传感器列少于 2 列，多变量关系诊断能力会受限。")
    if missing_rate > 0.1:
        warnings.append(f"缺失率约 {missing_rate * 100:.1f}%，建议先检查数据完整性。")
    if profile.row_count < 100:
        warnings.append("数据行数少于 100，趋势预测和工况分段结果可能不稳定。")
    if loaded.profile is None:
        warnings.append("未匹配设备专属配置，当前将按通用工业时序数据进行分析。")
    return {
        "file_id": file_id,
        "file_name": source_path.name,
        "size_bytes": source_path.stat().st_size,
        "row_count": profile.row_count,
        "sample_count": min(profile.row_count, 2000),
        "delimiter": _detect_delimiter(source_path),
        "columns": ["datetime", *profile.sensor_columns, *profile.label_columns],
        "datetime_column": "datetime",
        "sensor_count": len(profile.sensor_columns),
        "missing_rate": round(missing_rate, 6),
        "device_profile": loaded.context,
        "warnings": warnings,
    }


def _submit_analysis_job(
    *,
    file_id: str,
    source_path: Path,
    operation: str,
    config: AnalysisConfig,
    source_id: str | None = None,
    ingestion_id: str | None = None,
    ingestion_storage_path: Path | None = None,
) -> dict[str, Any]:
    """登记并提交异步任务，保持普通 API 与万悟适配接口行为一致。"""

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    repository = get_repository()
    repository.start_run(
        run_id=run_id,
        file_id=file_id,
        operation=operation,
        detector=config.detector,
        config=asdict(config),
        status="queued",
        source_id=source_id,
        ingestion_id=ingestion_id,
    )
    if ingestion_id and ingestion_storage_path:
        repository.mark_ingestion_submitted(
            ingestion_id,
            run_id=run_id,
            storage_path=ingestion_storage_path,
        )
    try:
        get_job_manager().submit(
            run_id,
            _execute_analysis_job,
            run_id,
            file_id,
            source_path,
            operation,
            config,
        )
    except JobQueueFullError as exc:
        repository.finish_run(run_id, "failed", 0.0, error=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "queued",
        "run_id": run_id,
        "operation": operation,
        "status_url": f"/api/v1/jobs/{run_id}",
        "result_url": f"/api/v1/jobs/{run_id}/result",
    }


def _submit_automatic_ingestion(
    source: dict[str, Any],
    ingestion_id: str,
    source_path: Path,
) -> str:
    """把自动采集快照登记为受控文件并提交现有分析队列。"""

    file_id = f"auto_{ingestion_id}"
    get_repository().register_file(file_id, source_path.name, source_path)
    config = _parse_config(source.get("analysis_config") or {})
    accepted = _submit_analysis_job(
        file_id=file_id,
        source_path=source_path,
        operation="analyze",
        config=config,
        source_id=str(source["source_id"]),
        ingestion_id=ingestion_id,
        ingestion_storage_path=source_path,
    )
    return str(accepted["run_id"])


def _job_status_payload(run: dict[str, Any]) -> dict[str, Any]:
    """普通 API 与万悟 JSON 接口共用任务状态结构。"""

    return {
        "status": "success",
        "run_id": run["run_id"],
        "job_status": run["status"],
        "operation": run["operation"],
        "detector": run["detector"],
        "file_id": run["file_id"],
        "file_name": run["file_name"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "duration_ms": run["duration_ms"],
        "error": run["error"],
        "result_ready": run["status"] == "success" and run["result"] is not None,
    }


def _get_run_or_404(run_id: str) -> dict[str, Any]:
    """读取任务，找不到时返回统一 404。"""

    run = get_repository().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="找不到对应异步任务")
    return run


def _job_result_payload(run_id: str) -> dict[str, Any]:
    """按状态返回异步结果或明确业务错误。"""

    run = _get_run_or_404(run_id)
    if run["status"] in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="任务尚未完成")
    if run["status"] == "cancelled":
        raise HTTPException(status_code=409, detail="任务已取消，没有可用结果")
    if run["status"] == "failed":
        raise HTTPException(status_code=422, detail=run["error"] or "任务执行失败")
    if run["result"] is None:
        raise HTTPException(status_code=409, detail="任务结果尚未就绪")
    return {"status": "success", "run_id": run_id, "result": run["result"]}


def _cancel_job_payload(run_id: str) -> dict[str, Any]:
    """取消排队任务，供路径参数接口和万悟 JSON 接口共同调用。"""

    repository = get_repository()
    run = _get_run_or_404(run_id)
    if run["status"] == "cancelled":
        return {
            "status": "success",
            "run_id": run_id,
            "job_status": "cancelled",
            "message": "任务此前已取消",
        }
    if run["status"] != "queued":
        raise HTTPException(
            status_code=409,
            detail=f"只有 queued 任务可以取消，当前状态为 {run['status']}",
        )
    if not get_job_manager().cancel(run_id):
        raise HTTPException(
            status_code=409,
            detail="任务已经获得执行线程，无法取消，请继续查询任务状态",
        )
    if not repository.cancel_run(run_id, "用户或平台取消了排队任务"):
        raise HTTPException(status_code=409, detail="任务状态已发生变化，请重新查询")
    return {
        "status": "success",
        "run_id": run_id,
        "job_status": "cancelled",
        "message": "排队任务已取消",
    }


def _start_persisted_run(
    logger: RunLogger,
    file_id: str,
    operation: str,
    detector: str,
    config: dict[str, Any],
) -> None:
    """统一创建数据库任务，避免各路由遗漏审计字段。"""

    get_repository().start_run(
        run_id=logger.run_id,
        file_id=file_id,
        operation=operation,
        detector=detector,
        config=config,
    )


def _finish_persisted_run(
    logger_record: dict[str, Any],
    response: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """使用与 JSONL 日志相同的耗时完成数据库任务归档。"""

    get_repository().finish_run(
        run_id=str(logger_record["run_id"]),
        status=str(logger_record["status"]),
        duration_ms=float(logger_record["duration_ms"]),
        result=response,
        error=error,
    )


def _public_case_record(case: dict[str, Any]) -> dict[str, Any]:
    """移除内部检索集合，只返回万悟和看板需要的可序列化字段。"""

    signature = case.get("signature", {})
    return {
        "case_id": case["case_id"],
        "confirmed_cause": case["confirmed_cause"],
        "source_run_id": case["source_run_id"],
        "source_record_id": case["source_record_id"],
        "sensor_groups": sorted(signature.get("groups", [])),
        "direction_features": sorted(signature.get("directions", [])),
        "dominant_sensor_groups": sorted(signature.get("dominant_groups", [])),
        "regime_context": signature.get("regime", ""),
        "evidence_summary": case["evidence_summary"],
        "feedback_note": case["feedback_note"],
        "handled_by": case["handled_by"],
        "closed_at": case["closed_at"],
        "archived_at": case.get("archived_at"),
        "archive_reason": case.get("archive_reason"),
    }


def _quick_diagnosis_presentation(response: dict[str, Any]) -> str:
    """生成万悟可以直接展示的短诊断摘要，不再要求外层模型重复整理。"""

    analysis = response["analysis"]
    profile = analysis.get("data_profile", {})
    events = analysis.get("anomaly_events", [])
    diagnosis = response.get("automatic_diagnosis") or {}
    diagnosis_text = str(diagnosis.get("diagnosis") or "")
    lines = [
        (
            f"文件：{response['file_name']}，数据点：{profile.get('row_count', 0)}，"
            f"传感器：{', '.join(profile.get('sensor_columns', [])) or '未识别'}。"
        ),
        f"检测器：{response['detector']}，异常事件：{len(events)} 个。",
    ]
    if events:
        for index, event in enumerate(events[:3], start=1):
            lines.append(
                f"事件{index}：{event.get('start_time')} 至 {event.get('end_time')}，"
                f"风险={event.get('severity', '未分级')}，"
                f"重点变量={', '.join(event.get('dominant_sensors', [])) or '未识别'}。"
            )
    else:
        lines.append("当前配置下未形成持续异常事件。")
    validation = analysis.get("detector_validation") or {}
    selection = analysis.get("model_selection") or {}
    if selection:
        lines.append(
            "模型选择："
            f"{selection.get('selected_detector_name', response['detector'])}；"
            f"目标={selection.get('analysis_goal_name', '综合平衡')}；"
            f"依据={selection.get('reason', '使用冻结设备配置')}"
        )
    if validation:
        agreement = validation.get("agreement") or {}
        lines.append(
            "多模型验证："
            f"{validation.get('model_count', 0)} 种检测器完成交叉核验，"
            f"一致性={agreement.get('level', '不可用')}；"
            f"{validation.get('conclusion', '未形成交叉验证结论')}"
        )
    if diagnosis_text:
        lines.extend(["诊断摘要：", diagnosis_text[:4000]])
    lines.append("提示：结果用于辅助排查，故障确诊仍需结合现场工况和人工复核。")
    return "\n".join(lines)


def _quick_analysis_payload(run_id: str, result: Any) -> dict[str, Any]:
    """压缩完整分析结果，避免万悟下一轮模型上下文被大 JSON 占满。"""

    full = _result_payload(run_id, result)
    return {
        # 这些字段是后续新增的企业数据适配和智能体可解释性证据。它们体量有限，
        # 应随快速诊断一起返回，避免后端已经执行但万悟只能看到旧版分析摘要。
        "device_profile": full["device_profile"],
        "data_profile": full["data_profile"],
        "data_quality": full["data_quality"],
        "visualization": full["visualization"],
        "anomaly_events": full["anomaly_events"][:10],
        "model_selection": full["model_selection"],
        "detector_validation": full["detector_validation"],
        "operating_regimes": full["operating_regimes"],
        "relationship_diagnostics": full["relationship_diagnostics"][:10],
        "root_cause_diagnoses": full["root_cause_diagnoses"][:10],
        "historical_case_matches": full["historical_case_matches"],
        "work_order_drafts": full["work_order_drafts"][:10],
        "forecast_results": full["forecast_results"],
        "risk_alerts": full["risk_alerts"][:10],
        "recommendations": full["recommendations"][:10],
        "execution_trace": full["execution_trace"],
        "summary": full["summary"],
        "limitations": full["limitations"],
    }


def _quick_automatic_payload(automatic: Any) -> dict[str, Any]:
    """压缩快速诊断的解释层，避免把同一份算法证据重复返回给万悟。

    ``automatic.to_dict()`` 面向本地调试，会包含完整 ``evidence.analysis_summary``。
    快速工具已经通过顶层 ``analysis`` 返回了结构化证据，再重复嵌套一份会明显增加
    万悟结果上下文，也可能诱发平台模型继续总结。因此比赛演示只返回解释正文、边界
    和知识来源；需要完整证据时读取同一响应的 ``analysis`` 字段。
    """

    payload = automatic.to_dict()
    evidence = payload.pop("evidence", {})
    if isinstance(evidence, dict):
        payload["knowledge_sources"] = [
            item.get("source")
            for item in evidence.get("knowledge", ())
            if isinstance(item, dict) and item.get("source")
        ]
    return payload


def _quick_cached_response(
    stored_run: dict[str, Any] | None,
    *,
    file_source: str,
) -> dict[str, Any] | None:
    """将数据库中的快速诊断结果恢复为万悟响应；旧记录不满足协议时返回空值。"""

    if stored_run is None:
        return None
    result = stored_run.get("result")
    if not isinstance(result, dict):
        return None
    required = {
        "status",
        "run_id",
        "file_id",
        "file_name",
        "size_bytes",
        "detector",
        "analysis",
        "automatic_diagnosis",
        "presentation",
        "model_call_count",
        "diagnosis_mode",
        "analysis_version",
    }
    if not required.issubset(result):
        return None
    if result.get("analysis_version") != QUICK_DIAGNOSIS_VERSION:
        return None
    cached = dict(result)
    # 返回本次接收方式，但任务和结果仍明确指向原始成功 run_id。
    cached["file_source"] = file_source
    cached["cache_hit"] = True
    return cached


def _execute_analysis_job(
    run_id: str,
    file_id: str,
    source_path: Path,
    operation: str,
    config: AnalysisConfig,
) -> None:
    """后台执行分析或自动诊断，并把最终状态写回 PostgreSQL。"""

    logger = RunLogger(run_id=run_id)
    repository = get_repository()
    try:
        repository.mark_run_running(run_id)
        result = analyze_file(
            source_path,
            config=config,
            write_report=True,
            run_detector_validation=True,
            case_matcher=repository.find_similar_cases,
        )
        response = _result_payload(run_id, result)
        diagnosis_status = None
        if operation == "diagnose":
            automatic = AutomaticDiagnosisService().diagnose(result, run_id=run_id)
            response["automatic_diagnosis"] = automatic.to_dict()
            diagnosis_status = automatic.status
    except Exception as exc:  # noqa: BLE001  后台任务必须自行落库，不能依赖 FastAPI。
        log_record = logger.finish(
            "failed",
            operation,
            {"file_id": file_id, "detector": config.detector},
            error=str(exc),
        )
        _finish_persisted_run(log_record, error=str(exc))
        return

    output_summary = {
        "events": len(result.events),
        "alerts": len(result.risk_alerts),
    }
    if diagnosis_status:
        output_summary["diagnosis_status"] = diagnosis_status
    log_record = logger.finish(
        "success",
        operation,
        {"file_id": file_id, "detector": config.detector},
        output_summary,
    )
    _finish_persisted_run(log_record, response=response)
    # 通知是分析后的主动动作。投递失败只进入通知审计，不覆盖已成功的分析和工单。
    try:
        dispatch_run_notifications(repository, run_id)
    except Exception as exc:  # noqa: BLE001 - 通知通道故障不能改变分析任务事实状态。
        source = repository.get_data_source_for_run(run_id)
        if source is not None:
            repository.record_source_poll(
                str(source["source_id"]),
                success=True,
                error=f"通知投递失败：{exc}",
            )


if app:

    @app.get("/integrations/wanwu/openapi.json", include_in_schema=False)
    def wanwu_openapi() -> dict[str, Any]:
        """返回仅包含万悟可稳定调用工具的精简 OpenAPI。"""

        return build_wanwu_openapi(
            app.openapi(),
            settings.api_public_base_url,
            api_key_required=bool(settings.industrial_api_key),
        )

    @app.get("/integrations/wanwu/quick-openapi.json", include_in_schema=False)
    def wanwu_quick_openapi() -> dict[str, Any]:
        """返回比赛演示专用的单工具 OpenAPI。"""

        return build_wanwu_openapi(
            app.openapi(),
            settings.api_public_base_url,
            quick_only=True,
            api_key_required=bool(settings.industrial_api_key),
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        """供万悟或部署平台检查服务是否在线。"""

        if not get_repository().ping():
            raise HTTPException(status_code=503, detail="PostgreSQL 数据库不可用")
        return {"status": "ok", "service": "shichi-qianji", "database": "postgresql"}

    @app.get("/api/v1/auth/config")
    def auth_config() -> dict[str, Any]:
        """告诉前端当前部署是否启用人员登录，不返回任何账号或密码信息。"""

        return {
            "status": "success",
            "auth_enabled": settings.auth_enabled,
            "session_hours": settings.auth_session_hours,
        }

    @app.post("/api/v1/auth/login", response_model=LoginResponse)
    def login(payload: LoginRequest) -> dict[str, Any]:
        """验证预置账号并签发可撤销的短期 Bearer 会话。"""

        if not settings.auth_enabled:
            raise HTTPException(status_code=409, detail="当前部署未启用人员登录")
        repository = get_repository()
        user = repository.get_user_by_username(payload.username)
        if user is None or not bool(user["active"]) or not verify_password(
            payload.password,
            str(user["password_hash"]),
        ):
            raise HTTPException(status_code=401, detail="账号或密码错误")
        token = generate_session_token()
        expires_at = (
            datetime.now().astimezone() + timedelta(hours=settings.auth_session_hours)
        ).isoformat()
        session = repository.create_session(
            user_id=str(user["user_id"]),
            token_hash=hash_session_token(token),
            expires_at=expires_at,
        )
        public_user = repository.get_user_by_session(hash_session_token(token))
        repository.record_audit(
            user_id=str(user["user_id"]),
            action="login",
            target_type="session",
            target_id=session["session_id"],
        )
        return {
            "status": "success",
            "token": token,
            "expires_at": session["expires_at"],
            "user": public_user,
        }

    @app.get("/api/v1/auth/me")
    def current_user(
        user: Annotated[dict[str, Any], Depends(_current_user)],
    ) -> dict[str, Any]:
        """恢复浏览器刷新前的登录态。"""

        return {"status": "success", "user": user}

    @app.post("/api/v1/auth/logout")
    def logout(
        authorization: Annotated[str | None, Header()] = None,
        user: Annotated[dict[str, Any] | None, Depends(_optional_current_user)] = None,
    ) -> dict[str, Any]:
        """撤销当前会话；关闭认证时保持兼容成功。"""

        if settings.auth_enabled:
            token = _bearer_token(authorization)
            get_repository().revoke_session(hash_session_token(token))
            get_repository().record_audit(
                user_id=user.get("user_id") if user else None,
                action="logout",
                target_type="session",
            )
        return {"status": "success"}

    @app.get("/api/v1/users")
    def list_users(
        _user: Annotated[
            dict[str, Any],
            Depends(_require_roles("系统管理员", "生产负责人")),
        ],
    ) -> dict[str, Any]:
        """返回可指派人员；密码、令牌和内部审计信息始终不出库。"""

        users = get_repository().list_users()
        return {"status": "success", "user_count": len(users), "users": users}

    @app.get("/api/v1/notifications/mine")
    def list_my_notifications(
        unread_only: bool = False,
        user: Annotated[dict[str, Any] | None, Depends(_current_user)] = None,
    ) -> dict[str, Any]:
        """返回当前人员收到的主动告警。"""

        notifications = (
            get_repository().list_notifications(
                limit=100,
                recipient_user_id=user.get("user_id"),
                unread_only=unread_only,
            )
            if user and user.get("user_id")
            else []
        )
        return {
            "status": "success",
            "notification_count": len(notifications),
            "unread_count": sum(1 for item in notifications if not item.get("read_at")),
            "notifications": notifications,
        }

    @app.post("/api/v1/notifications/acknowledge")
    def acknowledge_notification(
        payload: NotificationAcknowledgeRequest,
        user: Annotated[dict[str, Any], Depends(_current_user)],
    ) -> dict[str, Any]:
        """签收主动告警并写入人工审计轨迹。"""

        if not user.get("user_id"):
            raise HTTPException(status_code=409, detail="当前部署未启用人员身份")
        try:
            notification = get_repository().acknowledge_notification(
                payload.notification_id,
                user["user_id"],
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        get_repository().record_audit(
            user_id=user["user_id"],
            action="acknowledge_notification",
            target_type="notification",
            target_id=payload.notification_id,
        )
        return {"status": "success", "notification": notification}

    @app.get("/api/v1/system/diagnostics")
    def system_diagnostics() -> dict[str, Any]:
        """返回部署自检摘要，不暴露密钥、绝对路径和业务数据。"""

        knowledge_files = sorted(
            path
            for path in settings.knowledge_dir.glob("*")
            if path.is_file() and path.suffix.lower() in {".md", ".txt"}
        )
        database_ready = False
        device_profiles = {}
        device_profile_error: str | None = None
        try:
            device_profiles = load_device_profiles(settings.device_profiles_dir)
        except ValueError as exc:
            device_profile_error = str(exc)
        try:
            database_ready = get_repository().ping()
        except (OSError, RuntimeError, psycopg.Error):
            pass

        checks = {
            "database": "ok" if database_ready else "error",
            "knowledge_base": "ok" if knowledge_files else "warning",
            "default_skab": "ok" if settings.default_skab_file.exists() else "warning",
            "healthy_baseline": "ok" if settings.healthy_baseline_file.exists() else "warning",
            "device_profiles": (
                "ok"
                if any(profile.enabled for profile in device_profiles.values())
                else "warning"
            ),
        }
        warnings = []
        if not knowledge_files:
            warnings.append("未找到可检索的工业知识库文档")
        if not settings.default_skab_file.exists():
            warnings.append("默认 SKAB 样例不可用，仍可通过上传接口分析 CSV")
        if not settings.healthy_baseline_file.exists():
            warnings.append("健康基线不可用，部分 AutoEncoder 健康模型能力将降级")
        if not settings.llm_enabled:
            warnings.append("未配置大模型密钥，确定性工业分析仍可运行")
        if device_profile_error:
            warnings.append(f"设备配置读取失败：{device_profile_error}")
        elif not any(profile.enabled for profile in device_profiles.values()):
            warnings.append("未找到可启用的设备配置，上传数据将使用通用模式")

        return {
            "status": "ready" if checks["database"] == "ok" and not warnings else "degraded",
            "service": "shichi-qianji",
            "version": app.version,
            "database": {"engine": "postgresql", "ready": database_ready},
            "knowledge_base": {"ready": bool(knowledge_files), "document_count": len(knowledge_files)},
            "data_sources": {
                "default_skab_ready": settings.default_skab_file.exists(),
                "healthy_baseline_ready": settings.healthy_baseline_file.exists(),
            },
            "device_profiles": {
                "ready": bool(device_profiles) and device_profile_error is None,
                "profile_count": len(device_profiles),
                "enabled_count": sum(
                    profile.enabled for profile in device_profiles.values()
                ),
                "profiles": [
                    profile.public_summary() for profile in device_profiles.values()
                ],
            },
            "model": {
                "llm_enabled": settings.llm_enabled,
                "embedding_enabled": settings.embedding_enabled,
                "provider": settings.llm_provider,
                "chat_model": settings.llm_chat_model,
            },
            "rate_limits": {
                "chat_requests_per_minute": settings.llm_requests_per_minute,
                "embedding_requests_per_minute": settings.embedding_requests_per_minute,
            },
            "job_queue": get_job_manager().diagnostics(),
            "checks": checks,
            "warnings": warnings,
        }

    @app.get("/api/v1/device-profiles")
    def list_device_profiles(
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """返回可供前端或万悟选择的设备配置摘要，不返回本地路径。"""

        _check_api_key(x_api_key)
        try:
            profiles = load_device_profiles(settings.device_profiles_dir)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=f"设备配置不可用：{exc}") from exc
        selectable = [
            profile.public_summary()
            for profile in profiles.values()
            if profile.enabled
        ]
        return {
            "status": "success",
            "profiles": selectable,
            "count": len(selectable),
            "generic_mode_available": True,
        }

    @app.get("/api/v1/models")
    def list_models(
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """返回已持久化健康模型的脱敏元数据，供万悟和运维页面查看。"""

        _check_api_key(x_api_key)
        models = list_autoencoder_models()
        return {
            "status": "success",
            "model_count": len(models),
            "models": models,
        }

    @app.post(
        "/api/v1/files",
        response_model=FileUploadResponse,
        responses={400: {"model": ErrorResponse}},
    )
    async def upload_file(
        file: Annotated[UploadFile, File(...)],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """上传 CSV 并返回受控 file_id。"""

        _check_api_key(x_api_key)
        if not file.filename or not file.filename.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail="当前只允许上传 CSV 文件")
        content = await file.read(settings.max_upload_bytes + 1)
        file_id, target_path, metadata = _store_uploaded_csv(file.filename, content)
        return {
            "file_id": file_id,
            "file_name": target_path.name,
            "sha256": metadata["sha256"],
            "size_bytes": metadata["size_bytes"],
        }

    @app.post(
        "/api/v1/samples/skab/default",
        response_model=FileUploadResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def register_default_skab_sample(
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """登记默认 SKAB 样例，便于校赛演示时一键开始分析。"""

        _check_api_key(x_api_key)
        return _register_sample_file(settings.default_skab_file)

    @app.get(
        "/api/v1/files/{file_id}/preflight",
        response_model=FilePreflightResponse,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    def preflight_file(
        file_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """返回指定受控文件的真实结构画像，不暴露服务器本地路径。"""

        _check_api_key(x_api_key)
        source_path = _find_uploaded_file(file_id)
        return _build_file_preflight(file_id, source_path)

    @app.post(
        "/api/v1/analyze",
        response_model=AnalysisResponse,
        response_model_exclude_none=True,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    def analyze(
        payload: AnalysisRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """分析已上传文件，供万悟工作流 API 节点调用。"""

        _check_api_key(x_api_key)
        file_id = payload.file_id
        source_path = _find_uploaded_file(file_id)

        logger = RunLogger(run_id=f"run_{uuid.uuid4().hex[:12]}")
        config = _parse_request_config(payload.config.as_overrides())
        _start_persisted_run(
            logger,
            file_id,
            "analyze",
            config.detector,
            asdict(config),
        )
        try:
            result = analyze_file(
                source_path,
                config=config,
                write_report=True,
                run_detector_validation=True,
                case_matcher=get_repository().find_similar_cases,
            )
            response = _result_payload(logger.run_id, result)
        except Exception as exc:
            log_record = logger.finish(
                "failed",
                "analyze",
                {"file_id": file_id},
                error=str(exc),
            )
            _finish_persisted_run(log_record, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_record = logger.finish(
            "success",
            "analyze",
            {"file_id": file_id, "detector": config.detector},
            {"events": len(result.events), "alerts": len(result.risk_alerts)},
        )
        _finish_persisted_run(log_record, response=response)
        return response

    @app.post(
        "/api/v1/diagnose",
        response_model=AnalysisResponse,
        response_model_exclude_none=True,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    def diagnose(
        payload: AnalysisRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """一次完成确定性分析、知识检索和单次大模型诊断。"""

        _check_api_key(x_api_key)
        file_id = payload.file_id
        source_path = _find_uploaded_file(file_id)

        logger = RunLogger(run_id=f"run_{uuid.uuid4().hex[:12]}")
        config = _parse_request_config(payload.config.as_overrides())
        _start_persisted_run(
            logger,
            file_id,
            "diagnose",
            config.detector,
            asdict(config),
        )
        try:
            result = analyze_file(
                source_path,
                config=config,
                write_report=True,
                run_detector_validation=True,
                case_matcher=get_repository().find_similar_cases,
            )
            response = _result_payload(logger.run_id, result)
            automatic = AutomaticDiagnosisService().diagnose(
                result, run_id=logger.run_id
            )
            response["automatic_diagnosis"] = automatic.to_dict()
        except Exception as exc:
            log_record = logger.finish(
                "failed",
                "diagnose",
                {"file_id": file_id},
                error=str(exc),
            )
            _finish_persisted_run(log_record, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_record = logger.finish(
            "success",
            "diagnose",
            {"file_id": file_id, "detector": config.detector},
            {
                "events": len(result.events),
                "alerts": len(result.risk_alerts),
                "diagnosis_status": automatic.status,
            },
        )
        _finish_persisted_run(log_record, response=response)
        return response

    @app.post(
        "/api/v1/jobs",
        response_model=JobAcceptedResponse,
        status_code=202,
        responses={
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def create_job(
        payload: JobCreateRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """提交异步分析任务，立即返回供万悟轮询的 run_id。"""

        _check_api_key(x_api_key)
        source_path = _find_uploaded_file(payload.file_id)
        config = _parse_request_config(payload.config.as_overrides())
        return _submit_analysis_job(
            file_id=payload.file_id,
            source_path=source_path,
            operation=payload.operation,
            config=config,
        )

    @app.get(
        "/api/v1/jobs/{run_id}",
        response_model=JobStatusResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def get_job_status(
        run_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """返回异步任务当前状态；万悟选择器只需读取 job_status。"""

        _check_api_key(x_api_key)
        return _job_status_payload(_get_run_or_404(run_id))

    @app.delete(
        "/api/v1/jobs/{run_id}",
        response_model=JobCancelledResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    def cancel_job(
        run_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """取消仍在等待线程池执行的任务。"""

        _check_api_key(x_api_key)
        return _cancel_job_payload(run_id)

    @app.get(
        "/api/v1/jobs/{run_id}/result",
        response_model=JobResultResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    def get_job_result(
        run_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """任务成功后返回完整结果；未完成和失败使用不同状态码。"""

        _check_api_key(x_api_key)
        return _job_result_payload(run_id)

    @app.post(
        "/api/v1/wanwu/jobs/submit",
        operation_id="submit_industrial_analysis",
        summary="提交工业时序分析",
        response_model=WanwuJobAcceptedResponse,
        status_code=202,
        responses={
            400: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def wanwu_submit_job(
        payload: WanwuJobCreateRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """从万悟文件 URL 或 Base64 接收 CSV，并在一次调用中提交异步任务。"""

        _check_api_key(x_api_key)
        try:
            incoming = receive_wanwu_csv(
                file_url=payload.file_url,
                file_base64=payload.file_base64,
                requested_file_name=payload.file_name,
                max_bytes=settings.max_upload_bytes,
                download_timeout=settings.wanwu_download_timeout,
                allow_private_urls=settings.wanwu_allow_private_file_urls,
                allowed_private_hosts=settings.wanwu_allowed_file_hosts,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        file_id, source_path, metadata = _store_uploaded_csv(
            incoming.file_name,
            incoming.content,
        )
        config = _parse_request_config(payload.config.as_overrides())
        accepted = _submit_analysis_job(
            file_id=file_id,
            source_path=source_path,
            operation=payload.operation,
            config=config,
        )
        return {
            **accepted,
            "file_id": file_id,
            "file_name": incoming.file_name,
            "file_source": incoming.source_type,
            "sha256": metadata["sha256"],
            "size_bytes": metadata["size_bytes"],
        }

    @app.post(
        "/api/v1/wanwu/quick-diagnosis",
        operation_id="quick_industrial_diagnosis",
        summary="快速完成工业时序分析与诊断",
        response_model=WanwuQuickDiagnosisResponse,
        responses={
            400: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
        },
    )
    def wanwu_quick_diagnosis(
        payload: WanwuQuickDiagnosisRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """比赛演示专用单步工具，后端完成分析并避免再次调用外部大模型。"""

        _check_api_key(x_api_key)
        try:
            incoming = receive_wanwu_csv(
                file_url=payload.file_url,
                file_base64=payload.file_base64,
                requested_file_name=payload.file_name,
                max_bytes=settings.max_upload_bytes,
                download_timeout=settings.wanwu_download_timeout,
                allow_private_urls=settings.wanwu_allow_private_file_urls,
                allowed_private_hosts=settings.wanwu_allowed_file_hosts,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        config = _parse_request_config(payload.config.as_overrides())
        # 万悟可能因网络重试重复调用同一工具。先按内容哈希查找成功结果，
        # 命中时不再写入重复 CSV，也不重复生成工单和分析报告。
        file_sha256 = hashlib.sha256(incoming.content).hexdigest()
        cached = _quick_cached_response(
            get_repository().find_successful_run(
                file_sha256=file_sha256,
                operation="quick_diagnose",
                detector=config.detector,
                config=asdict(config),
            ),
            file_source=incoming.source_type,
        )
        if cached is not None:
            return cached

        file_id, source_path, metadata = _store_uploaded_csv(
            incoming.file_name,
            incoming.content,
        )

        logger = RunLogger(run_id=f"run_{uuid.uuid4().hex[:12]}")
        _start_persisted_run(
            logger,
            file_id,
            "quick_diagnose",
            config.detector,
            asdict(config),
        )
        try:
            result = analyze_file(
                source_path,
                config=config,
                write_report=True,
                run_detector_validation=True,
                case_matcher=get_repository().find_similar_cases,
            )
            # 快速工具不向 GLM-5 发起请求，避免“外层万悟调用 + 内部诊断调用”叠加限流。
            automatic = AutomaticDiagnosisService(
                settings,
                allow_external_calls=False,
            ).diagnose(result)
            analysis = _quick_analysis_payload(logger.run_id, result)
            response = {
                "status": "success",
                "run_id": logger.run_id,
                "file_id": file_id,
                "file_name": incoming.file_name,
                "file_source": incoming.source_type,
                "size_bytes": metadata["size_bytes"],
                "detector": config.detector,
                "analysis": analysis,
                "automatic_diagnosis": _quick_automatic_payload(automatic),
                "presentation": "",
                "model_call_count": 0,
                "diagnosis_mode": "deterministic",
                "analysis_version": QUICK_DIAGNOSIS_VERSION,
                "cache_hit": False,
            }
            response["presentation"] = _quick_diagnosis_presentation(response)
        except Exception as exc:
            log_record = logger.finish(
                "failed",
                "quick_diagnose",
                {"file_id": file_id, "detector": config.detector},
                error=str(exc),
            )
            _finish_persisted_run(log_record, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        log_record = logger.finish(
            "success",
            "quick_diagnose",
            {"file_id": file_id, "detector": config.detector},
            {
                "events": len(result.events),
                "alerts": len(result.risk_alerts),
                "model_call_count": 0,
                "diagnosis_mode": "deterministic",
            },
        )
        _finish_persisted_run(log_record, response=response)
        return response

    @app.post(
        "/api/v1/wanwu/jobs/status",
        operation_id="get_industrial_analysis_status",
        summary="查询工业分析任务状态",
        response_model=JobStatusResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def wanwu_job_status(
        payload: RunIdRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """使用 JSON 中的 run_id 查询状态，兼容万悟 OpenAPI 工具执行器。"""

        _check_api_key(x_api_key)
        return _job_status_payload(_get_run_or_404(payload.run_id))

    @app.post(
        "/api/v1/wanwu/jobs/result",
        operation_id="get_industrial_analysis_result",
        summary="获取工业分析结果",
        response_model=JobResultResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    def wanwu_job_result(
        payload: RunIdRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """任务成功后通过 JSON 请求获取完整结构化结果。"""

        _check_api_key(x_api_key)
        return _job_result_payload(payload.run_id)

    @app.post(
        "/api/v1/wanwu/jobs/cancel",
        operation_id="cancel_industrial_analysis",
        summary="取消排队中的工业分析任务",
        response_model=JobCancelledResponse,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    def wanwu_cancel_job(
        payload: RunIdRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """通过 JSON 请求取消尚未开始执行的任务。"""

        _check_api_key(x_api_key)
        return _cancel_job_payload(payload.run_id)

    @app.post(
        "/api/v1/wanwu/work-orders/list",
        operation_id="list_industrial_work_orders",
        summary="查询工业运维工单",
    )
    def wanwu_list_work_orders(
        payload: WanwuWorkOrderListRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """通过 JSON 条件查询工单，避免依赖 GET 查询参数绑定。"""

        _check_api_key(x_api_key)
        work_orders = get_repository().list_work_orders(
            limit=payload.limit,
            offset=payload.offset,
            status=payload.status,
            run_id=payload.run_id,
            search=payload.search,
            priority=payload.priority,
            include_archived=payload.include_archived,
            archived_only=payload.archived_only,
        )
        total = get_repository().count_work_orders(
            status=payload.status,
            run_id=payload.run_id,
            search=payload.search,
            priority=payload.priority,
            include_archived=payload.include_archived,
            archived_only=payload.archived_only,
        )
        return {
            "status": "success",
            "work_order_count": total,
            "page_count": len(work_orders),
            "offset": payload.offset,
            "limit": payload.limit,
            "work_orders": work_orders,
        }

    @app.post(
        "/api/v1/wanwu/work-orders/update",
        operation_id="update_industrial_work_order",
        summary="回写工业运维工单",
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    def wanwu_update_work_order(
        payload: WanwuWorkOrderUpdateRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """使用单个 JSON 请求回写工单状态、根因和现场复测说明。"""

        _check_api_key(x_api_key)
        values = payload.model_dump(exclude={"record_id"}, exclude_unset=True)
        try:
            work_order = get_repository().update_work_order(payload.record_id, values)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "success", "work_order": work_order}

    @app.post(
        "/api/v1/wanwu/cases/list",
        operation_id="list_industrial_feedback_cases",
        summary="查询已闭环工业故障案例",
    )
    def wanwu_list_cases(
        payload: WanwuCaseListRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """查询由现场确认工单形成的可追溯案例记忆。"""

        _check_api_key(x_api_key)
        if payload.include_archived or payload.archived_only:
            cases = get_repository().list_confirmed_cases(
                limit=payload.limit,
                include_archived=payload.include_archived,
                archived_only=payload.archived_only,
            )
        else:
            # 默认调用保持旧版仓储方法签名兼容，便于万悟适配层和测试替换仓储。
            cases = get_repository().list_confirmed_cases(limit=payload.limit)
        public_cases = [_public_case_record(item) for item in cases]
        return {
            "status": "success",
            "case_count": len(public_cases),
            "cases": public_cases,
        }

    @app.get("/api/v1/runs")
    def list_runs(
        limit: int = 20,
        status: str | None = None,
        include_archived: bool = False,
        archived_only: bool = False,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """返回历史分析任务摘要，供万悟任务中心或看板调用。"""

        _check_api_key(x_api_key)
        runs = get_repository().list_runs(
            limit=limit,
            status=status,
            include_archived=include_archived,
            archived_only=archived_only,
        )
        return {"status": "success", "run_count": len(runs), "runs": runs}

    @app.get("/api/v1/monitoring/status")
    def monitoring_status(
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """返回无人值守监测、采集批次和通知的统一看板数据。"""

        _check_api_key(x_api_key)
        repository = get_repository()
        sources = [_public_data_source(item) for item in repository.list_data_sources()]
        # 本机快照路径只供后端复现分析，不通过看板接口暴露。
        ingestions = [
            {key: value for key, value in item.items() if key != "storage_path"}
            for item in repository.list_ingestions(limit=100)
        ]
        notifications = repository.list_notifications(limit=100)
        return {
            "status": "success",
            "monitor": get_monitoring_service().status(),
            "notification_channels": {
                "wecom": {
                    "enabled": settings.wecom_enabled,
                    "configured": bool(settings.wecom_webhook_url),
                }
            },
            "source_count": len(sources),
            "enabled_source_count": sum(int(item["enabled"]) for item in sources),
            "sources": sources,
            "ingestions": ingestions,
            "notifications": notifications,
        }

    @app.post("/api/v1/monitoring/sources")
    def save_monitoring_source(
        payload: DataSourceRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """保存数据源和告警路由；启用后调度器自动开始轮询。"""

        _check_api_key(x_api_key)
        _validate_data_source(payload)
        source = get_repository().upsert_data_source(
            {
                "source_id": payload.source_id,
                "name": payload.name,
                "source_type": payload.source_type,
                "endpoint": payload.endpoint,
                "interval_seconds": payload.interval_seconds,
                "enabled": payload.enabled,
                "config": {
                    "timeout_seconds": payload.timeout_seconds,
                    "initial_scan_mode": payload.initial_scan_mode,
                    "request_headers": payload.request_headers,
                    "analysis_config": payload.analysis_config.as_overrides(),
                    "routing": payload.routing.model_dump(exclude_none=True),
                },
            }
        )
        if payload.enabled:
            get_monitoring_service().start()
        return {"status": "success", "source": _public_data_source(source)}

    @app.post("/api/v1/monitoring/sources/{source_id}/poll")
    def poll_monitoring_source(
        source_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """立即采集一次，主要用于配置验收和比赛现场演示。"""

        _check_api_key(x_api_key)
        try:
            result = get_monitoring_service().poll_once(source_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "success", "poll": result}

    @app.delete("/api/v1/monitoring/sources/{source_id}")
    def delete_monitoring_source(
        source_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """删除未产生历史的数据源；已有历史的数据源应停用。"""

        _check_api_key(x_api_key)
        try:
            source = get_repository().delete_data_source(source_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "success", "source": _public_data_source(source)}

    @app.get("/api/v1/runs/{run_id}")
    def get_run(
        run_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """返回指定任务的参数、状态和完整结构化结果。"""

        _check_api_key(x_api_key)
        run = get_repository().get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="找不到对应分析任务")
        return {"status": "success", "run": run}

    @app.get("/api/v1/model-calls")
    def list_model_calls(
        limit: int = 100,
        run_id: str | None = None,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """查询不含提示词和模型正文的调用审计记录。"""

        _check_api_key(x_api_key)
        calls = get_repository().list_model_calls(limit=limit, run_id=run_id)
        return {
            "status": "success",
            "call_count": len(calls),
            "content_stored": False,
            "calls": calls,
        }

    @app.get("/api/v1/work-orders")
    def list_work_orders(
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        run_id: str | None = None,
        search: str | None = None,
        priority: str | None = None,
        include_archived: bool = False,
        archived_only: bool = False,
        mine: bool = False,
        x_api_key: Annotated[str | None, Header()] = None,
        user: Annotated[dict[str, Any] | None, Depends(_optional_current_user)] = None,
    ) -> dict[str, Any]:
        """按优先级返回待办工单，可筛选状态和所属任务。"""

        _check_user_or_api_key(user, x_api_key)
        work_orders = get_repository().list_work_orders(
            limit=limit,
            offset=max(0, offset),
            status=status,
            run_id=run_id,
            search=search,
            priority=priority,
            assigned_user_id=(user.get("user_id") if mine and user else None),
            include_archived=include_archived,
            archived_only=archived_only,
        )
        total = get_repository().count_work_orders(
            status=status,
            run_id=run_id,
            search=search,
            priority=priority,
            assigned_user_id=(user.get("user_id") if mine and user else None),
            include_archived=include_archived,
            archived_only=archived_only,
        )
        return {
            "status": "success",
            "work_order_count": total,
            "page_count": len(work_orders),
            "offset": max(0, offset),
            "limit": max(1, min(200, limit)),
            "work_orders": work_orders,
        }

    @app.get("/api/v1/cases")
    def list_cases(
        limit: int = 50,
        include_archived: bool = False,
        archived_only: bool = False,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """返回已闭环故障案例，供本地看板核查持续学习状态。"""

        _check_api_key(x_api_key)
        if include_archived or archived_only:
            cases = get_repository().list_confirmed_cases(
                limit=limit,
                include_archived=include_archived,
                archived_only=archived_only,
            )
        else:
            cases = get_repository().list_confirmed_cases(limit=limit)
        public_cases = [_public_case_record(item) for item in cases]
        return {
            "status": "success",
            "case_count": len(public_cases),
            "cases": public_cases,
        }

    @app.delete("/api/v1/cases/{case_id}")
    def remove_case(
        case_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """永久移除案例记忆，但保留来源分析任务和原始证据。"""

        _check_api_key(x_api_key)
        try:
            result = get_repository().remove_confirmed_case(case_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "success", "case": result}

    @app.patch("/api/v1/work-orders/{record_id}")
    def update_work_order(
        record_id: str,
        payload: WorkOrderUpdateRequest,
        x_api_key: Annotated[str | None, Header()] = None,
        user: Annotated[dict[str, Any] | None, Depends(_optional_current_user)] = None,
    ) -> dict[str, Any]:
        """接收万悟或现场人员回写的工单状态与确认结果。"""

        _check_user_or_api_key(user, x_api_key)
        current_order = get_repository()._get_work_order(record_id)
        if current_order is None:
            raise HTTPException(status_code=404, detail=f"找不到工单：{record_id}")
        is_manager = user and user.get("role") in {"系统管理员", "生产负责人"}
        if (
            settings.auth_enabled
            and current_order.get("assigned_user_id")
            and current_order["assigned_user_id"] != (user or {}).get("user_id")
            and not is_manager
        ):
            raise HTTPException(status_code=403, detail="该工单已指派给其他人员")
        values = payload.model_dump(exclude_unset=True)
        if user and user.get("display_name") and not values.get("handled_by"):
            values["handled_by"] = user["display_name"]
        try:
            work_order = get_repository().update_work_order(
                record_id,
                values,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if user:
            get_repository().record_audit(
                user_id=user.get("user_id"),
                action="update_work_order",
                target_type="work_order",
                target_id=record_id,
                detail={"status": work_order["status"]},
            )
        return {"status": "success", "work_order": work_order}

    @app.post("/api/v1/work-orders/{record_id}/accept")
    def accept_work_order(
        record_id: str,
        user: Annotated[dict[str, Any], Depends(_current_user)],
    ) -> dict[str, Any]:
        """当前人员确认接单，并写入接单时间和责任人。"""

        if not user.get("user_id"):
            raise HTTPException(status_code=409, detail="当前部署未启用人员身份")
        try:
            work_order = get_repository().accept_work_order(record_id, user["user_id"])
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        get_repository().record_audit(
            user_id=user["user_id"],
            action="accept_work_order",
            target_type="work_order",
            target_id=record_id,
        )
        return {"status": "success", "work_order": work_order}

    @app.post("/api/v1/work-orders/{record_id}/assign")
    def assign_work_order(
        record_id: str,
        payload: WorkOrderAssignmentRequest,
        user: Annotated[
            dict[str, Any],
            Depends(_require_roles("系统管理员", "生产负责人")),
        ],
    ) -> dict[str, Any]:
        """由管理员或生产负责人调整工单责任人。"""

        try:
            work_order = get_repository().assign_work_order(record_id, payload.user_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        get_repository().record_audit(
            user_id=user.get("user_id"),
            action="assign_work_order",
            target_type="work_order",
            target_id=record_id,
            detail={"assigned_user_id": payload.user_id},
        )
        return {"status": "success", "work_order": work_order}

    @app.post("/api/v1/runs/{run_id}/archive")
    def archive_run(
        run_id: str,
        payload: ArchiveRequest | None = None,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """归档已结束的分析任务；归档只改变展示状态，不删除分析结果。"""

        _check_api_key(x_api_key)
        try:
            run = get_repository().archive_run(run_id, (payload or ArchiveRequest()).reason)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "success", "run": run}

    @app.post("/api/v1/runs/{run_id}/restore")
    def restore_run(
        run_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """恢复分析任务的默认展示状态。"""

        _check_api_key(x_api_key)
        try:
            run = get_repository().restore_run(run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "success", "run": run}

    @app.post("/api/v1/work-orders/{record_id}/archive")
    def archive_work_order(
        record_id: str,
        payload: ArchiveRequest | None = None,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """归档已完成或已关闭工单，并同步隐藏由它生成的历史案例。"""

        _check_api_key(x_api_key)
        try:
            work_order = get_repository().archive_work_order(
                record_id,
                (payload or ArchiveRequest()).reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "success", "work_order": work_order}

    @app.post("/api/v1/work-orders/{record_id}/restore")
    def restore_work_order(
        record_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """恢复已归档工单及其默认展示状态。"""

        _check_api_key(x_api_key)
        try:
            work_order = get_repository().restore_work_order(record_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "success", "work_order": work_order}

    @app.delete("/api/v1/work-orders/{record_id}")
    def delete_archived_work_order(
        record_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """永久删除已归档工单；正常队列中的工单必须先完成并归档。"""

        _check_api_key(x_api_key)
        try:
            result = get_repository().delete_archived_work_order(record_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "success", "deleted": result}

    @app.delete("/api/v1/runs/{run_id}")
    def delete_archived_run(
        run_id: str,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """永久删除已归档分析任务及其关联工单、通知和模型调用记录。"""

        _check_api_key(x_api_key)
        try:
            result = get_repository().delete_archived_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "success", "deleted": result}

    @app.post("/api/v1/model-compare")
    def model_compare(
        payload: ModelCompareRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """在同一份上传数据上运行多个检测器，返回可供万悟决策的比较结果。"""

        _check_api_key(x_api_key)
        file_id = payload.file_id
        source_path = _find_uploaded_file(file_id)

        detectors = list(payload.detectors)

        logger = RunLogger(run_id=f"run_{uuid.uuid4().hex[:12]}")
        base = payload.config.as_overrides()
        _start_persisted_run(
            logger,
            file_id,
            "model_compare",
            "multiple_detectors",
            {"detectors": detectors, "config": base},
        )
        records: list[dict[str, Any]] = []
        try:
            for detector in detectors:
                config = _parse_config({**base, "detector": detector})
                result = analyze_file(
                    source_path,
                    config=config,
                    write_report=False,
                    run_forecast=False,
                )
                metrics = result.metrics
                records.append(
                    {
                        "detector": detector,
                        "detector_name": result.detector_name,
                        "threshold": config.threshold,
                        "event_count": len(result.events),
                        "point_f1": metrics.f1_score if metrics else None,
                        "event_f1": metrics.event_f1_score if metrics else None,
                        "pr_auc": metrics.pr_auc if metrics else None,
                        "mean_detection_delay": metrics.mean_detection_delay if metrics else None,
                        "false_positive_events": (
                            metrics.false_positive_event_count if metrics else None
                        ),
                    }
                )
            response = {
                "run_id": logger.run_id,
                "status": "success",
                "file_id": file_id,
                "models": records,
                "selection_rule": "优先事件级 F1，其次 PR-AUC 和点级 F1；最终需结合误报与延迟人工确认",
            }
        except Exception as exc:
            log_record = logger.finish(
                "failed",
                "model_compare",
                {"file_id": file_id},
                error=str(exc),
            )
            _finish_persisted_run(log_record, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_record = logger.finish(
            "success",
            "model_compare",
            {"file_id": file_id, "detectors": detectors},
            {"model_count": len(records)},
        )
        _finish_persisted_run(log_record, response=response)
        return response

    @app.post("/api/v1/forecast-compare")
    def forecast_compare(
        payload: ForecastCompareRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """比较指定传感器的预测模型，供万悟工作流解释模型选择依据。"""

        _check_api_key(x_api_key)
        file_id = payload.file_id
        source_path = _find_uploaded_file(file_id)

        models = list(payload.models)
        unknown = set(models) - set(MODEL_LABELS)
        if not models or unknown:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的预测模型：{', '.join(sorted(unknown))}",
            )

        logger = RunLogger(run_id=f"run_{uuid.uuid4().hex[:12]}")
        _start_persisted_run(
            logger,
            file_id,
            "forecast_compare",
            "forecast_model_selection",
            {
                "models": models,
                "sensors": payload.sensors,
                "horizon": payload.horizon,
                "lookback": payload.lookback,
                "holdout": payload.holdout,
            },
        )
        try:
            dataframe = load_time_series(source_path)
            profile = build_profile(dataframe, source_path.name)
            requested_sensors = payload.sensors or profile.sensor_columns
            sensors = [str(item) for item in requested_sensors]
            invalid_sensors = set(sensors) - set(profile.sensor_columns)
            if not sensors or invalid_sensors:
                raise ValueError(f"数据中不存在传感器：{', '.join(sorted(invalid_sensors))}")

            horizon = payload.horizon
            lookback = payload.lookback
            holdout = payload.holdout
            results = forecast_sensors(
                dataframe,
                sensors,
                horizon=horizon,
                lookback=lookback,
                holdout=holdout,
                models=models,
            )
            response = {
                "run_id": logger.run_id,
                "status": "success",
                "file_id": file_id,
                "selection_rule": "每个传感器按时间顺序滚动回测，以 RMSE 为主、MAE 为辅选择最优模型",
                "forecast_results": results,
            }
        except Exception as exc:
            log_record = logger.finish(
                "failed",
                "forecast_compare",
                {"file_id": file_id},
                error=str(exc),
            )
            _finish_persisted_run(log_record, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_record = logger.finish(
            "success",
            "forecast_compare",
            {"file_id": file_id, "models": models, "sensors": sensors},
            {"sensor_count": len(results)},
        )
        _finish_persisted_run(log_record, response=response)
        return response


def run() -> None:
    """启动本地 HTTP 服务，供 PyCharm 或 uv 项目脚本调用。"""

    if app is None:
        raise RuntimeError("未安装 FastAPI，请执行 uv sync")
    import uvicorn

    uvicorn.run("app.api.server:app", host="0.0.0.0", port=8000, reload=False)
