"""FastMCP server exposing gemini-video-prompts as MCP tools.

Tools:
- generate_image  — Gemini image generation (wraps generate_image_job)
- generate_video  — Seedance 2.0 via Replicate (uses seedance.py adapter)
- start_video_job, get_video_job, cancel_video_job
                  — local async control surface for Seedance predictions

See MCP_DESIGN.md at the repo root for the full architecture.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from gemini_video_prompts.cli import (
    build_job_hash,
    build_resolved_image_job,
    ensure_dir,
    generate_image_job,
    init_client,
    now_iso,
    prompt_stem,
    resolve_output_root,
    slugify,
    summarize_job,
    write_json,
)

from . import seedance
from .seedance import SEEDANCE_MODEL_DEFAULT


mcp = FastMCP("gemini-prompts-mcp")

# Match a leading "CODE:" prefix in error messages so we can preserve already-
# coded errors at the MCP boundary (e.g. REPLICATE_TIMEOUT from the watchdog)
# instead of double-wrapping them as REPLICATE_ERROR: REPLICATE_TIMEOUT: ...
_CODED_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9_]+:")
_TERMINAL_PREDICTION_STATUSES = {"succeeded", "failed", "canceled"}


def _project_job_dir(job: dict[str, Any]) -> str:
    """Compute the job_dir path that ``generate_image_job`` would create.

    Used by ``dry_run`` to surface the projected output location without
    actually creating any directories or firing the model.
    """
    today = dt.date.today().isoformat()
    title_slug = slugify(job["title"]) or f"job-{job['source_index']}"
    job_hash = build_job_hash(job)
    model_slug = slugify(job["model"], max_len=80)
    return str(
        Path(job["out_root"])
        / today
        / model_slug
        / f"{int(job['source_index']):02d}_{title_slug}_{job_hash}"
    )


def _project_video_job_dir(
    *, model: str, title: str, params: dict[str, Any], out_root: Path
) -> str:
    """Compute the job_dir path that ``run_seedance_job`` would create.

    Mirrors ``_project_job_dir`` for Seedance video — same layout convention
    (``<out_root>/<today>/<model_slug>/01_<title_slug>_<hash>``) so vault
    logging stays uniform across image and video runs.
    """
    today = dt.date.today().isoformat()
    title_slug = slugify(title) or "job-1"
    model_slug = slugify(model, max_len=80)
    job_hash = seedance.build_seedance_job_hash(params)
    return str(out_root / today / model_slug / f"01_{title_slug}_{job_hash}")


def _video_job_dir(
    *, model: str, title: str, params: dict[str, Any], out_root: Path
) -> Path:
    return Path(
        _project_video_job_dir(
            model=model,
            title=title,
            params=params,
            out_root=out_root,
        )
    )


def _jobs_root(out_root: Path) -> Path:
    return out_root / "jobs"


def _job_status_path(out_root: Path, job_id: str) -> Path:
    return _jobs_root(out_root) / job_id / "status.json"


def _job_request_path(out_root: Path, job_id: str) -> Path:
    return _jobs_root(out_root) / job_id / "request.json"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_status(out_root: Path, job_id: str, status: dict[str, Any]) -> None:
    status["updated_at"] = now_iso()
    write_json(_job_status_path(out_root, job_id), status)


def _build_video_context(
    *,
    prompt: str,
    model: str,
    image: Optional[str],
    last_frame_image: Optional[str],
    reference_images: Optional[list[str]],
    reference_videos: Optional[list[str]],
    reference_audios: Optional[list[str]],
    duration: int,
    resolution: str,
    aspect_ratio: str,
    generate_audio: bool,
    seed: Optional[int],
    title: Optional[str],
    out_root: Optional[str],
) -> dict[str, Any]:
    api_params = seedance.build_seedance_video_params(
        prompt=prompt,
        image=image,
        last_frame_image=last_frame_image,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
        duration=duration,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        generate_audio=generate_audio,
        seed=seed,
    )
    references = seedance.build_references_map(
        image=image,
        last_frame_image=last_frame_image,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
    )
    mode = seedance.derive_mode(
        image=image,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
    )
    validation_warnings: list[str] = []
    validation_warnings.extend(seedance.check_prompt_references(prompt, references))
    validation_warnings.extend(seedance.check_total_reference_cap(references))

    title_str = title if title else prompt_stem(prompt)
    out_root_path = resolve_output_root(out_root) if out_root else resolve_output_root("out")
    resolved_params = {
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "generate_audio": generate_audio,
        "seed": seed,
    }
    job_dir = _video_job_dir(
        model=model,
        title=title_str,
        params=api_params,
        out_root=out_root_path,
    )
    return {
        "api_params": api_params,
        "references": references,
        "mode": mode,
        "validation_warnings": validation_warnings,
        "title": title_str,
        "title_slug": slugify(title_str) or "job-1",
        "out_root": out_root_path,
        "resolved_params": resolved_params,
        "job_dir": job_dir,
    }


def _check_video_files(api_params: dict[str, Any]) -> None:
    for path_key in ("image", "last_frame_image"):
        path_val = api_params.get(path_key)
        if path_val and not Path(path_val).is_file():
            raise RuntimeError(f"FILE_NOT_FOUND: {path_val}")
    for path_key in ("reference_images", "reference_videos", "reference_audios"):
        for path_val in api_params.get(path_key, []) or []:
            if not Path(path_val).is_file():
                raise RuntimeError(f"FILE_NOT_FOUND: {path_val}")


def _prediction_status(prediction: dict[str, Any]) -> str:
    return str(prediction.get("status") or "unknown")


def _prediction_error(prediction: dict[str, Any]) -> Optional[dict[str, Any]]:
    error = prediction.get("error")
    if not error:
        return None
    if isinstance(error, dict):
        return error
    return {"message": str(error), "type": "ReplicatePredictionError"}


def _outputs_from_prediction(prediction: dict[str, Any]) -> list[Any]:
    output = prediction.get("output") or []
    if isinstance(output, list):
        return output
    return [output]


def _prediction_metrics(prediction: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = dict(prediction.get("metrics") or {})
    predict_time = metrics.get("predict_time")
    if predict_time is not None and "predict_time_s" not in metrics:
        metrics["predict_time_s"] = predict_time
    metrics["download_time_s"] = (
        outputs[0].get("_metrics", {}).get("download_time_s") if outputs else None
    )
    metrics["cold_start"] = bool((metrics.get("predict_time_s") or 0.0) >= 40.0)
    return metrics


def _result_from_prediction(
    *,
    status: dict[str, Any],
    prediction: dict[str, Any],
) -> dict[str, Any]:
    job_dir = Path(status["job_dir"])
    title_slug = slugify(status["title"]) or "job-1"
    raw_outputs = _outputs_from_prediction(prediction)
    outputs = seedance.download_prediction_outputs(
        outputs=raw_outputs,
        out_dir=job_dir,
        base_name=title_slug,
    )
    metrics = _prediction_metrics(prediction, outputs)

    enriched_outputs: list[dict[str, Any]] = []
    for idx, out in enumerate(outputs, start=1):
        out_clean = dict(out)
        out_clean.pop("_metrics", None)
        out_clean["index"] = idx
        out_clean["media_info"] = seedance.probe_media_info(out_clean["path"])
        enriched_outputs.append(out_clean)

    return {
        "status": "ok",
        "created_at": now_iso(),
        "started_at": prediction.get("started_at") or status.get("started_at"),
        "completed_at": prediction.get("completed_at"),
        "title": status["title"],
        "model": status["model"],
        "model_version": prediction.get("version") or "@latest",
        "prediction_id": status["prediction_id"],
        "mode": status["mode"],
        "prompt": status["prompt"],
        "resolved_params": status["resolved_params"],
        "references": status["references"],
        "validation_warnings": status["validation_warnings"],
        "job_dir": status["job_dir"],
        "outputs": enriched_outputs,
        "metrics": metrics,
    }


def _merge_prediction_status(
    *,
    status: dict[str, Any],
    prediction: dict[str, Any],
    out_root: Path,
) -> dict[str, Any]:
    provider_status = _prediction_status(prediction)
    status["status"] = provider_status
    status["provider_prediction"] = prediction
    status["error"] = _prediction_error(prediction)
    if prediction.get("started_at"):
        status["started_at"] = prediction["started_at"]
    if prediction.get("completed_at"):
        status["completed_at"] = prediction["completed_at"]

    if provider_status == "succeeded" and not status.get("result"):
        result = _result_from_prediction(status=status, prediction=prediction)
        write_json(Path(status["job_dir"]) / "job.json", result)
        status["result"] = result
        status["outputs_downloaded"] = True
    elif provider_status in {"failed", "canceled"}:
        status["outputs_downloaded"] = False

    _write_status(out_root, status["job_id"], status)
    return status


def _load_async_video_status(job_id: str, out_root: Optional[str]) -> tuple[Path, dict[str, Any]]:
    out_root_path = resolve_output_root(out_root) if out_root else resolve_output_root("out")
    status_path = _job_status_path(out_root_path, job_id)
    if not status_path.is_file():
        raise RuntimeError(f"JOB_NOT_FOUND: {job_id}")
    return out_root_path, _read_json(status_path)


@mcp.tool()
def generate_image(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = "gemini-3-pro-image-preview",
    image: Optional[str] = None,
    images: Optional[list[str]] = None,
    aspect_ratio: Optional[str] = None,
    image_size: Optional[str] = None,
    temperature: float = 0.7,
    num_outputs: int = 1,
    title: Optional[str] = None,
    out_root: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Generate one or more images with the Gemini image model.

    Wraps the gemini-video-prompts CLI's image generation worker. Outputs
    land at ``<out_root>/<today>/<model>/<seq>_<title>_<hash>/<title>_NN.png``,
    matching the CLI's directory layout exactly.

    Args:
        prompt: The image generation prompt.
        system_prompt: Style / behavior instruction sent as system_instruction.
        model: Gemini image model id. Default ``gemini-3-pro-image-preview``.
        image: Path to a single reference image (img2img).
        images: List of additional reference image paths.
        aspect_ratio: e.g. ``"16:9"``, ``"9:16"``, ``"1:1"``, ``"3:4"``.
            Requires google-genai with ImageConfig support.
        image_size: e.g. ``"1K"``, ``"2K"``. Same caveat as aspect_ratio.
        temperature: 0..2; default 0.7.
        num_outputs: 1..4 images per call.
        title: Optional human-readable title; defaults to first words of prompt.
        out_root: Override output root; default ``<cli-repo>/out``.
        dry_run: If True, return the resolved job + projected_job_dir without
            calling the model or creating files.

    Returns:
        On success: the full result dict from ``generate_image_job`` with
        ``status``, ``title``, ``model``, ``prompt``, ``resolved_params``,
        ``input_count``, ``inputs``, ``attempts``, ``text``, ``job_dir``,
        and ``outputs[]`` (each with ``index``, ``path``, ``width``, ``height``).

        On dry_run: the summarized job plus ``status: "planned"`` and
        ``projected_job_dir``.

    Raises:
        RuntimeError: with codes ``IMAGE_NOT_FOUND``, ``INVALID_INPUT``,
            ``NO_IMAGE_RETURNED``, ``IMAGE_CONFIG_UNSUPPORTED``, or others
            propagated from the underlying generate_image_job.
    """
    if num_outputs < 1 or num_outputs > 4:
        raise RuntimeError("INVALID_INPUT: num_outputs must be between 1 and 4")

    batch_path = (Path.cwd() / "<inline>").resolve()
    job = build_resolved_image_job(
        prompt=prompt,
        title=title,
        model=model,
        system_prompt=system_prompt,
        image=image,
        images=images,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        temperature=temperature,
        num_outputs=num_outputs,
        out_root=out_root,
        source_index=1,
        source_format="inline",
        batch_file=str(batch_path),
    )

    if dry_run:
        summary = summarize_job(1, job)
        summary["status"] = "planned"
        summary["projected_job_dir"] = _project_job_dir(job)
        return summary

    # Asset existence checks live below the dry_run return — dry_run mirrors
    # the CLI's --plan and must not require referenced files to exist yet.
    if image is not None and not Path(image).expanduser().is_file():
        raise RuntimeError(f"IMAGE_NOT_FOUND: {image}")
    if images:
        for path_str in images:
            if not Path(path_str).expanduser().is_file():
                raise RuntimeError(f"IMAGE_NOT_FOUND: {path_str}")

    client, gtypes = init_client()
    out_root_path = job["out_root"]
    day_dir = ensure_dir(out_root_path / dt.date.today().isoformat())
    return generate_image_job(
        client=client,
        gtypes=gtypes,
        batch_path=batch_path,
        job=job,
        run_day_dir=day_dir,
    )


@mcp.tool()
def generate_video(
    prompt: str,
    model: str = SEEDANCE_MODEL_DEFAULT,
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
    title: Optional[str] = None,
    out_root: Optional[str] = None,
    timeout_s: int = 600,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Generate a video with Seedance 2.0 via Replicate.

    Wraps the Seedance adapter (``seedance.py``) — param mapping, multi-file
    handle lifecycle, sidecar sanitization, ffprobe ``media_info`` per output.
    Outputs land at
    ``<out_root>/<today>/<model_slug>/01_<title_slug>_<hash>/<title>_NN.mp4``.

    Mode discriminator (returned in ``mode``): ``text_to_video`` |
    ``first_last_frames`` | ``omni_reference`` per
    ``seedance-prompting-guide.md:25``.

    Reference token convention: bracket syntax (``[Image1]``, ``[Video1]``,
    ``[Audio1]``). The ``references[]`` return field carries provider-truthful
    tokens — paste them verbatim into the prompt rather than translating.

    Args:
        prompt: Text prompt for video generation.
        model: Replicate model_ref. Default ``bytedance/seedance-2.0``.
        image: First frame (img2vid). Mutually exclusive with reference_images.
        last_frame_image: Last frame; requires image. Mut.ex. with reference_images.
        reference_images: Up to 9 reference image paths (identity, style, or
            composition). Mut.ex. with image/last_frame_image.
        reference_videos: Up to 3 reference video paths; total ≤ 15s.
            Layerable on either mode above.
        reference_audios: Up to 3 reference audio paths; total ≤ 15s. Requires
            an anchor (image / reference_images / reference_videos).
        duration: 4..15 seconds, or -1 for the model's "intelligent" length.
        resolution: ``"480p"`` | ``"720p"`` | ``"1080p"``.
        aspect_ratio: One of the Seedance enum values (incl. ``"adaptive"``).
        generate_audio: If True, Seedance generates synchronized audio.
            Default False (production typically replaces with edited score).
        seed: Optional reproducibility seed.
        title: Optional human-readable title; defaults to first words of prompt.
        out_root: Override output root; default ``<cli-repo>/out``.
        timeout_s: Replicate poll timeout. Default 600.
        dry_run: If True, validate inputs + return ``status: "planned"`` plus
            ``projected_job_dir`` without firing or touching files.

    Returns:
        Real run: ``{ status, created_at, started_at, title, model,
        model_version, mode, prompt, resolved_params, references[],
        validation_warnings[], job_dir, outputs[], metrics }``.
        Dry run: same minus ``outputs``/``metrics``/``created_at``/
        ``model_version``, plus ``status: "planned"`` and ``projected_job_dir``.

    Raises:
        RuntimeError: with codes ``INVALID_INPUT`` (mut.ex., per-type cap,
        range, enum, anchor), ``FILE_NOT_FOUND`` (real run only),
        ``REPLICATE_ERROR`` (Replicate API failure),
        ``REPLICATE_NOT_INSTALLED`` / ``REPLICATE_API_TOKEN_MISSING``.
    """
    # Hard validation + Replicate-shape param dict (string paths)
    api_params = seedance.build_seedance_video_params(
        prompt=prompt,
        image=image,
        last_frame_image=last_frame_image,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
        duration=duration,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        generate_audio=generate_audio,
        seed=seed,
    )

    references = seedance.build_references_map(
        image=image,
        last_frame_image=last_frame_image,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
    )
    mode = seedance.derive_mode(
        image=image,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
    )

    # Soft warnings (non-blocking)
    validation_warnings: list[str] = []
    validation_warnings.extend(seedance.check_prompt_references(prompt, references))
    validation_warnings.extend(seedance.check_total_reference_cap(references))

    title_str = title if title else prompt_stem(prompt)
    out_root_path = resolve_output_root(out_root) if out_root else resolve_output_root("out")

    resolved_params = {
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "generate_audio": generate_audio,
        "seed": seed,
    }

    if dry_run:
        return {
            "status": "planned",
            "title": title_str,
            "model": model,
            "mode": mode,
            "prompt": prompt.strip(),
            "resolved_params": resolved_params,
            "references": references,
            "validation_warnings": validation_warnings,
            "projected_job_dir": _project_video_job_dir(
                model=model, title=title_str, params=api_params, out_root=out_root_path
            ),
        }

    # Asset existence checks live below the dry_run return — dry_run mirrors
    # the CLI's --plan and must not require referenced files to exist yet.
    for path_key in ("image", "last_frame_image"):
        path_val = api_params.get(path_key)
        if path_val and not Path(path_val).is_file():
            raise RuntimeError(f"FILE_NOT_FOUND: {path_val}")
    for path_key in ("reference_images", "reference_videos", "reference_audios"):
        for path_val in api_params.get(path_key, []) or []:
            if not Path(path_val).is_file():
                raise RuntimeError(f"FILE_NOT_FOUND: {path_val}")

    # Build job dir
    title_slug = slugify(title_str) or "job-1"
    model_slug = slugify(model, max_len=80)
    job_hash = seedance.build_seedance_job_hash(api_params)
    job_dir = ensure_dir(
        out_root_path / dt.date.today().isoformat() / model_slug
        / f"01_{title_slug}_{job_hash}"
    )

    started_at = now_iso()
    sidecar = seedance.run_seedance_job(
        api_params=api_params,
        return_params=api_params,  # already string paths from build_seedance_video_params
        out_dir=job_dir,
        base_name=title_slug,
        timeout_s=timeout_s,
        model_ref=model,
    )

    if not sidecar.get("success"):
        err = sidecar.get("error") or {}
        msg = (err.get("message") if isinstance(err, dict) else None) or "unknown error"
        # Preserve coded prefixes from the underlying call (e.g.,
        # REPLICATE_TIMEOUT from _run_with_timeout, REPLICATE_NOT_INSTALLED
        # from _ensure_replicate). Wrap only when the message doesn't already
        # carry a CODE: prefix.
        if _CODED_PREFIX_RE.match(msg):
            raise RuntimeError(msg)
        raise RuntimeError(f"REPLICATE_ERROR: {msg}")

    # Enrich each output with ffprobe-derived media_info; strip internal _metrics
    enriched_outputs: list[dict[str, Any]] = []
    for idx, out in enumerate(sidecar.get("outputs", []), start=1):
        out_clean = dict(out)
        out_clean.pop("_metrics", None)
        out_clean["index"] = idx
        out_clean["media_info"] = seedance.probe_media_info(out_clean["path"])
        enriched_outputs.append(out_clean)

    result = {
        "status": "ok",
        "created_at": now_iso(),
        "started_at": started_at,
        "title": title_str,
        "model": model,
        "model_version": (sidecar.get("model") or {}).get("version") or "@latest",
        "mode": mode,
        "prompt": prompt.strip(),
        "resolved_params": resolved_params,
        "references": references,
        "validation_warnings": validation_warnings,
        "job_dir": str(job_dir),
        "outputs": enriched_outputs,
        "metrics": {
            **sidecar.get("metrics", {}),
            "cold_start": sidecar.get("cold_start"),
        },
    }
    write_json(job_dir / "job.json", result)
    return result


@mcp.tool()
def start_video_job(
    prompt: str,
    model: str = SEEDANCE_MODEL_DEFAULT,
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
    title: Optional[str] = None,
    out_root: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> dict[str, Any]:
    """Start a Seedance video job and return immediately.

    This is the local-async counterpart to ``generate_video``. It validates
    inputs, creates a durable local job record under ``<out_root>/jobs/<job_id>``,
    starts a non-blocking Replicate prediction, and returns the current status.
    Use ``get_video_job(job_id)`` to poll or collect completed outputs.

    If ``out_root`` is set here, pass the same ``out_root`` to ``get_video_job``
    and ``cancel_video_job``. ``webhook_url`` is forwarded to Replicate for
    future HTTP receiver workflows; this stdio MCP still relies on polling.
    """
    ctx = _build_video_context(
        prompt=prompt,
        model=model,
        image=image,
        last_frame_image=last_frame_image,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
        duration=duration,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        generate_audio=generate_audio,
        seed=seed,
        title=title,
        out_root=out_root,
    )
    api_params = ctx["api_params"]
    _check_video_files(api_params)

    job_id = uuid.uuid4().hex[:12]
    out_root_path: Path = ctx["out_root"]
    job_dir = Path(f"{ctx['job_dir']}_{job_id}")

    request = {
        "job_id": job_id,
        "created_at": now_iso(),
        "provider": "replicate",
        "tool": "start_video_job",
        "model": model,
        "mode": ctx["mode"],
        "prompt": prompt.strip(),
        "resolved_params": ctx["resolved_params"],
        "provider_params": api_params,
        "references": ctx["references"],
        "validation_warnings": ctx["validation_warnings"],
        "job_dir": str(job_dir),
    }

    prediction = seedance.create_seedance_prediction(
        api_params=api_params,
        model_ref=model,
        webhook_url=webhook_url,
        webhook_events_filter=["completed"] if webhook_url else None,
    )
    prediction_id = prediction.get("id")
    if not prediction_id:
        raise RuntimeError("REPLICATE_ERROR: prediction create returned no id")

    job_dir = ensure_dir(job_dir)
    ensure_dir(_jobs_root(out_root_path) / job_id)
    write_json(_job_request_path(out_root_path, job_id), request)

    status = {
        "status": _prediction_status(prediction),
        "job_id": job_id,
        "prediction_id": prediction_id,
        "provider": "replicate",
        "created_at": request["created_at"],
        "updated_at": request["created_at"],
        "started_at": prediction.get("started_at"),
        "completed_at": prediction.get("completed_at"),
        "title": ctx["title"],
        "model": model,
        "mode": ctx["mode"],
        "prompt": prompt.strip(),
        "resolved_params": ctx["resolved_params"],
        "references": ctx["references"],
        "validation_warnings": ctx["validation_warnings"],
        "job_dir": str(job_dir),
        "request_path": str(_job_request_path(out_root_path, job_id)),
        "status_path": str(_job_status_path(out_root_path, job_id)),
        "outputs_downloaded": False,
        "error": _prediction_error(prediction),
        "provider_prediction": prediction,
    }
    _write_status(out_root_path, job_id, status)
    return status


@mcp.tool()
def get_video_job(
    job_id: str,
    out_root: Optional[str] = None,
    poll: bool = True,
) -> dict[str, Any]:
    """Return local status for an async video job, optionally polling Replicate."""
    out_root_path, status = _load_async_video_status(job_id, out_root)
    if not poll:
        return status

    if status.get("status") in _TERMINAL_PREDICTION_STATUSES:
        if status.get("status") == "succeeded" and not status.get("result"):
            prediction = status.get("provider_prediction")
            if not prediction:
                prediction_id = status.get("prediction_id")
                if not prediction_id:
                    raise RuntimeError(f"INVALID_JOB: {job_id} has no prediction_id")
                prediction = seedance.get_seedance_prediction(prediction_id)
            return _merge_prediction_status(
                status=status,
                prediction=prediction,
                out_root=out_root_path,
            )
        return status

    prediction_id = status.get("prediction_id")
    if not prediction_id:
        raise RuntimeError(f"INVALID_JOB: {job_id} has no prediction_id")
    prediction = seedance.get_seedance_prediction(prediction_id)
    return _merge_prediction_status(
        status=status,
        prediction=prediction,
        out_root=out_root_path,
    )


@mcp.tool()
def cancel_video_job(
    job_id: str,
    out_root: Optional[str] = None,
) -> dict[str, Any]:
    """Cancel a running async video job and persist the updated status."""
    out_root_path, status = _load_async_video_status(job_id, out_root)
    if status.get("status") in _TERMINAL_PREDICTION_STATUSES:
        return status

    prediction_id = status.get("prediction_id")
    if not prediction_id:
        raise RuntimeError(f"INVALID_JOB: {job_id} has no prediction_id")
    prediction = seedance.cancel_seedance_prediction(prediction_id)
    return _merge_prediction_status(
        status=status,
        prediction=prediction,
        out_root=out_root_path,
    )


def main() -> None:
    """Console-script entry point — runs the FastMCP server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
