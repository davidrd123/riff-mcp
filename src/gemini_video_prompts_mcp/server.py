"""FastMCP server exposing gemini-video-prompts as MCP tools.

Tools:
- generate_image  — Gemini or OpenAI GPT Image generation
- generate_video  — Seedance 2.5 / 2.0 via Replicate (uses seedance.py adapter)
- start_video_job, get_video_job, cancel_video_job
                  — local async control surface for Seedance predictions
- get_generation, list_generations
                  — durable provenance retrieval and search

See MCP_DESIGN.md at the repo root for the full architecture.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from gemini_video_prompts.cli import (
    DEFAULT_IMAGE_MODEL,
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

from . import fal_video, seedance
from .seedance import SEEDANCE_MODEL_DEFAULT

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback uses thread locks only
    fcntl = None  # type: ignore[assignment]


mcp = FastMCP("gemini-prompts-mcp")

# Match a leading "CODE:" prefix in error messages so we can preserve already-
# coded errors at the MCP boundary (e.g. REPLICATE_TIMEOUT from the watchdog)
# instead of double-wrapping them as REPLICATE_ERROR: REPLICATE_TIMEOUT: ...
_CODED_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9_]+:")
_TERMINAL_PREDICTION_STATUSES = {"succeeded", "failed", "canceled"}
_JOB_LOCKS_GUARD = threading.Lock()
_JOB_LOCKS: dict[str, threading.Lock] = {}


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


def _generation_index_path(out_root: Path) -> Path:
    return _jobs_root(out_root) / "index.ndjson"


@contextmanager
def _job_finalize_lock(out_root: Path, job_id: str):
    """Serialize finalization within this process and across Unix processes."""
    lock_key = str(_job_status_path(out_root, job_id).resolve())
    with _JOB_LOCKS_GUARD:
        thread_lock = _JOB_LOCKS.setdefault(lock_key, threading.Lock())

    with thread_lock:
        lock_path = _jobs_root(out_root) / job_id / ".finalize.lock"
        ensure_dir(lock_path.parent)
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _generation_index_write_lock(out_root: Path):
    """Serialize writes to the shared generation index."""
    index_path = _generation_index_path(out_root)
    lock_key = str(index_path.resolve())
    with _JOB_LOCKS_GUARD:
        thread_lock = _JOB_LOCKS.setdefault(lock_key, threading.Lock())

    with thread_lock:
        lock_path = _jobs_root(out_root) / ".index.lock"
        ensure_dir(lock_path.parent)
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_status(out_root: Path, job_id: str, status: dict[str, Any]) -> None:
    status["updated_at"] = now_iso()
    write_json(_job_status_path(out_root, job_id), status)


def _file_identity(path: str) -> dict[str, Any]:
    file_path = Path(path)
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "bytes": file_path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def _apply_reference_provenance(
    references: list[dict[str, Any]],
    identities: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for reference in references:
        record = dict(reference)
        path = str(Path(str(record.get("path"))).expanduser().resolve())
        identity = identities.get(path)
        if identity is not None:
            record.update(identity)
        else:
            try:
                record.update(_file_identity(path))
                record["hash_source"] = "post_submission_path_fallback"
            except OSError as exc:
                raise RuntimeError(f"FILE_HASH_FAILED: {path}: {exc}") from exc
        enriched.append(record)
    return enriched


def _enrich_output_provenance(output: dict[str, Any]) -> dict[str, Any]:
    record = dict(output)
    path = record.get("path")
    if not path:
        return record
    try:
        record.update(_file_identity(str(path)))
        record["hash_source"] = "downloaded_output_file"
    except OSError as exc:
        record["provenance_warning"] = f"could not hash output {path}: {exc}"
    return record


def _seed_provenance_from_status(
    status: dict[str, Any], prediction: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    prior = status.get("seed_provenance")
    requested_seed = (
        prior.get("requested_seed")
        if isinstance(prior, dict)
        else (status.get("resolved_params") or {}).get("seed")
    )
    return seedance.resolve_seed_provenance(
        requested_seed=requested_seed,
        prediction=prediction,
    )


def _generation_index_entry(
    status: dict[str, Any], *, event: str
) -> dict[str, Any]:
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    outputs = result.get("outputs") if isinstance(result, dict) else []
    references = status.get("references") or []
    output_paths = [
        output.get("path")
        for output in (outputs or [])
        if isinstance(output, dict) and output.get("path")
    ]
    reference_files = [
        {
            key: reference.get(key)
            for key in (
                "token",
                "role",
                "path",
                "bytes",
                "sha256",
                "hash_source",
            )
        }
        for reference in references
        if isinstance(reference, dict)
    ]
    output_files = [
        {
            key: output.get(key)
            for key in ("index", "path", "bytes", "sha256", "hash_source")
        }
        for output in (outputs or [])
        if isinstance(output, dict)
    ]
    return {
        "schema_version": 1,
        "event": event,
        "indexed_at": now_iso(),
        "generation_id": status["job_id"],
        "job_id": status["job_id"],
        "prediction_id": status.get("prediction_id"),
        "provider": status.get("provider"),
        "status": status.get("status"),
        "created_at": status.get("created_at"),
        "started_at": status.get("started_at"),
        "completed_at": status.get("completed_at"),
        "collected_at": result.get("collected_at"),
        "title": status.get("title"),
        "model": status.get("model"),
        "mode": status.get("mode"),
        "prompt": status.get("prompt"),
        "resolved_params": status.get("resolved_params"),
        "seed_provenance": status.get("seed_provenance"),
        "reference_count": len(references),
        "reference_files": reference_files,
        "job_dir": status.get("job_dir"),
        "output_paths": output_paths,
        "output_files": output_files,
    }


def _ensure_generation_index_line_boundary(index_path: Path) -> None:
    """Keep a crash-truncated tail from swallowing the next NDJSON event."""
    if not index_path.is_file() or index_path.stat().st_size == 0:
        return
    with index_path.open("rb+") as handle:
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) == b"\n":
            return
        handle.seek(0, os.SEEK_END)
        handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_generation_index(
    out_root: Path, status: dict[str, Any], *, event: str
) -> Optional[str]:
    index_path = _generation_index_path(out_root)
    marker_path = (
        _jobs_root(out_root) / status["job_id"] / f".index-event-{event}"
    )
    try:
        with _generation_index_write_lock(out_root):
            _ensure_generation_index_line_boundary(index_path)
            entries, _ = _read_generation_index(out_root)
            already_indexed = any(
                entry.get("job_id") == status["job_id"]
                and entry.get("event") == event
                for entry in entries
            )
            if not already_indexed:
                entry = _generation_index_entry(status, event=event)
                with index_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(entry, ensure_ascii=False, sort_keys=True)
                        + "\n"
                    )
                    handle.flush()
                    os.fsync(handle.fileno())

            ensure_dir(marker_path.parent)
            if not marker_path.exists():
                with marker_path.open("x", encoding="utf-8") as marker:
                    marker.write(now_iso() + "\n")
                    marker.flush()
                    os.fsync(marker.fileno())
    except (OSError, TypeError, ValueError) as exc:
        return f"could not append {event} event to {index_path}: {exc}"
    return None


def _append_generation_index_safely(
    out_root: Path, status: dict[str, Any], *, event: str
) -> None:
    warning = _append_generation_index(out_root, status, event=event)
    if warning:
        status.setdefault("provenance_warnings", []).append(warning)


def _read_generation_index(out_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    index_path = _generation_index_path(out_root)
    if not index_path.is_file():
        return [], []

    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    for line_number, raw_line in enumerate(
        index_path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            warnings.append(f"index line {line_number} is invalid JSON: {exc}")
            continue
        if isinstance(entry, dict) and entry.get("job_id"):
            entries.append(entry)
    return entries, warnings


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
    aspect_ratio: Optional[str],
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
        model=model,
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
        "aspect_ratio": api_params["aspect_ratio"],  # resolved if caller left it None
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
        out_clean = _enrich_output_provenance(out)
        out_clean.pop("_metrics", None)
        out_clean["index"] = idx
        out_clean["media_info"] = seedance.probe_media_info(out_clean["path"])
        enriched_outputs.append(out_clean)

    collected_at = now_iso()
    return {
        "status": "ok",
        "generation_id": status["job_id"],
        "created_at": (
            status.get("created_at")
            or prediction.get("created_at")
            or collected_at
        ),
        "started_at": prediction.get("started_at") or status.get("started_at"),
        "completed_at": prediction.get("completed_at"),
        "collected_at": collected_at,
        "title": status["title"],
        "model": status["model"],
        "model_version": prediction.get("version") or "@latest",
        "prediction_id": status["prediction_id"],
        "mode": status["mode"],
        "prompt": status["prompt"],
        "resolved_params": status["resolved_params"],
        "seed_provenance": status["seed_provenance"],
        "references": status["references"],
        "validation_warnings": status["validation_warnings"],
        "job_dir": status["job_dir"],
        "outputs": enriched_outputs,
        "metrics": metrics,
    }


def _merge_prediction_status_unlocked(
    *,
    status: dict[str, Any],
    prediction: dict[str, Any],
    out_root: Path,
) -> dict[str, Any]:
    provider_status = _prediction_status(prediction)
    status["status"] = provider_status
    status["provider_prediction"] = prediction
    status["error"] = _prediction_error(prediction)
    status["seed_provenance"] = _seed_provenance_from_status(status, prediction)
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

    if provider_status in _TERMINAL_PREDICTION_STATUSES:
        _append_generation_index_safely(out_root, status, event=provider_status)

    _write_status(out_root, status["job_id"], status)
    return status


def _merge_prediction_status(
    *,
    status: dict[str, Any],
    prediction: dict[str, Any],
    out_root: Path,
) -> dict[str, Any]:
    job_id = status["job_id"]
    with _job_finalize_lock(out_root, job_id):
        status_path = _job_status_path(out_root, job_id)
        if status_path.is_file():
            persisted = _read_json(status_path)
            persisted_state = persisted.get("status")
            if persisted_state in _TERMINAL_PREDICTION_STATUSES:
                if persisted_state != "succeeded" or persisted.get("result"):
                    return persisted
            status = persisted

        return _merge_prediction_status_unlocked(
            status=status,
            prediction=prediction,
            out_root=out_root,
        )


def _load_async_video_status(job_id: str, out_root: Optional[str]) -> tuple[Path, dict[str, Any]]:
    out_root_path = resolve_output_root(out_root) if out_root else resolve_output_root("out")
    status_path = _job_status_path(out_root_path, job_id)
    if not status_path.is_file():
        raise RuntimeError(f"JOB_NOT_FOUND: {job_id}")
    return out_root_path, _read_json(status_path)


@mcp.tool()
def compare_image_variants(
    prompt: str,
    variants: list[dict[str, Any]],
    image: Optional[str] = None,
    images: Optional[list[str]] = None,
    mask: Optional[str] = None,
    out_root: Optional[str] = None,
    dry_run: bool = True,
    allow_api_billing: bool = False,
) -> dict[str, Any]:
    """Compare 2..8 OpenAI model/quality/size variants of one shared prompt.

    Each variant requires explicit model, quality, size and optional label.
    No auto values; exactly one image per variant. References and optional PNG
    alpha mask are shared. dry_run=True previews the full billable batch for free.
    Start edit comparisons without a mask: use the same reference and direct
    instruction for every variant. Do not introduce a hand-drawn mask merely
    to compare models. Test masking separately if targeting errors warrant it.
    Obtain approval for every variant before setting allow_api_billing=True:
    these are OpenAI API charges, separate from ChatGPT/Codex subscriptions.
    Execution stops on the first failure, with no retries or model fallback.

    Returns comparison_dir, review_path (local side-by-side HTML), record_path
    (JSON), and results containing original outputs, latency, usage, and dated
    uncached list-price cost estimates when token breakdowns are available.
    Estimates are not invoices; missing usage is unknown, not zero. No matched
    seed is available and one sample does not establish a model ranking.
    Inspect saved job records after a failure before approving another attempt.
    """
    from gemini_video_prompts.image_comparison import compare_images

    return compare_images(prompt=prompt, variants=variants, image=image, images=images,
                          mask=mask, out_root=out_root, dry_run=dry_run,
                          allow_api_billing=allow_api_billing)


@mcp.tool()
def generate_image(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    image: Optional[str] = None,
    images: Optional[list[str]] = None,
    aspect_ratio: Optional[str] = None,
    image_size: Optional[str] = None,
    temperature: Optional[float] = None,
    num_outputs: int = 1,
    api: Optional[str] = None,
    thinking_level: Optional[str] = None,
    store: bool = False,
    previous_interaction_id: Optional[str] = None,
    title: Optional[str] = None,
    out_root: Optional[str] = None,
    dry_run: bool = False,
    provider: str = "gemini",
    size: Optional[str] = None,
    quality: Optional[str] = None,
    output_format: Optional[str] = None,
    background: Optional[str] = None,
    output_compression: Optional[int] = None,
    allow_api_billing: bool = False,
    mask: Optional[str] = None,
) -> dict[str, Any]:
    """Generate one or more images with Gemini (default) or OpenAI GPT Image 2.

    Wraps the gemini-video-prompts CLI's image generation worker. Outputs
    land at ``<out_root>/<today>/<model>/<seq>_<title>_<hash>/``, matching the
    CLI layout. Gemini saves PNG; OpenAI saves the returned PNG/JPEG/WebP bytes.

    Args:
        prompt: The image generation prompt.
        system_prompt: Style / behavior instruction sent as system_instruction.
        model: Defaults to ``gemini-nano-banana-2.1`` for Gemini or ``gpt-image-2`` for OpenAI.
            Pass ``gemini-3-pro-image`` explicitly for Nano Banana Pro.
            OpenAI also accepts explicit ``gpt-image-2.5-sunburst`` or
            ``gpt-image-2.5-flare``; account availability is provider-controlled.
        image: Path to a single reference image (img2img).
        images: List of additional reference image paths.
        mask: Optional OpenAI-only PNG alpha mask; omit by default. For an
            identifiable object, first use image plus a direct edit instruction.
            Use a mask for a user-supplied selection or a demonstrated targeting
            problem, not as routine preparation. Imperfect boundaries can harm
            edits. The mask applies to the first reference image.
            Transparent areas indicate edits. Requires a PNG first reference of
            matching dimensions and a mask smaller than 4 MiB. Mask guidance
            does not guarantee pixel-exact preservation outside the region.
        aspect_ratio: e.g. ``"16:9"``, ``"9:16"``, ``"1:1"``, ``"3:4"``.
            Requires google-genai with ImageConfig support.
        image_size: e.g. ``"1K"``, ``"2K"``. Same caveat as aspect_ratio.
        temperature: Optional override; omitted by default for Gemini 3.x models.
        num_outputs: 1..4 images per call.
        api: Gemini: ``generate_content`` (default) or ``interactions``.
            OpenAI: ``images`` (default); references automatically select edits.
        thinking_level: Optional ``minimal``/``low``/``medium``/``high``.
            Model support varies; Nano Banana 2.1 documents minimal/medium/high
            and Gemini 3.1 Flash Image minimal/high.
        store: Persist an Interactions request so it can be continued later.
        previous_interaction_id: Continue a stored interaction. Requires
            ``api="interactions"`` and sends ``prompt`` as the next instruction.
        title: Optional human-readable title; defaults to first words of prompt.
        out_root: Override output root; default ``<cli-repo>/out``.
        dry_run: If True, return the resolved job + projected_job_dir without
            calling the model or creating files.
        provider: ``gemini`` (default) or ``openai``. OpenAI requires
            ``uv sync --extra openai`` and ``OPENAI_API_KEY``; API billing applies.
        size: OpenAI pixel dimensions, e.g. ``1536x1024``, or ``auto``.
        quality: OpenAI ``auto``, ``low``, ``medium``, or ``high``; GPT Image 2.5
            also supports ``xhigh`` and ``max``.
        output_format: OpenAI ``png`` (default), ``jpeg``, or ``webp``.
        background: OpenAI ``auto``, ``opaque``, or ``transparent`` (PNG/WebP).
        output_compression: OpenAI JPEG/WebP compression, integer 0..100.
        allow_api_billing: Required True for a billable OpenAI request; default False.
            OpenAI API charges are separate from ChatGPT/Codex subscription limits.
            Ask the user to approve this request or a bounded batch before setting
            True. An installed key, provider selection, or auto-approved MCP tool
            does not establish spending authorization. Do not silently retry or
            switch from subscription generation to API billing. Dry runs are free.

        OpenAI does not accept Gemini aspect_ratio, image_size, temperature,
        system_prompt, thinking_level, or stored interaction options. Include
        all instructions in prompt and pass saved output paths as references
        for subsequent edits. OpenAI results also record provider, access,
        request_id, usage, response_metadata, and original image byte hashes.

    Returns:
        On success: the full result dict from ``generate_image_job`` with
        ``status``, ``title``, ``model``, ``prompt``, ``resolved_params``,
        ``input_count``, ``inputs``, ``attempts``, ``interaction_ids``,
        ``text``, ``job_dir``, and ``outputs[]`` (each with ``index``, ``path``,
        ``width``, ``height``). For the Interactions path,
        ``interaction_ids`` contains the provider IDs that can be supplied as
        ``previous_interaction_id`` on a later call.

        On dry_run: the summarized job plus ``status: "planned"`` and
        ``projected_job_dir``.

    Raises:
        RuntimeError: with codes ``IMAGE_NOT_FOUND``, ``INVALID_INPUT``,
            ``NO_IMAGE_RETURNED``, ``IMAGE_CONFIG_UNSUPPORTED``, or others
            propagated from the underlying generate_image_job.
    """
    if num_outputs < 1 or num_outputs > 4:
        raise RuntimeError("INVALID_INPUT: num_outputs must be between 1 and 4")

    api = api if api is not None else ("images" if provider == "openai" else "generate_content")
    if provider == "gemini" and api not in {"generate_content", "interactions"}:
        raise RuntimeError("INVALID_INPUT: Gemini api must be generate_content or interactions")
    if previous_interaction_id and api != "interactions":
        raise RuntimeError("INVALID_INPUT: previous_interaction_id requires api=interactions")
    batch_path = (Path.cwd() / "<inline>").resolve()
    job = build_resolved_image_job(
        prompt=prompt,
        title=title,
        model=model or ("gpt-image-2" if provider == "openai" else DEFAULT_IMAGE_MODEL),
        system_prompt=system_prompt,
        image=image,
        images=images,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        temperature=temperature,
        num_outputs=num_outputs,
        config={"api": api, "thinking_level": thinking_level, "store": store,
                "previous_interaction_id": previous_interaction_id},
        out_root=out_root,
        source_index=1,
        source_format="inline",
        batch_file=str(batch_path),
        provider=provider,
        size=size,
        quality=quality,
        output_format=output_format,
        background=background,
        output_compression=output_compression,
        mask=mask,
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

    out_root_path = job["out_root"]
    if provider == "openai":
        from gemini_video_prompts.openai_images import generate_openai_image_job

        return generate_openai_image_job(
            batch_path=batch_path, job=job,
            run_day_dir=out_root_path / dt.date.today().isoformat(),
            allow_api_billing=allow_api_billing,
        )
    client, gtypes = init_client()
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
    aspect_ratio: Optional[str] = None,
    generate_audio: bool = False,
    seed: Optional[int] = None,
    title: Optional[str] = None,
    out_root: Optional[str] = None,
    timeout_s: int = 600,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Generate a video with Seedance 2.5 (or 2.0) via Replicate.

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
        prompt: Text prompt for video generation. Put dialogue in double
            quotes; cite references by token (``[Image1]``, ``[Video1]``).
        model: Replicate model_ref. Default ``bytedance/seedance-2.5``;
            ``bytedance/seedance-2.0`` is still supported (it is the only one
            with 1080p/4k). Limits below are 2.5's; 2.0's are in parentheses.
        image: First frame (img2vid). On 2.5 mutually exclusive with ALL
            ``reference_*`` inputs (2.0: only with reference_images).
        last_frame_image: Last frame; requires image. Same exclusivity as image.
        reference_images: Up to 30 (2.0: 9) reference image paths (identity,
            style, or composition). Mut.ex. with image/last_frame_image.
        reference_videos: Up to 10 (2.0: 3) reference video paths; combined
            ≤ 30s (2.0: 15s). Motion transfer, style, editing, extension.
            Editing mode wants ``duration=-1``.
        reference_audios: Up to 10 (2.0: 3) reference audio paths; combined
            ≤ 30s (2.0: 15s). Requires an anchor (reference_images /
            reference_videos; on 2.0 also image).
        duration: 4..30 (2.0: 4..15) seconds, or -1 for "intelligent" length.
        resolution: ``"480p"`` | ``"720p"`` (2.0 also ``"1080p"`` | ``"4k"``).
        aspect_ratio: ``16:9``/``4:3``/``1:1``/``3:4``/``9:16``/``21:9``/
            ``adaptive`` (2.0 also ``9:21``). Leave unset to get ``"adaptive"``
            when a first frame is given (the frame's own ratio wins) and
            ``"16:9"`` otherwise. Explicit values are passed through as-is.
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
        RuntimeError: with codes ``INVALID_INPUT`` (unsupported model,
        mut.ex., per-type cap, range, enum, anchor), ``FILE_NOT_FOUND``
        (real run only),
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
        model=model,
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
        "aspect_ratio": api_params["aspect_ratio"],  # resolved if caller left it None
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
            "seed_provenance": seedance.resolve_seed_provenance(
                requested_seed=seed
            ),
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

    created_at = now_iso()
    started_at = created_at
    sidecar = seedance.run_seedance_job(
        api_params=api_params,
        return_params=api_params,  # already string paths from build_seedance_video_params
        out_dir=job_dir,
        base_name=title_slug,
        timeout_s=timeout_s,
        model_ref=model,
    )
    references = _apply_reference_provenance(
        references,
        sidecar.pop("reference_file_identities", {}),
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
        out_clean = _enrich_output_provenance(out)
        out_clean.pop("_metrics", None)
        out_clean["index"] = idx
        out_clean["media_info"] = seedance.probe_media_info(out_clean["path"])
        enriched_outputs.append(out_clean)

    collected_at = now_iso()
    result = {
        "status": "ok",
        "created_at": created_at,
        "started_at": started_at,
        "collected_at": collected_at,
        "title": title_str,
        "model": model,
        "model_version": (sidecar.get("model") or {}).get("version") or "@latest",
        "mode": mode,
        "prompt": prompt.strip(),
        "resolved_params": resolved_params,
        "seed_provenance": seedance.resolve_seed_provenance(
            requested_seed=seed
        ),
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
    aspect_ratio: Optional[str] = None,
    generate_audio: bool = False,
    seed: Optional[int] = None,
    title: Optional[str] = None,
    out_root: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> dict[str, Any]:
    """Start a Seedance video job and return immediately.

    This is the local-async counterpart to ``generate_video`` — same inputs,
    same per-model validation (see ``generate_video`` for the 2.5 / 2.0
    limits). It validates inputs, creates a durable local job record under
    ``<out_root>/jobs/<job_id>``,
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
        "generation_id": job_id,
        "created_at": now_iso(),
        "provider": "replicate",
        "tool": "start_video_job",
        "model": model,
        "mode": ctx["mode"],
        "prompt": prompt.strip(),
        "resolved_params": ctx["resolved_params"],
        "seed_provenance": seedance.resolve_seed_provenance(
            requested_seed=ctx["resolved_params"].get("seed")
        ),
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
    ctx["references"] = _apply_reference_provenance(
        ctx["references"],
        prediction.pop("_reference_file_identities", {}),
    )
    request["references"] = ctx["references"]
    prediction_id = prediction.get("id")
    if not prediction_id:
        raise RuntimeError("REPLICATE_ERROR: prediction create returned no id")

    job_dir = ensure_dir(job_dir)
    ensure_dir(_jobs_root(out_root_path) / job_id)
    write_json(_job_request_path(out_root_path, job_id), request)

    status = {
        "status": _prediction_status(prediction),
        "job_id": job_id,
        "generation_id": job_id,
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
        "seed_provenance": seedance.resolve_seed_provenance(
            requested_seed=ctx["resolved_params"].get("seed"),
            prediction=prediction,
        ),
        "references": ctx["references"],
        "validation_warnings": ctx["validation_warnings"],
        "job_dir": str(job_dir),
        "request_path": str(_job_request_path(out_root_path, job_id)),
        "status_path": str(_job_status_path(out_root_path, job_id)),
        "outputs_downloaded": False,
        "error": _prediction_error(prediction),
        "provider_prediction": prediction,
    }
    _append_generation_index_safely(out_root_path, status, event="created")
    _write_status(out_root_path, job_id, status)
    return status


@mcp.tool()
def start_fal_video_job(
    prompt: str,
    model: str = fal_video.I2V,
    image: Optional[str] = None,
    last_frame_image: Optional[str] = None,
    reference_images: Optional[list[str]] = None,
    reference_videos: Optional[list[str]] = None,
    reference_audios: Optional[list[str]] = None,
    duration: int = 5,
    resolution: str = "768p",
    aspect_ratio: Optional[str] = None,
    seed: Optional[int] = None,
    prompt_expansion_mode: str = "balanced",
    enable_safety_checker: bool = True,
    title: Optional[str] = None,
    out_root: Optional[str] = None,
    dry_run: bool = False,
    allow_api_billing: bool = False,
) -> dict[str, Any]:
    """Submit one H3 Max video to fal's durable queue, then return a local job_id.

    Uses separately billed FAL_KEY API access, not a subscription. A key or
    auto-approved tool is not spending authorization. Before executing, obtain
    user approval for endpoint, clip count, duration, resolution, and references;
    then set allow_api_billing=true. Honor existing approval within that scope.
    Reference-to-video may bill input references in addition to output seconds.
    Confirmation is caller acknowledgement, not an independently enforced cap.

    Args:
        prompt: 1..50000 characters. Reference tokens are Image 1, Video 1,
            Audio 1, etc., numbered independently in each ordered list.
        model: minimax/h3-max/image-to-video (default),
            minimax/h3-max/reference-to-video,
            minimax/h3-max/text-to-video, or
            minimax/h3-max-turbo/text-to-video. No automatic provider fallback.
        image: Local first frame, required for image-to-video only. Output
            inherits its aspect ratio. Remote URLs are not accepted by this adapter.
        last_frame_image: Optional local last frame for image-to-video.
        reference_images: Reference-to-video only; up to 9 local images.
        reference_videos: Up to 3 local video clips, each 2..15s, total <=15s.
        reference_audios: Up to 3 local audio clips, each 2..15s, total <=15s.
            Reference-to-video needs an image or video anchor; max 12 files
            across modalities. ffprobe is required for video/audio validation.
        duration: Integer 5..15 seconds. One video per submission, with native
            model audio; no separate generate_audio switch on these endpoints.
        resolution: 480p or 768p (default); uppercase accepted too.
        aspect_ratio: Reference-to-video: adaptive (default), 21:9, 16:9,
            4:3, 1:1, 3:4, or 9:16. Text-to-video: 16:9 (default), 21:9,
            4:3, 1:1, 3:4, or 9:16. Image-to-video does not accept it.
        seed: Optional integer; provider-returned seed is recorded separately.
        prompt_expansion_mode: balanced (default, about 1s rewrite) or quality
            (up to about 30s rewrite). This is prompt preprocessing effort.
            Both submitted prompt and returned expanded_prompt are preserved.
        enable_safety_checker: fal's safety checker; default true.
        title: Human-readable label. Each real submission gets a unique folder.
        out_root: Absolute output root recommended; default repository out/.
        dry_run: Validate parameter shape and preview references without reading
            credentials, reading media, writing files, or making requests.
        allow_api_billing: Explicit approval acknowledgement, default false.

    Use get_video_job(job_id, out_root) to poll and collect the video;
    poll=false and get_generation are local reads. list_generations finds jobs.
    cancel_video_job requests cancellation, which may not stop running work.
    References are sent as inline data URIs after approval (100 MiB total local
    limit). Job records keep hashes and paths, not those large encoded inputs.
    No automatic HTTP retries or resubmissions. fal can retry work within its
    own queue. If submission is uncertain, inspect the local record and fal
    dashboard before authorizing another billable attempt.
    """
    params, refs = fal_video.build_request(
        prompt=prompt, model=model, image=image, last_frame_image=last_frame_image,
        reference_images=reference_images, reference_videos=reference_videos,
        reference_audios=reference_audios, duration=duration, resolution=resolution,
        aspect_ratio=aspect_ratio, seed=seed, prompt_expansion_mode=prompt_expansion_mode,
        enable_safety_checker=enable_safety_checker,
    )
    root = resolve_output_root(out_root or "out")
    label = title or prompt_stem(prompt)
    plan = dict(status="planned", provider="fal", model=model, prompt=params["prompt"],
                resolved_params=params, references=refs, title=label,
                mode=model.rsplit("/", 1)[1].replace("-", "_"),
                billing_confirmation_required=True, billing_notice=fal_video.BILLING_NOTICE,
                out_root=str(root))
    if dry_run:
        return plan
    if allow_api_billing is not True:
        raise RuntimeError("API_BILLING_CONFIRMATION_REQUIRED: " + fal_video.BILLING_NOTICE)
    fal_video.api_key()
    payload, refs = fal_video.prepare_inputs(params, refs)
    job_id = uuid.uuid4().hex[:12]
    job_dir = ensure_dir(root / dt.date.today().isoformat() / slugify(model) /
                         f"{slugify(label) or 'video'}_{job_id}")
    ensure_dir(_jobs_root(root) / job_id)
    created = now_iso()
    status = {**plan, "status": "submitting", "job_id": job_id, "generation_id": job_id,
              "created_at": created, "job_dir": str(job_dir), "references": refs,
              "seed_provenance": {"requested_seed": seed, "returned_seed": None},
              "outputs_downloaded": False, "allow_api_billing": True}
    write_json(_job_request_path(root, job_id), {**status, "tool": "start_fal_video_job"})
    _write_status(root, job_id, status)
    try:
        receipt = fal_video.queue_request("POST", "https://queue.fal.run/" + model, payload=payload)
        # Save the receipt immediately, even if later validation fails.
        status["provider_receipt"] = receipt
        status["request_id"] = receipt.get("request_id")
        _write_status(root, job_id, status)
        if not receipt.get("request_id") or not all(receipt.get(k) for k in
                                                   ("status_url", "response_url", "cancel_url")):
            raise RuntimeError("FAL_INVALID_RESPONSE: incomplete submission receipt")
        status["status"] = "queued"
    except RuntimeError as exc:
        status["status"] = "submission_unknown"
        status["error"] = str(exc)
        _write_status(root, job_id, status)
        _append_generation_index_safely(root, status, event="created")
        raise RuntimeError(f"FAL_SUBMISSION_UNCERTAIN: job_id={job_id}; "
                           "inspect get_generation and fal dashboard; do not resubmit automatically") from None
    _append_generation_index_safely(root, status, event="created")
    _write_status(root, job_id, status)
    return status


def _poll_fal_video_job(root: Path, job_id: str, *, cancel: bool = False) -> dict[str, Any]:
    with _job_finalize_lock(root, job_id):
        status = _read_json(_job_status_path(root, job_id))
        if status["status"] in {"succeeded", "failed", "canceled"}:
            return status
        receipt = status.get("provider_receipt") or {}
        if not status.get("request_id") or not receipt.get("status_url"):
            raise RuntimeError("FAL_SUBMISSION_UNCERTAIN: no pollable receipt; inspect fal dashboard")
        if cancel:
            response = fal_video.queue_request("PUT", receipt["cancel_url"])
            status["cancellation_response"] = response
            status["cancellation_requested_at"] = now_iso()
            # Acceptance is not proof of cancellation or a refund.
            _write_status(root, job_id, status)
            return status
        provider_status = fal_video.queue_request("GET", receipt["status_url"])
        status["provider_status"] = provider_status
        state = provider_status.get("status")
        if state == "COMPLETED" and provider_status.get("error"):
            status.update(status="failed", error="FAL_GENERATION_FAILED: see provider dashboard",
                          completed_at=now_iso())
        elif state == "COMPLETED":
            status["status"] = "collecting"
            _write_status(root, job_id, status)
            try:
                result = status.get("provider_result")
                if result is None:
                    result = fal_video.queue_request("GET", receipt["response_url"])
                    status["provider_result"] = result
                    _write_status(root, job_id, status)
                video = result.get("video") or {}
                if not isinstance(video, dict) or not video.get("url"):
                    raise RuntimeError("FAL_INVALID_RESPONSE: completed result has no video URL")
                output = fal_video.download_video(video["url"], Path(status["job_dir"]) / "video_01.mp4")
                status["seed_provenance"]["returned_seed"] = result.get("seed")
                record = {k: status.get(k) for k in ("job_id", "generation_id", "request_id", "provider",
                          "model", "mode", "title", "prompt", "resolved_params", "references",
                          "created_at", "job_dir", "seed_provenance")}
                record.update(status="ok", collected_at=now_iso(), outputs=[output],
                              expanded_prompt=result.get("expanded_prompt"),
                              timings=result.get("timings"), provider_result=result)
                write_json(Path(status["job_dir"]) / "job.json", record)
                status.update(status="succeeded", result=record, outputs_downloaded=True,
                              completed_at=now_iso())
                status.pop("error", None)
            except RuntimeError as exc:
                status["error"] = str(exc)
                _write_status(root, job_id, status)
                raise
        elif state in ("IN_QUEUE", "IN_PROGRESS"):
            status["status"] = "queued" if state == "IN_QUEUE" else "processing"
        else:
            raise RuntimeError("FAL_INVALID_RESPONSE: unrecognized queue status")
        _write_status(root, job_id, status)
        if status["status"] in {"succeeded", "failed", "canceled"}:
            _append_generation_index_safely(root, status, event="completed")
        return status


@mcp.tool()
def get_video_job(
    job_id: str,
    out_root: Optional[str] = None,
    poll: bool = True,
) -> dict[str, Any]:
    """Return an async video job; optionally poll its saved provider (Replicate or fal).

    fal completion downloads the video and records the expanded prompt. Polling
    never resubmits inference. With poll=false this only reads the local record.
    """
    out_root_path, status = _load_async_video_status(job_id, out_root)
    if not poll:
        return status
    if status.get("provider") == "fal":
        return _poll_fal_video_job(out_root_path, job_id)

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
    """Request cancellation from the saved provider and persist its response.

    fal acceptance does not guarantee running work stops or charges are avoided.
    Continue polling the existing job; never resubmit it to check cancellation.
    """
    out_root_path, status = _load_async_video_status(job_id, out_root)
    if status.get("provider") == "fal":
        return _poll_fal_video_job(out_root_path, job_id, cancel=True)
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


@mcp.tool()
def get_generation(
    job_id: str,
    out_root: Optional[str] = None,
) -> dict[str, Any]:
    """Return the durable request, status, and result records for one job.

    This is a read-only provenance lookup. It never polls the provider or
    modifies the generation. For a running job, ``result`` remains ``None``;
    call ``get_video_job`` first when provider polling is desired.
    """
    out_root_path, status = _load_async_video_status(job_id, out_root)
    request_path = _job_request_path(out_root_path, job_id)
    request = _read_json(request_path) if request_path.is_file() else None

    job_path = Path(status["job_dir"]) / "job.json"
    result = status.get("result")
    if result is None and job_path.is_file():
        result = _read_json(job_path)

    return {
        "generation_id": job_id,
        "job_id": job_id,
        "state": status.get("status"),
        "seed_provenance": status.get("seed_provenance")
        or _seed_provenance_from_status(
            status,
            status.get("provider_prediction"),
        ),
        "request": request,
        "status_record": status,
        "result": result,
        "paths": {
            "request": str(request_path) if request_path.is_file() else None,
            "status": str(_job_status_path(out_root_path, job_id)),
            "result": str(job_path) if job_path.is_file() else None,
        },
    }


@mcp.tool()
def list_generations(
    out_root: Optional[str] = None,
    query: Optional[str] = None,
    status_filter: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List and search durable async generation records.

    New jobs append lifecycle summaries to ``jobs/index.ndjson``. The lookup
    also scans existing ``jobs/*/status.json`` records so generations created
    before the index existed are immediately discoverable.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise RuntimeError("INVALID_INPUT: limit must be an integer in [1, 200]")

    out_root_path = resolve_output_root(out_root) if out_root else resolve_output_root("out")
    entries, warnings = _read_generation_index(out_root_path)
    latest_by_job: dict[str, dict[str, Any]] = {}
    for entry in entries:
        latest_by_job[str(entry["job_id"])] = entry

    jobs_root = _jobs_root(out_root_path)
    if jobs_root.is_dir():
        for status_path in sorted(jobs_root.glob("*/status.json")):
            try:
                status = _read_json(status_path)
                if not isinstance(status.get("seed_provenance"), dict):
                    status["seed_provenance"] = _seed_provenance_from_status(
                        status,
                        status.get("provider_prediction"),
                    )
                entry = _generation_index_entry(status, event="snapshot")
                entry["indexed_at"] = status.get("updated_at")
                latest_by_job[str(entry["job_id"])] = entry
            except (OSError, ValueError, KeyError) as exc:
                warnings.append(f"could not read {status_path}: {exc}")

    generations = list(latest_by_job.values())
    if status_filter:
        normalized_status = status_filter.strip().casefold()
        generations = [
            item
            for item in generations
            if str(item.get("status") or "").casefold() == normalized_status
        ]
    if query and query.strip():
        needle = query.strip().casefold()
        generations = [
            item
            for item in generations
            if needle in json.dumps(item, ensure_ascii=False).casefold()
        ]

    generations.sort(
        key=lambda item: str(
            item.get("completed_at")
            or item.get("started_at")
            or item.get("created_at")
            or ""
        ),
        reverse=True,
    )
    total = len(generations)
    generations = generations[:limit]
    return {
        "index_path": str(_generation_index_path(out_root_path)),
        "total": total,
        "count": len(generations),
        "generations": generations,
        "warnings": warnings,
    }


def main() -> None:
    """Console-script entry point — runs the FastMCP server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
