CREATE TABLE IF NOT EXISTS uploaded_files (
    file_id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES uploaded_files(file_id),
    operation TEXT NOT NULL,
    detector TEXT NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms DOUBLE PRECISION,
    source_id TEXT,
    ingestion_id TEXT,
    archived_at TEXT,
    archive_reason TEXT
);

CREATE TABLE IF NOT EXISTS work_orders (
    record_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    source_work_order_id TEXT NOT NULL,
    event_number INTEGER NOT NULL,
    priority TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    assigned_role TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    required_feedback_json TEXT NOT NULL,
    confirmed_cause TEXT,
    feedback_note TEXT,
    handled_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    archive_reason TEXT,
    UNIQUE (run_id, source_work_order_id)
);

CREATE TABLE IF NOT EXISTS model_call_logs (
    call_id TEXT PRIMARY KEY,
    run_id TEXT,
    timestamp TEXT NOT NULL,
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms DOUBLE PRECISION NOT NULL,
    input_character_count INTEGER NOT NULL,
    output_character_count INTEGER NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    error_type TEXT,
    content_stored SMALLINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS data_sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    interval_seconds DOUBLE PRECISION NOT NULL CHECK (interval_seconds > 0),
    enabled SMALLINT NOT NULL DEFAULT 1,
    config_json TEXT NOT NULL DEFAULT '{}',
    last_poll_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_ingestions (
    ingestion_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES data_sources(source_id),
    fingerprint TEXT NOT NULL,
    item_key TEXT NOT NULL,
    file_name TEXT NOT NULL,
    status TEXT NOT NULL,
    storage_path TEXT,
    run_id TEXT REFERENCES analysis_runs(run_id) ON DELETE SET NULL,
    error TEXT,
    detected_at TEXT NOT NULL,
    submitted_at TEXT,
    finished_at TEXT,
    UNIQUE (source_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    record_id TEXT NOT NULL,
    priority TEXT NOT NULL,
    recipient_name TEXT NOT NULL,
    recipient_role TEXT NOT NULL,
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    UNIQUE (record_id, recipient_name, channel)
);

CREATE INDEX IF NOT EXISTS idx_runs_file_created
    ON analysis_runs(file_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status_created
    ON analysis_runs(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_archived_created
    ON analysis_runs(archived_at, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_orders_status_priority
    ON work_orders(status, priority, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_orders_run
    ON work_orders(run_id, event_number);
CREATE INDEX IF NOT EXISTS idx_work_orders_archived_updated
    ON work_orders(archived_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_calls_time
    ON model_call_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_model_calls_run
    ON model_call_logs(run_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ingestions_source_detected
    ON data_ingestions(source_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_status_created
    ON notifications(status, created_at DESC);
