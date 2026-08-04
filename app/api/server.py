"""时察千机 REST API。

接口使用受控 `file_id`，不接受任意服务器路径。万悟可通过 API 节点调用这些接口；本地
Streamlit 仍然直接调用分析核心，方便开发阶段离线运行。
"""

from __future__ import annotations

import secrets
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from app.analysis.detection import DETECTOR_RECOMMENDED_THRESHOLDS
from app.analysis.forecast import MODEL_LABELS, forecast_sensors
from app.analysis.pipeline import analyze_file
from app.analysis.profiling import build_profile
from app.api.jobs import JobQueueFullError, get_job_manager
from app.api.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    ErrorResponse,
    FileUploadResponse,
    ForecastCompareRequest,
    JobAcceptedResponse,
    JobCancelledResponse,
    JobCreateRequest,
    JobResultResponse,
    JobStatusResponse,
    ModelCompareRequest,
    RunIdRequest,
    WanwuJobAcceptedResponse,
    WanwuJobCreateRequest,
    WanwuWorkOrderListRequest,
    WanwuWorkOrderUpdateRequest,
    WorkOrderUpdateRequest,
)
from app.api.wanwu_openapi import build_wanwu_openapi
from app.config import get_settings
from app.data.loader import load_time_series
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
from app.storage import get_repository

try:
    from fastapi import FastAPI, File, Header, HTTPException, UploadFile
except ImportError:  # 让未安装 API 依赖时，核心分析和 Streamlit 仍可用。
    FastAPI = None
    File = Header = UploadFile = Any


settings = get_settings()
UPLOAD_DIR = settings.output_dir / "api_uploads"


@asynccontextmanager
async def _lifespan(_app: Any):
    """启动时清理中断任务，关闭时等待后台线程完成。"""

    get_repository().fail_incomplete_runs("服务重启导致任务中断，请重新提交")
    try:
        yield
    finally:
        cancelled_run_ids = get_job_manager().shutdown()
        get_job_manager.cache_clear()
        repository = get_repository()
        for run_id in cancelled_run_ids:
            repository.cancel_run(run_id, "服务关闭取消了尚未执行的排队任务")
        repository.fail_incomplete_runs("服务关闭导致任务中断，请重新提交")


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


def _check_api_key(api_key: str | None) -> None:
    """只有配置了服务密钥时才启用鉴权，便于本地开发。"""

    expected = getattr(settings, "industrial_api_key", "")
    if expected and not secrets.compare_digest(api_key or "", expected):
        raise HTTPException(status_code=401, detail="工业分析服务鉴权失败")


def _parse_config(payload: dict[str, Any] | None) -> AnalysisConfig:
    """将万悟工作流传入的可选参数限制在项目支持范围内。"""

    payload = payload or {}
    detector = str(payload.get("detector", settings.anomaly_detector))
    default_threshold = DETECTOR_RECOMMENDED_THRESHOLDS.get(
        detector,
        settings.anomaly_threshold,
    )
    return AnalysisConfig(
        detector=detector,
        threshold=float(payload.get("threshold", default_threshold)),
        rolling_window=max(5, int(payload.get("rolling_window", settings.rolling_window))),
        min_event_length=max(1, int(payload.get("min_event_length", settings.min_event_length))),
        merge_gap=max(0, int(payload.get("merge_gap", settings.merge_gap))),
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


def _result_payload(run_id: str, result: Any) -> dict[str, Any]:
    """返回稳定的机器可读协议，避免万悟依赖 Markdown 文本解析。"""

    summary = result.to_summary()
    return {
        "run_id": run_id,
        "status": "success",
        "data_profile": {
            "source_name": result.profile.source_name,
            "row_count": result.profile.row_count,
            "sensor_columns": result.profile.sensor_columns,
            "missing_total": result.profile.missing_total,
            "start_time": result.profile.start_time,
            "end_time": result.profile.end_time,
        },
        "detector": result.detector_name,
        "anomaly_events": [event.__dict__ for event in result.events],
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
        "summary": summary,
        "limitations": [
            "预测模型由滚动回测自动选择，仍需结合现场工况、设备边界和人工复核确认。",
            "候选根因来自内置通用故障模式，不是企业设备专属知识，不能替代现场确诊。",
            "无 anomaly 标签的企业数据不计算监督指标。",
        ],
    }


def _find_uploaded_file(file_id: str) -> Path:
    """校验 file_id、定位 CSV，并把历史上传目录补登记到数据库。"""

    if not file_id or Path(file_id).name != file_id:
        raise HTTPException(status_code=400, detail="必须提供合法 file_id")
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


def _submit_analysis_job(
    *,
    file_id: str,
    source_path: Path,
    operation: str,
    config: AnalysisConfig,
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


def _execute_analysis_job(
    run_id: str,
    file_id: str,
    source_path: Path,
    operation: str,
    config: AnalysisConfig,
) -> None:
    """后台执行分析或自动诊断，并把最终状态写回 SQLite。"""

    logger = RunLogger(run_id=run_id)
    repository = get_repository()
    try:
        repository.mark_run_running(run_id)
        result = analyze_file(source_path, config=config, write_report=True)
        response = _result_payload(run_id, result)
        diagnosis_status = None
        if operation == "diagnose":
            automatic = AutomaticDiagnosisService().diagnose(result)
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


if app:

    @app.get("/integrations/wanwu/openapi.json", include_in_schema=False)
    def wanwu_openapi() -> dict[str, Any]:
        """返回仅包含万悟可稳定调用工具的精简 OpenAPI。"""

        return build_wanwu_openapi(app.openapi(), settings.api_public_base_url)

    @app.get("/health")
    def health() -> dict[str, str]:
        """供万悟或部署平台检查服务是否在线。"""

        get_repository()
        return {"status": "ok", "service": "shichi-qianji", "database": "sqlite"}

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
            result = analyze_file(source_path, config=config, write_report=True)
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
            result = analyze_file(source_path, config=config, write_report=True)
            response = _result_payload(logger.run_id, result)
            automatic = AutomaticDiagnosisService().diagnose(result)
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
            status=payload.status,
            run_id=payload.run_id,
        )
        return {
            "status": "success",
            "work_order_count": len(work_orders),
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

    @app.get("/api/v1/runs")
    def list_runs(
        limit: int = 20,
        status: str | None = None,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """返回历史分析任务摘要，供万悟任务中心或看板调用。"""

        _check_api_key(x_api_key)
        runs = get_repository().list_runs(limit=limit, status=status)
        return {"status": "success", "run_count": len(runs), "runs": runs}

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

    @app.get("/api/v1/work-orders")
    def list_work_orders(
        limit: int = 50,
        status: str | None = None,
        run_id: str | None = None,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """按优先级返回待办工单，可筛选状态和所属任务。"""

        _check_api_key(x_api_key)
        work_orders = get_repository().list_work_orders(
            limit=limit,
            status=status,
            run_id=run_id,
        )
        return {
            "status": "success",
            "work_order_count": len(work_orders),
            "work_orders": work_orders,
        }

    @app.patch("/api/v1/work-orders/{record_id}")
    def update_work_order(
        record_id: str,
        payload: WorkOrderUpdateRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """接收万悟或现场人员回写的工单状态与确认结果。"""

        _check_api_key(x_api_key)
        try:
            work_order = get_repository().update_work_order(
                record_id,
                payload.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "success", "work_order": work_order}

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
