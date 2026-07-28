from __future__ import annotations

from pathlib import Path
import sys


def locate() -> tuple[Path, Path, Path]:
    here = Path(__file__).resolve()
    if "status-control-plane" in here.parts:
        repo = here.parents[2]
        module_root = repo / "status"
        contract_root = here.parent / "contracts"
    else:
        taskpack = here.parent.parent
        repo = taskpack
        module_root = taskpack / "implementation" / "repo_overlay" / "status"
        contract_root = taskpack / "governance"
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))
    return repo, module_root, contract_root
