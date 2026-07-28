"""Rebuildable SQLite journal, idempotency and outbox implementation."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping

from .models import canonical_json, stable_id


class StoreError(RuntimeError):
    pass


class RevisionConflict(StoreError):
    pass


class IdempotencyConflict(StoreError):
    pass


@dataclass(frozen=True)
class CommandOutcome:
    command_id: str
    idempotency_key: str
    fact_id: str
    event_id: str
    committed_revision: int
    replayed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "fact_id": self.fact_id,
            "event_id": self.event_id,
            "committed_revision": self.committed_revision,
            "replayed": self.replayed,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class RuntimeStore:
    def __init__(self, path: Path, migration_sql: Path | None = None) -> None:
        self.path = Path(path)
        self.migration_sql = migration_sql or Path(__file__).with_name("sql") / "001_runtime.sql"

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

    def migrate(self) -> None:
        sql = self.migration_sql.read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(sql)
            connection.commit()

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

    @staticmethod
    def _revision(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key='revision'"
        ).fetchone()
        if not row:
            raise StoreError("revision metadata is missing")
        return int(row[0])

    def current_revision(self) -> int:
        with self.connect(read_only=True) as connection:
            return self._revision(connection)

    def apply_command(
        self,
        *,
        idempotency_key: str,
        command_type: str,
        expected_revision: int,
        actor_hash: str,
        payload: Mapping[str, Any],
        fact_type: str,
        authority_target: str = "Private-Database",
        now: str | None = None,
    ) -> CommandOutcome:
        if not idempotency_key or len(idempotency_key) > 160:
            raise StoreError("invalid idempotency key")
        if not actor_hash or len(actor_hash) > 128:
            raise StoreError("invalid actor hash")
        timestamp = now or _now()
        payload_json = canonical_json(dict(payload))
        payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
        command_id = stable_id("command", idempotency_key)
        fact_id = stable_id("fact", fact_type, idempotency_key)
        event_id = stable_id("event", authority_target, fact_id)

        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT command_type,expected_revision,actor_hash,payload_hash,fact_type,result_json "
                "FROM command_journal WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if (existing["command_type"] != command_type or
                    int(existing["expected_revision"]) != expected_revision or
                    existing["actor_hash"] != actor_hash or
                    existing["payload_hash"] != payload_hash or
                    existing["fact_type"] != fact_type):
                    raise IdempotencyConflict("same idempotency key was used for a different request")
                result = json.loads(existing["result_json"])
                return CommandOutcome(
                    command_id=result["command_id"],
                    idempotency_key=idempotency_key,
                    fact_id=result["fact_id"],
                    event_id=result["event_id"],
                    committed_revision=int(result["committed_revision"]),
                    replayed=True,
                )

            current = self._revision(connection)
            if current != expected_revision:
                raise RevisionConflict(
                    f"expected revision {expected_revision}, current revision is {current}"
                )
            committed = current + 1
            result = {
                "command_id": command_id,
                "idempotency_key": idempotency_key,
                "fact_id": fact_id,
                "event_id": event_id,
                "committed_revision": committed,
            }
            connection.execute(
                "INSERT INTO runtime_facts(fact_id,fact_type,revision,payload_json,payload_hash,completed_at) "
                "VALUES(?,?,?,?,?,?)",
                (fact_id, fact_type, committed, payload_json, payload_hash, timestamp),
            )
            outbox_payload = canonical_json({
                "schema_version": 1,
                "event_id": event_id,
                "fact_id": fact_id,
                "fact_type": fact_type,
                "revision": committed,
                "payload": dict(payload),
                "payload_hash": payload_hash,
                "completed_at": timestamp,
                "authority_target": authority_target,
            })
            connection.execute(
                "INSERT INTO outbox(event_id,fact_id,authority_target,payload_json,payload_hash,status,created_at) "
                "VALUES(?,?,?,?,?,'PENDING',?)",
                (
                    event_id,
                    fact_id,
                    authority_target,
                    outbox_payload,
                    sha256(outbox_payload.encode("utf-8")).hexdigest(),
                    timestamp,
                ),
            )
            connection.execute(
                "INSERT INTO command_journal(command_id,idempotency_key,command_type,fact_type,expected_revision,"
                "committed_revision,actor_hash,payload_hash,result_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    command_id,
                    idempotency_key,
                    command_type,
                    fact_type,
                    expected_revision,
                    committed,
                    actor_hash,
                    payload_hash,
                    canonical_json(result),
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE schema_meta SET value=? WHERE key='revision'",
                (str(committed),),
            )
            return CommandOutcome(
                command_id=command_id,
                idempotency_key=idempotency_key,
                fact_id=fact_id,
                event_id=event_id,
                committed_revision=committed,
                replayed=False,
            )

    def pending_outbox(self, limit: int = 100, *, now: str | None = None, max_attempts: int = 5) -> list[dict[str, Any]]:
        instant = now or _now()
        with self.connect(read_only=True) as connection:
            rows = connection.execute(
                "SELECT event_id,fact_id,authority_target,payload_json,payload_hash,attempts,created_at "
                "FROM outbox WHERE attempts < ? AND (status='PENDING' OR "
                "(status='FAILED' AND (next_attempt_at IS NULL OR next_attempt_at<=?))) "
                "ORDER BY created_at,event_id LIMIT ?",
                (max(1, max_attempts), instant, max(1, min(limit, 1000))),
            ).fetchall()
            return [
                {
                    "event_id": row["event_id"],
                    "fact_id": row["fact_id"],
                    "authority_target": row["authority_target"],
                    "payload": json.loads(row["payload_json"]),
                    "payload_hash": row["payload_hash"],
                    "attempts": row["attempts"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def mark_sent(self, event_id: str, sent_at: str | None = None) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE outbox SET status='SENT',sent_at=?,last_error_code=NULL WHERE event_id=?",
                (sent_at or _now(), event_id),
            )
            if cursor.rowcount != 1:
                raise StoreError(f"unknown outbox event: {event_id}")

    def mark_failed(self, event_id: str, error_code: str, next_attempt_at: str | None = None) -> None:
        safe_code = "".join(ch for ch in error_code.upper() if ch.isalnum() or ch in "_-")[:64]
        if not safe_code:
            safe_code = "UNKNOWN_ERROR"
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE outbox SET status='FAILED',attempts=attempts+1,last_error_code=?,next_attempt_at=? "
                "WHERE event_id=?",
                (safe_code, next_attempt_at, event_id),
            )
            if cursor.rowcount != 1:
                raise StoreError(f"unknown outbox event: {event_id}")

    def latest_fact(self, fact_type: str) -> dict[str, Any] | None:
        with self.connect(read_only=True) as connection:
            row = connection.execute(
                "SELECT fact_id,fact_type,revision,payload_json,payload_hash,completed_at "
                "FROM runtime_facts WHERE fact_type=? ORDER BY revision DESC LIMIT 1",
                (fact_type,),
            ).fetchone()
            if not row:
                return None
            return {
                "fact_id": row["fact_id"],
                "fact_type": row["fact_type"],
                "revision": row["revision"],
                "payload": json.loads(row["payload_json"]),
                "payload_hash": row["payload_hash"],
                "completed_at": row["completed_at"],
            }

    def projection(self) -> dict[str, Any]:
        with self.connect(read_only=True) as connection:
            revision = self._revision(connection)
            rows = connection.execute(
                "SELECT fact_id,fact_type,revision,payload_json,payload_hash,completed_at "
                "FROM runtime_facts ORDER BY revision,fact_id"
            ).fetchall()
            return {
                "schema_version": 1,
                "revision": revision,
                "facts": [
                    {
                        "fact_id": row["fact_id"],
                        "fact_type": row["fact_type"],
                        "revision": row["revision"],
                        "payload": json.loads(row["payload_json"]),
                        "payload_hash": row["payload_hash"],
                        "completed_at": row["completed_at"],
                    }
                    for row in rows
                ],
            }
