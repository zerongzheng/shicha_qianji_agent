"""确定性根因排序和工单草案测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.diagnosis import diagnose_root_causes
from app.models import AnomalyEvent, HistoricalCaseMatch


def _event(
    dataframe: pd.DataFrame,
    sensors: list[str],
    start: int = 120,
    end: int = 149,
) -> AnomalyEvent:
    """构造一段固定高风险事件，便于只验证根因引擎。"""

    return AnomalyEvent(
        start_index=start,
        end_index=end,
        start_time=dataframe["datetime"].iloc[start],
        end_time=dataframe["datetime"].iloc[end],
        duration_points=end - start + 1,
        peak_score=9.0,
        severity="高风险",
        dominant_sensors=sensors,
        sensor_scores={sensor: 8.0 - index for index, sensor in enumerate(sensors)},
    )


def test_pressure_up_flow_down_ranks_restriction_first() -> None:
    """压力升高且流量下降时，应优先排查阀门卡滞、堵塞和出口阻力。"""

    rows = 220
    random = np.random.default_rng(3)
    pressure = random.normal(10.0, 0.05, rows)
    flow = random.normal(5.0, 0.04, rows)
    current = random.normal(2.0, 0.02, rows)
    pressure[120:150] += 1.5
    flow[120:150] -= 1.0
    current[120:150] += 0.3
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=rows, freq="s"),
            "Pressure": pressure,
            "Volume Flow RateRMS": flow,
            "Current": current,
        }
    )

    diagnoses, work_orders = diagnose_root_causes(
        dataframe,
        ["Pressure", "Volume Flow RateRMS", "Current"],
        [_event(dataframe, ["Pressure", "Volume Flow RateRMS", "Current"])],
        relationship_diagnostics=[],
        operating_regimes=None,
        trend_summary={},
        forecast_results={},
    )

    primary = diagnoses[0].primary_candidate
    assert primary is not None
    assert primary.pattern_id == "flow_restriction"
    assert primary.confidence >= 0.6
    assert work_orders[0].priority == "P1"
    assert "阀门" in work_orders[0].title


def test_vibration_and_current_rise_ranks_mechanical_load_first() -> None:
    """振动和电流同步升高时，应优先排查机械负载与卡阻。"""

    rows = 220
    random = np.random.default_rng(8)
    vibration = random.normal(0.2, 0.01, rows)
    current = random.normal(2.0, 0.03, rows)
    temperature = random.normal(35.0, 0.1, rows)
    vibration[120:150] += 0.25
    current[120:150] += 0.8
    temperature[120:150] += 1.0
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=rows, freq="s"),
            "Accelerometer1RMS": vibration,
            "Current": current,
            "Temperature": temperature,
        }
    )

    diagnoses, _ = diagnose_root_causes(
        dataframe,
        ["Accelerometer1RMS", "Current", "Temperature"],
        [_event(dataframe, ["Accelerometer1RMS", "Current", "Temperature"])],
        relationship_diagnostics=[],
        operating_regimes=None,
        trend_summary={},
        forecast_results={},
    )

    primary = diagnoses[0].primary_candidate
    assert primary is not None
    assert primary.pattern_id == "mechanical_load_or_jam"
    assert any("Current" in item for item in primary.supporting_evidence)


def test_isolated_sensor_change_prioritizes_measurement_chain() -> None:
    """只有单个测点显著偏离时，应先排除传感器和采集链路。"""

    rows = 220
    random = np.random.default_rng(12)
    pressure = random.normal(10.0, 0.05, rows)
    flow = random.normal(5.0, 0.05, rows)
    current = random.normal(2.0, 0.03, rows)
    pressure[120:150] += 2.0
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=rows, freq="s"),
            "Pressure": pressure,
            "Volume Flow RateRMS": flow,
            "Current": current,
        }
    )

    diagnoses, _ = diagnose_root_causes(
        dataframe,
        ["Pressure", "Volume Flow RateRMS", "Current"],
        [_event(dataframe, ["Pressure"])],
        relationship_diagnostics=[],
        operating_regimes=None,
        trend_summary={},
        forecast_results={},
    )

    primary = diagnoses[0].primary_candidate
    assert primary is not None
    assert primary.pattern_id == "sensor_or_acquisition"
    assert primary.confidence <= 0.78


def test_no_events_produces_no_diagnosis_or_work_order() -> None:
    """没有异常事件时不应凭空创建根因和检修任务。"""

    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=30, freq="s"),
            "Pressure": np.ones(30),
        }
    )

    diagnoses, work_orders = diagnose_root_causes(
        dataframe,
        ["Pressure"],
        [],
        relationship_diagnostics=[],
        operating_regimes=None,
        trend_summary={},
        forecast_results={},
    )

    assert diagnoses == []
    assert work_orders == []


def test_confirmed_historical_case_enters_ranked_candidates() -> None:
    """相似现场案例应进入候选排序，但单个反馈不强行覆盖完整机理证据。"""

    rows = 220
    pressure = np.ones(rows) * 10
    flow = np.ones(rows) * 5
    pressure[120:150] += 1.4
    flow[120:150] -= 0.9
    dataframe = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=rows, freq="s"),
            "Pressure": pressure,
            "Volume Flow RateRMS": flow,
        }
    )
    captured: dict[int, list[HistoricalCaseMatch]] = {}

    def matcher(_changes, _sensors, _regime):
        return [
            HistoricalCaseMatch(
                case_id="CASE-run_old:wo_old",
                confirmed_cause="阀门执行器反馈齿轮卡滞",
                similarity=0.95,
                source_run_id="run_old",
                source_record_id="run_old:wo_old",
                matched_sensor_groups=("flow", "pressure"),
                matched_directions=("flow:down", "pressure:up"),
                evidence_summary=("压力升高且流量下降",),
                feedback_note="更换执行器齿轮后恢复",
                handled_by="运维组",
                closed_at="2026-07-01T10:00:00+08:00",
            )
        ]

    diagnoses, _ = diagnose_root_causes(
        dataframe,
        ["Pressure", "Volume Flow RateRMS"],
        [_event(dataframe, ["Pressure", "Volume Flow RateRMS"])],
        relationship_diagnostics=[],
        operating_regimes=None,
        trend_summary={},
        forecast_results={},
        case_matcher=matcher,
        historical_matches_output=captured,
    )

    assert diagnoses[0].primary_candidate is not None
    historical = next(
        item
        for item in diagnoses[0].candidates
        if item.name == "阀门执行器反馈齿轮卡滞"
    )
    assert "历史已闭环工单" in historical.source
    assert historical.confidence >= 0.7
    assert captured[1][0].source_record_id == "run_old:wo_old"
