"""时察千机竞赛演示页面。

页面围绕“发现风险、查看证据、获得处置建议”组织，聊天 Agent 是辅助入口，
不是整个项目的唯一界面。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.agent import IndustrialAgent
from app.analysis import analyze_file
from app.config import get_settings
from app.data import save_uploaded_file
from app.models import AnalysisConfig, AnalysisResult


def run_app() -> None:
    """配置并渲染完整 Streamlit 页面。"""

    st.set_page_config(
        page_title="时察千机 | 工业时序智能体",
        page_icon="SQ",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_styles()
    _render_header()
    analysis_request = _render_sidebar()

    if analysis_request is not None:
        source_path, config = analysis_request
        with st.spinner("正在完成数据画像、异常检测、趋势分析与报告生成..."):
            try:
                st.session_state["analysis_result"] = analyze_file(source_path, config=config)
            # 页面是用户输入边界，需要把路径、格式和算法错误转成可读提示。
            except Exception as exc:  # noqa: BLE001
                st.error(f"分析失败：{exc}")

    result: AnalysisResult | None = st.session_state.get("analysis_result")
    if result is None:
        _render_empty_state()
        return

    overview_tab, evidence_tab, report_tab, agent_tab = st.tabs(
        ["风险总览", "时序证据", "诊断报告", "智能体协同"]
    )
    with overview_tab:
        _render_overview(result)
    with evidence_tab:
        _render_evidence(result)
    with report_tab:
        _render_report(result)
    with agent_tab:
        _render_agent(result)


def _render_header() -> None:
    """显示项目名称与当前定位。"""

    st.markdown(
        """
        <div class="project-header">
          <div>
            <div class="project-kicker">INDUSTRIAL TIME-SERIES AGENT</div>
            <h1>时察千机</h1>
            <p>多源感知 · 异常诊断 · 趋势预警 · 运维决策</p>
          </div>
          <div class="status-badge">分析引擎在线</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar() -> tuple[Path, AnalysisConfig] | None:
    """收集数据路径和算法参数；只有点击按钮时才触发计算。"""

    settings = get_settings()
    with st.sidebar:
        st.subheader("分析任务")
        source_mode = st.radio("数据来源", ["默认 SKAB 样例", "本地路径", "上传 CSV"])

        if source_mode == "默认 SKAB 样例":
            source_path = settings.default_skab_file
            st.caption(str(source_path))
        elif source_mode == "本地路径":
            raw_path = st.text_input("CSV 文件路径", value=str(settings.default_skab_file))
            source_path = Path(raw_path)
        else:
            uploaded_file = st.file_uploader("选择工业时序 CSV", type=["csv"])
            source_path = None
            if uploaded_file is not None:
                source_path = save_uploaded_file(uploaded_file, settings.output_dir / "uploads")

        st.divider()
        st.subheader("检测参数")
        detector_options = ["hybrid", "mad", "isolation_forest"]
        detector = st.selectbox(
            "异常检测器",
            options=detector_options,
            index=detector_options.index(settings.anomaly_detector),
            format_func={
                "hybrid": "时序-工况混合检测器（推荐）",
                "mad": "稳健 MAD",
                "isolation_forest": "Isolation Forest",
            }.get,
        )
        threshold = st.slider(
            "异常阈值",
            min_value=2.0,
            max_value=10.0,
            value=float(settings.anomaly_threshold),
            step=0.1,
            help="越低越敏感，也更容易误报。",
        )
        window = st.number_input(
            "滚动窗口（采样点）",
            min_value=5,
            max_value=501,
            value=int(settings.rolling_window),
            step=2,
        )
        min_event_length = st.number_input(
            "最短事件长度",
            min_value=1,
            max_value=60,
            value=int(settings.min_event_length),
        )

        run_clicked = st.button("开始智能分析", type="primary", width="stretch")
        st.caption("原始 CSV 由本地算法处理，大模型只读取结构化分析摘要。")

    if not run_clicked:
        return None
    if source_path is None:
        st.warning("请先上传一个 CSV 文件。")
        return None

    odd_window = int(window) if int(window) % 2 == 1 else int(window) + 1
    return Path(source_path), AnalysisConfig(
        detector=str(detector),
        threshold=float(threshold),
        rolling_window=odd_window,
        min_event_length=int(min_event_length),
        merge_gap=settings.merge_gap,
        contamination=settings.contamination,
    )


def _render_empty_state() -> None:
    """首次进入页面时给出简洁的可执行入口。"""

    settings = get_settings()
    left, right = st.columns([1.25, 0.75], gap="large")
    with left:
        st.subheader("从一份工业时序数据开始")
        st.write(
            "系统将自动完成字段识别、数据质量检查、多变量异常检测、标签评估、"
            "趋势研判和运维建议生成。"
        )
        st.info("在左侧选择数据后点击“开始智能分析”。")
    with right:
        st.metric("默认样例", "SKAB valve1 / 0.csv")
        st.caption(f"数据可用：{'是' if settings.default_skab_file.exists() else '否'}")


def _render_overview(result: AnalysisResult) -> None:
    """显示最值得决策者关注的风险和量化指标。"""

    highest_risk = result.events[0].severity if result.events else "正常"
    metrics = result.metrics
    metric_columns = st.columns(6)
    metric_columns[0].metric("设备风险", highest_risk)
    metric_columns[1].metric("检测器", result.detector_name)
    metric_columns[2].metric("异常事件", len(result.events))
    metric_columns[3].metric("数据点", result.profile.row_count)
    metric_columns[4].metric("点级 F1", f"{metrics.f1_score:.3f}" if metrics else "无标签")
    metric_columns[5].metric(
        "事件级 F1",
        f"{metrics.event_f1_score:.3f}" if metrics else "无标签",
    )

    left, right = st.columns([1.15, 0.85], gap="large")
    with left:
        st.subheader("设备级风险轨迹")
        st.plotly_chart(_build_risk_chart(result), width="stretch")
    with right:
        st.subheader("优先处置")
        for index, recommendation in enumerate(result.recommendations[:5], start=1):
            st.markdown(f"**{index}.** {recommendation}")

    if result.risk_alerts:
        st.subheader("预测驱动预警")
        for alert in result.risk_alerts[:6]:
            evidence = "；".join(alert.get("证据", []))
            st.warning(f"{alert.get('等级', '需关注')}｜{alert.get('类型', '风险预警')}｜{evidence}")

    if result.forecast_results:
        st.subheader("未来趋势预测")
        forecast_rows = [
            {
                "传感器": sensor,
                "最优模型": detail.get("模型名称", detail.get("模型", "未知")),
                "方向": detail.get("方向", "未知"),
                "预测风险": detail.get("风险", "未知"),
                "可信度": detail.get("不确定度", {}).get("预测可信度", "未知"),
                "当前值": detail.get("当前值"),
                "预测末值": detail.get("预测末值"),
                "回测 RMSE": detail.get("回测", {}).get("RMSE"),
                "回测 MAPE": detail.get("回测", {}).get("MAPE"),
            }
            for sensor, detail in result.forecast_results.items()
        ]
        st.dataframe(pd.DataFrame(forecast_rows), hide_index=True, width="stretch")

        selected_forecast_sensor = st.selectbox(
            "查看预测证据",
            options=list(result.forecast_results),
            key="forecast_sensor",
        )
        selected_forecast = result.forecast_results[selected_forecast_sensor]
        st.plotly_chart(
            _build_forecast_chart(result, selected_forecast_sensor),
            width="stretch",
        )
        left, right = st.columns(2, gap="large")
        with left:
            st.caption("候选模型滚动回测")
            model_rows = [
                {
                    "模型": model_name,
                    "MAE": metrics.get("MAE"),
                    "RMSE": metrics.get("RMSE"),
                    "MAPE": metrics.get("MAPE"),
                    "样本数": metrics.get("样本数"),
                }
                for model_name, metrics in selected_forecast.get("候选模型回测", {}).items()
            ]
            st.dataframe(pd.DataFrame(model_rows), hide_index=True, width="stretch")
        with right:
            st.caption("时频特征与不确定度")
            st.json(
                {
                    "模型选择": selected_forecast.get("选择依据"),
                    "频域特征": selected_forecast.get("频域特征", {}),
                    "不确定度": selected_forecast.get("不确定度", {}),
                }
            )

    st.subheader("高风险事件")
    if not result.events:
        st.success("当前参数下未形成连续异常事件。")
        return
    event_rows = [
        {
            "风险": event.severity,
            "开始时间": event.start_time,
            "结束时间": event.end_time,
            "持续点数": event.duration_points,
            "峰值分数": round(event.peak_score, 2),
            "主导传感器": "、".join(event.dominant_sensors),
        }
        for event in result.events[:12]
    ]
    st.dataframe(pd.DataFrame(event_rows), hide_index=True, width="stretch")


def _render_evidence(result: AnalysisResult) -> None:
    """展示单传感器曲线、异常点和算法分数，支撑可解释诊断。"""

    selected_sensor = st.selectbox("选择传感器", result.profile.sensor_columns)
    st.plotly_chart(_build_sensor_chart(result, selected_sensor), width="stretch")

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("传感器画像")
        profile = next(item for item in result.profile.sensors if item.name == selected_sensor)
        st.json(
            {
                "最小值": round(profile.min_value, 4),
                "最大值": round(profile.max_value, 4),
                "均值": round(profile.mean_value, 4),
                "标准差": round(profile.std_value, 4),
                "缺失率": f"{profile.missing_rate:.2%}",
            }
        )
    with right:
        st.subheader("近期趋势")
        detail = result.trend_summary.get(selected_sensor)
        if detail:
            st.json(detail)
        else:
            st.success("该传感器末段未发现明显趋势漂移。")


def _render_report(result: AnalysisResult) -> None:
    """显示并下载自动生成的分析报告。"""

    st.download_button(
        "下载 Markdown 报告",
        data=result.report_text,
        file_name=f"{result.source_path.stem}_analysis.md",
        mime="text/markdown",
    )
    st.markdown(result.report_text)


def _render_agent(result: AnalysisResult) -> None:
    """提供基于当前结果的自然语言协同分析。"""

    settings = get_settings()
    st.caption("Agent 可调用同一套分析工具和本地工业知识库，不替代确定性算法。")
    st.code(json.dumps(result.to_summary(), ensure_ascii=False, indent=2, default=str), language="json")

    if not settings.llm_enabled:
        st.warning("尚未检测到 DASHSCOPE_API_KEY。基础分析可正常使用，配置密钥后开放对话。")
        return

    if "messages" not in st.session_state:
        st.session_state["messages"] = []
    for message in st.session_state["messages"]:
        st.chat_message(message["role"]).write(message["content"])

    question = st.chat_input("例如：结合当前结果，解释最严重事件的证据和排查顺序")
    if not question:
        return
    enriched_question = (
        f"当前页面已经分析的文件是：{result.source_path}。"
        f"当前结构化摘要如下：{json.dumps(result.to_summary(), ensure_ascii=False, default=str)}\n"
        f"用户问题：{question}"
    )
    st.session_state["messages"].append({"role": "user", "content": question})
    st.chat_message("user").write(question)

    try:
        if "industrial_agent" not in st.session_state:
            st.session_state["industrial_agent"] = IndustrialAgent()
        response = st.chat_message("assistant").write_stream(
            st.session_state["industrial_agent"].stream(enriched_question)
        )
        st.session_state["messages"].append({"role": "assistant", "content": str(response)})
    # 大模型网络、鉴权和工具调用错误都应停留在对话区域，不能中断分析看板。
    except Exception as exc:  # noqa: BLE001
        st.error(f"Agent 调用失败：{exc}")


def _build_risk_chart(result: AnalysisResult) -> go.Figure:
    """绘制设备级异常分数，并叠加 SKAB 标签区间。"""

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=result.dataframe["datetime"],
            y=result.combined_score,
            name="设备风险分数",
            line={"color": "#D14D3F", "width": 1.8},
        )
    )
    predicted_indexes = result.predicted_labels.astype(bool)
    figure.add_trace(
        go.Scatter(
            x=result.dataframe.loc[predicted_indexes, "datetime"],
            y=result.combined_score.loc[predicted_indexes],
            name="检测异常",
            mode="markers",
            marker={"color": "#161B22", "size": 5},
        )
    )
    figure.update_layout(
        height=390,
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        legend={"orientation": "h", "y": 1.08},
        xaxis_title=None,
        yaxis_title="稳健异常分数",
    )
    return figure


def _build_sensor_chart(result: AnalysisResult, sensor: str) -> go.Figure:
    """绘制传感器原始值，并标注参与该事件的异常时间点。"""

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=result.dataframe["datetime"],
            y=result.dataframe[sensor],
            name=sensor,
            line={"color": "#197278", "width": 1.5},
        )
    )
    anomaly_mask = result.predicted_labels.astype(bool)
    figure.add_trace(
        go.Scatter(
            x=result.dataframe.loc[anomaly_mask, "datetime"],
            y=result.dataframe.loc[anomaly_mask, sensor],
            name="设备异常时段",
            mode="markers",
            marker={"color": "#D14D3F", "size": 5},
        )
    )
    figure.update_layout(
        height=460,
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        legend={"orientation": "h", "y": 1.08},
        xaxis_title=None,
        yaxis_title=sensor,
    )
    return figure


def _build_forecast_chart(result: AnalysisResult, sensor: str) -> go.Figure:
    """绘制近期历史、未来预测和 95% 预测区间。"""

    detail = result.forecast_results[sensor]
    future_times = pd.to_datetime(detail.get("预测时间", []))
    predictions = detail.get("预测值", [])
    lower = detail.get("下界", [])
    upper = detail.get("上界", [])
    history_size = min(180, len(result.dataframe))
    history = result.dataframe.iloc[-history_size:]

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=history["datetime"],
            y=history[sensor],
            name="历史值",
            line={"color": "#197278", "width": 1.6},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=future_times,
            y=upper,
            name="95% 上界",
            line={"color": "rgba(209,77,63,0)", "width": 0},
            showlegend=False,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=future_times,
            y=lower,
            name="95% 预测区间",
            line={"color": "rgba(209,77,63,0)", "width": 0},
            fill="tonexty",
            fillcolor="rgba(209,77,63,0.16)",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=future_times,
            y=predictions,
            name=f"预测值｜{detail.get('模型名称', detail.get('模型', '最优模型'))}",
            line={"color": "#D14D3F", "width": 2.2, "dash": "dash"},
        )
    )
    figure.update_layout(
        height=430,
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        legend={"orientation": "h", "y": 1.08},
        xaxis_title=None,
        yaxis_title=sensor,
    )
    return figure


def _inject_styles() -> None:
    """加入少量项目级样式，保持工业看板的克制和信息密度。"""

    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.4rem; padding-bottom: 3rem; }
        .project-header {
            display: flex; align-items: center; justify-content: space-between;
            border-bottom: 1px solid #d7dce1; padding: 0.2rem 0 1.1rem; margin-bottom: 1rem;
        }
        .project-header h1 { margin: 0.1rem 0; font-size: 2.2rem; color: #172026; }
        .project-header p { margin: 0; color: #54616a; }
        .project-kicker { color: #197278; font-size: 0.78rem; font-weight: 700; }
        .status-badge {
            border-left: 4px solid #197278; background: #eef6f5; color: #155e63;
            padding: 0.55rem 0.8rem; font-weight: 650;
        }
        div[data-testid="stMetric"] {
            border-top: 3px solid #197278; background: #f7f9fa; padding: 0.75rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
