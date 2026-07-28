"""Bounded, rebuildable SQLite journal for development-time Agent governance."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AgentStore:
    def __init__(self, path: Path, migration: Path | None = None) -> None:
        self.path = Path(path)
        self.migration = migration or Path(__file__).with_name("sql") / "002_agent_governance.sql"

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if read_only:
            connection = sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        if not read_only:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.executescript(self.migration.read_text(encoding="utf-8"))
            connection.commit()

    def upsert_run(self, run: Mapping[str, Any]) -> None:
        required = ("run_id", "project_id", "task_id", "provider", "intent_hash", "started_at")
        missing = [key for key in required if not str(run.get(key) or "").strip()]
        if missing:
            raise ValueError(f"run missing required fields: {','.join(missing)}")
        now = str(run.get("updated_at") or _now())
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO agent_runs(
                    run_id,project_id,task_id,provider,intent_hash,status,started_at,ended_at,
                    candidate_commit,artifact_digest,gate_verdict,evidence_manifest_path,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status, ended_at=COALESCE(excluded.ended_at,agent_runs.ended_at),
                    candidate_commit=COALESCE(excluded.candidate_commit,agent_runs.candidate_commit),
                    artifact_digest=COALESCE(excluded.artifact_digest,agent_runs.artifact_digest),
                    evidence_manifest_path=COALESCE(excluded.evidence_manifest_path,agent_runs.evidence_manifest_path),
                    updated_at=excluded.updated_at""",
                (
                    run["run_id"], run["project_id"], run["task_id"], run["provider"],
                    run["intent_hash"], run.get("status", "RUNNING"), run["started_at"],
                    run.get("ended_at"), run.get("candidate_commit"), run.get("artifact_digest"),
                    run.get("gate_verdict", "UNKNOWN"), run.get("evidence_manifest_path"), now,
                ),
            )

    def add_event(self, event: Mapping[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO agent_events(
                    event_id,run_id,session_id,provider,event_type,occurred_at,safe_json,
                    raw_object_ref,redaction_count,adapter_state,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event["event_id"], event["run_id"], event["session_id"], event["provider"],
                    event["event_type"], event["occurred_at"],
                    json.dumps(event["safe_payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    event.get("raw_object_ref"), int(event.get("redaction_count", 0)),
                    event.get("adapter_state", "NORMALIZED"), event.get("created_at") or _now(),
                ),
            )

    def record_gate(self, verdict: Mapping[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO gate_verdicts(
                    verdict_id,run_id,subject_commit,artifact_digest,acceptance_hash,
                    verifier_version,verdict,observations_json,verified_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    verdict["verdict_id"], verdict["run_id"], verdict["subject_commit"],
                    verdict["artifact_digest"], verdict["acceptance_hash"], verdict["verifier_version"],
                    verdict["verdict"], json.dumps(verdict["observations"], ensure_ascii=False, sort_keys=True),
                    verdict["verified_at"],
                ),
            )
            connection.execute(
                "UPDATE agent_runs SET gate_verdict=?,candidate_commit=?,artifact_digest=?,updated_at=? WHERE run_id=?",
                (verdict["verdict"], verdict["subject_commit"], verdict["artifact_digest"], verdict["verified_at"], verdict["run_id"]),
            )

    def add_candidate(self, candidate: Mapping[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO experience_candidates(
                    candidate_id,run_id,candidate_type,title,evidence_refs_json,state,
                    requires_owner_approval,created_at,approved_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    candidate["candidate_id"], candidate["run_id"], candidate["candidate_type"],
                    candidate["title"], json.dumps(candidate["evidence_refs"], ensure_ascii=False, sort_keys=True),
                    candidate["state"], 1 if candidate.get("requires_owner_approval", True) else 0,
                    candidate["created_at"], candidate.get("approved_at"),
                ),
            )

    def snapshot(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"runs": [], "events": [], "gates": [], "candidates": []}
        with self.connect(read_only=True) as connection:
            runs = [dict(row) for row in connection.execute("SELECT * FROM agent_runs ORDER BY updated_at DESC,run_id")]
            events = [dict(row) for row in connection.execute("SELECT * FROM agent_events ORDER BY occurred_at DESC,event_id LIMIT 200")]
            gates = [dict(row) for row in connection.execute("SELECT * FROM gate_verdicts ORDER BY verified_at DESC,verdict_id LIMIT 100")]
            candidates = [dict(row) for row in connection.execute("SELECT * FROM experience_candidates ORDER BY created_at DESC,candidate_id LIMIT 100")]
        for row in events:
            row["safe_payload"] = json.loads(row.pop("safe_json"))
        for row in gates:
            row["observations"] = json.loads(row.pop("observations_json"))
        for row in candidates:
            row["evidence_refs"] = json.loads(row.pop("evidence_refs_json"))
            row["requires_owner_approval"] = bool(row["requires_owner_approval"])
        return {"runs": runs, "events": events, "gates": gates, "candidates": candidates}
