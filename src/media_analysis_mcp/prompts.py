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


def context_block(
    *, prompt: str, intent: Optional[str], context: Optional[str]
) -> str:
    """Build the per-call context block woven into both describe and score
    user-content. Empty fields are omitted rather than rendered as 'None'."""
    parts: list[str] = []
    if intent and intent.strip():
        parts.append(f"Brief: {intent.strip()}")
    if context and context.strip():
        parts.append(f"Context for this evaluation: {context.strip()}")
    parts.append(f"The prompt that produced the image: {prompt.strip()}")
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
