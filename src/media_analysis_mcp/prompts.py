"""Prompt templates for media-analysis-mcp tools.

System instructions and per-call context blocks for describe_image and
score_image. Templates interpolate ``intent`` and ``context`` from the agent
so Gemini's evaluation is informed by Claude's conversation-private state
(prior iterations, the brief, what specifically to check this round).
"""
from __future__ import annotations

from typing import Optional


# Default 6-dim eval criteria — sourced from the global generation-review-loop
# skill (~/.claude/skills/generation-review-loop/SKILL.md, §Evaluation
# Dimensions). Override via the ``criteria`` arg for non-Patrick workflows.
SIX_DIMENSIONS: list[str] = [
    "prompt_fidelity",
    "preservation_fidelity",
    "style_lock",
    "scene_hierarchy",
    "story_service",
    "creative_brief_fidelity",
]


# Default categories for extract_visual_tokens — sourced from the GML vault's
# environment-coverage workflow (vault_gml/CLAUDE.md:164: "clean plate →
# tokens → genesis"). Override via the ``categories`` arg for other domains.
TOKEN_CATEGORIES: list[str] = [
    "lighting",
    "atmosphere",
    "palette",
    "materials",
    "spatial_grammar",
]


def context_block(
    *, prompt: str, intent: Optional[str], context: Optional[str]
) -> str:
    """Build the per-call context block woven into both describe and score
    user-content for image and video tools. Empty fields are omitted rather
    than rendered as 'None'.

    The "media" wording is deliberately modality-neutral so the same helper
    works for both image and video describe/score paths.
    """
    parts: list[str] = []
    if intent and intent.strip():
        parts.append(f"Brief: {intent.strip()}")
    if context and context.strip():
        parts.append(f"Context for this evaluation: {context.strip()}")
    parts.append(f"The prompt that produced the media: {prompt.strip()}")
    return "\n\n".join(parts)


def describe_image_system_prompt() -> str:
    """System instruction for describe_image — Claude is the judge.

    Emphasis: observation only. No scoring. No verdicts. Direct visual
    language. The agent will read this description against the brief and
    apply its own rubric.
    """
    return (
        "You are an experienced visual director reviewing an AI-generated image. "
        "Your job is to OBSERVE and DESCRIBE — not to score or judge. The agent "
        "calling you will use your description, combined with conversation-"
        "private context the model does not see, to make their own judgment.\n\n"
        "For each of the eight observation categories, write 1–3 sentences of "
        "grounded, specific observation. Cite what you actually see. Avoid "
        "hedge words ('seems', 'appears'); use direct visual language. If a "
        "category is not relevant or not present, say so explicitly rather than "
        "padding with vague description.\n\n"
        "Categories (return all eight):\n"
        "- composition — framing, layout, scale relationships, hero placement\n"
        "- subject_elements — what objects/figures are present, their actions\n"
        "- color_and_palette — dominant colors, accents, color relationships\n"
        "- style_and_rendering — medium, fidelity, technique signals\n"
        "- lighting_and_atmosphere — light direction, time of day, mood\n"
        "- text_and_signage — any visible writing/branding, or 'none observed'\n"
        "- notable_or_unexpected — anything distinctive or surprising\n"
        "- artifacts_or_failures — visible AI errors, anatomy issues, "
        "compositing seams, or 'none observed'\n\n"
        "If something doesn't fit the categories cleanly, add it to "
        "freeform_observations. Otherwise leave that field empty."
    )


def score_image_system_prompt(criteria: list[str]) -> str:
    """System instruction for score_image — Gemini is the judge.

    Emphasis: calibrated 0-100 scoring against the supplied criteria, with
    grounded notes per criterion and a decision_hint that can be acted on.
    """
    criteria_lines = "\n".join(f"- {c}" for c in criteria)
    return (
        "You are an experienced visual director scoring an AI-generated image "
        "against the production brief. Your scores will be acted on, so be "
        "calibrated:\n"
        "- 80–100: the image meets the brief on this dimension\n"
        "- 60–79: close, but a specific fix is needed\n"
        "- Below 60: brief not met; re-roll or escalation warranted\n"
        "- null: dimension not applicable (e.g., preservation_fidelity on a "
        "Genesis / text-to-image shot)\n\n"
        f"Criteria to score (return one entry per criterion, in this order):\n"
        f"{criteria_lines}\n\n"
        "For each criterion: name (exact match from the list above), score "
        "(0-100 int or null), and 1–3 sentences of grounded evidence — what "
        "specifically supports the score.\n\n"
        "Then write a 1–2 sentence summary tying the scores together, and a "
        "decision_hint chosen as follows (loose guide; agent has final say):\n"
        "- accept — all relevant dimensions ≥ 80\n"
        "- iterate — one or two dimensions below 80 with a specific fix\n"
        "- reroll — composition or geometry fundamentally broken\n"
        "- direction_gate — two or more valid directions exist, OR creative "
        "brief itself may need revision, OR same failure across attempts"
    )


def describe_video_system_prompt() -> str:
    """System instruction for describe_video — observation-only mode.

    Same observation discipline as the image version, plus four video-specific
    categories that surface motion, timing, continuity, and audio.
    """
    return (
        "You are an experienced visual director reviewing an AI-generated "
        "video. Your job is to OBSERVE and DESCRIBE — not to score or judge. "
        "The agent calling you will use your description, combined with "
        "conversation-private context the model does not see, to make their "
        "own judgment.\n\n"
        "For each of the twelve observation categories, write 1–3 sentences "
        "of grounded, specific observation. Cite what you actually see and "
        "hear. Avoid hedge words ('seems', 'appears'); use direct visual / "
        "auditory language. If a category is not relevant or not present, "
        "say so explicitly rather than padding.\n\n"
        "Categories (return all twelve):\n"
        "- composition — framing, layout, scale relationships\n"
        "- subject_elements — objects/figures and their actions\n"
        "- color_and_palette — dominant colors, accents, relationships\n"
        "- style_and_rendering — medium, fidelity, technique signals\n"
        "- lighting_and_atmosphere — light direction, time of day, mood\n"
        "- text_and_signage — visible writing/branding, or 'none observed'\n"
        "- notable_or_unexpected — distinctive or surprising elements\n"
        "- artifacts_or_failures — visible AI errors, or 'none observed'\n"
        "- motion_and_camera — camera move (pan/push/orbit/static), subject "
        "motion, motion quality (smooth/judder/morph)\n"
        "- pacing_and_timing — beat rhythm, when key moments hit, perceived "
        "shot length\n"
        "- frame_continuity — how subject identity / style / lighting hold "
        "across frames; flicker, drift, morph artifacts\n"
        "- audio_quality — dialogue intelligibility, music, SFX, sync; "
        "'no audio' if silent\n\n"
        "If something doesn't fit the categories cleanly, add it to "
        "freeform_observations. Otherwise leave that field empty."
    )


def score_video_system_prompt(criteria: list[str]) -> str:
    """System instruction for score_video — Gemini is the judge.

    Same six dimension names as ``score_image`` by default, but with
    video-adapted prompt language per generation-review-loop SKILL.md
    §Generalizing to Video (lines 290-300).
    """
    criteria_lines = "\n".join(f"- {c}" for c in criteria)
    return (
        "You are an experienced visual director scoring an AI-generated "
        "video against the production brief. Your scores will be acted on, "
        "so be calibrated:\n"
        "- 80–100: the video meets the brief on this dimension\n"
        "- 60–79: close, but a specific fix is needed\n"
        "- Below 60: brief not met; re-roll or escalation warranted\n"
        "- null: dimension not applicable\n\n"
        f"Criteria to score (return one entry per criterion, in this order):\n"
        f"{criteria_lines}\n\n"
        "Adapt each dimension's read for video:\n"
        "- prompt_fidelity: did the motion, camera move, and action match "
        "the instruction?\n"
        "- preservation_fidelity: did style/setting/character stay "
        "consistent across frames? (null on pure text-to-video)\n"
        "- style_lock: aesthetic register consistent with the locked look\n"
        "- scene_hierarchy: camera grammar correct; intended hero remains "
        "dominant across the clip\n"
        "- story_service: clip communicates the right beat at the right "
        "pace\n"
        "- creative_brief_fidelity: clip solves the underlying creative "
        "problem (character identity, emotional tone, scene purpose), not "
        "just executes the prompt\n\n"
        "For each criterion: name (exact match from list above), score "
        "(0-100 int or null), and 1–3 sentences of grounded evidence — what "
        "specifically supports the score.\n\n"
        "Then write a 1–2 sentence summary tying the scores together, and a "
        "decision_hint:\n"
        "- accept — all relevant dimensions ≥ 80\n"
        "- iterate — one or two below 80 with a specific fix\n"
        "- reroll — composition, motion, or geometry fundamentally broken\n"
        "- direction_gate — two or more valid directions, OR creative brief "
        "may need revision, OR same failure across attempts"
    )


def compare_images_system_prompt(criteria: list[str]) -> str:
    """System instruction for compare_images — Gemini picks the best of N.

    Multiple candidate images are passed in numbered order. Gemini compares
    them across the requested criteria, names the differences, and picks
    one. Returns ``best_index`` as a 1-indexed position in the input list
    so the wrapper can resolve it back to a path.
    """
    criteria_lines = "\n".join(f"- {c}" for c in criteria)
    return (
        "You are an experienced visual director comparing AI-generated "
        "candidate images and picking the best one for the brief. The "
        "candidates are presented in numbered order — 'Image 1' is the "
        "first uploaded image, 'Image 2' is the second, and so on.\n\n"
        f"Compare the candidates across these criteria:\n{criteria_lines}\n\n"
        "In ``comparison``, write 2–4 sentences walking through the "
        "differences across criteria. Cite specific visual evidence — what "
        "you actually see in each candidate, not generic adjectives.\n\n"
        "In ``best_index``, return the 1-indexed position of the strongest "
        "candidate (1 for the first image, 2 for the second, etc.).\n\n"
        "In ``reasoning``, give 1–2 sentences naming the decisive factor "
        "that put your pick above the others."
    )


def extract_visual_tokens_system_prompt(categories: list[str]) -> str:
    """System instruction for extract_visual_tokens — token deconstruct.

    Output is short token phrases per category (1–3 words each), not prose.
    Used to feed the env-coverage workflow: read a clean plate, extract
    tokens by category, then write a fresh genesis prompt from the tokens
    so a new shot inherits the same visual world.
    """
    categories_lines = "\n".join(f"- {c}" for c in categories)
    return (
        "You are a visual director deconstructing an image into reusable "
        "prompt tokens. Output short, concrete token phrases — 1–3 words "
        "each — that another generation prompt could paste in verbatim. "
        "Avoid full sentences; avoid hedge words; avoid generic adjectives "
        "like 'beautiful' or 'cinematic'.\n\n"
        f"Categories to extract (return one entry per category, in this "
        f"order):\n{categories_lines}\n\n"
        "For each category: ``category`` (exact match from the list above) "
        "and ``tokens`` (3–8 short phrases). Concrete visual vocabulary the "
        "image actually shows — not interpretations.\n\n"
        "Examples of good tokens:\n"
        "  lighting: 'high-key commercial', 'warm key light', 'no cast shadows'\n"
        "  palette: 'candy pink', 'electric mint', 'warm cream'\n"
        "  materials: 'glossy lacquer', 'matte fabric', 'chrome'\n"
        "  spatial_grammar: 'isometric 3/4', 'flat horizontal plane'\n"
    )


def reference_label(role: str, index: int = 1) -> str:
    """Label inserted before each reference image in the multimodal contents
    list, so Gemini knows what role each image plays.
    """
    role_labels = {
        "base_plate": "BASE PLATE the output image was mutated from",
        "identity_ref": f"IDENTITY REFERENCE {index} for character/asset carry-through",
        "style_ref": f"STYLE REFERENCE {index} for aesthetic comparison",
        "target": "OUTPUT IMAGE to evaluate",
    }
    return role_labels.get(role, role)
