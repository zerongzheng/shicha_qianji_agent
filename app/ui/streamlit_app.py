"""时察千机竞赛演示页面。

页面围绕“发现风险、查看证据、获得处置建议”组织，聊天 Agent 是辅助入口，
不是整个项目的唯一界面。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.agent import IndustrialAgent
from app.analysis import analyze_file
from app.analysis.detection import (
    DETECTOR_RECOMMENDED_THRESHOLDS,
    recommended_event_policy,
)
from app.config import get_settings
from app.data import save_uploaded_file
from app.diagnosis import AutomaticDiagnosisService, build_fallback_diagnosis
from app.llm import format_llm_error
from app.model_store import list_autoencoder_models
from app.models import AnalysisConfig, AnalysisResult
from app.reporting.case_package import build_case_package_from_result
from app.storage import get_repository


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
                repository = get_repository()
                result = analyze_file(
                    source_path,
                    config=config,
                    case_matcher=repository.find_similar_cases,
                )
                st.session_state["analysis_result"] = result
                # Streamlit 直接调用分析核心，不经过 FastAPI 任务队列，因此在这里补齐
                # 任务、工单和历史案例的持久化，保证页面演示也能完成真实闭环。
                try:
                    st.session_state["analysis_run_id"] = repository.record_local_analysis(
                        source_path,
                        operation="streamlit_analyze",
                        detector=config.detector,
                        config=asdict(config),
                        result=result,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.warning(f"分析结果已生成，但暂未写入闭环记录：{exc}")
            # 页面是用户输入边界，需要把路径、格式和算法错误转成可读提示。
            except Exception as exc:  # noqa: BLE001
                st.error(f"分析失败：{exc}")

    result: AnalysisResult | None = st.session_state.get("analysis_result")
    if result is None:
        _render_empty_state()
        return

    (
        overview_tab,
        evidence_tab,
        regime_tab,
        root_cause_tab,
        relationship_tab,
        report_tab,
        agent_tab,
        work_order_tab,
        history_tab,
    ) = st.tabs(
        [
            "风险总览",
            "时序证据",
            "工况识别",
            "根因诊断",
            "关联诊断",
            "诊断报告",
            "智能决策",
            "运维闭环",
            "历史记录",
        ]
    )
    with overview_tab:
        _render_overview(result)
    with evidence_tab:
        _render_evidence(result)
    with regime_tab:
        _render_regimes(result)
    with root_cause_tab:
        _render_root_causes(result)
    with relationship_tab:
        _render_relationships(result)
    with report_tab:
        _render_report(result)
    with agent_tab:
        _render_agent(result)
    with work_order_tab:
        _render_work_order_center(result)
    with history_tab:
        _render_history_center()


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
        detector_options = [
            "time_frequency_relation",
            "window_autoencoder",
            "hybrid",
            "pca_reconstruction",
            "mad",
            "isolation_forest",
        ]
        detector = st.selectbox(
            "异常检测器",
            options=detector_options,
            index=detector_options.index(settings.anomaly_detector),
            format_func={
                "time_frequency_relation": "时频关系多路径检测器（推荐）",
                "window_autoencoder": "滑动窗口 AutoEncoder 检测器（快速基线）",
                "hybrid": "时序-工况混合检测器",
                "pca_reconstruction": "PCA 多变量重构检测器",
                "mad": "稳健 MAD",
                "isolation_forest": "Isolation Forest",
            }.get,
        )
        threshold = st.slider(
            "异常阈值",
            min_value=2.0,
            max_value=10.0,
            value=float(
                DETECTOR_RECOMMENDED_THRESHOLDS.get(detector, settings.anomaly_threshold)
            ),
            step=0.1,
            help="越低越敏感，也更容易误报。",
            key=f"threshold_{detector}",
        )
        window = st.number_input(
            "滚动窗口（采样点）",
            min_value=5,
            max_value=501,
            value=int(settings.rolling_window),
            step=2,
        )
        recommended_min_event_length, recommended_merge_gap = recommended_event_policy(
            str(detector)
        )
        min_event_length = st.number_input(
            "最短事件长度",
            min_value=1,
            max_value=60,
            value=recommended_min_event_length,
            key=f"min_event_length_{detector}",
        )
        merge_gap = st.number_input(
            "事件合并间隔",
            min_value=0,
            max_value=120,
            value=recommended_merge_gap,
            key=f"merge_gap_{detector}",
        )

        run_clicked = st.button("开始智能分析", type="primary", width="stretch")
        st.caption("原始 CSV 由本地算法处理，大模型只读取结构化分析摘要。")
        stored_models = list_autoencoder_models()
        if stored_models:
            latest = stored_models[0]
            st.caption(
                f"健康模型仓库：{len(stored_models)} 个｜最新 v{latest['format_version']}｜"
                f"{latest['sensor_count']} 传感器"
            )

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
        merge_gap=int(merge_gap),
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
        stored_models = list_autoencoder_models()
        st.metric("已训练健康模型", len(stored_models))
        if stored_models:
            latest = stored_models[0]
            st.caption(
                f"最新模型：{latest['model_id']}｜v{latest['format_version']}｜"
                f"{latest['window_size']} 点窗口"
            )


def _render_overview(result: AnalysisResult) -> None:
    """显示最值得决策者关注的风险和量化指标。"""

    highest_risk = result.events[0].severity if result.events else "正常"
    metrics = result.metrics
    st.subheader("设备健康总览")
    st.caption(
        f"{result.profile.source_name}｜{result.profile.start_time} 至 {result.profile.end_time}｜"
        f"{len(result.profile.sensor_columns)} 个传感器"
    )
    metric_columns = st.columns(6)
    metric_columns[0].metric("当前风险", highest_risk)
    metric_columns[1].metric("异常事件", len(result.events))
    metric_columns[2].metric("重点测点", len(_top_sensors(result)))
    metric_columns[3].metric("数据点", result.profile.row_count)
    metric_columns[4].metric("点级 F1", f"{metrics.f1_score:.3f}" if metrics else "无标签")
    metric_columns[5].metric(
        "事件级 F1",
        f"{metrics.event_f1_score:.3f}" if metrics else "无标签",
    )

    st.markdown(
        "<div class='workflow-strip'><span>数据接入</span><b>→</b><span>异常发现</span>"
        "<b>→</b><span>原因研判</span><b>→</b><span>风险预测</span><b>→</b>"
        "<span>处置建议</span></div>",
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.15, 0.85], gap="large")
    with left:
        st.subheader("异常事件时间线")
        st.plotly_chart(_build_risk_chart(result), width="stretch")
    with right:
        st.subheader("重点传感器贡献")
        st.plotly_chart(_build_sensor_contribution_chart(result), width="stretch")

    left, right = st.columns([1.15, 0.85], gap="large")
    with left:
        st.subheader("优先处置")
        for index, recommendation in enumerate(result.recommendations[:5], start=1):
            st.markdown(f"**{index}.** {recommendation}")
    with right:
        st.subheader("风险判断")
        st.info(_risk_explanation(result))

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

    st.subheader("异常事件清单")
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


def _top_sensors(result: AnalysisResult) -> list[str]:
    """按异常事件中的传感器贡献统计重点测点。"""

    scores: dict[str, float] = {}
    for event in result.events:
        for sensor, score in event.sensor_scores.items():
            scores[sensor] = scores.get(sensor, 0.0) + float(score)
    return [
        sensor
        for sensor, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:5]
    ]


def _risk_explanation(result: AnalysisResult) -> str:
    """将设备级风险转成评委和运维人员都能快速理解的判断。"""

    if not result.events:
        return "当前没有形成满足持续时间要求的异常事件，系统建议保持监测。"
    event = result.events[0]
    sensors = "、".join(event.dominant_sensors) or "未识别"
    return (
        f"当前识别到 {len(result.events)} 个异常事件，最高风险为{event.severity}。"
        f"首个事件持续 {event.duration_points} 个采样点，重点关注 {sensors}。"
        "候选原因用于安排排查顺序，最终结论仍需结合设备工况和现场确认。"
    )


def _build_sensor_contribution_chart(result: AnalysisResult) -> go.Figure:
    """绘制异常事件中主导传感器的累计贡献。"""

    scores: dict[str, float] = {}
    for event in result.events:
        for sensor, score in event.sensor_scores.items():
            scores[sensor] = scores.get(sensor, 0.0) + float(score)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:8]
    figure = go.Figure(
        go.Bar(
            x=[value for _, value in ordered][::-1],
            y=[sensor for sensor, _ in ordered][::-1],
            orientation="h",
            marker_color="#197278",
            hovertemplate="%{y}<br>累计贡献：%{x:.2f}<extra></extra>",
        )
    )
    figure.update_layout(
        height=390,
        margin={"l": 10, "r": 10, "t": 20, "b": 30},
        xaxis_title="累计异常贡献",
        yaxis_title=None,
        showlegend=False,
    )
    return figure


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


def _render_relationships(result: AnalysisResult) -> None:
    """展示异常事件内的相关性变化和领先滞后线索。"""

    if not result.relationship_diagnostics:
        st.info("当前异常事件不足以形成稳定的多传感器关系判断。")
        return

    options = {
        f"事件 {item['事件编号']}｜{item['开始时间']}": item
        for item in result.relationship_diagnostics
    }
    selected_label = st.selectbox("选择异常事件", options=list(options), key="relationship_event")
    diagnostic = options[selected_label]
    st.subheader(diagnostic["关系结论"])
    st.caption(diagnostic["使用边界"])

    relation_rows = pd.DataFrame(diagnostic["重点关系"])
    left, right = st.columns([1.1, 0.9], gap="large")
    with left:
        st.dataframe(relation_rows, hide_index=True, width="stretch")
    with right:
        st.plotly_chart(_build_relationship_chart(diagnostic), width="stretch")

    st.subheader("建议排查顺序")
    for index, relation in enumerate(diagnostic["重点关系"][:3], start=1):
        st.markdown(
            f"**{index}.** {relation['时滞解释']}；事件前后相关性变化 "
            f"{relation['相关性变化']}，优先核查两测点对应的共同负载、控制指令和部件链路。"
        )


def _render_root_causes(result: AnalysisResult) -> None:
    """展示确定性候选根因、证据缺口和待确认工单。"""

    if not result.event_diagnoses:
        st.success("当前没有异常事件需要生成候选根因。")
        return

    options = {
        f"事件 {item.event_number}｜{item.event_start}｜{item.risk_level}": item
        for item in result.event_diagnoses
    }
    selected_label = st.selectbox("选择异常事件", list(options), key="root_cause_event")
    diagnosis = options[selected_label]
    primary = diagnosis.primary_candidate
    if primary is None:
        st.warning("当前证据不足以形成可排序候选，需要补充设备和工况资料。")
        return

    metrics = st.columns(4)
    metrics[0].metric("诊断状态", diagnosis.diagnosis_status)
    metrics[1].metric("首要候选", primary.name)
    metrics[2].metric("候选置信度", f"{primary.confidence:.0%}")
    metrics[3].metric("工况上下文", diagnosis.regime_context)
    st.progress(primary.confidence, text=f"置信等级：{primary.confidence_level}")
    st.caption("置信度来自通用模式与时序证据匹配，不代表企业设备故障已确诊。")

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("支持证据")
        for item in primary.supporting_evidence:
            st.markdown(f"- {item}")
    with right:
        st.subheader("证据缺口")
        for item in primary.missing_evidence:
            st.markdown(f"- {item}")

    st.subheader("候选根因排序")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "排名": index,
                    "候选根因": item.name,
                    "类别": item.category,
                    "置信度": f"{item.confidence:.0%}",
                    "等级": item.confidence_level,
                    "规则来源": item.source,
                }
                for index, item in enumerate(diagnosis.candidates, start=1)
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    st.subheader("现场处置顺序")
    for index, action in enumerate(diagnosis.work_order_actions, start=1):
        st.markdown(f"**{index}.** {action}")

    work_order = next(
        (
            item
            for item in result.work_order_drafts
            if item.event_number == diagnosis.event_number
        ),
        None,
    )
    if work_order:
        with st.expander(f"工单草案 {work_order.work_order_id}", expanded=True):
            st.json(
                {
                    "优先级": work_order.priority,
                    "标题": work_order.title,
                    "状态": work_order.status,
                    "建议角色": work_order.assigned_role,
                    "必须回写": list(work_order.required_feedback),
                }
            )


def _render_regimes(result: AnalysisResult) -> None:
    """展示稳定工况、过渡强度和异常事件工况上下文。"""

    regimes = result.operating_regimes
    if regimes is None:
        st.info("当前分析未生成工况识别结果。")
        return

    metrics = st.columns(4)
    metrics[0].metric("稳定工况", regimes.state_count)
    metrics[1].metric("过渡点", int(regimes.transition_mask.sum()))
    metrics[2].metric("事件上下文", len(regimes.event_contexts))
    metrics[3].metric("抑制事件", regimes.suppressed_event_count)

    st.plotly_chart(_build_regime_chart(result), width="stretch")
    st.subheader("异常事件与工况切换关系")
    if regimes.event_contexts:
        st.dataframe(pd.DataFrame(regimes.event_contexts), hide_index=True, width="stretch")
    else:
        st.success("当前没有异常事件需要进行工况归因。")
    st.caption("工况切换重合只用于提示干扰来源，默认不会删除告警事件。")


def _render_report(result: AnalysisResult) -> None:
    """显示并下载自动生成的分析报告。"""

    if st.button("生成案例材料包", key="export_case_package"):
        try:
            package = build_case_package_from_result(result)
            st.session_state["case_package"] = package
            st.success(f"案例材料已生成：{package.case_dir}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"案例材料生成失败：{exc}")

    st.download_button(
        "下载 Markdown 报告",
        data=result.report_text,
        file_name=f"{result.source_path.stem}_analysis.md",
        mime="text/markdown",
    )
    package = st.session_state.get("case_package")
    if package is not None and package.result.source_path == result.source_path:
        st.subheader("案例材料下载")
        columns = st.columns(4)
        columns[0].download_button(
            "案例摘要",
            data=package.markdown_path.read_text(encoding="utf-8"),
            file_name="case_summary.md",
            mime="text/markdown",
        )
        columns[1].download_button(
            "事件明细",
            data=package.events_csv_path.read_bytes(),
            file_name="anomaly_events.csv",
            mime="text/csv",
        )
        columns[2].download_button(
            "风险图 HTML",
            data=package.chart_html_path.read_bytes(),
            file_name="risk_evidence.html",
            mime="text/html",
        )
        columns[3].download_button(
            "结构化摘要",
            data=package.summary_json_path.read_bytes(),
            file_name="case_summary.json",
            mime="application/json",
        )
    st.markdown(result.report_text)


def _render_work_order_center(result: AnalysisResult) -> None:
    """展示并回写工单，形成“检测 - 处置 - 反馈 - 案例”闭环。"""

    repository = get_repository()
    run_id = st.session_state.get("analysis_run_id")
    st.subheader("运维工单闭环")
    st.caption("算法生成待确认工单，现场人员补充确认结果；已完成工单会沉淀为历史案例。")

    if run_id:
        st.info(f"当前分析任务：{run_id}")
    else:
        st.warning("当前分析尚未建立持久化任务记录，请重新点击“开始智能分析”。")

    work_orders = repository.list_work_orders(run_id=run_id) if run_id else []
    if not work_orders:
        st.success("当前任务没有待处理工单。")
    else:
        selected = st.selectbox(
            "选择需要处理的工单",
            options=work_orders,
            format_func=lambda item: (
                f"{item['record_id']}｜{item['priority']}｜{item['title']}｜{item['status']}"
            ),
            key="work_order_selector",
        )
        with st.container(border=True):
            st.markdown(f"**{selected['title']}**")
            metrics = st.columns(4)
            metrics[0].metric("优先级", selected["priority"])
            metrics[1].metric("状态", selected["status"])
            metrics[2].metric("事件编号", selected["event_number"])
            metrics[3].metric("责任角色", selected["assigned_role"])
            st.markdown("**建议动作**")
            for action in selected["actions"]:
                st.markdown(f"- {action}")
            st.markdown("**算法证据**")
            for evidence in selected["evidence_summary"]:
                st.markdown(f"- {evidence}")

            statuses = ["待确认", "已确认", "处理中", "已完成", "已关闭"]
            status = st.selectbox(
                "更新状态",
                statuses,
                index=statuses.index(selected["status"]),
                key=f"status_{selected['record_id']}",
            )
            cause = st.text_input(
                "现场确认根因",
                value=selected["confirmed_cause"] or "",
                key=f"cause_{selected['record_id']}",
                placeholder="例如：阀门执行器卡滞（待现场确认）",
            )
            feedback = st.text_area(
                "处置与复测记录",
                value=selected["feedback_note"] or "",
                key=f"feedback_{selected['record_id']}",
                height=100,
                placeholder="填写处理动作、复测结果和是否恢复正常",
            )
            handled_by = st.text_input(
                "处理人员或小组",
                value=selected["handled_by"] or "",
                key=f"handled_{selected['record_id']}",
            )
            if st.button("保存现场反馈", type="primary", key=f"save_{selected['record_id']}"):
                try:
                    updated = repository.update_work_order(
                        selected["record_id"],
                        {
                            "status": status,
                            "confirmed_cause": cause,
                            "feedback_note": feedback,
                            "handled_by": handled_by,
                        },
                    )
                    st.success(f"工单已更新：{updated['status']}")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"工单更新失败：{exc}")

    st.divider()
    st.subheader("已沉淀的历史案例")
    cases = repository.list_confirmed_cases(limit=20)
    if not cases:
        st.info("完成一条工单并填写确认根因后，这里会出现可追溯案例。")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "案例编号": item["case_id"],
                    "确认根因": item["confirmed_cause"],
                    "来源任务": item["source_run_id"],
                    "处理人员": item["handled_by"] or "未填写",
                    "反馈": item["feedback_note"] or "未填写",
                    "更新时间": item["closed_at"],
                }
                for item in cases
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _render_history_center() -> None:
    """集中查看历史分析任务、工单和已确认案例。"""

    repository = get_repository()
    st.subheader("历史分析与案例")
    st.caption("用于追踪历次分析、复核处理结果，并查看已沉淀的现场案例。")

    left, right = st.columns([1.0, 1.0], gap="large")
    with left:
        st.markdown("**分析任务**")
        status_options = ["全部", "success", "failed", "running", "queued", "cancelled"]
        selected_status = st.selectbox(
            "任务状态",
            status_options,
            format_func=lambda value: "全部" if value == "全部" else value,
            key="history_run_status",
        )
        runs = repository.list_runs(
            limit=50,
            status=None if selected_status == "全部" else selected_status,
        )
        if runs:
            run_table = pd.DataFrame(
                [
                    {
                        "任务编号": item["run_id"],
                        "文件": item["file_name"],
                        "操作": item["operation"],
                        "检测器": item["detector"],
                        "状态": item["status"],
                        "耗时(ms)": round(item["duration_ms"], 1)
                        if item["duration_ms"] is not None
                        else None,
                        "开始时间": item["started_at"],
                    }
                    for item in runs
                ]
            )
            st.dataframe(run_table, hide_index=True, width="stretch")
            run_options = {item["run_id"]: item for item in runs}
            selected_run_id = st.selectbox(
                "查看任务详情",
                list(run_options),
                key="history_selected_run",
            )
            detail = repository.get_run(selected_run_id)
            if detail:
                st.json(
                    {
                        "任务编号": detail["run_id"],
                        "状态": detail["status"],
                        "文件": detail["file_name"],
                        "参数": detail["config"],
                        "错误": detail["error"],
                        "结果摘要": (
                            detail["result"].get("summary")
                            if isinstance(detail.get("result"), dict)
                            else None
                        ),
                    }
                )
        else:
            st.info("暂无符合条件的历史分析任务。")

    with right:
        st.markdown("**工单与历史案例**")
        work_orders = repository.list_work_orders(limit=50)
        if work_orders:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "工单编号": item["record_id"],
                            "优先级": item["priority"],
                            "标题": item["title"],
                            "状态": item["status"],
                            "确认根因": item["confirmed_cause"] or "待确认",
                            "更新时间": item["updated_at"],
                        }
                        for item in work_orders
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("暂无历史工单。")

        cases = repository.list_confirmed_cases(limit=50)
        if cases:
            st.markdown("**已确认案例**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "案例编号": item["case_id"],
                            "确认根因": item["confirmed_cause"],
                            "相似案例来源任务": item["source_run_id"],
                            "处理人员": item["handled_by"] or "未填写",
                            "处置反馈": item["feedback_note"] or "未填写",
                        }
                        for item in cases
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("暂无已确认案例。完成工单反馈后，案例会显示在这里。")


def _render_agent(result: AnalysisResult) -> None:
    """提供单次自动诊断和可选的多轮追问。"""

    settings = get_settings()
    st.subheader("自动诊断结论")
    st.caption("确定性分析和知识检索先完成，GLM-5 仅调用一次生成最终诊断。")

    result_key = str(result.source_path)
    stored_diagnosis = st.session_state.get("automatic_diagnosis")
    if st.session_state.get("automatic_diagnosis_source") != result_key:
        stored_diagnosis = None
        st.session_state.pop("automatic_diagnosis", None)

    if st.button("生成完整诊断", type="primary", key="generate_diagnosis"):
        if not settings.llm_enabled:
            stored_diagnosis = build_fallback_diagnosis(result)
            st.info("未配置大模型，当前展示确定性降级诊断。")
        else:
            with st.spinner("正在检索工业知识并生成诊断结论..."):
                automatic = AutomaticDiagnosisService(settings).diagnose(result)
                stored_diagnosis = automatic.diagnosis
                if automatic.status == "fallback" and automatic.error:
                    st.error(automatic.error)
                    st.info("已切换为确定性降级诊断，工业分析结果不受影响。")
        st.session_state["automatic_diagnosis"] = stored_diagnosis
        st.session_state["automatic_diagnosis_source"] = result_key

    if stored_diagnosis:
        st.markdown(stored_diagnosis)

    with st.expander("查看智能体证据与继续追问"):
        st.code(
            json.dumps(result.to_summary(), ensure_ascii=False, indent=2, default=str),
            language="json",
        )
        st.caption("多轮追问会产生额外模型请求，比赛接口每分钟最多调用 5 次。")

        if not settings.llm_enabled:
            st.warning("尚未检测到 LLM_API_KEY。基础分析和降级诊断仍可正常使用。")
            return

        if "messages" not in st.session_state:
            st.session_state["messages"] = []
        for message in st.session_state["messages"]:
            st.chat_message(message["role"]).write(message["content"])

        question = st.chat_input("继续追问当前异常证据或排查顺序")
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
            st.session_state["messages"].append(
                {"role": "assistant", "content": str(response)}
            )
        # 大模型网络、鉴权和工具调用错误都应停留在对话区域，不能中断分析看板。
        except Exception as exc:  # noqa: BLE001
            st.error(format_llm_error(exc))


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


def _build_regime_chart(result: AnalysisResult) -> go.Figure:
    """绘制稳定工况编号、过渡强度和异常事件位置。"""

    regimes = result.operating_regimes
    if regimes is None:
        return go.Figure()
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=result.dataframe["datetime"],
            y=regimes.regime_labels + 1,
            name="稳定工况编号",
            line={"color": "#197278", "width": 1.8, "shape": "hv"},
            yaxis="y",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=result.dataframe["datetime"],
            y=regimes.transition_score,
            name="工况切换强度",
            line={"color": "#D14D3F", "width": 1.3},
            fill="tozeroy",
            fillcolor="rgba(209,77,63,0.10)",
            yaxis="y2",
        )
    )
    transition_indexes = regimes.transition_mask.astype(bool)
    figure.add_trace(
        go.Scatter(
            x=result.dataframe.loc[transition_indexes, "datetime"],
            y=regimes.transition_score.loc[transition_indexes],
            name="过渡区",
            mode="markers",
            marker={"color": "#161B22", "size": 4},
            yaxis="y2",
        )
    )
    figure.update_layout(
        height=430,
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        legend={"orientation": "h", "y": 1.08},
        xaxis_title=None,
        yaxis={"title": "工况编号", "dtick": 1},
        yaxis2={"title": "切换强度", "overlaying": "y", "side": "right"},
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


def _build_relationship_chart(diagnostic: dict[str, object]) -> go.Figure:
    """用紧凑关系图展示事件内最重要的传感器关联变化。"""

    sensors = list(diagnostic.get("主导传感器", []))
    relations = list(diagnostic.get("重点关系", []))
    positions = {
        sensor: (
            float(np.cos(2 * np.pi * index / max(len(sensors), 1))),
            float(np.sin(2 * np.pi * index / max(len(sensors), 1))),
        )
        for index, sensor in enumerate(sensors)
    }
    figure = go.Figure()
    for relation in relations:
        left_sensor = str(relation["传感器A"])
        right_sensor = str(relation["传感器B"])
        if left_sensor not in positions or right_sensor not in positions:
            continue
        left = positions[left_sensor]
        right = positions[right_sensor]
        change = abs(float(relation["相关性变化"]))
        figure.add_trace(
            go.Scatter(
                x=[left[0], right[0]],
                y=[left[1], right[1]],
                mode="lines",
                line={"width": 1.5 + 5 * min(change, 1.0), "color": "#D14D3F"},
                hovertext=str(relation["时滞解释"]),
                hoverinfo="text",
                showlegend=False,
            )
        )
    figure.add_trace(
        go.Scatter(
            x=[positions[sensor][0] for sensor in sensors],
            y=[positions[sensor][1] for sensor in sensors],
            text=sensors,
            mode="markers+text",
            textposition="bottom center",
            marker={"size": 24, "color": "#197278", "line": {"width": 2, "color": "white"}},
            hoverinfo="text",
            showlegend=False,
        )
    )
    figure.update_layout(
        height=360,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        xaxis={"visible": False, "range": [-1.4, 1.4]},
        yaxis={"visible": False, "range": [-1.4, 1.4], "scaleanchor": "x"},
        plot_bgcolor="white",
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
        .workflow-strip {
            display: flex; align-items: center; justify-content: space-between;
            gap: 0.5rem; margin: 0.8rem 0 1.1rem; padding: 0.65rem 0.8rem;
            border-top: 1px solid #d7dce1; border-bottom: 1px solid #d7dce1;
            color: #155e63; font-size: 0.88rem; font-weight: 650;
        }
        .workflow-strip b { color: #a3adb3; font-weight: 500; }
        div[data-testid="stMetric"] {
            border-top: 3px solid #197278; background: #f7f9fa; padding: 0.75rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
