"""FastMCP server exposing media analysis tools.

Tools (per MCP_DESIGN.md sequencing):
- describe_image, score_image     — Step 5 (this file)
- describe_video, score_video     — Step 6 (pending)
- extract_video_frames            — Step 7 (pending)
- compare_images,                 — Step 8 (pending)
  extract_visual_tokens

The describe / score split exists to A/B which judgment locus produces
better real-world iteration. ``describe_*`` returns observation-only output
that Claude reads against the brief; ``score_*`` returns Gemini's own
calibrated 0-100 verdict per criterion. Same backend, different system
prompt + response schema.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from . import ffmpeg_utils, gemini_media, prompts, schemas


mcp = FastMCP("media-analysis-mcp")


def _build_image_contents(
    *,
    image_path: str,
    prompt: str,
    intent: Optional[str],
    context: Optional[str],
    base_plate_path: Optional[str],
    identity_refs: Optional[list[str]],
    style_refs: Optional[list[str]],
    image_module: Any,
) -> list[Any]:
    """Build the Gemini multimodal ``contents`` list — text labels interleaved
    with Pillow images. Order: context block → base plate → identity refs →
    style refs → target image. Each reference is preceded by a labeled string
    so the model knows the role of each image.
    """
    parts: list[Any] = [
        prompts.context_block(prompt=prompt, intent=intent, context=context),
    ]
    if base_plate_path:
        parts.append(prompts.reference_label("base_plate") + ":")
        parts.append(gemini_media.load_image(base_plate_path, image_module=image_module))
    if identity_refs:
        for idx, ref in enumerate(identity_refs, start=1):
            parts.append(prompts.reference_label("identity_ref", idx) + ":")
            parts.append(gemini_media.load_image(ref, image_module=image_module))
    if style_refs:
        for idx, ref in enumerate(style_refs, start=1):
            parts.append(prompts.reference_label("style_ref", idx) + ":")
            parts.append(gemini_media.load_image(ref, image_module=image_module))
    parts.append(prompts.reference_label("target") + ":")
    parts.append(gemini_media.load_image(image_path, image_module=image_module))
    return parts


def _context_used(
    *,
    prompt: str,
    intent: Optional[str],
    context: Optional[str],
    base_plate_path: Optional[str],
    identity_refs: Optional[list[str]],
    style_refs: Optional[list[str]],
) -> dict[str, Any]:
    """Echo the inputs in the return so the agent can audit what the eval
    had to work with."""
    return {
        "prompt": prompt,
        "intent": intent,
        "context": context,
        "base_plate_path": base_plate_path,
        "identity_refs": list(identity_refs) if identity_refs else [],
        "style_refs": list(style_refs) if style_refs else [],
    }


@mcp.tool()
def describe_image(
    image_path: str,
    prompt: str,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    base_plate_path: Optional[str] = None,
    identity_refs: Optional[list[str]] = None,
    style_refs: Optional[list[str]] = None,
    model: str = "gemini-3.1-pro-preview",
    temperature: float = 0.3,
    system_prompt: Optional[str] = None,
) -> dict[str, Any]:
    """Rich structured observations of an image. No scoring, no verdict —
    Claude is the judge.

    Returns the eight fixed observation categories (composition,
    subject_elements, color_and_palette, style_and_rendering,
    lighting_and_atmosphere, text_and_signage, notable_or_unexpected,
    artifacts_or_failures) plus optional freeform_observations and
    context_used echo.

    Args:
        image_path: Absolute path to the image to describe.
        prompt: The gen prompt that produced the image (or empty if not
            available — describe-mode is still useful as a generic visual
            read).
        intent: The creative brief — what this generation was trying to
            solve. Routed into the description as "Brief: ...".
        context: Per-call freeform notes — prior iterations, what to focus
            on this round. Routed as "Context for this evaluation: ...".
        base_plate_path: Optional reference image; the source plate the
            output was mutated from. Helps Gemini call out what changed.
        identity_refs: Optional list of character/asset reference paths.
            Useful for evaluating identity carry-through across shot types.
        style_refs: Optional list of style anchor paths.
        model: Gemini model id. Default ``gemini-3.1-pro-preview``.
        temperature: 0..1. Default 0.3 — low for description consistency.
        system_prompt: Override the default observation-mode system
            instruction. Rare.

    Raises:
        RuntimeError: ``IMAGE_NOT_FOUND``, ``API_KEY_MISSING``, or other
        coded errors from gemini_media helpers.
    """
    if not Path(image_path).expanduser().is_file():
        raise RuntimeError(f"IMAGE_NOT_FOUND: {image_path}")

    image_module = gemini_media.require_pillow()
    client, gtypes = gemini_media.init_client()

    system_instruction = system_prompt or prompts.describe_image_system_prompt()
    contents = _build_image_contents(
        image_path=image_path,
        prompt=prompt,
        intent=intent,
        context=context,
        base_plate_path=base_plate_path,
        identity_refs=identity_refs,
        style_refs=style_refs,
        image_module=image_module,
    )

    parsed: schemas.ImageDescriptionResult = gemini_media.call_structured(
        client=client,
        gtypes=gtypes,
        model=model,
        system_instruction=system_instruction,
        contents=contents,
        response_schema=schemas.ImageDescriptionResult,
        temperature=temperature,
    )

    return {
        "model": model,
        "image_path": str(Path(image_path).expanduser().resolve()),
        "observations": parsed.observations.model_dump(),
        "freeform_observations": parsed.freeform_observations,
        "context_used": _context_used(
            prompt=prompt,
            intent=intent,
            context=context,
            base_plate_path=base_plate_path,
            identity_refs=identity_refs,
            style_refs=style_refs,
        ),
    }


@mcp.tool()
def score_image(
    image_path: str,
    prompt: str,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    base_plate_path: Optional[str] = None,
    identity_refs: Optional[list[str]] = None,
    style_refs: Optional[list[str]] = None,
    criteria: Optional[list[str]] = None,
    model: str = "gemini-3.1-pro-preview",
    temperature: float = 0.3,
    system_prompt: Optional[str] = None,
) -> dict[str, Any]:
    """Calibrated scored evaluation against criteria. Gemini is the judge.

    Default criteria are the 6 dimensions from the global
    ``generation-review-loop`` skill (prompt_fidelity, preservation_fidelity,
    style_lock, scene_hierarchy, story_service, creative_brief_fidelity).
    Override via the ``criteria`` arg for non-Patrick workflows.

    Returns per-criterion ``{score, notes}``, a 1-2 sentence summary, and a
    ``decision_hint`` (advisory; agent has final say).

    Args:
        image_path: Absolute path to the image to score.
        prompt: The gen prompt that produced the image.
        intent: The creative brief — feeds creative_brief_fidelity dim.
        context: Per-call freeform notes — directs attention to the
            specific dim being checked this iteration.
        base_plate_path: Reference for preservation_fidelity dim.
        identity_refs: References for identity-carry checks within
            scene_hierarchy / creative_brief_fidelity dims.
        style_refs: References for style_lock dim.
        criteria: Override the default 6-dim list. Each entry becomes one
            evaluation in the response.
        model: Gemini model id. Default ``gemini-3.1-pro-preview``.
        temperature: 0..1. Default 0.3 — low for scoring consistency.
        system_prompt: Override the default scoring-mode system
            instruction. Rare.

    Raises:
        RuntimeError: ``IMAGE_NOT_FOUND``, ``API_KEY_MISSING``, or other
        coded errors from gemini_media helpers.
    """
    if not Path(image_path).expanduser().is_file():
        raise RuntimeError(f"IMAGE_NOT_FOUND: {image_path}")

    criteria_list = list(criteria) if criteria else list(prompts.SIX_DIMENSIONS)

    image_module = gemini_media.require_pillow()
    client, gtypes = gemini_media.init_client()

    system_instruction = system_prompt or prompts.score_image_system_prompt(criteria_list)
    contents = _build_image_contents(
        image_path=image_path,
        prompt=prompt,
        intent=intent,
        context=context,
        base_plate_path=base_plate_path,
        identity_refs=identity_refs,
        style_refs=style_refs,
        image_module=image_module,
    )

    parsed: schemas.ImageScoreResult = gemini_media.call_structured(
        client=client,
        gtypes=gtypes,
        model=model,
        system_instruction=system_instruction,
        contents=contents,
        response_schema=schemas.ImageScoreResult,
        temperature=temperature,
    )

    # Strict post-parse validation: Gemini's ``response_schema`` only enforces
    # the structural shape (list of {name, score, notes}); it can't constrain
    # which criterion names appear. Without this check, a model that drops a
    # requested dim or invents a new one passes silently and the
    # name-keyed dict below quietly omits or accepts bogus entries.
    parsed_names = [ev.name for ev in parsed.evaluations]
    expected = set(criteria_list)
    actual = set(parsed_names)
    if actual != expected or len(parsed_names) != len(actual):
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        duplicates = sorted({n for n in parsed_names if parsed_names.count(n) > 1})
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        if duplicates:
            details.append(f"duplicates={duplicates}")
        raise RuntimeError(
            f"SCHEMA_MISMATCH: Gemini returned criterion names that do not "
            f"match the request exactly. Expected: {sorted(expected)}. "
            + "; ".join(details)
        )

    # Transform list-of-evaluations to dict for caller convenience
    evaluations_dict = {
        ev.name: {"score": ev.score, "notes": ev.notes}
        for ev in parsed.evaluations
    }

    return {
        "model": model,
        "image_path": str(Path(image_path).expanduser().resolve()),
        "evaluations": evaluations_dict,
        "summary": parsed.summary,
        "decision_hint": parsed.decision_hint,
        "context_used": _context_used(
            prompt=prompt,
            intent=intent,
            context=context,
            base_plate_path=base_plate_path,
            identity_refs=identity_refs,
            style_refs=style_refs,
        ),
    }


def _build_video_contents(
    *,
    video_file: Any,
    video_mime: str,
    prompt: str,
    intent: Optional[str],
    context: Optional[str],
    base_plate_path: Optional[str],
    identity_refs: Optional[list[str]],
    style_refs: Optional[list[str]],
    fps: Optional[float],
    image_module: Any,
    gtypes: Any,
) -> list[Any]:
    """Build the Gemini multimodal ``contents`` list for video tools.

    Same structure as ``_build_image_contents`` but the target slot is a
    Files API video object passed via ``gtypes.FileData``. Reference images
    (base_plate / identity_refs / style_refs) are still passed inline as
    Pillow Images — Gemini handles mixed image+video contents lists.

    When ``fps`` is set, attaches ``VideoMetadata`` to the video Part so
    Gemini samples that many frames per second instead of its default (1).
    The ``start_offset='0s'`` is required by the SDK whenever ``fps`` is
    set — fps-only metadata fails to apply (ImgVidCaptioner's lesson).
    """
    parts: list[Any] = [
        prompts.context_block(prompt=prompt, intent=intent, context=context),
    ]
    if base_plate_path:
        parts.append(prompts.reference_label("base_plate") + ":")
        parts.append(gemini_media.load_image(base_plate_path, image_module=image_module))
    if identity_refs:
        for idx, ref in enumerate(identity_refs, start=1):
            parts.append(prompts.reference_label("identity_ref", idx) + ":")
            parts.append(gemini_media.load_image(ref, image_module=image_module))
    if style_refs:
        for idx, ref in enumerate(style_refs, start=1):
            parts.append(prompts.reference_label("style_ref", idx) + ":")
            parts.append(gemini_media.load_image(ref, image_module=image_module))
    parts.append("OUTPUT VIDEO to evaluate:")

    part_kwargs: dict[str, Any] = {
        "file_data": gtypes.FileData(file_uri=video_file.uri, mime_type=video_mime),
    }
    if fps is not None:
        # SDK accepts int when fps is whole-number; pass float otherwise.
        fps_value = int(fps) if fps == int(fps) else fps
        part_kwargs["video_metadata"] = gtypes.VideoMetadata(
            fps=fps_value, start_offset="0s"
        )
    parts.append(gtypes.Part(**part_kwargs))
    return parts


def _validate_fps(fps: Optional[float]) -> None:
    """Range-check fps (caller passes None when unspecified). Raises
    ``INVALID_INPUT`` for values outside (0, 24] or non-numeric inputs.

    Explicitly rejects ``bool`` because ``bool`` subclasses ``int`` in
    Python, and JSON/MCP clients can deliver ``True``/``False`` where a
    number is expected. ``isinstance(True, (int, float))`` is True, so
    without the explicit bool check ``True`` silently coerces to 1.0.
    """
    if fps is None:
        return
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or fps <= 0.0 or fps > 24.0:
        raise RuntimeError(
            f"INVALID_INPUT: fps must be a number in (0, 24] (got {fps!r})"
        )


@mcp.tool()
def describe_video(
    video_path: str,
    prompt: str,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    base_plate_path: Optional[str] = None,
    identity_refs: Optional[list[str]] = None,
    style_refs: Optional[list[str]] = None,
    fps: Optional[float] = None,
    model: str = "gemini-3.1-pro-preview",
    temperature: float = 0.3,
    system_prompt: Optional[str] = None,
    upload_timeout_s: int = 300,
) -> dict[str, Any]:
    """Rich structured observations of a video. No scoring, no verdict —
    Claude is the judge.

    Returns the eight image-shared observation categories plus four
    video-specific (motion_and_camera, pacing_and_timing, frame_continuity,
    audio_quality), an optional freeform field, and ``context_used`` echo.

    Video upload via Gemini's Files API — the file is uploaded, polled until
    ``state == ACTIVE``, used in the multimodal call, then deleted in a
    ``finally`` block. ``upload_timeout_s`` bounds the upload+process wait.

    Args:
        video_path: Absolute path to the video file (.mp4 / .mov / .webm).
        prompt: The gen prompt that produced the video.
        intent: The creative brief.
        context: Per-call freeform notes.
        base_plate_path: Optional reference frame the video was generated
            from. Useful for comparing how motion / continuity drift from
            the start frame.
        identity_refs: Optional list of character/asset reference paths.
        style_refs: Optional list of style anchor paths.
        model: Gemini model id. Default ``gemini-3.1-pro-preview``.
        temperature: 0..1. Default 0.3.
        system_prompt: Override the default observation-mode instruction.
        upload_timeout_s: How long to wait for Files API to mark the upload
            ACTIVE before raising ``VIDEO_PROCESSING_TIMEOUT``.

    Raises:
        RuntimeError: ``VIDEO_NOT_FOUND``, ``VIDEO_UPLOAD_FAILED``,
        ``VIDEO_PROCESSING_TIMEOUT``, ``VIDEO_PROCESSING_FAILED``,
        ``API_KEY_MISSING``, or ``NO_RESPONSE``.
    """
    if not Path(video_path).expanduser().is_file():
        raise RuntimeError(f"VIDEO_NOT_FOUND: {video_path}")
    _validate_fps(fps)

    image_module = gemini_media.require_pillow()
    client, gtypes = gemini_media.init_client()
    video_mime = gemini_media.video_mime_type(video_path)

    uploaded = gemini_media.upload_and_poll_video(
        client, video_path, timeout_s=upload_timeout_s
    )
    try:
        system_instruction = system_prompt or prompts.describe_video_system_prompt()
        contents = _build_video_contents(
            video_file=uploaded,
            video_mime=video_mime,
            prompt=prompt,
            intent=intent,
            context=context,
            base_plate_path=base_plate_path,
            identity_refs=identity_refs,
            style_refs=style_refs,
            fps=fps,
            image_module=image_module,
            gtypes=gtypes,
        )

        parsed: schemas.VideoDescriptionResult = gemini_media.call_structured(
            client=client,
            gtypes=gtypes,
            model=model,
            system_instruction=system_instruction,
            contents=contents,
            response_schema=schemas.VideoDescriptionResult,
            temperature=temperature,
        )
    finally:
        gemini_media.cleanup_uploaded(client, uploaded)

    return {
        "model": model,
        "video_path": str(Path(video_path).expanduser().resolve()),
        "fps": fps,
        "observations": parsed.observations.model_dump(),
        "freeform_observations": parsed.freeform_observations,
        "context_used": _context_used(
            prompt=prompt,
            intent=intent,
            context=context,
            base_plate_path=base_plate_path,
            identity_refs=identity_refs,
            style_refs=style_refs,
        ),
    }


@mcp.tool()
def score_video(
    video_path: str,
    prompt: str,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    base_plate_path: Optional[str] = None,
    identity_refs: Optional[list[str]] = None,
    style_refs: Optional[list[str]] = None,
    criteria: Optional[list[str]] = None,
    fps: Optional[float] = None,
    model: str = "gemini-3.1-pro-preview",
    temperature: float = 0.3,
    system_prompt: Optional[str] = None,
    upload_timeout_s: int = 300,
) -> dict[str, Any]:
    """Calibrated scored evaluation of a video against criteria.

    Same six default dimensions as ``score_image``, but with video-adapted
    prompt language per ``generation-review-loop`` SKILL.md §Generalizing
    to Video. Reuses ``ImageScoreResult`` schema — shape is identical to
    image scoring; only the system prompt and lifecycle differ.

    Args mirror ``describe_video`` plus ``criteria`` to override the default
    six-dim list. ``upload_timeout_s`` bounds the Files API upload wait.

    Raises:
        Same coded errors as ``describe_video`` plus ``SCHEMA_MISMATCH`` if
        Gemini returns criterion names that don't match the request.
    """
    if not Path(video_path).expanduser().is_file():
        raise RuntimeError(f"VIDEO_NOT_FOUND: {video_path}")
    _validate_fps(fps)

    criteria_list = list(criteria) if criteria else list(prompts.SIX_DIMENSIONS)

    image_module = gemini_media.require_pillow()
    client, gtypes = gemini_media.init_client()
    video_mime = gemini_media.video_mime_type(video_path)

    uploaded = gemini_media.upload_and_poll_video(
        client, video_path, timeout_s=upload_timeout_s
    )
    try:
        system_instruction = system_prompt or prompts.score_video_system_prompt(
            criteria_list
        )
        contents = _build_video_contents(
            video_file=uploaded,
            video_mime=video_mime,
            prompt=prompt,
            intent=intent,
            context=context,
            base_plate_path=base_plate_path,
            identity_refs=identity_refs,
            style_refs=style_refs,
            fps=fps,
            image_module=image_module,
            gtypes=gtypes,
        )

        parsed: schemas.ImageScoreResult = gemini_media.call_structured(
            client=client,
            gtypes=gtypes,
            model=model,
            system_instruction=system_instruction,
            contents=contents,
            response_schema=schemas.ImageScoreResult,
            temperature=temperature,
        )
    finally:
        gemini_media.cleanup_uploaded(client, uploaded)

    # Strict post-parse validation — same as score_image.
    parsed_names = [ev.name for ev in parsed.evaluations]
    expected = set(criteria_list)
    actual = set(parsed_names)
    if actual != expected or len(parsed_names) != len(actual):
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        duplicates = sorted({n for n in parsed_names if parsed_names.count(n) > 1})
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        if duplicates:
            details.append(f"duplicates={duplicates}")
        raise RuntimeError(
            f"SCHEMA_MISMATCH: Gemini returned criterion names that do not "
            f"match the request exactly. Expected: {sorted(expected)}. "
            + "; ".join(details)
        )

    evaluations_dict = {
        ev.name: {"score": ev.score, "notes": ev.notes}
        for ev in parsed.evaluations
    }

    return {
        "model": model,
        "video_path": str(Path(video_path).expanduser().resolve()),
        "fps": fps,
        "evaluations": evaluations_dict,
        "summary": parsed.summary,
        "decision_hint": parsed.decision_hint,
        "context_used": _context_used(
            prompt=prompt,
            intent=intent,
            context=context,
            base_plate_path=base_plate_path,
            identity_refs=identity_refs,
            style_refs=style_refs,
        ),
    }


@mcp.tool()
def compare_images(
    image_paths: list[str],
    prompt: str,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    criteria: Optional[list[str]] = None,
    model: str = "gemini-3.1-pro-preview",
    temperature: float = 0.3,
    system_prompt: Optional[str] = None,
) -> dict[str, Any]:
    """Pick the best of N candidate images against a brief.

    Returns a comparison narrative + the chosen candidate (resolved to its
    1-indexed position and absolute path) + a 1-2 sentence reasoning string.

    Default criteria are the 6 dimensions from ``generation-review-loop``;
    override for non-Patrick workflows.

    **Calibration caveat.** Cross-image grounding is harder than single-image
    analysis — the model can correctly *pick* the better candidate while
    misattributing which image holds which specific detail in the
    ``comparison`` text. The ``pick.best_index`` is the most reliable field;
    the ``comparison`` and ``reasoning`` strings should be read as
    directional, not authoritative on sub-image specifics. For
    detail-sensitive cross-image reasoning, prefer ``describe_image`` on
    each candidate separately and let the agent reason across the
    descriptions.

    Args:
        image_paths: List of 2+ candidate image paths. Order matters —
            ``best_index`` in the return is 1-indexed against this list.
        prompt: The shared gen prompt that produced these candidates (or
            empty if the candidates came from different prompts).
        intent: The creative brief — what the candidates are competing to
            satisfy.
        context: Per-call freeform notes — what specifically you want the
            comparison to weight (e.g., "I'm only worried about style_lock
            this round; ignore composition variance").
        criteria: Override the default 6-dim list.
        model: Gemini model id.
        temperature: 0..1. Default 0.3 — low for selection consistency.
        system_prompt: Override the default comparison-mode instruction.

    Raises:
        RuntimeError: ``INVALID_INPUT`` (fewer than 2 candidates),
        ``IMAGE_NOT_FOUND``, ``API_KEY_MISSING``, or other coded errors.
        ``SCHEMA_MISMATCH`` if Gemini returns a ``best_index`` outside the
        valid range [1, len(image_paths)].
    """
    if not isinstance(image_paths, list) or len(image_paths) < 2:
        raise RuntimeError(
            f"INVALID_INPUT: compare_images needs at least 2 candidates "
            f"(got {len(image_paths) if isinstance(image_paths, list) else type(image_paths).__name__})"
        )
    for path_str in image_paths:
        if not Path(path_str).expanduser().is_file():
            raise RuntimeError(f"IMAGE_NOT_FOUND: {path_str}")

    criteria_list = list(criteria) if criteria else list(prompts.SIX_DIMENSIONS)

    image_module = gemini_media.require_pillow()
    client, gtypes = gemini_media.init_client()

    system_instruction = system_prompt or prompts.compare_images_system_prompt(
        criteria_list
    )

    # Build the multimodal contents: context block, then numbered candidates.
    contents: list[Any] = [
        prompts.context_block(prompt=prompt, intent=intent, context=context),
    ]
    resolved_paths: list[str] = []
    for idx, path_str in enumerate(image_paths, start=1):
        resolved = str(Path(path_str).expanduser().resolve())
        resolved_paths.append(resolved)
        contents.append(f"Candidate Image {idx}:")
        contents.append(gemini_media.load_image(path_str, image_module=image_module))

    parsed: schemas.ImageComparisonResult = gemini_media.call_structured(
        client=client,
        gtypes=gtypes,
        model=model,
        system_instruction=system_instruction,
        contents=contents,
        response_schema=schemas.ImageComparisonResult,
        temperature=temperature,
    )

    if parsed.best_index < 1 or parsed.best_index > len(image_paths):
        raise RuntimeError(
            f"SCHEMA_MISMATCH: best_index={parsed.best_index} is outside the "
            f"valid range [1, {len(image_paths)}]"
        )

    return {
        "model": model,
        "image_paths": resolved_paths,
        "comparison": parsed.comparison,
        "pick": {
            "best_index": parsed.best_index,
            "best_path": resolved_paths[parsed.best_index - 1],
            "reasoning": parsed.reasoning,
        },
        "context_used": {
            "prompt": prompt,
            "intent": intent,
            "context": context,
            "criteria": criteria_list,
        },
    }


@mcp.tool()
def extract_visual_tokens(
    image_path: str,
    categories: Optional[list[str]] = None,
    intent: Optional[str] = None,
    model: str = "gemini-3-flash-preview",
    temperature: float = 0.3,
    system_prompt: Optional[str] = None,
) -> dict[str, Any]:
    """Deconstruct an image into reusable prompt tokens by category.

    Default categories are the env-coverage workflow's five (lighting,
    atmosphere, palette, materials, spatial_grammar) per
    ``vault_gml/CLAUDE.md:164``. Output is short token phrases per category
    (1–3 words each) — concrete visual vocabulary another genesis prompt
    can paste in verbatim.

    Defaults to Flash because this is the cheap, descriptive lane —
    extraction is straightforward enough that Pro reasoning isn't needed.

    Args:
        image_path: Absolute path to the image to deconstruct.
        categories: Override the default 5-category list.
        intent: Optional brief — focuses the extraction (e.g., "I'm only
            interested in tokens relevant to teal-orange grade and
            anamorphic optics, skip color-palette specifics").
        model: Gemini model id. Default ``gemini-3-flash-preview``.
        temperature: 0..1. Default 0.3.
        system_prompt: Override the default extraction instruction.

    Raises:
        RuntimeError: ``IMAGE_NOT_FOUND``, ``API_KEY_MISSING``,
        ``SCHEMA_MISMATCH`` (Gemini returned categories that don't match
        the request), or other coded errors.
    """
    if not Path(image_path).expanduser().is_file():
        raise RuntimeError(f"IMAGE_NOT_FOUND: {image_path}")

    categories_list = list(categories) if categories else list(prompts.TOKEN_CATEGORIES)

    image_module = gemini_media.require_pillow()
    client, gtypes = gemini_media.init_client()

    system_instruction = system_prompt or prompts.extract_visual_tokens_system_prompt(
        categories_list
    )

    contents: list[Any] = []
    if intent and intent.strip():
        contents.append(f"Brief: {intent.strip()}")
    contents.append("Image to deconstruct:")
    contents.append(gemini_media.load_image(image_path, image_module=image_module))

    parsed: schemas.VisualTokensResult = gemini_media.call_structured(
        client=client,
        gtypes=gtypes,
        model=model,
        system_instruction=system_instruction,
        contents=contents,
        response_schema=schemas.VisualTokensResult,
        temperature=temperature,
    )

    # Strict post-parse validation — same approach as score_image's criterion check.
    parsed_names = [cat.category for cat in parsed.categories]
    expected = set(categories_list)
    actual = set(parsed_names)
    if actual != expected or len(parsed_names) != len(actual):
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        duplicates = sorted({n for n in parsed_names if parsed_names.count(n) > 1})
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        if duplicates:
            details.append(f"duplicates={duplicates}")
        raise RuntimeError(
            f"SCHEMA_MISMATCH: Gemini returned category names that do not "
            f"match the request exactly. Expected: {sorted(expected)}. "
            + "; ".join(details)
        )

    tokens_dict = {cat.category: list(cat.tokens) for cat in parsed.categories}

    return {
        "model": model,
        "image_path": str(Path(image_path).expanduser().resolve()),
        "tokens": tokens_dict,
        "context_used": {
            "intent": intent,
            "categories": categories_list,
        },
    }


@mcp.tool()
def extract_video_frames(
    video_path: str,
    timestamps: list[ffmpeg_utils.Timestamp],
    out_dir: Optional[str] = None,
    title_prefix: Optional[str] = None,
) -> dict[str, Any]:
    """Extract one PNG frame per timestamp via ffmpeg.

    Frame-accurate seek (``-ss`` after ``-i``) — slower than fast seek but
    lands on the exact target frame, which matters for cut-detection
    workflows where frames are sampled at sub-second resolution.

    Args:
        video_path: Absolute path to the video file.
        timestamps: List of seconds (``5.5``) or HH:MM:SS / MM:SS strings
            (``"00:00:05.500"``). Order is preserved in the returned list.
        out_dir: Default ``<video_dir>/frames/``.
        title_prefix: Default ``<video_basename_without_ext>``.

    Returns:
        ``{video_path, frame_count, frames: [{timestamp_s, path, width,
        height}]}`` with one entry per input timestamp.

    Raises:
        RuntimeError: ``VIDEO_NOT_FOUND``, ``FFMPEG_NOT_INSTALLED``,
        ``FFMPEG_FAILED`` (e.g., timestamp past the video end), or
        ``INVALID_INPUT`` (malformed timestamp).
    """
    video = Path(video_path).expanduser().resolve()
    if not video.is_file():
        raise RuntimeError(f"VIDEO_NOT_FOUND: {video}")

    resolved_out_dir = (
        Path(out_dir).expanduser().resolve()
        if out_dir
        else (video.parent / "frames").resolve()
    )
    resolved_prefix = title_prefix if title_prefix else video.stem

    image_module = gemini_media.require_pillow()

    frames = ffmpeg_utils.extract_frames(
        video_path=video,
        timestamps=timestamps,
        out_dir=resolved_out_dir,
        title_prefix=resolved_prefix,
        image_module=image_module,
    )

    return {
        "video_path": str(video),
        "frame_count": len(frames),
        "frames": frames,
    }


def main() -> None:
    """Console-script entry point — runs the FastMCP server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
