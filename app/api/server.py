"""时察千机 REST API。

接口使用受控 `file_id`，不接受任意服务器路径。万悟可通过 API 节点调用这些接口；本地
Streamlit 仍然直接调用分析核心，方便开发阶段离线运行。
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from app.analysis.detection import DETECTOR_RECOMMENDED_THRESHOLDS
from app.analysis.forecast import MODEL_LABELS, forecast_sensors
from app.analysis.pipeline import analyze_file
from app.analysis.profiling import build_profile
from app.config import get_settings
from app.data.loader import load_time_series
from app.diagnosis import AutomaticDiagnosisService, diagnosis_to_dict
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
app = FastAPI(title="时察千机工业时序分析服务", version="0.4.0") if FastAPI else None


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


if app:

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

    @app.post("/api/v1/files")
    async def upload_file(
        file: Annotated[UploadFile, File(...)],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """上传 CSV 并返回受控 file_id。"""

        _check_api_key(x_api_key)
        if not file.filename or not file.filename.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail="当前只允许上传 CSV 文件")
        file_id = uuid.uuid4().hex
        target_dir = UPLOAD_DIR / file_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / Path(file.filename).name
        target_path.write_bytes(await file.read())
        metadata = get_repository().register_file(file_id, target_path.name, target_path)
        return {
            "file_id": file_id,
            "file_name": target_path.name,
            "sha256": metadata["sha256"],
            "size_bytes": metadata["size_bytes"],
        }

    @app.post("/api/v1/analyze")
    def analyze(
        payload: dict[str, Any],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """分析已上传文件，供万悟工作流 API 节点调用。"""

        _check_api_key(x_api_key)
        file_id = str(payload.get("file_id", ""))
        source_path = _find_uploaded_file(file_id)

        logger = RunLogger(run_id=f"run_{uuid.uuid4().hex[:12]}")
        config = _parse_request_config(payload.get("config"))
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

    @app.post("/api/v1/diagnose")
    def diagnose(
        payload: dict[str, Any],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """一次完成确定性分析、知识检索和单次大模型诊断。"""

        _check_api_key(x_api_key)
        file_id = str(payload.get("file_id", ""))
        source_path = _find_uploaded_file(file_id)

        logger = RunLogger(run_id=f"run_{uuid.uuid4().hex[:12]}")
        config = _parse_request_config(payload.get("config"))
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
        payload: dict[str, Any],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """接收万悟或现场人员回写的工单状态与确认结果。"""

        _check_api_key(x_api_key)
        try:
            work_order = get_repository().update_work_order(record_id, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "success", "work_order": work_order}

    @app.post("/api/v1/model-compare")
    def model_compare(
        payload: dict[str, Any],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """在同一份上传数据上运行多个检测器，返回可供万悟决策的比较结果。"""

        _check_api_key(x_api_key)
        file_id = str(payload.get("file_id", ""))
        source_path = _find_uploaded_file(file_id)

        requested = payload.get(
            "detectors",
            [
                "mad",
                "isolation_forest",
                "pca_reconstruction",
                "window_autoencoder",
                "time_frequency_relation",
                "hybrid",
            ],
        )
        detectors = [str(item) for item in requested]
        allowed = {
            "mad",
            "isolation_forest",
            "pca_reconstruction",
            "window_autoencoder",
            "time_frequency_relation",
            "hybrid",
        }
        if not detectors or any(detector not in allowed for detector in detectors):
            raise HTTPException(
                status_code=400,
                detail=(
                    "detectors 只能包含 mad、isolation_forest、"
                    "pca_reconstruction、window_autoencoder、time_frequency_relation、hybrid"
                ),
            )

        logger = RunLogger(run_id=f"run_{uuid.uuid4().hex[:12]}")
        base = payload.get("config") or {}
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
        payload: dict[str, Any],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """比较指定传感器的预测模型，供万悟工作流解释模型选择依据。"""

        _check_api_key(x_api_key)
        file_id = str(payload.get("file_id", ""))
        source_path = _find_uploaded_file(file_id)

        requested_models = payload.get("models", list(MODEL_LABELS))
        models = [str(item) for item in requested_models]
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
                "sensors": payload.get("sensors"),
                "horizon": payload.get("horizon", settings.forecast_horizon),
                "lookback": payload.get("lookback", settings.forecast_lookback),
                "holdout": payload.get("holdout", settings.forecast_holdout),
            },
        )
        try:
            dataframe = load_time_series(source_path)
            profile = build_profile(dataframe, source_path.name)
            requested_sensors = payload.get("sensors") or profile.sensor_columns
            sensors = [str(item) for item in requested_sensors]
            invalid_sensors = set(sensors) - set(profile.sensor_columns)
            if not sensors or invalid_sensors:
                raise ValueError(f"数据中不存在传感器：{', '.join(sorted(invalid_sensors))}")

            horizon = max(1, min(300, int(payload.get("horizon", settings.forecast_horizon))))
            lookback = max(30, int(payload.get("lookback", settings.forecast_lookback)))
            holdout = max(5, int(payload.get("holdout", settings.forecast_holdout)))
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
