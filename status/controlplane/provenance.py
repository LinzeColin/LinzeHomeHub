"""Lightweight in-toto/SLSA-inspired provenance statements."""

from __future__ import annotations

from typing import Any, Mapping

from .models import content_hash, stable_id, utc_now


def provenance_statement(
    *,
    candidate_commit: str,
    artifact_name: str,
    artifact_digest: str,
    deployment_digest: str | None,
    integration_base: str,
    taskpack_version: str,
    acceptance_hash: str,
    builder: Mapping[str, Any],
    materials: list[Mapping[str, Any]],
) -> dict[str, Any]:
    predicate = {
        "taskpack_version": taskpack_version,
        "integration_base": integration_base,
        "candidate_commit": candidate_commit,
        "deployment_digest": deployment_digest,
        "acceptance_hash": acceptance_hash,
        "builder": dict(builder),
        "materials": [dict(item) for item in materials],
        "generated_at": utc_now(),
    }
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": artifact_name, "digest": {"sha256": artifact_digest}}],
        "predicateType": "https://status.linzezhang.com/provenance/v1",
        "predicate": predicate,
        "statement_id": stable_id("provenance", candidate_commit, artifact_digest),
        "predicate_hash": content_hash(predicate),
    }
