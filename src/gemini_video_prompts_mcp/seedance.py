"""Seedance 2.0 (Replicate) adapter for the gemini-prompts-mcp generate_video tool.

Owns:
- Replicate-shape param mapping from MCP tool inputs
- Hard validation per MCP_DESIGN.md (mut.ex., per-type caps, range checks)
- Soft warnings (12-file vault cap, prompt-token mismatches)
- Mode derivation (text_to_video / first_last_frames / omni_reference)
- References map ([{token, path, role}] in upload order, bracket-syntax tokens)
- Multi-file handle lifecycle (image, last_frame_image, reference_*)
- Sidecar sanitization — file handles never leak into returned JSON
- ffprobe-derived media_info per output (graceful when ffprobe missing)

Pure functions are I/O-free and unit-testable (build_seedance_video_params,
derive_mode, build_references_map, check_*). The single I/O entry point is
``run_seedance_job`` which opens handles, calls ``replicate_min.generate``,
and replaces sidecar inputs/resolved_params with the clean string-path dict
the caller passed in. ``replicate_min``'s built-in filter only drops legacy
keys (image, image_input, input_images); Seedance handles live elsewhere
and would leak as file objects without this explicit step.
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from . import replicate_min


SEEDANCE_MODEL_DEFAULT = "bytedance/seedance-2.0"

VALID_RESOLUTIONS = {"480p", "720p", "1080p", "4k"}  # "4k" = 10-bit H.265/HEVC, high bitrate
VALID_ASPECT_RATIOS = {"16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "9:21", "adaptive"}

# Per-type schema caps (hard — Replicate enforces these)
MAX_REFERENCE_IMAGES = 9
MAX_REFERENCE_VIDEOS = 3
MAX_REFERENCE_AUDIOS = 3

# Vault working ceiling (soft warning) — seedance-prompting-guide.md:23.
# Per-type caps sum to 15 (9+3+3); 12 is the production-practice ceiling.
TOTAL_REFERENCE_WARNING_CAP = 12


# --------------------------------------------------------------------------- #
# Pure functions                                                              #
# --------------------------------------------------------------------------- #


def derive_mode(
    *,
    image: Optional[str],
    reference_images: Optional[list[str]],
    reference_videos: Optional[list[str]] = None,
    reference_audios: Optional[list[str]] = None,
) -> str:
    """Return the named Seedance mode.

    Per ``seedance-prompting-guide.md:25``: ``text_to_video`` |
    ``first_last_frames`` | ``omni_reference``. ``omni_reference`` is
    broadened here to "any reference type set" so the mode discriminator
    always answers the agent's actual question — "is this pure text or are
    there refs to manage?" — even for reference-videos-only or reference-
    audios-only calls. The seedance guide's narrower definition (refs are
    images) is preserved in ``references[].role``, which carries the exact
    type per uploaded asset.
    """
    if image is not None:
        return "first_last_frames"
    if reference_images or reference_videos or reference_audios:
        return "omni_reference"
    return "text_to_video"


def build_references_map(
    *,
    image: Optional[str],
    last_frame_image: Optional[str],
    reference_images: Optional[list[str]],
    reference_videos: Optional[list[str]],
    reference_audios: Optional[list[str]],
) -> list[dict[str, Any]]:
    """Build ``[{token, path, role}]`` in Seedance upload order.

    Tokens are Replicate-Seedance bracket syntax (``[Image1]``, ``[Video1]``,
    ``[Audio1]``). Roles are provider-agnostic — a future Fal adapter would
    return the same role names with whatever token form Fal accepts.
    """
    refs: list[dict[str, Any]] = []

    img_idx = 1
    if image is not None:
        refs.append(
            {
                "token": f"[Image{img_idx}]",
                "path": str(Path(image).expanduser().resolve()),
                "role": "FIRST_FRAME",
            }
        )
        img_idx += 1
        if last_frame_image is not None:
            refs.append(
                {
                    "token": f"[Image{img_idx}]",
                    "path": str(Path(last_frame_image).expanduser().resolve()),
                    "role": "LAST_FRAME",
                }
            )
            img_idx += 1
    elif reference_images:
        for path_str in reference_images:
            refs.append(
                {
                    "token": f"[Image{img_idx}]",
                    "path": str(Path(path_str).expanduser().resolve()),
                    "role": "REFERENCE_IMAGE",
                }
            )
            img_idx += 1

    if reference_videos:
        for vi, path_str in enumerate(reference_videos, start=1):
            refs.append(
                {
                    "token": f"[Video{vi}]",
                    "path": str(Path(path_str).expanduser().resolve()),
                    "role": "REFERENCE_VIDEO",
                }
            )

    if reference_audios:
        for ai, path_str in enumerate(reference_audios, start=1):
            refs.append(
                {
                    "token": f"[Audio{ai}]",
                    "path": str(Path(path_str).expanduser().resolve()),
                    "role": "REFERENCE_AUDIO",
                }
            )

    return refs


def build_seedance_video_params(
    *,
    prompt: str,
    image: Optional[str] = None,
    last_frame_image: Optional[str] = None,
    reference_images: Optional[list[str]] = None,
    reference_videos: Optional[list[str]] = None,
    reference_audios: Optional[list[str]] = None,
    duration: int = 5,
    resolution: str = "720p",
    aspect_ratio: str = "16:9",
    generate_audio: bool = False,
    seed: Optional[int] = None,
) -> dict[str, Any]:
    """Validate inputs and return a string-only Replicate-shaped params dict.

    Pure: no I/O, no file handles. Caller (``run_seedance_job``) opens
    handles for each path before invoking ``replicate.run``.

    Raises ``RuntimeError`` with ``INVALID_INPUT:`` prefix for any hard
    validation failure (mut.ex., per-type caps, range, enum, anchor).
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise RuntimeError("INVALID_INPUT: prompt is required")

    ref_images = list(reference_images or [])
    ref_videos = list(reference_videos or [])
    ref_audios = list(reference_audios or [])

    # Mutual exclusivity (Seedance schema): only reference_images is
    # mut.ex. with image/last_frame_image. Videos/audios can layer on top.
    if (image is not None or last_frame_image is not None) and ref_images:
        raise RuntimeError(
            "INVALID_INPUT: image / last_frame_image are mutually exclusive "
            "with reference_images"
        )

    # last_frame_image requires a first frame
    if last_frame_image is not None and image is None:
        raise RuntimeError(
            "INVALID_INPUT: last_frame_image requires a first frame (image)"
        )

    # Per-type schema caps
    if len(ref_images) > MAX_REFERENCE_IMAGES:
        raise RuntimeError(
            f"INVALID_INPUT: reference_images exceeds per-type cap "
            f"({len(ref_images)} > {MAX_REFERENCE_IMAGES})"
        )
    if len(ref_videos) > MAX_REFERENCE_VIDEOS:
        raise RuntimeError(
            f"INVALID_INPUT: reference_videos exceeds per-type cap "
            f"({len(ref_videos)} > {MAX_REFERENCE_VIDEOS})"
        )
    if len(ref_audios) > MAX_REFERENCE_AUDIOS:
        raise RuntimeError(
            f"INVALID_INPUT: reference_audios exceeds per-type cap "
            f"({len(ref_audios)} > {MAX_REFERENCE_AUDIOS})"
        )

    # Duration: -1 (intelligent) or 4..15. Replicate's published schema is
    # looser in some places, but the live Seedance endpoint rejects <4.
    if duration != -1 and not (4 <= duration <= 15):
        raise RuntimeError(
            f"INVALID_INPUT: duration must be -1 or in [4, 15] (got {duration})"
        )

    # Resolution / aspect_ratio enum
    if resolution not in VALID_RESOLUTIONS:
        raise RuntimeError(
            f"INVALID_INPUT: resolution must be one of "
            f"{sorted(VALID_RESOLUTIONS)} (got {resolution!r})"
        )
    if aspect_ratio not in VALID_ASPECT_RATIOS:
        raise RuntimeError(
            f"INVALID_INPUT: aspect_ratio must be one of "
            f"{sorted(VALID_ASPECT_RATIOS)} (got {aspect_ratio!r})"
        )

    # reference_audios anchor requirement (schema)
    if ref_audios and not (image is not None or ref_images or ref_videos):
        raise RuntimeError(
            "INVALID_INPUT: reference_audios requires at least one of "
            "image / reference_images / reference_videos"
        )

    # Build params (string paths — caller converts to handles)
    params: dict[str, Any] = {
        "prompt": prompt.strip(),
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "generate_audio": generate_audio,
    }
    if seed is not None:
        params["seed"] = seed
    if image is not None:
        params["image"] = str(Path(image).expanduser().resolve())
    if last_frame_image is not None:
        params["last_frame_image"] = str(Path(last_frame_image).expanduser().resolve())
    if ref_images:
        params["reference_images"] = [
            str(Path(p).expanduser().resolve()) for p in ref_images
        ]
    if ref_videos:
        params["reference_videos"] = [
            str(Path(p).expanduser().resolve()) for p in ref_videos
        ]
    if ref_audios:
        params["reference_audios"] = [
            str(Path(p).expanduser().resolve()) for p in ref_audios
        ]

    return params


def build_seedance_job_hash(params: dict[str, Any]) -> str:
    """8-char hex hash over the resolved Seedance params for dir naming."""
    raw = json.dumps(params, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


# --------------------------------------------------------------------------- #
# Soft warnings                                                               #
# --------------------------------------------------------------------------- #


def check_prompt_references(
    prompt: str, references: list[dict[str, Any]]
) -> list[str]:
    """For each ``{token, path, role}``, warn if the token is not present in
    the prompt text.

    Uses tokens **from** the references list (provider-truthful) — never
    hardcoded bracket or @-syntax. Same code stays correct across providers.
    """
    warnings: list[str] = []
    for ref in references:
        token = ref.get("token", "")
        if not token:
            continue
        if token not in prompt:
            path_name = Path(ref.get("path", "")).name
            role = ref.get("role", "")
            warnings.append(
                f"{token} ({role}: {path_name}) uploaded but not referenced "
                f"in prompt — model may not assign it a role"
            )
    return warnings


def check_total_reference_cap(references: list[dict[str, Any]]) -> list[str]:
    """Soft check: warn if total references exceed the vault working cap (12).

    Per-type caps are schema-enforced as hard errors elsewhere; this is the
    project-specific working ceiling per ``seedance-prompting-guide.md:23``.
    Fires with a warning, never blocks (Portability Principle).
    """
    if len(references) > TOTAL_REFERENCE_WARNING_CAP:
        return [
            f"Total references ({len(references)}) exceed the vault working "
            f"cap of {TOTAL_REFERENCE_WARNING_CAP} (per "
            f"seedance-prompting-guide.md:23). Replicate's schema does not "
            f"enforce this; firing anyway."
        ]
    return []


# --------------------------------------------------------------------------- #
# ffprobe                                                                     #
# --------------------------------------------------------------------------- #


def probe_media_info(path: str) -> dict[str, Any]:
    """ffprobe-derived media info: ``{duration_s, fps, width, height, has_audio}``.

    Graceful when ffprobe is unavailable: returns ``{error: ..., <fields>: None}``
    rather than raising. The vault-logging contract treats media_info as a
    nice-to-have — its absence shouldn't fail the whole tool call.
    """
    null_info: dict[str, Any] = {
        "duration_s": None,
        "fps": None,
        "width": None,
        "height": None,
        "has_audio": None,
    }
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {**null_info, "error": f"ffprobe rc={result.returncode}"}
        data = json.loads(result.stdout or "{}")
    except FileNotFoundError:
        return {**null_info, "error": "ffprobe not installed"}
    except subprocess.TimeoutExpired:
        return {**null_info, "error": "ffprobe timeout"}
    except json.JSONDecodeError as e:
        return {**null_info, "error": f"ffprobe output unparseable: {e}"}

    format_info = data.get("format") or {}
    streams = data.get("streams") or []
    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    info: dict[str, Any] = {
        "duration_s": (
            float(format_info["duration"]) if "duration" in format_info else None
        ),
        "fps": None,
        "width": None,
        "height": None,
        "has_audio": has_audio,
    }
    if video_stream:
        info["width"] = video_stream.get("width")
        info["height"] = video_stream.get("height")
        rate = video_stream.get("r_frame_rate") or ""
        if "/" in rate:
            num_str, den_str = rate.split("/", 1)
            try:
                num = int(num_str)
                den = int(den_str)
                if den > 0:
                    info["fps"] = round(num / den, 2)
            except (ValueError, ZeroDivisionError):
                pass
    return info


# --------------------------------------------------------------------------- #
# Reference aspect-ratio guard (I/O — reads file headers / ffprobe)           #
# --------------------------------------------------------------------------- #
#
# Seedance's omni_reference path validates each reference's aspect ratio against
# the requested render ``aspect_ratio`` and, on a mismatch, fails server-side
# (~30-60s in) with a MISLEADING error:
#   ValueError: Error processing image /tmp/tmp…download for aspect ratio
#   validation: unknown file extension:
# The "unknown file extension" wording is a red herring — verified via source +
# a Files-API probe that the SDK stamps the real basename/content_type and the
# upload URL already ends in ``.png``; ByteDance discards it server-side and only
# trips when it must reconcile an off-ratio reference. The single-frame
# ``image=`` / ``last_frame_image=`` keys tolerate off-ratio inputs (the server
# letterboxes them); only ``reference_images`` / ``reference_videos`` are strict.
# This guard turns that opaque server failure into an instant, specific local one.

# Relative tolerance on the ratio match. Heuristic — the server's exact tolerance
# is unknown; 2% admits common "16:9-ish" exports (e.g. 1344x768 = 1.75, 1.6% off)
# while still catching genuinely wrong ratios (4:3, 1:1, 9:16).
ASPECT_RATIO_TOLERANCE = 0.02


def _parse_aspect_ratio(s: str) -> Optional[float]:
    """``"16:9"`` -> 1.778. Returns None for ``"adaptive"`` / unparseable."""
    if ":" not in s:
        return None
    a, _, b = s.partition(":")
    try:
        an, bn = float(a), float(b)
    except ValueError:
        return None
    return an / bn if bn else None


def _jpeg_dimensions(path: str) -> Optional[tuple[int, int]]:
    """(width, height) from a JPEG by scanning Start-Of-Frame markers."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"\xff\xd8":  # SOI
                return None
            while True:
                marker = f.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                m = marker[1]
                # Standalone markers (no length payload): RSTn (D0-D7), TEM (01).
                if 0xD0 <= m <= 0xD9 or m == 0x01:
                    continue
                seg = f.read(2)
                if len(seg) < 2:
                    return None
                seg_len = struct.unpack(">H", seg)[0]
                # SOF markers carry dimensions: C0-CF except DHT(C4), JPG(C8), DAC(CC).
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    f.read(1)  # sample precision
                    h, w = struct.unpack(">HH", f.read(4))
                    return w, h
                f.seek(seg_len - 2, 1)
    except (OSError, struct.error):
        return None


def _image_dimensions(path: str) -> Optional[tuple[int, int]]:
    """(width, height) for common image formats. Returns None when unknown.

    Dependency-free header parsing (PNG/JPEG/GIF/WebP) so it works under the
    documented ``uv run --with replicate --with python-dotenv`` invocation, which
    has no PIL. PIL is used opportunistically when importable (broadest coverage).
    """
    try:  # opportunistic — best coverage when the venv has Pillow
        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        pass
    try:
        with open(path, "rb") as f:
            head = f.read(32)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return tuple(struct.unpack(">II", head[16:24]))  # type: ignore[return-value]
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return tuple(struct.unpack("<HH", head[6:10]))  # type: ignore[return-value]
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        fourcc = head[12:16]
        if fourcc == b"VP8 ":
            w = struct.unpack("<H", head[26:28])[0] & 0x3FFF
            h = struct.unpack("<H", head[28:30])[0] & 0x3FFF
            return w, h
        if fourcc == b"VP8L":
            b0, b1, b2, b3 = head[21], head[22], head[23], head[24]
            w = 1 + (((b1 & 0x3F) << 8) | b0)
            h = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return w, h
        if fourcc == b"VP8X":
            w = 1 + (head[24] | (head[25] << 8) | (head[26] << 16))
            h = 1 + (head[27] | (head[28] << 8) | (head[29] << 16))
            return w, h
    if head[:2] == b"\xff\xd8":
        return _jpeg_dimensions(path)
    return None


def _video_aspect_ratio(path: str) -> Optional[float]:
    """Display aspect ratio of a video via ffprobe. None if ffprobe missing/fails."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,display_aspect_ratio",
                "-of", "json", path,
            ],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        streams = json.loads(out).get("streams") or []
        if not streams:
            return None
        s = streams[0]
        dar = s.get("display_aspect_ratio")
        if isinstance(dar, str) and ":" in dar and dar != "0:1":
            wn, hn = (float(x) for x in dar.split(":", 1))
            if hn:
                return wn / hn
        w, h = s.get("width"), s.get("height")
        if w and h:
            return float(w) / float(h)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return None


def assert_reference_aspect_ratios(
    api_params: dict[str, Any],
    *,
    tolerance: float = ASPECT_RATIO_TOLERANCE,
) -> None:
    """Reject reference_images/reference_videos that don't match the render ratio.

    Pre-flight for the omni_reference server bug documented above. Raises
    ``RuntimeError('INVALID_INPUT: ...')`` naming every offending file and its
    actual ratio. References whose dimensions can't be determined are skipped
    (unknowns don't block — we never fabricate a rejection). No mutation: callers
    fix off-ratio refs themselves. Scope is intentionally only the strict
    ``reference_*`` keys, not ``image`` / ``last_frame_image``.
    """
    aspect_ratio = api_params.get("aspect_ratio")
    target = _parse_aspect_ratio(aspect_ratio) if isinstance(aspect_ratio, str) else None
    if target is None:  # "adaptive" or unparseable — no fixed ratio to enforce
        return

    offenders: list[str] = []

    def _off(ratio: float) -> bool:
        return abs(ratio - target) / target > tolerance

    for path in api_params.get("reference_images") or []:
        dims = _image_dimensions(path)
        if not dims or not dims[1]:
            continue
        w, h = dims
        ratio = w / h
        if _off(ratio):
            pct = abs(ratio - target) / target * 100
            offenders.append(
                f"  {path}: {w}x{h} (ratio {ratio:.3f}, {pct:.0f}% off "
                f"{aspect_ratio}={target:.3f})"
            )

    for path in api_params.get("reference_videos") or []:
        ratio = _video_aspect_ratio(path)
        if ratio is None:
            continue
        if _off(ratio):
            pct = abs(ratio - target) / target * 100
            offenders.append(
                f"  {path}: ratio {ratio:.3f} ({pct:.0f}% off "
                f"{aspect_ratio}={target:.3f})"
            )

    if offenders:
        raise RuntimeError(
            "INVALID_INPUT: Seedance omni_reference requires every reference to "
            f"match the render aspect_ratio ({aspect_ratio}); off-ratio references "
            "fail server-side with a misleading 'unknown file extension' error. "
            "Re-crop/re-export these to match (or change aspect_ratio):\n"
            + "\n".join(offenders)
        )


# --------------------------------------------------------------------------- #
# I/O entry point                                                             #
# --------------------------------------------------------------------------- #


@contextmanager
def _open_seedance_file_handles(api_params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield Replicate params with Seedance path fields opened as binary files.

    Runs ``assert_reference_aspect_ratios`` first as a fail-fast pre-flight, so an
    off-ratio reference is rejected locally before any upload rather than ~30-60s
    later as an opaque server error. This is the single chokepoint for every
    submission path (``run_seedance_job`` + ``create_seedance_prediction``).
    """
    assert_reference_aspect_ratios(api_params)
    open_handles: list[Any] = []
    params = dict(api_params)

    try:
        if "image" in params:
            f = open(params["image"], "rb")
            open_handles.append(f)
            params["image"] = f
        if "last_frame_image" in params:
            f = open(params["last_frame_image"], "rb")
            open_handles.append(f)
            params["last_frame_image"] = f
        if "reference_images" in params:
            opened: list[Any] = []
            for path_str in params["reference_images"]:
                f = open(path_str, "rb")
                open_handles.append(f)
                opened.append(f)
            params["reference_images"] = opened
        if "reference_videos" in params:
            opened = []
            for path_str in params["reference_videos"]:
                f = open(path_str, "rb")
                open_handles.append(f)
                opened.append(f)
            params["reference_videos"] = opened
        if "reference_audios" in params:
            opened = []
            for path_str in params["reference_audios"]:
                f = open(path_str, "rb")
                open_handles.append(f)
                opened.append(f)
            params["reference_audios"] = opened
        yield params
    finally:
        for handle in open_handles:
            try:
                handle.close()
            except Exception:
                pass


def run_seedance_job(
    *,
    api_params: dict[str, Any],
    return_params: dict[str, Any],
    out_dir: Path,
    base_name: str,
    timeout_s: int = 600,
    model_ref: str = SEEDANCE_MODEL_DEFAULT,
) -> dict[str, Any]:
    """Open file handles, call ``replicate_min.generate``, sanitize sidecar.

    ``api_params`` carries string paths under ``image``/``last_frame_image``/
    ``reference_*``; this function opens each as a binary handle, calls the
    generic Replicate wrapper, then closes every handle in a ``finally`` block.

    The returned sidecar's ``inputs`` and ``resolved_params`` are replaced
    with ``return_params`` (string paths only). ``replicate_min``'s built-in
    filter excludes only legacy keys (``image``, ``image_input``,
    ``input_images``); Seedance handles live under ``last_frame_image``,
    ``reference_images``, ``reference_videos``, ``reference_audios`` and
    would leak as file handles into returned JSON without this step.
    """
    with _open_seedance_file_handles(api_params) as params:
        sidecar = replicate_min.generate(
            model_ref=model_ref,
            params=params,
            out_dir=out_dir,
            base_name=base_name,
            timeout_s=timeout_s,
        )

    # Sanitize: replace anything that might carry handles with the clean
    # string-path dict the caller passed in.
    sidecar["inputs"] = dict(return_params)
    sidecar["resolved_params"] = dict(return_params)
    return sidecar


def create_seedance_prediction(
    *,
    api_params: dict[str, Any],
    model_ref: str = SEEDANCE_MODEL_DEFAULT,
    webhook_url: Optional[str] = None,
    webhook_events_filter: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Create a Seedance prediction without waiting for completion."""
    with _open_seedance_file_handles(api_params) as params:
        return replicate_min.create_prediction(
            model_ref=model_ref,
            params=params,
            webhook_url=webhook_url,
            webhook_events_filter=webhook_events_filter,
        )


def get_seedance_prediction(prediction_id: str) -> dict[str, Any]:
    """Fetch the latest Seedance prediction state."""
    return replicate_min.get_prediction(prediction_id)


def cancel_seedance_prediction(prediction_id: str) -> dict[str, Any]:
    """Cancel a running Seedance prediction."""
    return replicate_min.cancel_prediction(prediction_id)


def download_prediction_outputs(
    *,
    outputs: list[Any],
    out_dir: Path,
    base_name: str,
) -> list[dict[str, Any]]:
    """Download completed prediction outputs into the job directory."""
    return replicate_min.write_outputs(outputs, out_dir, base_name)
