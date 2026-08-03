"""预测、预警和 API 协议回归测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analysis import analyze_file
from app.analysis.forecast import build_time_frequency_features, forecast_sensors
from app.analysis.warning import build_risk_alerts
from app.api.server import _result_payload
from app.models import AnalysisConfig, AnomalyEvent


def test_forecast_returns_future_values_and_backtest_metrics() -> None:
    """线性时序应生成正确长度的未来预测和回测字段。"""

    row_count = 120
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "Pressure": np.linspace(10.0, 12.0, row_count),
        }
    )

    result = forecast_sensors(dataframe, ["Pressure"], horizon=12, lookback=60, holdout=12)

    assert len(result["Pressure"]["预测值"]) == 12
    assert result["Pressure"]["方向"] == "持续上升"
    assert result["Pressure"]["回测"]["样本数"] == 12
    assert result["Pressure"]["回测"]["RMSE"] is not None
    assert result["Pressure"]["模型"] == "linear_trend"
    assert len(result["Pressure"]["候选模型回测"]) >= 3
    assert result["Pressure"]["不确定度"]["预测可信度"] in {"高", "中", "低"}


def test_time_frequency_features_are_finite() -> None:
    """周期信号应产生有限的主频、频带能量和谱熵。"""

    x_axis = np.arange(128, dtype=float)
    features = build_time_frequency_features(np.sin(2 * np.pi * x_axis / 16))

    assert features["主频"] > 0
    assert 0 <= features["低频能量占比"] <= 1
    assert 0 <= features["高频能量占比"] <= 1
    assert 0 <= features["谱熵"] <= 1


def test_warning_combines_current_event_and_forecast_risk() -> None:
    """当前事件和未来趋势风险都应形成结构化预警。"""

    event = AnomalyEvent(
        start_index=10,
        end_index=14,
        start_time=pd.Timestamp("2026-01-01"),
        end_time=pd.Timestamp("2026-01-01 00:00:04"),
        duration_points=5,
        peak_score=8.0,
        severity="高风险",
        dominant_sensors=["Pressure"],
        sensor_scores={"Pressure": 8.0},
    )
    alerts = build_risk_alerts(
        {"Pressure": {"风险": "需关注", "方向": "持续上升", "预测末值偏移标准差": 3.2, "回测": {"RMSE": 0.2}}},
        [event],
    )

    assert len(alerts) == 2
    assert {alert["类型"] for alert in alerts} == {"当前异常事件", "趋势预测预警"}


def test_api_module_is_importable() -> None:
    """安装 FastAPI 后 API 应能被万悟或 Uvicorn 导入。"""

    server = pytest.importorskip("app.api.server")
    if server.app is None:
        pytest.skip("当前环境未安装 FastAPI")
    paths = {route.path for route in server.app.routes}
    assert "/health" in paths
    assert "/api/v1/models" in paths
    assert "/api/v1/files" in paths
    assert "/api/v1/analyze" in paths
    assert "/api/v1/diagnose" in paths
    assert "/api/v1/runs" in paths
    assert "/api/v1/runs/{run_id}" in paths
    assert "/api/v1/work-orders" in paths
    assert "/api/v1/work-orders/{record_id}" in paths
    assert "/api/v1/model-compare" in paths
    assert "/api/v1/forecast-compare" in paths


def test_api_payload_exposes_root_causes_and_work_orders(tmp_path) -> None:
    """万悟分析协议应直接暴露确定性根因和工单草案。"""

    rows = 180
    pressure = np.ones(rows)
    flow = np.ones(rows)
    pressure[110:130] += 3.0
    flow[110:130] -= 0.8
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=rows, freq="s"),
            "Pressure": pressure,
            "Volume Flow RateRMS": flow,
        }
    )
    csv_path = tmp_path / "api_sample.csv"
    dataframe.to_csv(csv_path, sep=";", index=False)
    result = analyze_file(
        csv_path,
        AnalysisConfig(
            detector="mad",
            threshold=3.0,
            rolling_window=31,
            min_event_length=2,
        ),
        write_report=False,
        run_forecast=False,
        run_regime=False,
    )
    payload = _result_payload("run_test", result)

    assert payload["root_cause_diagnoses"]
    assert payload["work_order_drafts"]
    assert payload["work_order_drafts"][0]["record_id"].startswith("run_test:")
