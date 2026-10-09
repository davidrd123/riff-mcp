"""Gemini multimodal call dispatch for media-analysis-mcp.

Image-loading helpers, client init, the structured-output call wrapper, and
video upload+poll for the Files API.

Error codes raised here:
- GOOGLE_GENAI_NOT_INSTALLED — google-genai missing from venv
- PILLOW_NOT_INSTALLED — Pillow missing from venv
- API_KEY_MISSING — GEMINI_API_KEY env var not set
- IMAGE_NOT_FOUND — image path doesn't exist
- VIDEO_NOT_FOUND — video path doesn't exist
- VIDEO_UPLOAD_FAILED — Files API upload failed
- VIDEO_PROCESSING_TIMEOUT — file did not become ACTIVE within timeout
- VIDEO_PROCESSING_FAILED — file ended in FAILED state
- AUDIO_NOT_FOUND — audio path doesn't exist
- AUDIO_UPLOAD_FAILED — Files API audio upload failed
- AUDIO_PROCESSING_TIMEOUT — audio did not become ACTIVE within timeout
- AUDIO_PROCESSING_FAILED — audio ended in FAILED state
- NO_RESPONSE — Gemini returned no parseable response
"""
from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Optional


def _load_dotenv_if_available() -> None:
    """Best-effort .env load — fails silently if python-dotenv not installed."""
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        pass


def init_client() -> tuple[Any, Any]:
    """Return a configured google-genai client + the types module.

    Loads .env (if dotenv available), reads ``GEMINI_API_KEY``, instantiates
    ``genai.Client``. Raises coded errors on any precondition failure.
    """
    _load_dotenv_if_available()
    try:
        from google import genai  # type: ignore
        from google.genai import types as gtypes  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "GOOGLE_GENAI_NOT_INSTALLED: google-genai is not installed. "
            "Run `uv sync` in the project root."
        ) from exc

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "API_KEY_MISSING: GEMINI_API_KEY is not set. Add it to .env or "
            "export it in the environment that launches the MCP server."
        )
    return genai.Client(api_key=api_key), gtypes


def require_pillow() -> Any:
    """Return the Pillow ``Image`` module or raise PILLOW_NOT_INSTALLED."""
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "PILLOW_NOT_INSTALLED: Pillow is not installed. Run `uv sync`."
        ) from exc
    return Image


def load_image(path: str, *, image_module: Any) -> Any:
    """Load an image file as a Pillow Image, RGB-converted.

    Raises ``IMAGE_NOT_FOUND`` for missing files.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise RuntimeError(f"IMAGE_NOT_FOUND: {p}")
    img = image_module.open(p)
    return img.convert("RGB")


def _upload_and_poll_media(
    client: Any,
    path: str,
    *,
    media_kind: str,
    timeout_s: int = 300,
    poll_interval_s: float = 2.0,
) -> Any:
    """Upload media to Gemini's Files API and poll until ACTIVE.

    Returns the file object (with ``.uri`` and ``.mime_type``) ready to be
    passed in a ``contents`` list as ``gtypes.FileData``. ``media_kind`` is
    used only to preserve the public coded-error contract for video and audio.
    Caller is responsible for ``cleanup_uploaded(client, file)`` in a
    ``finally`` block — uploaded files persist on Gemini's side for ~48h
    otherwise.

    Pattern lifted from DMPOST31's ``nano_analyze_media`` helper.
    """
    kind = media_kind.upper()
    p = Path(path).expanduser()
    if not p.is_file():
        raise RuntimeError(f"{kind}_NOT_FOUND: {p}")

    try:
        uploaded = client.files.upload(file=str(p))
    except Exception as exc:
        raise RuntimeError(f"{kind}_UPLOAD_FAILED: {exc}") from exc

    deadline = time.perf_counter() + timeout_s
    while True:
        # Refresh the file status.
        try:
            current = client.files.get(name=uploaded.name)
        except Exception:
            current = uploaded

        state_obj = getattr(current, "state", None)
        # ``state`` may be an enum or a string depending on SDK version.
        state_str = (
            getattr(state_obj, "name", None)
            or (state_obj.value if hasattr(state_obj, "value") else str(state_obj))
            or ""
        )
        state_str = state_str.upper().split(".")[-1]

        if state_str == "ACTIVE":
            return current
        if state_str == "FAILED":
            # Best-effort cleanup so the FAILED file doesn't leak on Gemini's
            # side. The caller's finally block doesn't run for this raise:
            # we never returned the file object, so cleanup_uploaded(client,
            # uploaded) wouldn't be paired in the caller. Delete here.
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
            raise RuntimeError(
                f"{kind}_PROCESSING_FAILED: file {uploaded.name} ended in FAILED state"
            )
        if time.perf_counter() > deadline:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
            raise RuntimeError(
                f"{kind}_PROCESSING_TIMEOUT: file {uploaded.name} did not reach "
                f"ACTIVE state within {timeout_s}s (last state: {state_str!r})"
            )
        time.sleep(poll_interval_s)


def upload_and_poll_video(
    client: Any,
    path: str,
    *,
    timeout_s: int = 300,
    poll_interval_s: float = 2.0,
) -> Any:
    """Upload a video and poll until ACTIVE. Caller must clean it up."""
    return _upload_and_poll_media(
        client,
        path,
        media_kind="video",
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )


def upload_and_poll_audio(
    client: Any,
    path: str,
    *,
    timeout_s: int = 300,
    poll_interval_s: float = 2.0,
) -> Any:
    """Upload audio and poll until ACTIVE. Caller must clean it up.

    The returned Files API object's ``mime_type`` is authoritative. In
    particular, Gemini may identify an ``.m4a`` as ``audio/mp4a-latm``;
    callers must not relabel it as ``video/mp4`` or even a guessed
    ``audio/mp4`` when constructing ``FileData``.
    """
    return _upload_and_poll_media(
        client,
        path,
        media_kind="audio",
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )


def cleanup_uploaded(client: Any, file_obj: Any) -> None:
    """Delete a previously-uploaded Files API resource. Best-effort — never
    raises. Always pair an ``upload_and_poll_video`` with a ``cleanup_uploaded``
    in ``finally``."""
    try:
        client.files.delete(name=file_obj.name)
    except Exception:
        pass


def video_mime_type(path: str) -> str:
    """Best-effort mime-type guess for a video file. Falls back to
    ``video/mp4`` when ``mimetypes`` can't determine it."""
    mt, _ = mimetypes.guess_type(path)
    if mt and mt.startswith("video/"):
        return mt
    return "video/mp4"


def call_structured(
    *,
    client: Any,
    gtypes: Any,
    model: str,
    system_instruction: str,
    contents: list[Any],
    response_schema: Any,
    temperature: Optional[float] = None,
) -> Any:
    """Make a structured-output call to Gemini and return the parsed
    Pydantic instance.

    ``response_schema`` is a Pydantic model class. The Gemini client converts
    it to a schema for the model and validates the response shape. Newer
    google-genai versions expose ``response.parsed`` (a Pydantic instance);
    older versions require parsing ``response.text`` manually.
    """
    config_kwargs: dict[str, Any] = {
        "system_instruction": system_instruction,
        "response_mime_type": "application/json",
        "response_schema": response_schema,
    }
    if temperature is not None:
        config_kwargs["temperature"] = temperature
    config = gtypes.GenerateContentConfig(**config_kwargs)
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return parsed
    text: Optional[str] = getattr(response, "text", None)
    if text:
        return response_schema.model_validate_json(text)
    raise RuntimeError(
        "NO_RESPONSE: Gemini returned no usable response (no parsed object, "
        "no text)."
    )


def usage_summary(response: Any) -> Optional[dict[str, Any]]:
    """Return the token counts Gemini reported for one response, or None.

    Keeps the google-genai ``usage_metadata`` field names. Thinking tokens are
    billed at the output rate, so billable output is ``candidates_token_count``
    plus ``thoughts_token_count``. No prices are applied here.
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    summary: dict[str, Any] = {
        name: getattr(usage, name, None)
        for name in (
            "prompt_token_count",
            "candidates_token_count",
            "thoughts_token_count",
            "total_token_count",
            "cached_content_token_count",
        )
    }
    by_modality: dict[str, int] = {}
    for detail in getattr(usage, "prompt_tokens_details", None) or []:
        modality = getattr(detail, "modality", None)
        name = str(getattr(modality, "name", None) or modality).lower()
        by_modality[name] = by_modality.get(name, 0) + (
            getattr(detail, "token_count", None) or 0
        )
    if by_modality:
        summary["prompt_tokens_by_modality"] = by_modality
    return summary


def call_unstructured(
    *,
    client: Any,
    gtypes: Any,
    model: str,
    system_instruction: str,
    contents: list[Any],
    temperature: Optional[float] = None,
    thinking_level: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    usage_out: Optional[dict[str, Any]] = None,
) -> str:
    """Make a free-form (no response schema) call to Gemini and return the
    response text. Companion to ``call_structured`` for the analyze_* tools.

    Used when the caller has an ad-hoc question and doesn't want to commit to
    a fixed response taxonomy. The describe_* / score_* tools bind to a
    Pydantic schema for repeatability; analyze_* opts out for flexibility.

    Pass a dict as ``usage_out`` to receive the response's token counts (see
    ``usage_summary``). It is left untouched when Gemini reports none.
    """
    config_kwargs: dict[str, Any] = {
        "system_instruction": system_instruction,
    }
    if temperature is not None:
        config_kwargs["temperature"] = temperature
    if thinking_level is not None:
        config_kwargs["thinking_config"] = {
            "thinking_level": thinking_level,
        }
    if max_output_tokens is not None:
        config_kwargs["max_output_tokens"] = max_output_tokens
    config = gtypes.GenerateContentConfig(**config_kwargs)
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )
    if usage_out is not None:
        usage_out.update(usage_summary(response) or {})
    text: Optional[str] = getattr(response, "text", None)
    if text and text.strip():
        return text.strip()
    raise RuntimeError(
        "NO_RESPONSE: Gemini returned no usable text response."
    )


def validate_video_processing(processing: str, fps: Optional[float]) -> None:
    if processing not in {"static", "agentic"}:
        raise RuntimeError("INVALID_INPUT: processing must be static or agentic")
    if processing == "agentic" and fps is not None:
        raise RuntimeError("INVALID_INPUT: omit fps with agentic processing")


def call_agentic_video(
    *, client: Any, model: str, system_instruction: str, contents: list[Any],
    temperature: Optional[float], thinking_level: Optional[str],
    max_output_tokens: Optional[int],
) -> dict[str, Any]:
    """Translate the ordered video/reference context to Interactions; retain evidence."""
    import base64
    import io

    inputs = []
    for part in contents:
        if isinstance(part, str):
            inputs.append({"type": "text", "text": part})
        elif getattr(part, "file_data", None) is not None:
            inputs.append({"type": "video", "uri": part.file_data.file_uri,
                           "mime_type": part.file_data.mime_type, "processing": "agentic"})
        else:
            # Builders supply loaded RGB Pillow images for reference slots.
            buffer = io.BytesIO()
            part.save(buffer, format="PNG")
            inputs.append({"type": "image", "mime_type": "image/png",
                           "data": base64.b64encode(buffer.getvalue()).decode("ascii")})
    config = {k: v for k, v in {
        "temperature": temperature, "thinking_level": thinking_level,
        "max_output_tokens": max_output_tokens,
    }.items() if v is not None}
    response = client.interactions.with_raw_response.create(
        model=model, input=inputs, system_instruction=system_instruction,
        generation_config=config, store=False,
    )
    # Keep wire steps: SDK typed parsing can discard new processing step types.
    payload = response.json()
    steps = payload.get("steps", [])
    answer = "\n".join(
        content["text"] for step in steps if step.get("type") == "model_output"
        for content in step.get("content", [])
        if content.get("type") == "text" and content.get("text")
    ).strip()
    if not answer:
        raise RuntimeError("NO_RESPONSE: Gemini returned no usable text response.")
    trace = [s for s in steps if s.get("type") in {"processing_call", "processing_result"}]
    calls = {s.get("id") for s in trace if s.get("type") == "processing_call"} - {None}
    verified = any(s.get("type") == "processing_result" and s.get("call_id") in calls
                   for s in trace)
    return {"answer": answer, "processing_trace": trace,
            "agentic_processing_verified": verified, "interaction": payload}
