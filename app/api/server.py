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
        "work_order_drafts": [asdict(item) for item in result.work_order_drafts],
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


if app:

    @app.get("/health")
    def health() -> dict[str, str]:
        """供万悟或部署平台检查服务是否在线。"""

        return {"status": "ok", "service": "shichi-qianji"}

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
    ) -> dict[str, str]:
        """上传 CSV 并返回受控 file_id。"""

        _check_api_key(x_api_key)
        if not file.filename or not file.filename.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail="当前只允许上传 CSV 文件")
        file_id = uuid.uuid4().hex
        target_dir = UPLOAD_DIR / file_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / Path(file.filename).name
        target_path.write_bytes(await file.read())
        return {"file_id": file_id, "file_name": target_path.name}

    @app.post("/api/v1/analyze")
    def analyze(
        payload: dict[str, Any],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """分析已上传文件，供万悟工作流 API 节点调用。"""

        _check_api_key(x_api_key)
        file_id = str(payload.get("file_id", ""))
        if not file_id or Path(file_id).name != file_id:
            raise HTTPException(status_code=400, detail="必须提供合法 file_id")
        matches = list((UPLOAD_DIR / file_id).glob("*.csv"))
        if len(matches) != 1:
            raise HTTPException(status_code=404, detail="找不到对应上传文件")

        logger = RunLogger(run_id=f"run_{uuid.uuid4().hex[:12]}")
        try:
            config = _parse_config(payload.get("config"))
            result = analyze_file(matches[0], config=config, write_report=True)
            response = _result_payload(logger.run_id, result)
            logger.finish(
                "success",
                "analyze",
                {"file_id": file_id, "detector": config.detector},
                {"events": len(result.events), "alerts": len(result.risk_alerts)},
            )
            return response
        except Exception as exc:
            logger.finish("failed", "analyze", {"file_id": file_id}, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/diagnose")
    def diagnose(
        payload: dict[str, Any],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """一次完成确定性分析、知识检索和单次大模型诊断。"""

        _check_api_key(x_api_key)
        file_id = str(payload.get("file_id", ""))
        if not file_id or Path(file_id).name != file_id:
            raise HTTPException(status_code=400, detail="必须提供合法 file_id")
        matches = list((UPLOAD_DIR / file_id).glob("*.csv"))
        if len(matches) != 1:
            raise HTTPException(status_code=404, detail="找不到对应上传文件")

        logger = RunLogger(run_id=f"run_{uuid.uuid4().hex[:12]}")
        try:
            config = _parse_config(payload.get("config"))
            result = analyze_file(matches[0], config=config, write_report=True)
            response = _result_payload(logger.run_id, result)
            automatic = AutomaticDiagnosisService().diagnose(result)
            response["automatic_diagnosis"] = automatic.to_dict()
            logger.finish(
                "success",
                "diagnose",
                {"file_id": file_id, "detector": config.detector},
                {
                    "events": len(result.events),
                    "alerts": len(result.risk_alerts),
                    "diagnosis_status": automatic.status,
                },
            )
            return response
        except Exception as exc:
            logger.finish("failed", "diagnose", {"file_id": file_id}, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/model-compare")
    def model_compare(
        payload: dict[str, Any],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """在同一份上传数据上运行多个检测器，返回可供万悟决策的比较结果。"""

        _check_api_key(x_api_key)
        file_id = str(payload.get("file_id", ""))
        if not file_id or Path(file_id).name != file_id:
            raise HTTPException(status_code=400, detail="必须提供合法 file_id")
        matches = list((UPLOAD_DIR / file_id).glob("*.csv"))
        if len(matches) != 1:
            raise HTTPException(status_code=404, detail="找不到对应上传文件")

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
        records: list[dict[str, Any]] = []
        try:
            base = payload.get("config") or {}
            for detector in detectors:
                config = _parse_config({**base, "detector": detector})
                result = analyze_file(
                    matches[0],
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
            logger.finish(
                "success",
                "model_compare",
                {"file_id": file_id, "detectors": detectors},
                {"model_count": len(records)},
            )
            return response
        except Exception as exc:
            logger.finish("failed", "model_compare", {"file_id": file_id}, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/forecast-compare")
    def forecast_compare(
        payload: dict[str, Any],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """比较指定传感器的预测模型，供万悟工作流解释模型选择依据。"""

        _check_api_key(x_api_key)
        file_id = str(payload.get("file_id", ""))
        if not file_id or Path(file_id).name != file_id:
            raise HTTPException(status_code=400, detail="必须提供合法 file_id")
        matches = list((UPLOAD_DIR / file_id).glob("*.csv"))
        if len(matches) != 1:
            raise HTTPException(status_code=404, detail="找不到对应上传文件")

        requested_models = payload.get("models", list(MODEL_LABELS))
        models = [str(item) for item in requested_models]
        unknown = set(models) - set(MODEL_LABELS)
        if not models or unknown:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的预测模型：{', '.join(sorted(unknown))}",
            )

        logger = RunLogger(run_id=f"run_{uuid.uuid4().hex[:12]}")
        try:
            dataframe = load_time_series(matches[0])
            profile = build_profile(dataframe, matches[0].name)
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
            logger.finish(
                "success",
                "forecast_compare",
                {"file_id": file_id, "models": models, "sensors": sensors},
                {"sensor_count": len(results)},
            )
            return response
        except Exception as exc:
            logger.finish("failed", "forecast_compare", {"file_id": file_id}, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc


def run() -> None:
    """启动本地 HTTP 服务，供 PyCharm 或 uv 项目脚本调用。"""

    if app is None:
        raise RuntimeError("未安装 FastAPI，请执行 uv sync")
    import uvicorn

    uvicorn.run("app.api.server:app", host="0.0.0.0", port=8000, reload=False)
