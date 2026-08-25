CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active SMALLINT NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS user_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS recipient_user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL;
ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS read_at TEXT;
ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS acknowledged_at TEXT;
ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS acknowledged_by TEXT REFERENCES users(user_id) ON DELETE SET NULL;

ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS assigned_user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL;
ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS accepted_at TEXT;
ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS accepted_by TEXT REFERENCES users(user_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_users_role_active
    ON users(role, active);
CREATE INDEX IF NOT EXISTS idx_sessions_token_active
    ON user_sessions(token_hash, revoked_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_audit_target_created
    ON audit_logs(target_type, target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_recipient_created
    ON notifications(recipient_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_orders_assigned_updated
    ON work_orders(assigned_user_id, updated_at DESC);
