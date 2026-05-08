"""FastMCP server exposing gemini-video-prompts as MCP tools.

Tools:
- generate_image  — Gemini image generation (wraps generate_image_job)
- generate_video  — Seedance via Replicate (Step 3, pending)

See MCP_DESIGN.md at the repo root for the full architecture.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from gemini_video_prompts.cli import (
    build_job_hash,
    build_resolved_image_job,
    ensure_dir,
    generate_image_job,
    init_client,
    slugify,
    summarize_job,
)


mcp = FastMCP("gemini-prompts-mcp")


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


def main() -> None:
    """Console-script entry point — runs the FastMCP server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
