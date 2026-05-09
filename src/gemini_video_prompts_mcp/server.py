"""FastMCP server exposing gemini-video-prompts as MCP tools.

Tools:
- generate_image  — Gemini image generation (wraps generate_image_job)
- generate_video  — Seedance 2.0 via Replicate (uses seedance.py adapter)

See MCP_DESIGN.md at the repo root for the full architecture.
"""
from __future__ import annotations

import datetime as dt
import re
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
        duration: 1..15 seconds, or -1 for the model's "intelligent" length.
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


def main() -> None:
    """Console-script entry point — runs the FastMCP server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
