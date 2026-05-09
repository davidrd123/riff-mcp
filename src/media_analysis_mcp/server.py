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

from . import gemini_media, prompts, schemas


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


def main() -> None:
    """Console-script entry point — runs the FastMCP server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
