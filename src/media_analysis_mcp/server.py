"""FastMCP server exposing media analysis tools.

Tools (per MCP_DESIGN.md sequencing):
- describe_image, score_image     — Step 5 (this file)
- describe_video, score_video     — Step 6
- analyze_audio                   — free-form audio Q&A / transcription
- extract_video_frames            — Step 7
- compare_images,                 — Step 8
  extract_visual_tokens

The describe / score split exists to A/B which judgment locus produces
better real-world iteration. ``describe_*`` returns observation-only output
that Claude reads against the brief; ``score_*`` returns Gemini's own
calibrated 0-100 verdict per criterion. Same backend, different system
prompt + response schema.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

from mcp.server.fastmcp import FastMCP

from . import ffmpeg_utils, gemini_media, prompts, schemas


mcp = FastMCP("media-analysis-mcp")
DEFAULT_ANALYSIS_MODEL = "gemini-3.8-flash"
DEFAULT_MULTI_IMAGE_THINKING_LEVEL = "high"
DEFAULT_MULTI_IMAGE_MAX_OUTPUT_TOKENS = 65_536
DEFAULT_VIDEO_ANALYSIS_MODEL = DEFAULT_ANALYSIS_MODEL
DEFAULT_VIDEO_THINKING_LEVEL = "high"
DEFAULT_VIDEO_MAX_OUTPUT_TOKENS = 65_536
DEFAULT_AUDIO_ANALYSIS_MODEL = DEFAULT_ANALYSIS_MODEL


def _build_image_contents(
    *,
    image_path: str,
    prompt: Optional[str],
    intent: Optional[str],
    context: Optional[str],
    base_plate_path: Optional[str],
    identity_refs: Optional[list[str]],
    style_refs: Optional[list[str]],
    image_module: Any,
    question: Optional[str] = None,
) -> list[Any]:
    """Build the Gemini multimodal ``contents`` list — text labels interleaved
    with Pillow images. Order: context block → base plate → identity refs →
    style refs → target image. Each reference is preceded by a labeled string
    so the model knows the role of each image.

    When ``question`` is set (analyze_image), it anchors the context block;
    ``prompt`` becomes optional supporting context rather than required.
    """
    parts: list[Any] = [
        prompts.context_block(
            prompt=prompt, intent=intent, context=context, question=question
        ),
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
    prompt: Optional[str],
    intent: Optional[str],
    context: Optional[str],
    base_plate_path: Optional[str],
    identity_refs: Optional[list[str]],
    style_refs: Optional[list[str]],
    question: Optional[str] = None,
) -> dict[str, Any]:
    """Echo the inputs in the return so the agent can audit what the eval
    had to work with. ``question`` is included only when set (analyze_*)."""
    out: dict[str, Any] = {
        "prompt": prompt,
        "intent": intent,
        "context": context,
        "base_plate_path": base_plate_path,
        "identity_refs": list(identity_refs) if identity_refs else [],
        "style_refs": list(style_refs) if style_refs else [],
    }
    if question is not None:
        out["question"] = question
    return out


def _normalize_question(question: str) -> str:
    """Return a stripped analyze_* question or raise a coded input error."""
    if not isinstance(question, str) or not question.strip():
        raise RuntimeError("INVALID_INPUT: question is required.")
    return question.strip()


def _normalize_image_inputs(
    image_paths: list[str], labels: Optional[list[str]]
) -> tuple[list[str], list[str]]:
    """Validate and resolve an ordered, equal-role multi-image request."""
    if not isinstance(image_paths, list) or not 2 <= len(image_paths) <= 10:
        count = (
            len(image_paths)
            if isinstance(image_paths, list)
            else type(image_paths).__name__
        )
        raise RuntimeError(
            f"INVALID_INPUT: analyze_images needs 2 to 10 image paths (got {count})"
        )

    resolved_paths: list[str] = []
    for path_str in image_paths:
        if not isinstance(path_str, str) or not path_str.strip():
            raise RuntimeError(
                "INVALID_INPUT: every analyze_images path must be a non-empty string"
            )
        path = Path(path_str).expanduser()
        if not path.is_file():
            raise RuntimeError(f"IMAGE_NOT_FOUND: {path_str}")
        resolved_paths.append(str(path.resolve()))

    if len(set(resolved_paths)) != len(resolved_paths):
        raise RuntimeError("INVALID_INPUT: analyze_images image paths must be distinct")

    if labels is None:
        normalized_labels = [Path(path).name for path in resolved_paths]
    else:
        if not isinstance(labels, list) or len(labels) != len(resolved_paths):
            count = len(labels) if isinstance(labels, list) else type(labels).__name__
            raise RuntimeError(
                "INVALID_INPUT: labels must contain exactly one entry per image "
                f"(got {count} labels for {len(resolved_paths)} images)"
            )
        normalized_labels = []
        for label in labels:
            if not isinstance(label, str) or not label.strip():
                raise RuntimeError(
                    "INVALID_INPUT: every analyze_images label must be a non-empty string"
                )
            normalized_labels.append(label.strip())

    return resolved_paths, normalized_labels


def _build_multi_image_contents(
    *,
    image_paths: list[str],
    image_labels: list[str],
    prompt: Optional[str],
    intent: Optional[str],
    context: Optional[str],
    image_module: Any,
    question: str,
) -> list[Any]:
    """Build one ordered, explicitly labeled, equal-role image request."""
    parts: list[Any] = [
        prompts.context_block(
            prompt=prompt, intent=intent, context=context, question=question
        ),
    ]
    for idx, (path, label) in enumerate(
        zip(image_paths, image_labels, strict=True), start=1
    ):
        parts.append(f"IMAGE {idx} — {label}:")
        parts.append(gemini_media.load_image(path, image_module=image_module))

    parts.append(
        "Answer the Question above by referring to evidence with its exact "
        "IMAGE number and label. Treat every image as an equal-role source. "
        "Do not choose a winner unless the Question explicitly asks you to."
    )
    return parts


@mcp.tool()
def describe_image(
    image_path: str,
    prompt: str,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    base_plate_path: Optional[str] = None,
    identity_refs: Optional[list[str]] = None,
    style_refs: Optional[list[str]] = None,
    model: str = DEFAULT_ANALYSIS_MODEL,
    temperature: Optional[float] = None,
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
        model: Gemini model id. Default ``gemini-3.8-flash``.
        temperature: Optional sampling override; omitted by default.
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
    model: str = DEFAULT_ANALYSIS_MODEL,
    temperature: Optional[float] = None,
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
        model: Gemini model id. Default ``gemini-3.8-flash``.
        temperature: Optional sampling override; omitted by default.
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


@mcp.tool()
def analyze_image(
    image_path: str,
    question: str,
    prompt: Optional[str] = None,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    base_plate_path: Optional[str] = None,
    identity_refs: Optional[list[str]] = None,
    style_refs: Optional[list[str]] = None,
    model: str = DEFAULT_ANALYSIS_MODEL,
    temperature: Optional[float] = None,
    system_prompt: Optional[str] = None,
    thinking_level: Optional[
        Literal["minimal", "low", "medium", "high"]
    ] = None,
) -> dict[str, Any]:
    """Free-form image analysis. Same multimodal plumbing as describe_image,
    but no response schema — Gemini answers ``question`` in prose.

    Use when describe_image's 8-category taxonomy is too rigid or off-axis
    for what you actually want to know. For repeatable structured
    observation across iterations, prefer describe_image.

    Args:
        image_path: Absolute path to the image to analyze.
        question: The agent's question for Gemini to answer.
        prompt: Optional gen prompt that produced this image.
        intent: Optional creative brief.
        context: Optional per-call notes.
        base_plate_path: Optional reference image (source plate).
        identity_refs: Optional character/asset reference paths.
        style_refs: Optional style anchor paths.
        model: Gemini model id. Default ``gemini-3.8-flash``.
        temperature: Optional sampling override; omitted by default.
        system_prompt: Override the default analyze-mode instruction.
        thinking_level: Gemini reasoning depth. Omitted by default, so the
            model's own default applies. Thinking tokens are billed as
            output and are usually most of a call's cost.

    Returns:
        The model, resolved image path, question, free-form answer, an echo
        of the context, and ``usage``: the token counts Gemini reported
        (``None`` when it reported none). ``usage`` carries no prices.

    Raises:
        RuntimeError: ``IMAGE_NOT_FOUND``, ``API_KEY_MISSING``,
        ``NO_RESPONSE``, or other coded errors.
    """
    question_text = _normalize_question(question)
    if not Path(image_path).expanduser().is_file():
        raise RuntimeError(f"IMAGE_NOT_FOUND: {image_path}")

    image_module = gemini_media.require_pillow()
    client, gtypes = gemini_media.init_client()

    system_instruction = system_prompt or prompts.analyze_image_system_prompt()
    contents = _build_image_contents(
        image_path=image_path,
        prompt=prompt,
        intent=intent,
        context=context,
        base_plate_path=base_plate_path,
        identity_refs=identity_refs,
        style_refs=style_refs,
        image_module=image_module,
        question=question_text,
    )

    usage: dict[str, Any] = {}
    answer = gemini_media.call_unstructured(
        client=client,
        gtypes=gtypes,
        model=model,
        system_instruction=system_instruction,
        contents=contents,
        temperature=temperature,
        thinking_level=thinking_level,
        usage_out=usage,
    )

    return {
        "model": model,
        "image_path": str(Path(image_path).expanduser().resolve()),
        "question": question_text,
        "answer": answer,
        "thinking_level": thinking_level,
        "usage": usage or None,
        "context_used": _context_used(
            prompt=prompt,
            intent=intent,
            context=context,
            base_plate_path=base_plate_path,
            identity_refs=identity_refs,
            style_refs=style_refs,
            question=question_text,
        ),
    }


@mcp.tool()
def analyze_images(
    image_paths: list[str],
    question: str,
    labels: Optional[list[str]] = None,
    prompt: Optional[str] = None,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    model: str = DEFAULT_ANALYSIS_MODEL,
    temperature: Optional[float] = None,
    system_prompt: Optional[str] = None,
    thinking_level: Optional[
        Literal["minimal", "low", "medium", "high"]
    ] = DEFAULT_MULTI_IMAGE_THINKING_LEVEL,
    max_output_tokens: Optional[int] = DEFAULT_MULTI_IMAGE_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Ask one open-ended question across two to ten equal-role images.

    Every image is inserted into one Gemini request behind an explicit
    ``IMAGE N — label`` marker. Unlike ``compare_images``, this tool has no
    response schema, candidate roles, criteria, forced ranking, or winner.
    It is intended for collection reading, pattern discovery, mode and
    counterexample analysis, and other questions that depend on jointly
    seeing several images.

    Args:
        image_paths: Ordered list of two to ten distinct local image paths.
        question: Cross-image question for Gemini to answer in prose.
        labels: Optional ordered labels. Defaults to each file's basename.
        prompt: Optional shared generation prompt or source note.
        intent: Optional shared creative brief.
        context: Optional per-call notes.
        model: Gemini model id. Default ``gemini-3.8-flash``.
        temperature: Optional sampling override.
        system_prompt: Override the neutral multi-image instruction.
        thinking_level: Gemini reasoning depth. Defaults to ``high``. Pass
            ``None`` to use the model/API default.
        max_output_tokens: Maximum output budget. Defaults to 65,536. Pass
            ``None`` to use the model/API default.

    Returns:
        Resolved ordered paths and labels, reasoning controls, the original
        question, a free-form answer, an echo of shared context, and
        ``usage``: the token counts Gemini reported (``None`` when it
        reported none; no prices applied).

    Raises:
        RuntimeError: ``INVALID_INPUT`` for list/label errors,
        ``IMAGE_NOT_FOUND`` for a missing source, ``API_KEY_MISSING``,
        ``NO_RESPONSE``, or other provider errors.
    """
    question_text = _normalize_question(question)
    resolved_paths, normalized_labels = _normalize_image_inputs(image_paths, labels)

    image_module = gemini_media.require_pillow()
    client, gtypes = gemini_media.init_client()
    system_instruction = system_prompt or prompts.analyze_images_system_prompt()
    contents = _build_multi_image_contents(
        image_paths=resolved_paths,
        image_labels=normalized_labels,
        prompt=prompt,
        intent=intent,
        context=context,
        image_module=image_module,
        question=question_text,
    )
    usage: dict[str, Any] = {}
    answer = gemini_media.call_unstructured(
        client=client,
        gtypes=gtypes,
        model=model,
        system_instruction=system_instruction,
        contents=contents,
        temperature=temperature,
        thinking_level=thinking_level,
        max_output_tokens=max_output_tokens,
        usage_out=usage,
    )

    return {
        "model": model,
        "image_paths": resolved_paths,
        "image_labels": normalized_labels,
        "thinking_level": thinking_level,
        "max_output_tokens": max_output_tokens,
        "question": question_text,
        "answer": answer,
        "usage": usage or None,
        "context_used": {
            "prompt": prompt,
            "intent": intent,
            "context": context,
            "question": question_text,
            "image_labels": normalized_labels,
        },
    }


def _build_video_contents(
    *,
    video_file: Any,
    video_mime: str,
    prompt: Optional[str],
    intent: Optional[str],
    context: Optional[str],
    base_plate_path: Optional[str],
    identity_refs: Optional[list[str]],
    style_refs: Optional[list[str]],
    fps: Optional[float],
    image_module: Any,
    gtypes: Any,
    question: Optional[str] = None,
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
        prompts.context_block(
            prompt=prompt, intent=intent, context=context, question=question
        ),
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


def _normalize_video_inputs(
    video_paths: list[str], labels: Optional[list[str]]
) -> tuple[list[str], list[str]]:
    """Validate and resolve an ordered multi-video request.

    Gemini 2.5+ accepts at most ten videos in one request. Requiring two here
    keeps ``analyze_videos`` semantically distinct from ``analyze_video``.
    """
    if not isinstance(video_paths, list) or not 2 <= len(video_paths) <= 10:
        count = len(video_paths) if isinstance(video_paths, list) else type(video_paths).__name__
        raise RuntimeError(
            "INVALID_INPUT: analyze_videos needs 2 to 10 video paths "
            f"(got {count})"
        )

    resolved_paths: list[str] = []
    for path_str in video_paths:
        if not isinstance(path_str, str) or not path_str.strip():
            raise RuntimeError(
                "INVALID_INPUT: every analyze_videos path must be a non-empty string"
            )
        path = Path(path_str).expanduser()
        if not path.is_file():
            raise RuntimeError(f"VIDEO_NOT_FOUND: {path_str}")
        resolved_paths.append(str(path.resolve()))

    if len(set(resolved_paths)) != len(resolved_paths):
        raise RuntimeError(
            "INVALID_INPUT: analyze_videos video paths must be distinct"
        )

    if labels is None:
        normalized_labels = [Path(path).name for path in resolved_paths]
    else:
        if not isinstance(labels, list) or len(labels) != len(resolved_paths):
            count = len(labels) if isinstance(labels, list) else type(labels).__name__
            raise RuntimeError(
                "INVALID_INPUT: labels must contain exactly one entry per video "
                f"(got {count} labels for {len(resolved_paths)} videos)"
            )
        normalized_labels = []
        for label in labels:
            if not isinstance(label, str) or not label.strip():
                raise RuntimeError(
                    "INVALID_INPUT: every analyze_videos label must be a non-empty string"
                )
            normalized_labels.append(label.strip())

    return resolved_paths, normalized_labels


def _build_multi_video_contents(
    *,
    video_files: list[Any],
    video_mimes: list[str],
    video_labels: list[str],
    prompt: Optional[str],
    intent: Optional[str],
    context: Optional[str],
    base_plate_path: Optional[str],
    identity_refs: Optional[list[str]],
    style_refs: Optional[list[str]],
    fps: Optional[float],
    image_module: Any,
    gtypes: Any,
    question: str,
) -> list[Any]:
    """Build one ordered, explicitly labeled multi-video Gemini request."""
    parts: list[Any] = [
        prompts.context_block(
            prompt=prompt, intent=intent, context=context, question=question
        ),
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

    for idx, (video_file, video_mime, label) in enumerate(
        zip(video_files, video_mimes, video_labels, strict=True), start=1
    ):
        parts.append(f"VIDEO {idx} — {label}:")
        part_kwargs: dict[str, Any] = {
            "file_data": gtypes.FileData(
                file_uri=video_file.uri, mime_type=video_mime
            ),
        }
        if fps is not None:
            fps_value = int(fps) if fps == int(fps) else fps
            part_kwargs["video_metadata"] = gtypes.VideoMetadata(
                fps=fps_value, start_offset="0s"
            )
        parts.append(gtypes.Part(**part_kwargs))

    parts.append(
        "Answer the Question above by referring to each source with its exact "
        "VIDEO number and label. Keep observations grounded to the correct file."
    )
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


def _build_audio_contents(
    *,
    audio_file: Any,
    audio_mime: str,
    prompt: Optional[str],
    intent: Optional[str],
    context: Optional[str],
    question: str,
    gtypes: Any,
) -> list[Any]:
    """Build a free-form Gemini request around one uploaded audio file.

    Unlike ``_build_video_contents``, this intentionally attaches no
    ``VideoMetadata`` and uses the Files API's detected audio MIME type.
    """
    return [
        prompts.context_block(
            prompt=prompt, intent=intent, context=context, question=question
        ),
        "AUDIO RECORDING to analyze:",
        gtypes.Part(
            file_data=gtypes.FileData(
                file_uri=audio_file.uri,
                mime_type=audio_mime,
            )
        ),
    ]


@mcp.tool()
def analyze_audio(
    audio_path: str,
    question: str,
    prompt: Optional[str] = None,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    model: str = DEFAULT_AUDIO_ANALYSIS_MODEL,
    temperature: Optional[float] = None,
    system_prompt: Optional[str] = None,
    upload_timeout_s: int = 300,
) -> dict[str, Any]:
    """Free-form audio analysis and transcription with Gemini.

    The file is uploaded through Gemini's Files API, polled until ACTIVE,
    analyzed, and deleted in a ``finally`` block. The Files API's detected
    MIME type is passed through unchanged, which makes this safe for M4A/AAC
    recordings that cannot be routed through ``analyze_video``.

    Args:
        audio_path: Absolute path to a local audio file.
        question: The analysis or transcription request.
        prompt: Optional provenance or production prompt.
        intent: Optional creative or analytical brief.
        context: Optional per-call notes.
        model: Gemini model id. Default ``gemini-3.8-flash``.
        temperature: Optional sampling override; omitted by default.
        system_prompt: Optional audio-analysis system instruction override.
        upload_timeout_s: Bounds the Files API upload+processing wait.

    Raises:
        RuntimeError: ``AUDIO_NOT_FOUND``, ``AUDIO_UPLOAD_FAILED``,
        ``AUDIO_PROCESSING_TIMEOUT``, ``AUDIO_PROCESSING_FAILED``,
        ``AUDIO_MIME_MISMATCH``, ``API_KEY_MISSING``, ``INVALID_INPUT``,
        or ``NO_RESPONSE``.
    """
    question_text = _normalize_question(question)
    path = Path(audio_path).expanduser()
    if not path.is_file():
        raise RuntimeError(f"AUDIO_NOT_FOUND: {audio_path}")

    client, gtypes = gemini_media.init_client()
    uploaded = gemini_media.upload_and_poll_audio(
        client, audio_path, timeout_s=upload_timeout_s
    )
    try:
        audio_mime = getattr(uploaded, "mime_type", None)
        if not audio_mime or not str(audio_mime).startswith("audio/"):
            raise RuntimeError(
                "AUDIO_MIME_MISMATCH: Gemini Files API returned "
                f"{audio_mime!r} for {audio_path}"
            )
        contents = _build_audio_contents(
            audio_file=uploaded,
            audio_mime=str(audio_mime),
            prompt=prompt,
            intent=intent,
            context=context,
            question=question_text,
            gtypes=gtypes,
        )
        answer = gemini_media.call_unstructured(
            client=client,
            gtypes=gtypes,
            model=model,
            system_instruction=(
                system_prompt or prompts.analyze_audio_system_prompt()
            ),
            contents=contents,
            temperature=temperature,
        )
    finally:
        gemini_media.cleanup_uploaded(client, uploaded)

    return {
        "model": model,
        "audio_path": str(path.resolve()),
        "detected_mime_type": str(audio_mime),
        "question": question_text,
        "answer": answer,
        "context_used": {
            "prompt": prompt,
            "intent": intent,
            "context": context,
            "question": question_text,
        },
    }


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
    model: str = DEFAULT_VIDEO_ANALYSIS_MODEL,
    temperature: Optional[float] = None,
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
        model: Gemini model id. Default ``gemini-3.8-flash``.
        temperature: Optional sampling override; omitted by default.
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
    model: str = DEFAULT_VIDEO_ANALYSIS_MODEL,
    temperature: Optional[float] = None,
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
def analyze_video(
    video_path: str,
    question: str,
    prompt: Optional[str] = None,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    base_plate_path: Optional[str] = None,
    identity_refs: Optional[list[str]] = None,
    style_refs: Optional[list[str]] = None,
    fps: Optional[float] = None,
    model: str = DEFAULT_VIDEO_ANALYSIS_MODEL,
    temperature: Optional[float] = None,
    system_prompt: Optional[str] = None,
    upload_timeout_s: int = 300,
    thinking_level: Optional[
        Literal["minimal", "low", "medium", "high"]
    ] = DEFAULT_VIDEO_THINKING_LEVEL,
    max_output_tokens: Optional[int] = DEFAULT_VIDEO_MAX_OUTPUT_TOKENS,
    processing: Literal["static", "agentic"] = "static",
) -> dict[str, Any]:
    """Free-form video analysis. Same Files API plumbing as describe_video,
    but no response schema — Gemini answers ``question`` in prose.

    Use when describe_video's 12-category taxonomy is too rigid or off-axis
    for what you actually want to know (e.g., "how does the camera move
    in the second half?", "is the boy on the right's posture stable?").
    For repeatable structured observation, prefer describe_video.

    Args:
        video_path: Absolute path to the video file (.mp4 / .mov / .webm).
        question: The agent's question for Gemini to answer.
        prompt: Optional gen prompt that produced this video.
        intent: Optional creative brief.
        context: Optional per-call notes.
        base_plate_path: Optional reference frame.
        identity_refs: Optional character/asset reference paths.
        style_refs: Optional style anchor paths.
        fps: Sampling rate Gemini uses when reading the video. Default
            None uses static sampling at the API default (1 fps).
        processing: ``static`` preserves the existing request path. ``agentic``
            uses Interactions for adaptive inspection; omit fps. Supported by
            Gemini 3.8/3.7/3.6 Flash and 3.5 Flash Lite. Returns processing_trace,
            agentic_processing_verified (matched call/result evidence), and full
            interaction metadata. Save the returned result to retain the trace.
            This is a synchronous, single-turn API call; no automatic fallback.
        model: Gemini model id. Default ``gemini-3.8-flash``.
        temperature: Optional sampling override; omitted by default.
        system_prompt: Override the default analyze-mode instruction.
        upload_timeout_s: Bounds the Files API upload+process wait.
        thinking_level: Gemini reasoning depth. Defaults to ``high``. Pass
            ``None`` to use the model/API default.
        max_output_tokens: Maximum output budget; thinking can consume this
            budget on thinking models. Defaults to 65,536. Pass ``None`` to
            use the model/API default.

    Raises:
        RuntimeError: ``VIDEO_NOT_FOUND``, ``VIDEO_UPLOAD_FAILED``,
        ``VIDEO_PROCESSING_TIMEOUT``, ``VIDEO_PROCESSING_FAILED``,
        ``API_KEY_MISSING``, ``INVALID_INPUT``, or ``NO_RESPONSE``.
    """
    question_text = _normalize_question(question)
    if not Path(video_path).expanduser().is_file():
        raise RuntimeError(f"VIDEO_NOT_FOUND: {video_path}")
    _validate_fps(fps)
    gemini_media.validate_video_processing(processing, fps)
    processing_result: dict[str, Any] = {}

    image_module = gemini_media.require_pillow()
    client, gtypes = gemini_media.init_client()
    video_mime = gemini_media.video_mime_type(video_path)

    uploaded = gemini_media.upload_and_poll_video(
        client, video_path, timeout_s=upload_timeout_s
    )
    try:
        system_instruction = system_prompt or prompts.analyze_video_system_prompt()
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
            question=question_text,
        )

        call_kwargs = dict(
            client=client,
            model=model,
            system_instruction=system_instruction,
            contents=contents,
            temperature=temperature,
            thinking_level=thinking_level,
            max_output_tokens=max_output_tokens,
        )
        if processing == "agentic":
            processing_result = gemini_media.call_agentic_video(**call_kwargs)
            answer = processing_result["answer"]
        else:
            answer = gemini_media.call_unstructured(gtypes=gtypes, **call_kwargs)
    finally:
        gemini_media.cleanup_uploaded(client, uploaded)

    return {
        "model": model,
        "video_path": str(Path(video_path).expanduser().resolve()),
        "fps": fps,
        "processing": processing,
        **processing_result,
        "thinking_level": thinking_level,
        "max_output_tokens": max_output_tokens,
        "question": question_text,
        "answer": answer,
        "context_used": _context_used(
            prompt=prompt,
            intent=intent,
            context=context,
            base_plate_path=base_plate_path,
            identity_refs=identity_refs,
            style_refs=style_refs,
            question=question_text,
        ),
    }


@mcp.tool()
def analyze_videos(
    video_paths: list[str],
    question: str,
    labels: Optional[list[str]] = None,
    prompt: Optional[str] = None,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    base_plate_path: Optional[str] = None,
    identity_refs: Optional[list[str]] = None,
    style_refs: Optional[list[str]] = None,
    fps: Optional[float] = None,
    model: str = DEFAULT_VIDEO_ANALYSIS_MODEL,
    temperature: Optional[float] = None,
    system_prompt: Optional[str] = None,
    upload_timeout_s: int = 300,
    thinking_level: Optional[
        Literal["minimal", "low", "medium", "high"]
    ] = DEFAULT_VIDEO_THINKING_LEVEL,
    max_output_tokens: Optional[int] = DEFAULT_VIDEO_MAX_OUTPUT_TOKENS,
    processing: Literal["static", "agentic"] = "static",
) -> dict[str, Any]:
    """Ask one grounded question across two to ten ordered videos.

    Each video is uploaded through Gemini's Files API and inserted into one
    multimodal request behind an explicit ``VIDEO N — label`` marker. This is
    intended for edit comparisons, continuity checks, candidate ranking, and
    other questions whose answer depends on seeing multiple clips together.
    ``analyze_video`` remains the lower-cost choice for one source.

    Args:
        video_paths: Ordered list of two to ten distinct local video paths.
        question: Cross-video question for Gemini to answer.
        labels: Optional ordered labels. Defaults to each file's basename.
        prompt: Optional shared generation prompt or editorial instruction.
        intent: Optional shared creative brief.
        context: Optional per-call notes.
        base_plate_path: Optional reference image shared by all videos.
        identity_refs: Optional shared character/asset image references.
        style_refs: Optional shared style image references.
        fps: Sampling rate applied independently to every video. Default None
            uses static sampling at 1 fps; explicit range is (0, 24].
        processing: ``static`` preserves the existing request path. ``agentic``
            uses Interactions for adaptive inspection; omit fps. Supported by
            Gemini 3.8/3.7/3.6 Flash and 3.5 Flash Lite. Returns processing_trace,
            agentic_processing_verified (matched call/result evidence), and full
            interaction metadata. Save the returned result to retain the trace.
            This is a synchronous, single-turn API call; no automatic fallback.
        model: Gemini model id. Default ``gemini-3.8-flash``.
        temperature: Optional sampling override.
        system_prompt: Override the default analyze-mode instruction.
        upload_timeout_s: Per-file Files API processing timeout.
        thinking_level: Gemini reasoning depth. Defaults to ``high``. Pass
            ``None`` to use the model/API default.
        max_output_tokens: Maximum output budget; thinking can consume this
            budget on thinking models. Defaults to 65,536. Pass ``None`` to
            use the model/API default.

    Returns:
        The resolved ordered paths and labels, model/fps, original question,
        free-form answer, and an echo of the evaluation context.

    Raises:
        RuntimeError: ``INVALID_INPUT`` for list/label/fps errors,
        ``VIDEO_NOT_FOUND`` for any missing source, Files API errors from any
        upload, ``API_KEY_MISSING``, or ``NO_RESPONSE``.
    """
    question_text = _normalize_question(question)
    resolved_paths, normalized_labels = _normalize_video_inputs(video_paths, labels)
    _validate_fps(fps)
    gemini_media.validate_video_processing(processing, fps)
    processing_result: dict[str, Any] = {}

    image_module = gemini_media.require_pillow()
    client, gtypes = gemini_media.init_client()
    uploaded_files: list[Any] = []
    video_mimes = [gemini_media.video_mime_type(path) for path in resolved_paths]

    try:
        for path in resolved_paths:
            uploaded_files.append(
                gemini_media.upload_and_poll_video(
                    client, path, timeout_s=upload_timeout_s
                )
            )

        system_instruction = system_prompt or prompts.analyze_video_system_prompt()
        contents = _build_multi_video_contents(
            video_files=uploaded_files,
            video_mimes=video_mimes,
            video_labels=normalized_labels,
            prompt=prompt,
            intent=intent,
            context=context,
            base_plate_path=base_plate_path,
            identity_refs=identity_refs,
            style_refs=style_refs,
            fps=fps,
            image_module=image_module,
            gtypes=gtypes,
            question=question_text,
        )

        call_kwargs = dict(
            client=client,
            model=model,
            system_instruction=system_instruction,
            contents=contents,
            temperature=temperature,
            thinking_level=thinking_level,
            max_output_tokens=max_output_tokens,
        )
        if processing == "agentic":
            processing_result = gemini_media.call_agentic_video(**call_kwargs)
            answer = processing_result["answer"]
        else:
            answer = gemini_media.call_unstructured(gtypes=gtypes, **call_kwargs)
    finally:
        for uploaded in uploaded_files:
            gemini_media.cleanup_uploaded(client, uploaded)

    context_used = _context_used(
        prompt=prompt,
        intent=intent,
        context=context,
        base_plate_path=base_plate_path,
        identity_refs=identity_refs,
        style_refs=style_refs,
        question=question_text,
    )
    context_used["video_labels"] = normalized_labels

    return {
        "model": model,
        "video_paths": resolved_paths,
        "video_labels": normalized_labels,
        "fps": fps,
        "processing": processing,
        **processing_result,
        "thinking_level": thinking_level,
        "max_output_tokens": max_output_tokens,
        "question": question_text,
        "answer": answer,
        "context_used": context_used,
    }


@mcp.tool()
def compare_images(
    image_paths: list[str],
    prompt: str,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    criteria: Optional[list[str]] = None,
    model: str = DEFAULT_ANALYSIS_MODEL,
    temperature: Optional[float] = None,
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
        model: Gemini model id. Default ``gemini-3.8-flash``.
        temperature: Optional sampling override; omitted by default.
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
    model: str = DEFAULT_ANALYSIS_MODEL,
    temperature: Optional[float] = None,
    system_prompt: Optional[str] = None,
) -> dict[str, Any]:
    """Deconstruct an image into reusable prompt tokens by category.

    Default categories are the env-coverage workflow's five (lighting,
    atmosphere, palette, materials, spatial_grammar) per
    ``vault_gml/CLAUDE.md:164``. Output is short token phrases per category
    (1–3 words each) — concrete visual vocabulary another genesis prompt
    can paste in verbatim.

    Defaults to Gemini 3.5 Flash because this is a descriptive lane where
    the stable Flash model is enough and Pro image generation is not needed.

    Args:
        image_path: Absolute path to the image to deconstruct.
        categories: Override the default 5-category list.
        intent: Optional brief — focuses the extraction (e.g., "I'm only
            interested in tokens relevant to teal-orange grade and
            anamorphic optics, skip color-palette specifics").
        model: Gemini model id. Default ``gemini-3.8-flash``.
        temperature: Optional sampling override; omitted by default.
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


@mcp.tool()
def start_batch_analysis(
    system_prompt_path: str,
    user_prompt: str,
    out_dir: str,
    input_dir: Optional[str] = None,
    pattern: str = "*",
    inputs: Optional[list[str]] = None,
    tier: Literal["batch", "standard", "flex"] = "batch",
    model: str = DEFAULT_ANALYSIS_MODEL,
    fps: Optional[float] = None,
    thinking_level: Optional[str] = "low",
    temperature: Optional[float] = None,
    max_output_tokens: int = 65_536,
    media_resolution: Optional[str] = None,
    prompt_version: Optional[str] = None,
    concurrency: int = 8,
    chunk_size: int = 200,
    probe: int = 1,
    flex_fallback: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Start one Gemini request per file over a folder (or a list of files) and return at once.

    For dataset captioning and any per-file question: the system prompt file is sent whole with every file, plus
    ``user_prompt``. Files are video, image or audio (by extension). ``tier``:

    - ``batch`` (default): the Gemini Batch API, half price, asynchronous (Google's target 24 h, expiry 48 h; our runs
      took minutes). The first ``probe`` files run on the standard tier first so a refused setting fails fast and
      their tokens price the rest; then jobs of up to ``chunk_size`` files, uploaded just before submission.
    - ``standard``: ordinary calls, ``concurrency`` at a time.
    - ``flex``: ordinary calls at half price that may be refused (503/429) under load; retried with backoff, and
      finished on standard only if ``flex_fallback`` (full price).

    Results land in ``out_dir``: ``items/<key>.json`` (text, finish reason, tokens, attempts), ``texts/<key>.txt``,
    ``run.json`` (status, counts, cost). Rerunning on the same folder skips files already done under the same file
    hash and settings. ``prompt_version`` names the prompt text; reusing a name with different text is refused.
    ``dry_run`` plans and estimates the cost without any call. Otherwise the run is started as its own process (it
    outlives this server); follow it with ``get_batch_analysis`` and stop it with ``cancel_batch_analysis``.
    """
    from . import batch

    spec = batch.make_spec(
        out_dir=out_dir, system_prompt_path=system_prompt_path, user_prompt=user_prompt, input_dir=input_dir,
        pattern=pattern, inputs=inputs, tier=tier, model=model, fps=fps, thinking_level=thinking_level,
        temperature=temperature, max_output_tokens=max_output_tokens, media_resolution=media_resolution,
        prompt_version=prompt_version, concurrency=concurrency, chunk_size=chunk_size, probe=probe,
        flex_fallback=flex_fallback,
    )
    plan = batch.run(spec, dry_run=True)
    if dry_run:
        return plan
    busy = batch.status(out_dir) if (Path(spec.out_dir) / "run.json").exists() else {}
    if busy.get("runner_alive"):
        raise RuntimeError(f"RUN_BUSY: a runner (pid {busy.get('pid')}) is already working in {spec.out_dir}")
    batch.Run(spec.out_dir).save_request(spec)
    pid = batch.launch_detached(spec.out_dir)
    return {**plan, "dry_run": False, "pid": pid,
            "next": "get_batch_analysis(out_dir) for progress; cancel_batch_analysis(out_dir) to stop"}


@mcp.tool()
def get_batch_analysis(out_dir: str, include_items: bool = False, limit: int = 50) -> dict[str, Any]:
    """A batch run's status: whether its runner is alive, the plan, each batch job's state, counts of files done
    and failed, tokens and cost so far. ``include_items`` adds up to ``limit`` per-file rows (key, ok, tier, finish
    reason, error, the first 300 characters of the reply)."""
    from . import batch

    return batch.status(out_dir, items=include_items, limit=limit)


@mcp.tool()
def cancel_batch_analysis(out_dir: str) -> dict[str, Any]:
    """Ask a batch run to stop: its runner finishes the request in hand, cancels a running batch job and records
    what was done. Files already done stay done; rerun ``start_batch_analysis`` on the folder to continue."""
    from . import batch

    return batch.request_cancel(out_dir)


def main() -> None:
    """Console-script entry point — runs the FastMCP server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
