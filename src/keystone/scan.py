"""Security scan plane. Network OFF, CPU only, runs before anything is loaded.

We integrate commodity scanners rather than writing one -- the brand is the
rating, not the scanner (design doc section 14).
"""

from __future__ import annotations

import time
from pathlib import Path

from keystone.schema import ScanResult, Status

_PICKLE_SUFFIXES = {".bin", ".pkl", ".pickle", ".pt", ".pth", ".ckpt", ".joblib", ".npy", ".npz"}


def scan_serialization(root: Path) -> ScanResult:
    """picklescan over every pickle-format artifact in the tree."""
    started = time.monotonic()
    findings: list[dict] = []
    status = Status.PASS

    try:
        from picklescan.scanner import scan_file_path
    except ImportError:
        return ScanResult(
            scanner="picklescan",
            status=Status.ERROR,
            findings=[{"error": "picklescan not installed"}],
            duration_s=0.0,
        )

    targets = [
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _PICKLE_SUFFIXES
    ]
    for path in targets:
        try:
            result = scan_file_path(path)
        except Exception as exc:  # a scanner crash is itself signal
            findings.append({"file": path.relative_to(root).as_posix(), "error": repr(exc)})
            status = Status.ERROR
            continue
        if getattr(result, "issues_count", 0):
            status = Status.FAIL
            findings.append(
                {
                    "file": path.relative_to(root).as_posix(),
                    "issues": result.issues_count,
                    "globals": [
                        {"module": g.module, "name": g.name, "safety": str(g.safety)}
                        for g in getattr(result, "globals", [])
                    ],
                }
            )

    return ScanResult(
        scanner="picklescan",
        status=status,
        findings=findings,
        duration_s=round(time.monotonic() - started, 3),
    )


def scan_format_hygiene(root: Path) -> ScanResult:
    """Flag pickle-format weights when no safetensors equivalent exists.

    Not a vulnerability on its own, but it is the precondition for one, and
    procurement asks about it.
    """
    has_safetensors = any(root.rglob("*.safetensors"))
    pickles = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in {".bin", ".pt", ".pth", ".ckpt"}
    ]
    if pickles and not has_safetensors:
        return ScanResult(
            scanner="format_hygiene",
            status=Status.WARN,
            findings=[{"reason": "pickle-only weights, no safetensors", "files": pickles[:20]}],
        )
    return ScanResult(scanner="format_hygiene", status=Status.PASS)


def run_all(root: Path) -> list[ScanResult]:
    return [scan_serialization(root), scan_format_hygiene(root)]
