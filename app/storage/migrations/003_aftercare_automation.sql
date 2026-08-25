ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS sla_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS last_sla_action_at TEXT;
ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS reinspection_status TEXT;
ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS reinspection_scheduled_at TEXT;
ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS reinspection_run_id TEXT REFERENCES analysis_runs(run_id) ON DELETE SET NULL;
ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS reinspection_summary TEXT;

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS notification_kind TEXT NOT NULL DEFAULT 'initial';
ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS escalation_level INTEGER NOT NULL DEFAULT 0;

ALTER TABLE notifications
    DROP CONSTRAINT IF EXISTS notifications_record_id_recipient_name_channel_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_delivery_stage
    ON notifications(
        record_id,
        recipient_name,
        channel,
        notification_kind,
        escalation_level
    );

CREATE INDEX IF NOT EXISTS idx_work_orders_sla_candidates
    ON work_orders(status, accepted_at, sla_level, created_at);
CREATE INDEX IF NOT EXISTS idx_work_orders_reinspection_candidates
    ON work_orders(reinspection_status, reinspection_scheduled_at);
CREATE INDEX IF NOT EXISTS idx_runs_source_finished
    ON analysis_runs(source_id, status, started_at DESC);
