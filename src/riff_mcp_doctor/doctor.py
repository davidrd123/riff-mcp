"""Doctor command — reports environment + dependency readiness.

Exits 0 when every required check passes, 1 otherwise. Optional checks
(network) only contribute to the exit code when ``--network`` is requested.

Output is plain text. ``--json`` emits a structured result for scripting.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from typing import Optional

# ``GEMINI_API_KEY`` and ``REPLICATE_API_TOKEN`` are the only secrets the
# servers read directly. Everything else is either a Python import or a
# binary on PATH.
GEMINI_KEY = "GEMINI_API_KEY"
REPLICATE_TOKEN = "REPLICATE_API_TOKEN"

GENERATION_SERVER = "gemini-prompts-mcp"
ANALYSIS_SERVER = "media-analysis-mcp"
BATCH_CLI = "gemini-video-prompts"


@dataclass
class CheckResult:
    name: str
    category: str
    status: str  # "ok" | "warn" | "fail" | "skipped"
    detail: str
    required_for: list[str] = field(default_factory=list)


def check_env(name: str, required_for: list[str]) -> CheckResult:
    val = os.getenv(name)
    if val:
        return CheckResult(
            name=name,
            category="env",
            status="ok",
            detail=f"set ({len(val)} chars)",
            required_for=required_for,
        )
    return CheckResult(
        name=name,
        category="env",
        status="fail",
        detail="not set",
        required_for=required_for,
    )


def check_python_pkg(import_name: str, required_for: list[str]) -> CheckResult:
    try:
        spec = importlib.util.find_spec(import_name)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return CheckResult(
            name=import_name,
            category="python",
            status="fail",
            detail="not importable",
            required_for=required_for,
        )
    return CheckResult(
        name=import_name,
        category="python",
        status="ok",
        detail="importable",
        required_for=required_for,
    )


def check_binary(name: str, required_for: list[str]) -> CheckResult:
    path = shutil.which(name)
    if path is None:
        return CheckResult(
            name=name,
            category="binary",
            status="fail",
            detail="not on PATH",
            required_for=required_for,
        )
    return CheckResult(
        name=name,
        category="binary",
        status="ok",
        detail=path,
        required_for=required_for,
    )


def check_gemini_network() -> CheckResult:
    if not os.getenv(GEMINI_KEY):
        return CheckResult(
            name="gemini",
            category="network",
            status="skipped",
            detail=f"{GEMINI_KEY} not set",
            required_for=[GENERATION_SERVER, ANALYSIS_SERVER],
        )
    try:
        from google import genai  # type: ignore
    except ImportError:
        return CheckResult(
            name="gemini",
            category="network",
            status="skipped",
            detail="google-genai not installed",
            required_for=[GENERATION_SERVER, ANALYSIS_SERVER],
        )
    try:
        client = genai.Client(api_key=os.environ[GEMINI_KEY])
        models = list(client.models.list())
        return CheckResult(
            name="gemini",
            category="network",
            status="ok",
            detail=f"{len(models)} models reachable",
            required_for=[GENERATION_SERVER, ANALYSIS_SERVER],
        )
    except Exception as exc:  # noqa: BLE001 — surface any failure
        return CheckResult(
            name="gemini",
            category="network",
            status="fail",
            detail=f"{type(exc).__name__}: {exc}",
            required_for=[GENERATION_SERVER, ANALYSIS_SERVER],
        )


def check_replicate_network() -> CheckResult:
    if not os.getenv(REPLICATE_TOKEN):
        return CheckResult(
            name="replicate",
            category="network",
            status="skipped",
            detail=f"{REPLICATE_TOKEN} not set",
            required_for=[GENERATION_SERVER],
        )
    try:
        import replicate  # type: ignore
    except ImportError:
        return CheckResult(
            name="replicate",
            category="network",
            status="skipped",
            detail="replicate not installed",
            required_for=[GENERATION_SERVER],
        )
    try:
        # Cheapest authenticated GET — listing the caller's account/models.
        # ``replicate.models.list`` returns a paginator; iterating once is enough.
        page = replicate.models.list()
        first = next(iter(page), None)
        detail = "auth ok" if first is not None else "auth ok (empty page)"
        return CheckResult(
            name="replicate",
            category="network",
            status="ok",
            detail=detail,
            required_for=[GENERATION_SERVER],
        )
    except Exception as exc:  # noqa: BLE001 — surface any failure
        return CheckResult(
            name="replicate",
            category="network",
            status="fail",
            detail=f"{type(exc).__name__}: {exc}",
            required_for=[GENERATION_SERVER],
        )


def run_all_checks(*, network: bool = False) -> list[CheckResult]:
    """Return all check results in display order."""
    results: list[CheckResult] = []

    # Env vars
    results.append(check_env(GEMINI_KEY, [GENERATION_SERVER, ANALYSIS_SERVER, BATCH_CLI]))
    results.append(check_env(REPLICATE_TOKEN, [GENERATION_SERVER]))

    # Python packages — uses import names, not pip names. ``google.genai`` ships
    # as the ``google-genai`` distribution; ``PIL`` ships as ``pillow``;
    # ``yaml`` as ``PyYAML``; ``dotenv`` as ``python-dotenv``.
    pkgs = [
        ("google.genai", [GENERATION_SERVER, ANALYSIS_SERVER, BATCH_CLI]),
        ("replicate", [GENERATION_SERVER]),
        ("mcp", [GENERATION_SERVER, ANALYSIS_SERVER]),
        ("PIL", [GENERATION_SERVER, ANALYSIS_SERVER, BATCH_CLI]),
        ("pydantic", [GENERATION_SERVER, ANALYSIS_SERVER]),
        ("yaml", [BATCH_CLI]),
        ("dotenv", [GENERATION_SERVER, ANALYSIS_SERVER, BATCH_CLI]),
    ]
    for import_name, required_for in pkgs:
        results.append(check_python_pkg(import_name, required_for))

    # Binaries on PATH — ffprobe is required by both ``generate_video``
    # (post-output media_info) and ``describe_video`` (Gemini upload prep).
    # ffmpeg is required by ``extract_video_frames``.
    results.append(check_binary(
        "ffmpeg",
        [f"{ANALYSIS_SERVER}.extract_video_frames"],
    ))
    results.append(check_binary(
        "ffprobe",
        [
            f"{GENERATION_SERVER}.generate_video (media_info)",
            f"{ANALYSIS_SERVER}.describe_video",
        ],
    ))

    # Network (optional)
    if network:
        results.append(check_gemini_network())
        results.append(check_replicate_network())

    return results


_STATUS_LABEL = {
    "ok": "OK",
    "warn": "WARN",
    "fail": "FAIL",
    "skipped": "SKIP",
}


def format_text(results: list[CheckResult]) -> str:
    """Pretty-print check results as plain text."""
    lines: list[str] = []
    lines.append("riff-mcp doctor")
    lines.append("=" * 64)

    by_category: dict[str, list[CheckResult]] = {}
    for r in results:
        by_category.setdefault(r.category, []).append(r)

    category_titles = {
        "env": "Environment variables",
        "python": "Python packages",
        "binary": "Binaries on PATH",
        "network": "Network (--network)",
    }

    for cat in ("env", "python", "binary", "network"):
        rows = by_category.get(cat)
        if not rows:
            continue
        lines.append("")
        lines.append(category_titles[cat])
        name_w = max(len(r.name) for r in rows)
        for r in rows:
            label = _STATUS_LABEL[r.status]
            lines.append(f"  {r.name:<{name_w}}  {label:<5}  {r.detail}")

    lines.append("")
    lines.append("=" * 64)
    failures = [r for r in results if r.status == "fail"]
    if not failures:
        lines.append("All checks passed.")
    else:
        lines.append(f"{len(failures)} check(s) failed:")
        for r in failures:
            users = ", ".join(r.required_for) if r.required_for else "unknown"
            lines.append(f"  - {r.name}: {r.detail} (required for: {users})")
    return "\n".join(lines) + "\n"


def format_json(results: list[CheckResult]) -> str:
    return json.dumps(
        {"results": [asdict(r) for r in results]},
        indent=2,
    ) + "\n"


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="riff-mcp-doctor",
        description=(
            "Diagnose riff-mcp environment readiness — checks env vars, "
            "Python packages, and binaries needed by the MCP servers and CLI."
        ),
    )
    parser.add_argument(
        "--network",
        action="store_true",
        help="Also verify Gemini + Replicate API tokens by issuing a cheap GET.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit results as JSON instead of plain text.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    results = run_all_checks(network=args.network)
    output = format_json(results) if args.json else format_text(results)
    sys.stdout.write(output)
    failures = [r for r in results if r.status == "fail"]
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
