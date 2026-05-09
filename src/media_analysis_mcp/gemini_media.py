"""Gemini multimodal call dispatch for media-analysis-mcp.

Image-loading helpers, client init, and the structured-output call wrapper.
Video upload+poll lands in a future commit (Step 6 — describe_video /
score_video).

Error codes raised here:
- GOOGLE_GENAI_NOT_INSTALLED — google-genai missing from venv
- PILLOW_NOT_INSTALLED — Pillow missing from venv
- API_KEY_MISSING — GEMINI_API_KEY env var not set
- IMAGE_NOT_FOUND — image path doesn't exist
- NO_RESPONSE — Gemini returned no parseable response
"""
from __future__ import annotations

import os
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
