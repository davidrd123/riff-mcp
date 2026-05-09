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


def upload_and_poll_video(
    client: Any,
    path: str,
    *,
    timeout_s: int = 300,
    poll_interval_s: float = 2.0,
) -> Any:
    """Upload a video to Gemini's Files API and poll until ``state == 'ACTIVE'``.

    Returns the file object (with ``.uri`` and ``.mime_type``) ready to be
    passed in a ``contents`` list as ``gtypes.FileData``. Caller is responsible
    for ``cleanup_uploaded(client, file)`` in a ``finally`` block — uploaded
    files persist on Gemini's side for ~48h otherwise.

    Pattern lifted from DMPOST31's ``nano_analyze_media`` helper.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise RuntimeError(f"VIDEO_NOT_FOUND: {p}")

    try:
        uploaded = client.files.upload(file=str(p))
    except Exception as exc:
        raise RuntimeError(f"VIDEO_UPLOAD_FAILED: {exc}") from exc

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
            raise RuntimeError(
                f"VIDEO_PROCESSING_FAILED: file {uploaded.name} ended in FAILED state"
            )
        if time.perf_counter() > deadline:
            # Best-effort cleanup before raising.
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
            raise RuntimeError(
                f"VIDEO_PROCESSING_TIMEOUT: file {uploaded.name} did not reach "
                f"ACTIVE state within {timeout_s}s (last state: {state_str!r})"
            )
        time.sleep(poll_interval_s)


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
    temperature: float = 0.3,
) -> Any:
    """Make a structured-output call to Gemini and return the parsed
    Pydantic instance.

    ``response_schema`` is a Pydantic model class. The Gemini client converts
    it to a schema for the model and validates the response shape. Newer
    google-genai versions expose ``response.parsed`` (a Pydantic instance);
    older versions require parsing ``response.text`` manually.
    """
    config = gtypes.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=response_schema,
    )
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
