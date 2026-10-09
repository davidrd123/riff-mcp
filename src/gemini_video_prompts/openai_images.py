"""Direct GPT Image API adapter. Imported only for explicit OpenAI jobs."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from .cli import (
    build_job_hash, ensure_dir, load_dotenv_if_available, now_iso,
    require_pillow, resolve_image_inputs, slugify, write_json,
)


BILLING_NOTICE = (
    "This request uses the OpenAI API and incurs charges separate from your ChatGPT/Codex "
    "subscription. An installed API key is not spending authorization. Ask the user to approve "
    "this request or a bounded batch before setting allow_api_billing=true "
    "(CLI: --allow-api-billing)."
)


def validate_job(job: dict[str, Any]) -> dict[str, Any]:
    """Validate before credentials, filesystem writes, or paid calls; return API params."""
    if job.get("mask") is not None:
        if not isinstance(job["mask"], str) or not job["mask"].strip():
            raise RuntimeError("INVALID_INPUT: mask must be a non-empty path")
        if not job.get("image") and not job.get("images"):
            raise RuntimeError("INVALID_INPUT: mask requires a reference image")
    model = job["model"]
    is_image_25 = model in {"gpt-image-2.5-sunburst", "gpt-image-2.5-flare"}
    if not is_image_25 and model != "gpt-image-2" and not re.fullmatch(r"gpt-image-2-\d{4}-\d{2}-\d{2}", model):
        raise RuntimeError("INVALID_INPUT: provider=openai supports gpt-image-2 (or a dated snapshot), gpt-image-2.5-sunburst, or gpt-image-2.5-flare")
    for key in ("aspect_ratio", "image_size", "temperature", "system_prompt"):
        if job.get(key) is not None and job.get(key) != "":
            raise RuntimeError(f"INVALID_INPUT: {key} is not supported by the OpenAI Images API; "
                               "use size for dimensions and include instructions in prompt")
    config = job.get("config") or {}
    for key, value in config.items():
        if key == "api" and value == "images":
            continue
        if key in {"thinking_level", "previous_interaction_id"} and value is None:
            continue
        if key == "store" and value is False:
            continue
        raise RuntimeError(f"INVALID_INPUT: config.{key} is not supported by the OpenAI Images API")
    count = job.get("num_outputs", 1)
    if type(count) is not int or not 1 <= count <= 4:
        raise RuntimeError("INVALID_INPUT: num_outputs must be an integer between 1 and 4")
    params = {"model": model, "prompt": job["prompt"], "n": count}
    quality_choices = {"auto", "low", "medium", "high"}
    if is_image_25:
        quality_choices.update({"xhigh", "max"})
    for key, default, choices in (
        ("quality", "auto", quality_choices),
        ("output_format", "png", {"png", "jpeg", "webp"}),
        ("background", "auto", {"auto", "opaque", "transparent"}),
    ):
        value = job.get(key) if job.get(key) is not None else default
        if not isinstance(value, str) or value not in choices:
            raise RuntimeError(f"INVALID_INPUT: {key} must be one of {', '.join(sorted(choices))}")
        params[key] = value
    size = job.get("size") if job.get("size") is not None else "auto"
    if size != "auto":
        match = re.fullmatch(r"([1-9]\d{0,3})x([1-9]\d{0,3})", str(size))
        if not match:
            raise RuntimeError("INVALID_INPUT: size must be auto or WIDTHxHEIGHT")
        width, height = map(int, match.groups())
        if (max(width, height) > 3840 or width % 16 or height % 16
                or max(width, height) > 3 * min(width, height)
                or not 655_360 <= width * height <= 8_294_400):
            raise RuntimeError("INVALID_INPUT: size requires edges divisible by 16 and <=3840, "
                               "aspect ratio <=3:1, and 655360..8294400 total pixels")
    params["size"] = size
    compression = job.get("output_compression")
    if compression is not None:
        if type(compression) is not int or not 0 <= compression <= 100:
            raise RuntimeError("INVALID_INPUT: output_compression must be an integer from 0 to 100")
        if params["output_format"] == "png":
            raise RuntimeError("INVALID_INPUT: output_compression requires jpeg or webp")
        params["output_compression"] = compression
    if params["background"] == "transparent" and params["output_format"] == "jpeg":
        raise RuntimeError("INVALID_INPUT: transparent backgrounds require png or webp")
    return params


def init_openai_client() -> Any:
    load_dotenv_if_available()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_AUTH_ERROR: OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OPENAI_SETUP_ERROR: Install OpenAI support with `uv sync --extra openai`") from exc
    # An ambiguous timeout must not silently submit another billable generation.
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=300.0, max_retries=0)


def validate_assets(job: dict[str, Any], batch_path: Path) -> tuple[list[Path], Path | None]:
    image_module = require_pillow()
    inputs = resolve_image_inputs(job, base_dir=batch_path.parent.resolve())
    if len(inputs) > 16:
        raise RuntimeError("INVALID_INPUT: OpenAI accepts at most 16 reference images")
    for path in inputs:
        if not path.is_file():
            raise RuntimeError(f"IMAGE_NOT_FOUND: {path}")
        if path.stat().st_size >= 50 * 1024 * 1024:
            raise RuntimeError(f"INVALID_INPUT: reference image must be smaller than 50 MB: {path}")
        with image_module.open(path) as img:
            if img.format not in {"PNG", "JPEG", "WEBP"}:
                raise RuntimeError(f"INVALID_INPUT: OpenAI reference must be PNG, JPEG, or WebP: {path}")
            img.verify()

    mask = None
    if job.get("mask") is not None:
        mask = Path(job["mask"]).expanduser()
        if not mask.is_absolute():
            mask = batch_path.parent / mask
        mask = mask.resolve()
        if not mask.is_file():
            raise RuntimeError(f"MASK_NOT_FOUND: {mask}")
        if mask.stat().st_size >= 4 * 1024 * 1024:
            raise RuntimeError("INVALID_INPUT: mask must be smaller than 4 MiB")
        with image_module.open(inputs[0]) as first, image_module.open(mask) as region:
            if first.format != "PNG" or region.format != "PNG":
                raise RuntimeError("INVALID_INPUT: masked edits require PNG source and mask")
            if region.size != first.size or "A" not in region.getbands():
                raise RuntimeError("INVALID_INPUT: mask needs alpha and matching source dimensions")
            if region.getchannel("A").getextrema()[0] != 0:
                raise RuntimeError("INVALID_INPUT: mask needs fully transparent editable pixels")
            region.load()
    return inputs, mask


def generate_openai_image_job(
    *, batch_path: Path, job: dict[str, Any], run_day_dir: Path, client: Any = None,
    allow_api_billing: bool = False,
) -> dict[str, Any]:
    params = validate_job(job)
    if allow_api_billing is not True:
        raise RuntimeError(f"API_BILLING_CONFIRMATION_REQUIRED: {BILLING_NOTICE}")
    image_module = require_pillow()
    inputs, mask = validate_assets(job, batch_path)

    title_slug = slugify(job["title"])
    job_dir = ensure_dir(run_day_dir / slugify(job["model"], max_len=80)
                         / f"{int(job['source_index']):02d}_{title_slug}_{build_job_hash(job)}")
    result: dict[str, Any] = {
        "status": "running", "started_at": now_iso(), "batch_file": str(batch_path.resolve()),
        "source_index": job["source_index"], "mode": "image", "title": job["title"],
        "provider": "openai", "access": "api_key", "model": job["model"],
        "allow_api_billing": True,
        "prompt": job["prompt"], "resolved_params": params,
        "input_count": len(inputs), "inputs": [str(path) for path in inputs],
        "input_sha256": [hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs],
        "api": "images.edits" if inputs else "images.generations",
        "attempts": 0, "interaction_ids": [], "text": None,
        "job_dir": str(job_dir), "outputs": [],
    }
    if mask is not None:
        result["mask"] = str(mask)
        result["mask_sha256"] = hashlib.sha256(mask.read_bytes()).hexdigest()
    write_json(job_dir / "job.json", result)
    started = time.monotonic()
    owned_client = client is None
    try:
        if owned_client:
            client = init_openai_client()
        with ExitStack() as stack:
            if inputs:
                files = [stack.enter_context(path.open("rb")) for path in inputs]
                result["attempts"] = 1
                mask_args = {"mask": stack.enter_context(mask.open("rb"))} if mask else {}
                response = client.images.edit(image=files, **mask_args, **params)
            else:
                result["attempts"] = 1
                response = client.images.generate(**params)
        result["request_id"] = getattr(response, "_request_id", None)
        usage = getattr(response, "usage", None)
        result["usage"] = usage.model_dump(mode="json") if usage is not None else None
        result["response_metadata"] = {
            key: getattr(response, key, None)
            for key in ("created", "size", "quality", "output_format", "background", "model")
        }
        data = response.data or []
        if not data:
            raise RuntimeError("NO_IMAGE_RETURNED: OpenAI returned no image data")
        for index, item in enumerate(data, start=1):
            if not item.b64_json:
                raise RuntimeError("NO_IMAGE_RETURNED: OpenAI returned an image without base64 data")
            raw = base64.b64decode(item.b64_json, validate=True)
            # Save original provider bytes, preserving alpha and embedded provenance.
            with image_module.open(io.BytesIO(raw)) as img:
                width, height = img.size
                actual_format = (img.format or "").lower()
                img.verify()
            if actual_format not in {"png", "jpeg", "webp"}:
                raise RuntimeError("INVALID_IMAGE_RETURNED: unsupported image format")
            path = job_dir / f"{title_slug}_{index:02d}.{actual_format}"
            path.write_bytes(raw)
            result["outputs"].append({
                "index": index, "path": str(path), "width": width, "height": height,
                "mime_type": f"image/{actual_format}", "sha256": hashlib.sha256(raw).hexdigest(),
                "revised_prompt": getattr(item, "revised_prompt", None),
            })
        if len(data) != params["n"]:
            raise RuntimeError("OUTPUT_COUNT_MISMATCH: OpenAI returned a different number of images")
        result.update(status="ok", created_at=now_iso())
    except Exception as exc:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        # Keep structured provider diagnostics, without serializing request headers/body.
        result.update(status="failed", created_at=now_iso(), error={
            "type": type(exc).__name__, "code": getattr(exc, "code", None),
            "status_code": getattr(exc, "status_code", None),
            "request_id": getattr(exc, "request_id", None),
        })
        write_json(job_dir / "job.json", result)
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(
            f"OPENAI_IMAGE_ERROR: {type(exc).__name__}; code={getattr(exc, 'code', None)}; "
            f"request_id={getattr(exc, 'request_id', None)}; record={job_dir / 'job.json'}"
        ) from exc
    finally:
        if owned_client and client is not None:
            client.close()
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    write_json(job_dir / "job.json", result)
    return result
