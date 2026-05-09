"""ffmpeg subprocess helpers for media-analysis-mcp.

Currently exposes ``extract_frames`` for timestamp-based PNG extraction.
The video upload+poll lifecycle for Gemini lives in ``gemini_media.py``;
this module is for *local* media transformations (ffmpeg / ffprobe).

Error codes raised here:
- FFMPEG_NOT_INSTALLED — ffmpeg binary not found on PATH
- FFMPEG_FAILED — ffmpeg subprocess returned non-zero
- INVALID_INPUT — malformed timestamp (neither float nor HH:MM:SS)
- VIDEO_NOT_FOUND — video file does not exist
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional, Union


Timestamp = Union[float, int, str]


def _ensure_ffmpeg() -> None:
    """Raise ``FFMPEG_NOT_INSTALLED`` if ffmpeg isn't on PATH."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "FFMPEG_NOT_INSTALLED: ffmpeg binary not found on PATH. "
            "Install it (e.g., `brew install ffmpeg` on macOS)."
        )


def parse_timestamp(ts: Timestamp) -> tuple[str, float]:
    """Convert a ``Timestamp`` to ``(ffmpeg_arg, seconds_float)``.

    Accepts:
    - numeric: ``5.5`` → (``"5.5"``, ``5.5``)
    - numeric string: ``"5.5"`` → (``"5.5"``, ``5.5``)
    - HH:MM:SS or HH:MM:SS.mmm: ``"00:00:05.500"`` → (``"00:00:05.500"``, ``5.5``)
    - MM:SS or MM:SS.mmm: ``"00:05.500"`` → (``"00:05.500"``, ``5.5``)

    Raises ``INVALID_INPUT`` for anything else.
    """
    if isinstance(ts, (int, float)):
        if ts < 0:
            raise RuntimeError(
                f"INVALID_INPUT: timestamp must be non-negative (got {ts!r})"
            )
        return str(float(ts)), float(ts)

    if not isinstance(ts, str):
        raise RuntimeError(
            f"INVALID_INPUT: timestamp must be a number or string (got {type(ts).__name__})"
        )

    s = ts.strip()
    if ":" in s:
        parts = s.split(":")
        try:
            if len(parts) == 3:
                seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                seconds = int(parts[0]) * 60 + float(parts[1])
            else:
                raise ValueError
        except ValueError as exc:
            raise RuntimeError(
                f"INVALID_INPUT: timestamp {ts!r} is not a valid HH:MM:SS / "
                f"MM:SS form"
            ) from exc
        if seconds < 0:
            raise RuntimeError(
                f"INVALID_INPUT: timestamp {ts!r} is negative"
            )
        return s, seconds

    try:
        f = float(s)
    except ValueError as exc:
        raise RuntimeError(
            f"INVALID_INPUT: timestamp {ts!r} is not parseable as a number"
        ) from exc
    if f < 0:
        raise RuntimeError(f"INVALID_INPUT: timestamp {ts!r} is negative")
    return s, f


def extract_frames(
    *,
    video_path: Path,
    timestamps: Iterable[Timestamp],
    out_dir: Path,
    title_prefix: str,
    image_module: Any,
) -> list[dict[str, Any]]:
    """Extract one PNG frame per timestamp via ``ffmpeg``.

    Uses frame-accurate seek (``-ss`` after ``-i``) — slower than fast seek
    but lands on the exact target frame, which matters for cut-detection
    workflows where frames are sampled at sub-second resolution.

    Returns a list of ``{timestamp_s, path, width, height}`` in input order.
    Raises ``FFMPEG_FAILED`` on any subprocess error.

    ``image_module`` is the Pillow ``Image`` module (passed in to avoid this
    file importing PIL directly — ``gemini_media.require_pillow()`` is the
    canonical entry).
    """
    _ensure_ffmpeg()

    if not video_path.is_file():
        raise RuntimeError(f"VIDEO_NOT_FOUND: {video_path}")

    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for ts in timestamps:
        ffmpeg_arg, seconds = parse_timestamp(ts)
        out_path = (out_dir / f"{title_prefix}_t{seconds:06.3f}.png").resolve()

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-loglevel", "error",
            "-i", str(video_path),
            "-ss", ffmpeg_arg,
            "-frames:v", "1",
            "-y",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "").strip().splitlines()[-3:]
            raise RuntimeError(
                f"FFMPEG_FAILED: ffmpeg exited {proc.returncode} extracting "
                f"frame at {ts!r}: {' | '.join(stderr_tail) or '(no stderr)'}"
            )
        if not out_path.is_file():
            raise RuntimeError(
                f"FFMPEG_FAILED: ffmpeg returned 0 but no output file at {out_path} "
                f"(timestamp {ts!r}). Likely the timestamp is past the video end."
            )

        try:
            with image_module.open(out_path) as img:
                width, height = img.size
        except Exception:
            width, height = 0, 0

        results.append(
            {
                "timestamp_s": seconds,
                "path": str(out_path),
                "width": int(width),
                "height": int(height),
            }
        )

    return results
