PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS command_journal (
    command_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    command_type TEXT NOT NULL,
    fact_type TEXT NOT NULL,
    expected_revision INTEGER NOT NULL,
    committed_revision INTEGER NOT NULL,
    actor_hash TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_facts (
    fact_id TEXT PRIMARY KEY,
    fact_type TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    event_id TEXT PRIMARY KEY,
    fact_id TEXT NOT NULL,
    authority_target TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','SENT','FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    FOREIGN KEY(fact_id) REFERENCES runtime_facts(fact_id)
);

CREATE INDEX IF NOT EXISTS idx_outbox_status_created
    ON outbox(status, created_at);

CREATE TABLE IF NOT EXISTS cursors (
    cursor_name TEXT PRIMARY KEY,
    cursor_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1');
INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('revision', '0');
