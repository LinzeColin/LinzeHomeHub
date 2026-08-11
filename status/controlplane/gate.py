"""Independent deterministic Gate bound to an immutable Candidate subject."""

from __future__ import annotations

try:
    from status.controlplane.v3.gate import legacy_observation_gate as _legacy_observation_gate
except ModuleNotFoundError as exc:
    # The frozen unit runner imports this module as ``controlplane.gate`` after
    # adding ``status/`` to sys.path.  Keep that supported without masking an
    # unrelated missing dependency inside the v3 implementation.
    if exc.name != "status":
        raise
    from .v3.gate import legacy_observation_gate as _legacy_observation_gate
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping


_ALLOWED_OBSERVATION_STATES = {"PASS", "FAIL", "UNKNOWN", "NOT_RUN", "WAIVED"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def evaluate(contract: Mapping[str, Any], observations: Mapping[str, Any], *, verified_at: str | None = None) -> dict[str, Any]:
    # LEGACY_CALLER_OBSERVATIONS_HAVE_NO_RELEASE_AUTHORITY
    return _legacy_observation_gate(contract, observations, test_only=bool(contract.get("allow_test_only_legacy_evaluation")))
