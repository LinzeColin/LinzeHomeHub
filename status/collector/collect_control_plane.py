#!/usr/bin/env python3
"""Run after the existing status and GitHub collectors."""

from pathlib import Path
import sys

STATUS = Path(__file__).resolve().parents[1]
if str(STATUS) not in sys.path:
    sys.path.insert(0, str(STATUS))

from controlplane.collector import collect_control_plane


def main() -> int:
    data = STATUS / "data"
    private = STATUS / "private"
    collect_control_plane(
        status_path=data / "snapshot.json",
        github_path=data / "github_public.json",
        output_private=private / "control-plane.json",
        output_public=data / "control-plane.json",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
