PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    intent_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    candidate_commit TEXT,
    artifact_digest TEXT,
    gate_verdict TEXT NOT NULL DEFAULT 'UNKNOWN',
    evidence_manifest_path TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    safe_json TEXT NOT NULL,
    raw_object_ref TEXT,
    redaction_count INTEGER NOT NULL DEFAULT 0 CHECK(redaction_count >= 0),
    adapter_state TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_events_run_time
    ON agent_events(run_id, occurred_at, event_id);

CREATE TABLE IF NOT EXISTS gate_verdicts (
    verdict_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    subject_commit TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    acceptance_hash TEXT NOT NULL,
    verifier_version TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK(verdict IN ('PASS','FAIL','BLOCKED')),
    observations_json TEXT NOT NULL,
    verified_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gate_verdicts_run_time
    ON gate_verdicts(run_id, verified_at DESC);

CREATE TABLE IF NOT EXISTS experience_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    candidate_type TEXT NOT NULL CHECK(candidate_type IN ('SKILL','ADR','PROFILE','CONVENTION','FAILURE_RUNBOOK')),
    title TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PROPOSED','APPROVED','REJECTED','STALE','RETIRED')),
    requires_owner_approval INTEGER NOT NULL DEFAULT 1 CHECK(requires_owner_approval IN (0,1)),
    created_at TEXT NOT NULL,
    approved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_experience_candidates_state
    ON experience_candidates(state, candidate_type, created_at DESC);
