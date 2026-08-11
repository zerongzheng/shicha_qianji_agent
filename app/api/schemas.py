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
    # 旧数据库结果没有执行轨迹，默认空列表保证历史任务仍能正常打开。
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)
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
