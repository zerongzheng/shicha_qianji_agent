"""时察千机 HTTP API 的 Pydantic 协议。

分析算法内部继续使用 dataclass，HTTP 边界使用 Pydantic。两者职责不同：dataclass 便于
数值模块传递结果，Pydantic 负责校验外部输入、生成 OpenAPI，并让万悟准确识别字段类型。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DetectorName = Literal[
    "mad",
    "isolation_forest",
    "pca_reconstruction",
    "window_autoencoder",
    "time_frequency_relation",
    "hybrid",
]
JobOperation = Literal["analyze", "diagnose"]
JobStatus = Literal["queued", "running", "success", "failed", "cancelled"]
WorkOrderStatus = Literal["待确认", "已确认", "处理中", "待验证", "已完成", "已关闭"]


class StrictApiModel(BaseModel):
    """拒绝未声明字段，尽早暴露万悟变量名拼写错误。"""

    model_config = ConfigDict(extra="forbid")


class AnalysisConfigRequest(StrictApiModel):
    """一次分析可覆盖的算法参数；空值继续使用服务端推荐配置。"""

    device_profile_id: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="可选设备配置编号；不填写时根据 CSV 表头自动匹配",
    )
    detector_selection_mode: Literal["auto", "manual"] | None = Field(
        default=None,
        description="auto 按任务目标和设备配置选择主模型；manual 保持 detector 指定模型",
    )
    analysis_goal: Literal[
        "balanced",
        "high_recall",
        "low_false_alarm",
        "relationship_fault",
        "nonlinear_pattern",
        "fast_screening",
    ] | None = "balanced"
    detector: DetectorName | None = None
    threshold: float | None = Field(default=None, gt=0, le=100)
    rolling_window: int | None = Field(default=None, ge=5, le=2001)
    min_event_length: int | None = Field(default=None, ge=1, le=1000)
    merge_gap: int | None = Field(default=None, ge=0, le=1000)
    contamination: float | None = Field(default=None, gt=0, lt=0.5)
    hybrid_mad_weight: float | None = Field(default=None, ge=0, le=1)
    hybrid_forest_weight: float | None = Field(default=None, ge=0, le=1)
    hybrid_pca_weight: float | None = Field(default=None, ge=0, le=1)
    autoencoder_window: int | None = Field(default=None, ge=4, le=512)
    autoencoder_hidden: int | None = Field(default=None, ge=8, le=1024)
    autoencoder_bottleneck: int | None = Field(default=None, ge=2, le=256)
    autoencoder_max_iter: int | None = Field(default=None, ge=50, le=5000)
    autoencoder_max_training_windows: int | None = Field(
        default=None,
        ge=100,
        le=100000,
    )
    tfr_time_weight: float | None = Field(default=None, ge=0, le=1)
    tfr_frequency_weight: float | None = Field(default=None, ge=0, le=1)
    tfr_relation_weight: float | None = Field(default=None, ge=0, le=1)
    tfr_frequency_components: int | None = Field(default=None, ge=1, le=128)
    tfr_relation_components: int | None = Field(default=None, ge=1, le=128)
    suppress_transition_events: bool | None = None
    regime_window: int | None = Field(default=None, ge=5, le=2001)
    regime_max_states: int | None = Field(default=None, ge=1, le=6)
    regime_transition_quantile: float | None = Field(default=None, gt=0.5, lt=1)
    regime_suppression_overlap: float | None = Field(default=None, ge=0, le=1)
    regime_suppression_peak_ratio: float | None = Field(default=None, gt=0, le=20)

    def as_overrides(self) -> dict[str, Any]:
        """仅返回用户实际填写的参数，保留服务端动态推荐默认值。"""

        return self.model_dump(exclude_none=True)


class AnalysisRequest(StrictApiModel):
    """同步分析和同步诊断共用请求体。"""

    file_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    config: AnalysisConfigRequest = Field(default_factory=AnalysisConfigRequest)


class AnalysisResponse(StrictApiModel):
    """同步和异步分析共用的顶层结果协议。"""

    run_id: str
    status: Literal["success"]
    # 旧数据库中的历史结果没有设备配置字段，默认空对象保证升级后仍可查看。
    device_profile: dict[str, Any] = Field(default_factory=dict)
    data_profile: dict[str, Any]
    data_quality: dict[str, Any] | None = None
    # 三个字段在自适应预处理升级前不存在，默认值保证旧数据库任务仍可读取。
    raw_data_profile: dict[str, Any] | None = None
    preprocessing: dict[str, Any] = Field(default_factory=dict)
    detector: str
    visualization: dict[str, Any] | None = None
    anomaly_events: list[dict[str, Any]]
    model_selection: dict[str, Any] = Field(default_factory=dict)
    detector_validation: dict[str, Any] = Field(default_factory=dict)
    operating_regimes: dict[str, Any] | None
    relationship_diagnostics: list[dict[str, Any]]
    root_cause_diagnoses: list[dict[str, Any]]
    historical_case_matches: dict[str, list[dict[str, Any]]]
    work_order_drafts: list[dict[str, Any]]
    forecast_results: dict[str, Any]
    risk_alerts: list[dict[str, Any]]
    recommendations: list[str]
    optimization_recommendations: list[dict[str, Any]] = Field(default_factory=list)
    # 旧数据库结果没有执行轨迹，默认空列表保证历史任务仍能正常打开。
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)
    # 决策台账是后续版本新增字段；旧任务没有该字段时按空列表兼容读取。
    agent_decisions: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any]
    limitations: list[str]
    automatic_diagnosis: dict[str, Any] | None = None


class FileUploadResponse(StrictApiModel):
    """文件上传成功后的可追踪元数据。"""

    file_id: str
    file_name: str
    sha256: str
    size_bytes: int


class FilePreflightResponse(StrictApiModel):
    """文件预检结果，供 Vue 工作台在提交分析前展示真实数据画像。"""

    file_id: str
    file_name: str
    size_bytes: int
    row_count: int
    sample_count: int
    delimiter: str
    columns: list[str]
    datetime_column: str
    sensor_count: int
    missing_rate: float
    device_profile: dict[str, Any]
    warnings: list[str]


class JobCreateRequest(AnalysisRequest):
    """异步任务请求；diagnose 会在算法完成后再调用一次大模型。"""

    operation: JobOperation = "analyze"


class JobAcceptedResponse(StrictApiModel):
    """异步任务提交成功后的轻量响应。"""

    status: Literal["queued"]
    run_id: str
    operation: JobOperation
    status_url: str
    result_url: str


class WanwuJobCreateRequest(StrictApiModel):
    """万悟专用单步提交协议，文件和分析参数均通过 JSON 传入。"""

    file_url: str | None = Field(
        default=None,
        max_length=4000,
        description="万悟文件节点返回的临时下载地址",
    )
    file_base64: str | None = Field(
        default=None,
        max_length=36000000,
        description="小型 CSV 的 Base64 内容，可带 data URI 前缀",
    )
    file_name: str | None = Field(
        default=None,
        max_length=255,
        description="CSV 文件名；URL 不含文件名或使用 Base64 时建议填写",
    )
    operation: JobOperation = "analyze"
    config: AnalysisConfigRequest = Field(default_factory=AnalysisConfigRequest)

    @model_validator(mode="after")
    def validate_file_source(self) -> WanwuJobCreateRequest:
        """URL 与 Base64 必须且只能提供一种，避免平台变量绑定歧义。"""

        source_count = int(bool(self.file_url)) + int(bool(self.file_base64))
        if source_count != 1:
            raise ValueError("file_url 和 file_base64 必须且只能提供一个")
        return self


class WanwuQuickDiagnosisRequest(StrictApiModel):
    """万悟低调用额度演示协议：接收 CSV 后一次返回完整工业诊断。"""

    file_url: str | None = Field(
        default=None,
        max_length=4000,
        description="万悟文件节点返回的临时下载地址",
    )
    file_base64: str | None = Field(
        default=None,
        max_length=36000000,
        description="小型 CSV 的 Base64 内容，可带 data URI 前缀",
    )
    file_name: str | None = Field(
        default=None,
        max_length=255,
        description="CSV 文件名；使用 Base64 或 URL 无文件名时建议填写",
    )
    config: AnalysisConfigRequest = Field(default_factory=AnalysisConfigRequest)

    @model_validator(mode="after")
    def validate_file_source(self) -> WanwuQuickDiagnosisRequest:
        """URL 与 Base64 必须且只能提供一种，避免平台变量绑定歧义。"""

        source_count = int(bool(self.file_url)) + int(bool(self.file_base64))
        if source_count != 1:
            raise ValueError("file_url 和 file_base64 必须且只能提供一个")
        return self


class WanwuJobAcceptedResponse(JobAcceptedResponse):
    """万悟单步提交成功后返回文件与任务两层追踪编号。"""

    file_id: str
    file_name: str
    file_source: Literal["url", "base64"]
    sha256: str
    size_bytes: int


class WanwuQuickDiagnosisResponse(StrictApiModel):
    """快速诊断工具的稳定响应，完全由后端确定性流程生成。"""

    status: Literal["success"]
    run_id: str
    file_id: str
    file_name: str
    file_source: Literal["url", "base64"]
    size_bytes: int
    detector: str
    analysis: dict[str, Any]
    automatic_diagnosis: dict[str, Any] | None = None
    presentation: str
    model_call_count: int
    diagnosis_mode: Literal["deterministic"]
    analysis_version: str
    cache_hit: bool = False


class RunIdRequest(StrictApiModel):
    """万悟工具不替换路径参数，因此统一在 JSON 中传递 run_id。"""

    run_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class WanwuAutonomousCycleRequest(StrictApiModel):
    """万悟无人值守工作流一次巡检周期的输入。"""

    source_id: str | None = Field(
        default=None,
        min_length=4,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="留空时轮询到期数据源；填写时立即轮询指定数据源",
    )
    max_sources: int = Field(
        default=1,
        ge=1,
        le=1,
        description="当前工作流单轮固定处理一个数据源，保证每个任务完整追踪",
    )


class WanwuAutonomousCycleResponse(StrictApiModel):
    """万悟选择器节点可直接读取的巡检周期结果。"""

    status: Literal["success"]
    cycle_status: Literal["no_data", "analysis_queued", "partial_failure", "busy"]
    orchestrator: Literal["backend", "wanwu"]
    polled_source_count: int
    detected_count: int
    submitted_count: int
    duplicate_count: int
    failed_count: int
    run_ids: list[str]
    primary_run_id: str | None = None
    polls: list[dict[str, Any]]
    next_action: str


class WanwuMonitoringStatusRequest(StrictApiModel):
    """限制万悟状态节点返回的审计记录数量。"""

    limit: int = Field(default=20, ge=1, le=100)


class WanwuMonitoringStatusResponse(StrictApiModel):
    """万悟工作流和辅助智能体共用的无人值守运行状态。"""

    status: Literal["success"]
    orchestrator: Literal["backend", "wanwu"]
    monitor: dict[str, Any]
    notification_channels: dict[str, Any]
    source_count: int
    enabled_source_count: int
    sources: list[dict[str, Any]]
    ingestions: list[dict[str, Any]]
    notifications: list[dict[str, Any]]


class WanwuDataSourceListRequest(StrictApiModel):
    """万悟查询工业数据源时使用的精简条件。"""

    enabled_only: bool = Field(default=False, description="仅返回已启用的数据源")
    source_type: Literal["directory", "http_csv"] | None = Field(
        default=None,
        description="按目录监控或 HTTP CSV 接口筛选；留空返回全部",
    )


class WanwuDataSourceView(StrictApiModel):
    """不含鉴权请求头的数据源公开视图，字段可被万悟画布直接引用。"""

    source_id: str
    name: str
    source_type: Literal["directory", "http_csv"]
    endpoint: str
    interval_seconds: float
    enabled: bool
    analysis_config: dict[str, Any]
    routing: dict[str, Any]
    initial_scan_mode: Literal["latest", "new_only", "all"]
    timeout_seconds: float
    last_poll_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str
    request_header_count: int


class WanwuDataSourceListResponse(StrictApiModel):
    """万悟可直接展示或交给选择器使用的数据源清单。"""

    status: Literal["success"]
    orchestrator: Literal["backend", "wanwu"]
    source_count: int
    enabled_source_count: int
    sources: list[WanwuDataSourceView]


class WanwuDataSourceConfigureRequest(StrictApiModel):
    """万悟配置数据源所需的最小业务字段，不接收密钥。"""

    source_id: str | None = Field(
        default=None,
        min_length=6,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="留空时新建；填写已有编号时更新并保留分析、路由和鉴权配置",
    )
    name: str = Field(min_length=2, max_length=100, description="数据源名称")
    source_type: Literal["directory", "http_csv"] = Field(
        description="directory 为后端可访问目录，http_csv 为返回 CSV 的 HTTP 接口"
    )
    endpoint: str = Field(
        min_length=1,
        max_length=2000,
        description="监控目录绝对路径或 HTTP CSV 地址",
    )
    interval_seconds: float = Field(
        default=60,
        ge=1,
        le=86400,
        description="数据源达到再次巡检条件的时间间隔",
    )
    enabled: bool = Field(default=True, description="是否允许无人值守工作流巡检")
    timeout_seconds: float = Field(
        default=15,
        ge=1,
        le=120,
        description="HTTP 数据源连接超时；目录数据源保留该配置但不使用",
    )
    initial_scan_mode: Literal["latest", "new_only", "all"] = Field(
        default="new_only",
        description="首次巡检处理最新批次、只等待新批次或处理全部历史批次",
    )


class WanwuDataSourceConfigureResponse(StrictApiModel):
    """数据源持久化结果，不回显请求头和外部通知密钥。"""

    status: Literal["success"]
    action: Literal["created", "updated"]
    orchestrator: Literal["backend", "wanwu"]
    source: WanwuDataSourceView
    next_action: str


class WanwuDataSourceVerifyRequest(StrictApiModel):
    """验证一个已保存数据源，不采集、不分析也不改变轮询时间。"""

    source_id: str = Field(
        min_length=6,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class WanwuDataSourceVerifyResponse(StrictApiModel):
    """万悟数据源验收节点使用的稳定响应。"""

    status: Literal["success"]
    source_id: str
    source_type: Literal["directory", "http_csv"]
    reachable: bool
    csv_file_count: int | None = None
    latest_file_name: str | None = None
    latest_file_size_bytes: int | None = None
    http_status: int | None = None
    content_type: str | None = None
    sample_bytes_read: int | None = None
    checked_at: str
    message: str


class WanwuNotificationDispatchRequest(RunIdRequest):
    """分析成功后由万悟工作流显式触发主动通知。"""


class WanwuNotificationDispatchResponse(StrictApiModel):
    """通知工具返回投递数量与各渠道状态，不包含企业微信密钥。"""

    status: Literal["success"]
    run_id: str
    notification_count: int
    sent_count: int
    failed_count: int
    notifications: list[dict[str, Any]]


class WanwuWorkOrderListRequest(StrictApiModel):
    """万悟工单列表查询条件。"""

    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100000)
    status: WorkOrderStatus | None = None
    priority: Literal["P1", "P2", "P3"] | None = None
    search: str | None = Field(default=None, max_length=200)
    run_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    include_archived: bool = False
    archived_only: bool = False


class WanwuCaseListRequest(StrictApiModel):
    """万悟查询已闭环案例时使用的简单 JSON 条件。"""

    limit: int = Field(default=50, ge=1, le=200)
    include_archived: bool = False
    archived_only: bool = False


class ArchiveRequest(StrictApiModel):
    """归档操作的可选说明，便于后续审计和现场管理。"""

    reason: str | None = Field(default=None, max_length=500)


class JobStatusResponse(StrictApiModel):
    """供万悟选择器节点轮询的稳定状态协议。"""

    status: Literal["success"]
    run_id: str
    job_status: JobStatus
    operation: str
    detector: str
    file_id: str
    file_name: str
    started_at: str
    finished_at: str | None = None
    duration_ms: float | None = None
    error: str | None = None
    result_ready: bool


class JobCancelledResponse(StrictApiModel):
    """排队任务取消成功后的响应。"""

    status: Literal["success"]
    run_id: str
    job_status: Literal["cancelled"]
    message: str


class JobResultResponse(StrictApiModel):
    """异步任务成功后返回完整分析结果。"""

    status: Literal["success"]
    run_id: str
    result: AnalysisResponse


class WorkOrderUpdateRequest(StrictApiModel):
    """现场人员或万悟工单卡片允许回写的字段。"""

    status: WorkOrderStatus
    confirmed_cause: str | None = Field(default=None, max_length=500)
    feedback_note: str | None = Field(default=None, max_length=4000)
    handled_by: str | None = Field(default=None, max_length=200)


class LoginRequest(StrictApiModel):
    """本地竞赛演示登录请求；不开放公开注册。"""

    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(StrictApiModel):
    """登录成功后的短期会话令牌和当前用户。"""

    status: Literal["success"]
    token: str
    expires_at: str
    user: dict[str, Any]


class NotificationAcknowledgeRequest(StrictApiModel):
    """通知签收只需要通知编号，接收人员来自登录会话。"""

    notification_id: str = Field(min_length=8, max_length=128)


class WorkOrderAssignmentRequest(StrictApiModel):
    """管理员或负责人指派工单时使用的用户编号。"""

    user_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class NotificationRecipient(StrictApiModel):
    """一个告警接收人及其职责。"""

    recipient_name: str = Field(min_length=1, max_length=100)
    recipient_role: str = Field(min_length=1, max_length=100)


class NotificationRoutingRequest(StrictApiModel):
    """按工单优先级路由接收人；外部通知密钥由部署环境统一管理。"""

    priority_routes: dict[Literal["P1", "P2", "P3"], list[NotificationRecipient]] = Field(
        default_factory=dict
    )


class DataSourceRequest(StrictApiModel):
    """无人值守数据源配置。"""

    source_id: str | None = Field(
        default=None,
        min_length=6,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    name: str = Field(min_length=2, max_length=100)
    source_type: Literal["directory", "http_csv"]
    endpoint: str = Field(min_length=1, max_length=2000)
    interval_seconds: float = Field(default=60, ge=1, le=86400)
    enabled: bool = True
    timeout_seconds: float = Field(default=15, ge=1, le=120)
    initial_scan_mode: Literal["latest", "new_only", "all"] = Field(
        default="latest",
        description="首次接入时处理最新一批、只等待新批次或处理全部历史批次",
    )
    request_headers: dict[str, str] = Field(default_factory=dict)
    analysis_config: AnalysisConfigRequest = Field(default_factory=AnalysisConfigRequest)
    routing: NotificationRoutingRequest = Field(default_factory=NotificationRoutingRequest)


class WanwuWorkOrderUpdateRequest(WorkOrderUpdateRequest):
    """万悟通过 JSON 同时传递工单编号和现场反馈。"""

    record_id: str = Field(min_length=8, max_length=300)


class ModelCompareRequest(StrictApiModel):
    """异常检测器对比请求。"""

    file_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    detectors: list[DetectorName] = Field(
        default_factory=lambda: [
            "mad",
            "isolation_forest",
            "pca_reconstruction",
            "window_autoencoder",
            "time_frequency_relation",
            "hybrid",
        ],
        min_length=1,
        max_length=6,
    )
    config: AnalysisConfigRequest = Field(default_factory=AnalysisConfigRequest)


class ForecastCompareRequest(StrictApiModel):
    """预测模型对比请求。"""

    file_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    sensors: list[str] | None = Field(default=None, max_length=100)
    horizon: int = Field(default=30, ge=1, le=300)
    lookback: int = Field(default=120, ge=30, le=10000)
    holdout: int = Field(default=30, ge=5, le=2000)
    models: list[
        Literal[
            "persistence",
            "moving_average",
            "linear_trend",
            "lag_ridge",
            "time_frequency_ridge",
        ]
    ] = Field(
        default_factory=lambda: [
            "persistence",
            "moving_average",
            "linear_trend",
            "lag_ridge",
            "time_frequency_ridge",
        ],
        min_length=1,
        max_length=5,
    )


class ErrorResponse(StrictApiModel):
    """FastAPI 业务错误的公开结构。"""

    detail: str
