from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import mimetypes
import os
import re
import sys
import time
import base64
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


DEFAULT_VIDEO_MODEL = "veo-3.1-fast-generate-preview"
DEFAULT_IMAGE_MODEL = "gemini-3-pro-image-preview"
DEFAULT_IMAGE_TEMPERATURE = 0.7
BLOCK_SEPARATOR = re.compile(r"(?m)^\s*---+\s*$")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def slugify(text: str, *, max_len: int = 48) -> str:
    value = text.strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9_-]+", "", value)
    value = re.sub(r"-{2,}", "-", value)
    value = value.strip("-_")
    if not value:
        return "untitled"
    return value[:max_len]


def prompt_stem(prompt: str) -> str:
    words = [word for word in re.split(r"\s+", prompt.strip()) if word]
    if not words:
        return "untitled"
    return slugify(" ".join(words[:6]))


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        pass


def init_client() -> tuple[Any, Any]:
    load_dotenv_if_available()
    try:
        from google import genai  # type: ignore
        from google.genai import types as gtypes  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "google-genai is not installed. Run `pip install -e .` in this repo."
        ) from exc

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    return genai.Client(api_key=api_key), gtypes


def require_pillow() -> Any:
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Pillow is not installed. Run `pip install -e .` in this repo.") from exc
    return Image


def load_input_image(path: Path, *, image_module: Any) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {path}")
    mime_type, _ = mimetypes.guess_type(str(path))
    if not (mime_type and mime_type.startswith("image/")):
        raise RuntimeError(f"Expected image/* input, got {mime_type or 'unknown'}: {path}")
    img = image_module.open(path)
    return img.convert("RGB")


def decode_inline_image(part: Any, *, image_module: Any) -> Optional[Any]:
    inline = getattr(part, "inline_data", None)
    if inline is None:
        return None
    mime_type = getattr(inline, "mime_type", None)
    if not (isinstance(mime_type, str) and mime_type.startswith("image/")):
        return None
    data = getattr(inline, "data", None)
    raw: Optional[bytes]
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    elif isinstance(data, str):
        try:
            raw = base64.b64decode(data)
        except Exception:
            raw = None
    else:
        raw = None
    if not raw:
        return None
    try:
        img = image_module.open(io.BytesIO(raw))
        return img.convert("RGB")
    except Exception:
        return None


def image_size_dict(img: Any) -> dict[str, int]:
    try:
        width, height = img.size  # type: ignore[attr-defined]
        return {"width": int(width), "height": int(height)}
    except Exception:
        return {"width": 0, "height": 0}


def build_image_config(*, aspect_ratio: Optional[str], image_size: Optional[str], gtypes: Any) -> Any:
    if not aspect_ratio and not image_size:
        return None
    if not hasattr(gtypes, "ImageConfig"):
        raise RuntimeError(
            "Installed google-genai does not expose types.ImageConfig. "
            "Upgrade the package before using aspect_ratio or image_size."
        )
    return gtypes.ImageConfig(  # type: ignore[call-arg]
        aspect_ratio=aspect_ratio or None,
        image_size=image_size or None,
    )


def coerce_scalar(text: str) -> Any:
    value = text.strip()
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except Exception:
            return value
    if (value.startswith("{") and value.endswith("}")) or (
        value.startswith("[") and value.endswith("]")
    ):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def normalize_job_dict(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(raw)
    config = dict(normalized.get("config") or {})
    for key in list(normalized.keys()):
        if key.startswith("config."):
            config[key.split(".", 1)[1]] = normalized.pop(key)
    images = normalized.get("images")
    if isinstance(images, str):
        normalized["images"] = [part.strip() for part in images.split(",") if part.strip()]
    if config:
        normalized["config"] = config
    return normalized


def parse_block_header(block: str) -> tuple[dict[str, Any], str]:
    if "\n\n" not in block:
        return {}, block.strip()

    head, body = block.split("\n\n", 1)
    header: dict[str, Any] = {}
    for line in head.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if ":" not in stripped:
            return {}, block.strip()
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
            return {}, block.strip()
        header[key] = coerce_scalar(value)
    return normalize_job_dict(header), body.strip()


def looks_like_header_block(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    for line in lines:
        if ":" not in line:
            return False
        key, _value = line.split(":", 1)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", key.strip()):
            return False
    return True


def parse_txt_batch(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    blocks: list[str]
    if BLOCK_SEPARATOR.search(text):
        blocks = [chunk.strip() for chunk in BLOCK_SEPARATOR.split(text) if chunk.strip()]
    elif "\n\n" in text:
        paragraph_blocks = [chunk.strip() for chunk in re.split(r"(?:\r?\n){2,}", text) if chunk.strip()]
        if paragraph_blocks and looks_like_header_block(paragraph_blocks[0]):
            blocks = ["\n\n".join(paragraph_blocks)]
        else:
            blocks = paragraph_blocks
    else:
        blocks = [line.strip() for line in text.splitlines() if line.strip()]

    jobs: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        metadata, prompt = parse_block_header(block)
        if not prompt:
            continue
        job = {"prompt": prompt, "source_index": index, "source_format": "txt"}
        job.update(metadata)
        jobs.append(job)
    return jobs


def require_yaml() -> Any:
    if yaml is None:
        raise RuntimeError("PyYAML is not installed. Run `pip install -e .` in this repo.")
    return yaml


def parse_yaml_batch(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    yaml_mod = require_yaml()
    payload = yaml_mod.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("YAML batch must be a mapping with top-level defaults/jobs.")

    defaults = normalize_job_dict(dict(payload.get("defaults") or {}))
    jobs_raw = payload.get("jobs")
    if not isinstance(jobs_raw, list) or not jobs_raw:
        raise RuntimeError("YAML batch must contain a non-empty jobs list.")

    jobs: list[dict[str, Any]] = []
    for index, entry in enumerate(jobs_raw, start=1):
        if not isinstance(entry, dict):
            raise RuntimeError(f"jobs[{index}] must be a mapping.")
        job = {"source_index": index, "source_format": "yaml"}
        job.update(normalize_job_dict(dict(entry)))
        jobs.append(job)
    return defaults, jobs


def load_prompt_from_file(base_dir: Path, path_str: str) -> str:
    path = resolve_input_path(base_dir, path_str)
    return path.read_text(encoding="utf-8").strip()


def resolve_input_path(base_dir: Path, path_str: str) -> Path:
    candidate = Path(path_str).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_dir / candidate).resolve()


def resolve_output_root(path_str: str) -> Path:
    candidate = Path(path_str).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (PROJECT_ROOT / candidate).resolve()


def batch_defaults_and_jobs(batch_path: Path, fmt: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if fmt == "auto":
        suffix = batch_path.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            fmt = "yaml"
        else:
            fmt = "txt"

    if fmt == "yaml":
        return parse_yaml_batch(batch_path)
    if fmt == "txt":
        return {}, parse_txt_batch(batch_path)
    raise RuntimeError(f"Unsupported format: {fmt}")


def resolved_job(
    *,
    batch_path: Path,
    defaults: dict[str, Any],
    job: dict[str, Any],
    cli_overrides: dict[str, Any],
) -> dict[str, Any]:
    base_dir = batch_path.parent.resolve()
    merged: dict[str, Any] = dict(defaults)
    merged.update(job)
    config = dict(defaults.get("config") or {})
    config.update(job.get("config") or {})
    merged["config"] = config

    for key, value in cli_overrides.items():
        if value is not None:
            merged[key] = value

    prompt = merged.get("prompt")
    prompt_file = merged.get("prompt_file")
    if prompt_file:
        prompt = load_prompt_from_file(base_dir, str(prompt_file))
    if not isinstance(prompt, str) or not prompt.strip():
        raise RuntimeError(f"Job {job.get('source_index')} has no prompt.")

    title = str(merged.get("title") or prompt_stem(prompt))
    mode = str(merged.get("mode") or "video").strip().lower()
    if mode not in {"video", "image"}:
        raise RuntimeError(f"Unsupported mode for job {job.get('source_index')}: {mode}")
    if mode == "image":
        default_model = os.getenv("GEMINI_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL
        default_temperature = DEFAULT_IMAGE_TEMPERATURE
        default_num_outputs = 1
        default_system_prompt = ""
    else:
        default_model = os.getenv("GEMINI_VIDEO_MODEL") or DEFAULT_VIDEO_MODEL
        default_temperature = None
        default_num_outputs = None
        default_system_prompt = None
    model = str(merged.get("model") or default_model)
    out_root = resolve_output_root(str(merged.get("out_root") or "out"))

    resolved = {
        "mode": mode,
        "title": title,
        "prompt": prompt.strip(),
        "model": model,
        "duration_seconds": merged.get("duration_seconds"),
        "aspect_ratio": merged.get("aspect_ratio"),
        "enhance_prompt": merged.get("enhance_prompt"),
        "number_of_videos": merged.get("number_of_videos"),
        "image": merged.get("image"),
        "images": merged.get("images"),
        "reference_images": merged.get("reference_images"),
        "video": merged.get("video"),
        "video_uri": merged.get("video_uri"),
        "num_outputs": merged.get("num_outputs", default_num_outputs),
        "temperature": merged.get("temperature", default_temperature),
        "system_prompt": merged.get("system_prompt", default_system_prompt),
        "image_size": merged.get("image_size"),
        "config": dict(merged.get("config") or {}),
        "out_root": out_root,
        "source_index": merged.get("source_index"),
        "source_format": merged.get("source_format"),
        "batch_file": str(batch_path.resolve()),
    }
    return resolved


def build_job_hash(job: dict[str, Any]) -> str:
    payload = {
        "mode": job["mode"],
        "prompt": job["prompt"],
        "model": job["model"],
        "duration_seconds": job.get("duration_seconds"),
        "aspect_ratio": job.get("aspect_ratio"),
        "enhance_prompt": job.get("enhance_prompt"),
        "number_of_videos": job.get("number_of_videos"),
        "image": job.get("image"),
        "images": job.get("images"),
        "reference_images": job.get("reference_images"),
        "video": job.get("video"),
        "video_uri": job.get("video_uri"),
        "num_outputs": job.get("num_outputs"),
        "temperature": job.get("temperature"),
        "system_prompt": job.get("system_prompt"),
        "image_size": job.get("image_size"),
        "config": job.get("config") or {},
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


def summarize_job(index: int, job: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": index,
        "mode": job["mode"],
        "title": job["title"],
        "model": job["model"],
        "duration_seconds": job.get("duration_seconds"),
        "aspect_ratio": job.get("aspect_ratio"),
        "number_of_videos": job.get("number_of_videos") or 1,
        "image": job.get("image"),
        "images": job.get("images"),
        "reference_images": job.get("reference_images"),
        "video": job.get("video"),
        "video_uri": job.get("video_uri"),
        "num_outputs": job.get("num_outputs") or 1,
        "temperature": job.get("temperature"),
        "system_prompt": job.get("system_prompt"),
        "image_size": job.get("image_size"),
        "config": job.get("config") or {},
        "prompt_preview": job["prompt"][:120],
        "out_root": str(job["out_root"]),
    }


def build_video_config(job: dict[str, Any], gtypes: Any) -> Any:
    config_kwargs = dict(job.get("config") or {})
    for key in ("duration_seconds", "aspect_ratio", "enhance_prompt", "number_of_videos"):
        value = job.get(key)
        if value is not None:
            config_kwargs[key] = value
    return gtypes.GenerateVideosConfig(**config_kwargs)


def resolve_reference_images(job: dict[str, Any], *, base_dir: Path, gtypes: Any) -> list[Any]:
    references: list[Any] = []

    images = job.get("images") or []
    if isinstance(images, list):
        for image_path in images:
            path = resolve_input_path(base_dir, str(image_path))
            references.append(
                gtypes.VideoGenerationReferenceImage(
                    image=gtypes.Image.from_file(location=str(path)),
                    reference_type="asset",
                )
            )

    raw_reference_images = job.get("reference_images") or []
    if isinstance(raw_reference_images, list):
        for entry in raw_reference_images:
            if not isinstance(entry, dict):
                raise RuntimeError("reference_images entries must be mappings.")
            image_value = entry.get("image")
            if not image_value:
                raise RuntimeError("reference_images entries require an image field.")
            path = resolve_input_path(base_dir, str(image_value))
            references.append(
                gtypes.VideoGenerationReferenceImage(
                    image=gtypes.Image.from_file(location=str(path)),
                    reference_type=entry.get("reference_type") or "asset",
                )
            )

    return references


def resolve_image_inputs(job: dict[str, Any], *, base_dir: Path) -> list[Path]:
    resolved: list[Path] = []
    if job.get("image"):
        resolved.append(resolve_input_path(base_dir, str(job["image"])))
    images = job.get("images") or []
    if isinstance(images, list):
        for image_path in images:
            resolved.append(resolve_input_path(base_dir, str(image_path)))
    seen: set[str] = set()
    unique: list[Path] = []
    for path in resolved:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def save_generated_videos(
    *,
    client: Any,
    generated_videos: list[Any],
    job_dir: Path,
    title_slug: str,
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for index, generated in enumerate(generated_videos, start=1):
        video = generated.video
        client.files.download(file=video)
        output_path = (job_dir / f"{title_slug}_{index:02d}.mp4").resolve()
        video.save(str(output_path))
        outputs.append(
            {
                "index": index,
                "path": str(output_path),
                "mime_type": getattr(video, "mime_type", None),
                "uri": getattr(video, "uri", None),
            }
        )
    return outputs


def save_generated_images(
    *,
    generated_images: list[Any],
    job_dir: Path,
    title_slug: str,
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for index, image in enumerate(generated_images, start=1):
        output_path = (job_dir / f"{title_slug}_{index:02d}.png").resolve()
        image.save(str(output_path), format="PNG")
        outputs.append(
            {
                "index": index,
                "path": str(output_path),
                **image_size_dict(image),
            }
        )
    return outputs


def generate_job(
    *,
    client: Any,
    gtypes: Any,
    batch_path: Path,
    job: dict[str, Any],
    run_day_dir: Path,
    poll_seconds: int,
) -> dict[str, Any]:
    title_slug = slugify(job["title"]) or f"job-{job['source_index']}"
    job_hash = build_job_hash(job)
    model_slug = slugify(job["model"], max_len=80)
    job_dir = ensure_dir(
        run_day_dir / model_slug / f"{int(job['source_index']):02d}_{title_slug}_{job_hash}"
    )

    request_kwargs: dict[str, Any] = {
        "model": job["model"],
        "prompt": job["prompt"],
    }

    base_dir = batch_path.parent.resolve()
    reference_images = resolve_reference_images(job, base_dir=base_dir, gtypes=gtypes)
    config = dict(job.get("config") or {})
    if reference_images:
        config["reference_images"] = reference_images
    request_kwargs["config"] = build_video_config({**job, "config": config}, gtypes)

    if job.get("image"):
        image_path = resolve_input_path(base_dir, str(job["image"]))
        request_kwargs["image"] = gtypes.Image.from_file(location=str(image_path))
    if job.get("video") and job.get("video_uri"):
        raise RuntimeError("Use either video or video_uri, not both.")
    if job.get("video"):
        video_path = resolve_input_path(base_dir, str(job["video"]))
        request_kwargs["video"] = gtypes.Video.from_file(location=str(video_path))
    elif job.get("video_uri"):
        request_kwargs["video"] = gtypes.Video(uri=str(job["video_uri"]))

    started_at = now_iso()
    operation = client.models.generate_videos(**request_kwargs)
    operation_name = getattr(operation, "name", None)

    while not operation.done:
        print(
            f"[{job['source_index']}] waiting {poll_seconds}s for {job['title']} "
            f"({job['model']})"
        )
        time.sleep(poll_seconds)
        operation = client.operations.get(operation)

    response = getattr(operation, "response", None)
    generated_videos = list(getattr(response, "generated_videos", None) or [])
    if not generated_videos:
        raise RuntimeError("No generated videos returned.")

    outputs = save_generated_videos(
        client=client,
        generated_videos=generated_videos,
        job_dir=job_dir,
        title_slug=title_slug,
    )
    result = {
        "status": "ok",
        "created_at": now_iso(),
        "started_at": started_at,
        "batch_file": str(batch_path.resolve()),
        "source_index": job["source_index"],
        "title": job["title"],
        "model": job["model"],
        "operation_name": operation_name,
        "prompt": job["prompt"],
        "resolved_params": {
            "duration_seconds": job.get("duration_seconds"),
            "aspect_ratio": job.get("aspect_ratio"),
            "enhance_prompt": job.get("enhance_prompt"),
            "number_of_videos": job.get("number_of_videos"),
            "image": job.get("image"),
            "images": job.get("images"),
            "reference_images": job.get("reference_images"),
            "video": job.get("video"),
            "video_uri": job.get("video_uri"),
            "config": job.get("config") or {},
        },
        "job_dir": str(job_dir),
        "outputs": outputs,
    }
    write_json(job_dir / "job.json", result)
    return result


def generate_image_job(
    *,
    client: Any,
    gtypes: Any,
    batch_path: Path,
    job: dict[str, Any],
    run_day_dir: Path,
) -> dict[str, Any]:
    image_module = require_pillow()
    title_slug = slugify(job["title"]) or f"job-{job['source_index']}"
    job_hash = build_job_hash(job)
    model_slug = slugify(job["model"], max_len=80)
    job_dir = ensure_dir(
        run_day_dir / model_slug / f"{int(job['source_index']):02d}_{title_slug}_{job_hash}"
    )

    base_dir = batch_path.parent.resolve()
    image_inputs = resolve_image_inputs(job, base_dir=base_dir)
    contents: list[Any] = [job["prompt"]]
    for path in image_inputs:
        contents.append(load_input_image(path, image_module=image_module))

    cfg_kwargs: dict[str, Any] = {
        "response_modalities": ["IMAGE", "TEXT"],
        "system_instruction": job.get("system_prompt") or None,
        "temperature": job.get("temperature"),
    }
    image_cfg = build_image_config(
        aspect_ratio=job.get("aspect_ratio"),
        image_size=job.get("image_size"),
        gtypes=gtypes,
    )
    if image_cfg is not None:
        cfg_kwargs["image_config"] = image_cfg
    config = gtypes.GenerateContentConfig(  # type: ignore[call-arg]
        **{key: value for key, value in cfg_kwargs.items() if value is not None}
    )

    requested_outputs = int(job.get("num_outputs") or 1)
    if requested_outputs < 1 or requested_outputs > 4:
        raise RuntimeError("num_outputs must be between 1 and 4")

    generated_images: list[Any] = []
    texts: list[str] = []
    attempts = 0
    started_at = now_iso()
    while len(generated_images) < requested_outputs and attempts < requested_outputs:
        attempts += 1
        response = client.models.generate_content(
            model=job["model"],
            contents=contents,
            config=config,
        )
        response_candidates = getattr(response, "candidates", None) or []
        for response_candidate in response_candidates:
            content = getattr(response_candidate, "content", None) or response_candidate
            parts = getattr(content, "parts", None) or []
            for part in parts:
                if hasattr(part, "as_image") and callable(getattr(part, "as_image")):
                    try:
                        image_obj = part.as_image()  # type: ignore[call-arg]
                        if image_obj is not None:
                            generated_images.append(image_obj.convert("RGB"))
                            continue
                    except Exception:
                        pass
                inline_img = decode_inline_image(part, image_module=image_module)
                if inline_img is not None:
                    generated_images.append(inline_img)
                    continue
                text_value = getattr(part, "text", None)
                if text_value:
                    texts.append(text_value)

    generated_images = generated_images[:requested_outputs]
    if not generated_images:
        raise RuntimeError("No image parts returned from Gemini.")

    outputs = save_generated_images(
        generated_images=generated_images,
        job_dir=job_dir,
        title_slug=title_slug,
    )
    result = {
        "status": "ok",
        "created_at": now_iso(),
        "started_at": started_at,
        "batch_file": str(batch_path.resolve()),
        "source_index": job["source_index"],
        "mode": "image",
        "title": job["title"],
        "model": job["model"],
        "prompt": job["prompt"],
        "resolved_params": {
            "aspect_ratio": job.get("aspect_ratio"),
            "image_size": job.get("image_size"),
            "temperature": job.get("temperature"),
            "system_prompt": job.get("system_prompt"),
            "num_outputs": requested_outputs,
            "image": job.get("image"),
            "images": job.get("images"),
            "config": job.get("config") or {},
        },
        "input_count": len(image_inputs),
        "inputs": [str(path) for path in image_inputs],
        "attempts": attempts,
        "text": "\n".join(texts).strip() or None,
        "job_dir": str(job_dir),
        "outputs": outputs,
    }
    write_json(job_dir / "job.json", result)
    return result


def plan_payload(batch_path: Path, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "batch_file": str(batch_path.resolve()),
        "job_count": len(jobs),
        "jobs": [summarize_job(index, job) for index, job in enumerate(jobs, start=1)],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch Gemini image or video generation from text or YAML prompt files."
    )
    parser.add_argument(
        "batch",
        nargs="?",
        help="Path to a .txt, .yaml, or .yml batch file. Omit when using --prompt.",
    )
    parser.add_argument(
        "--prompt",
        help="Inline prompt text. Mutually exclusive with a batch file argument.",
    )
    parser.add_argument(
        "--image",
        dest="inline_image",
        help="Single input image for the inline prompt (relative paths resolve against cwd).",
    )
    parser.add_argument(
        "--images",
        dest="inline_images",
        help="Comma-separated input image paths for the inline prompt.",
    )
    parser.add_argument(
        "--title",
        dest="inline_title",
        help="Optional title for the inline prompt (defaults to first words of the prompt).",
    )
    parser.add_argument(
        "--mode",
        choices=["image", "video"],
        help="Override mode for all jobs. Default: use job/defaults or fallback to video.",
    )
    parser.add_argument(
        "--format",
        choices=["auto", "txt", "yaml"],
        default="auto",
        help="Force the input format. Default: infer from extension.",
    )
    parser.add_argument("--model", help="Override the model code for all jobs.")
    parser.add_argument("--duration-seconds", type=int, help="Override duration_seconds.")
    parser.add_argument("--aspect-ratio", help='Override aspect_ratio, e.g. "16:9" or "9:16".')
    parser.add_argument(
        "--enhance-prompt",
        dest="enhance_prompt",
        action="store_true",
        help="Enable prompt enhancement for all jobs.",
    )
    parser.add_argument(
        "--no-enhance-prompt",
        dest="enhance_prompt",
        action="store_false",
        help="Disable prompt enhancement for all jobs.",
    )
    parser.set_defaults(enhance_prompt=None)
    parser.add_argument("--number-of-videos", type=int, help="Override number_of_videos.")
    parser.add_argument("--num-outputs", type=int, help="Override num_outputs for image generation.")
    parser.add_argument("--temperature", type=float, help="Override image generation temperature.")
    parser.add_argument("--system-prompt", help="Override system_prompt for image generation.")
    parser.add_argument("--image-size", help="Override image_size for image generation.")
    parser.add_argument("--out-root", help="Override the output root directory.")
    parser.add_argument("--poll-seconds", type=int, default=10, help="Polling interval.")
    parser.add_argument("--limit", type=int, help="Only run the first N jobs.")
    parser.add_argument("--plan", action="store_true", help="Print the resolved plan only.")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on the first failed generation instead of continuing.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    if args.prompt and args.batch:
        print("Specify either a batch file or --prompt, not both.", file=sys.stderr)
        return 2
    if not args.prompt and not args.batch:
        print("Provide a batch file path or --prompt.", file=sys.stderr)
        return 2

    if args.prompt:
        batch_path = (Path.cwd() / "<inline>").resolve()
        inline_job: dict[str, Any] = {
            "prompt": args.prompt,
            "source_index": 1,
            "source_format": "inline",
        }
        if args.inline_title:
            inline_job["title"] = args.inline_title
        if args.inline_image:
            inline_job["image"] = args.inline_image
        if args.inline_images:
            inline_job["images"] = [
                part.strip() for part in args.inline_images.split(",") if part.strip()
            ]
        defaults, jobs_raw = {}, [inline_job]
    else:
        batch_path = Path(args.batch).expanduser().resolve()
        if not batch_path.is_file():
            print(f"Batch file not found: {batch_path}", file=sys.stderr)
            return 2
        defaults, jobs_raw = batch_defaults_and_jobs(batch_path, args.format)
    cli_overrides = {
        "mode": args.mode,
        "model": args.model,
        "duration_seconds": args.duration_seconds,
        "aspect_ratio": args.aspect_ratio,
        "enhance_prompt": args.enhance_prompt,
        "number_of_videos": args.number_of_videos,
        "num_outputs": args.num_outputs,
        "temperature": args.temperature,
        "system_prompt": args.system_prompt,
        "image_size": args.image_size,
        "out_root": args.out_root,
    }
    jobs = [
        resolved_job(
            batch_path=batch_path,
            defaults=defaults,
            job=job,
            cli_overrides=cli_overrides,
        )
        for job in jobs_raw
    ]
    if args.limit is not None:
        jobs = jobs[: args.limit]

    if not jobs:
        print("No jobs found after parsing.", file=sys.stderr)
        return 2

    plan = plan_payload(batch_path, jobs)
    if args.plan:
        print(json.dumps(plan, indent=2))
        return 0

    out_root = ensure_dir(Path(str(jobs[0]["out_root"])).resolve())
    day_dir = ensure_dir(out_root / dt.date.today().isoformat())
    manifest_path = day_dir / f"run-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

    client, gtypes = init_client()
    results: list[dict[str, Any]] = []
    failures = 0

    for job in jobs:
        print(
            f"[{job['source_index']}] generating {job['title']} "
            f"({job['mode']}) with {job['model']}"
        )
        try:
            if job["mode"] == "image":
                result = generate_image_job(
                    client=client,
                    gtypes=gtypes,
                    batch_path=batch_path,
                    job=job,
                    run_day_dir=day_dir,
                )
            else:
                result = generate_job(
                    client=client,
                    gtypes=gtypes,
                    batch_path=batch_path,
                    job=job,
                    run_day_dir=day_dir,
                    poll_seconds=args.poll_seconds,
                )
            print(f"[{job['source_index']}] saved {len(result['outputs'])} output(s) to {result['job_dir']}")
            results.append(result)
        except Exception as exc:
            failures += 1
            failure = {
                "status": "error",
                "created_at": now_iso(),
                "batch_file": str(batch_path),
                "source_index": job["source_index"],
                "title": job["title"],
                "model": job["model"],
                "prompt": job["prompt"],
                "error": str(exc),
            }
            results.append(failure)
            print(f"[{job['source_index']}] error: {exc}", file=sys.stderr)
            if args.fail_fast:
                break

    manifest = {
        "created_at": now_iso(),
        "batch_file": str(batch_path),
        "job_count": len(jobs),
        "failure_count": failures,
        "results": results,
    }
    write_json(manifest_path, manifest)
    print(f"Wrote manifest: {manifest_path}")
    return 1 if failures else 0
