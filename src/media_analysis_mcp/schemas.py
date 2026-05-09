"""Pydantic response schemas for media-analysis-mcp tools.

These classes drive Gemini's structured output (``response_schema``) so the
model is forced to return JSON conforming to the shapes the agent consumes.
The ``parsed`` attribute on the response yields a Pydantic instance directly;
the wrapper falls back to ``model_validate_json`` on response.text if needed.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------- describe_image ----------


class ImageObservations(BaseModel):
    """Eight fixed observation categories. Each field is rich prose
    (1–3 sentences), not a tag list."""

    composition: str = Field(
        ..., description="Framing, layout, scale relationships, hero placement"
    )
    subject_elements: str = Field(
        ..., description="What objects/figures are present and their actions"
    )
    color_and_palette: str = Field(
        ..., description="Dominant colors, accents, color relationships"
    )
    style_and_rendering: str = Field(
        ..., description="Medium, fidelity, technique signals; pixel art / "
        "photoreal / painterly / etc."
    )
    lighting_and_atmosphere: str = Field(
        ..., description="Light direction, time of day, mood, atmospheric depth"
    )
    text_and_signage: str = Field(
        ..., description="Any visible writing, branding, UI elements, or 'none'"
    )
    notable_or_unexpected: str = Field(
        ..., description="Anything surprising, distinctive, or off-brief"
    )
    artifacts_or_failures: str = Field(
        ...,
        description="Visible AI errors, anatomy issues, compositing seams, "
        "physics violations, or 'none observed'",
    )


class ImageDescriptionResult(BaseModel):
    """describe_image return shape (Gemini-produced fields only).

    The wrapper adds ``model``, ``image_path``, and ``context_used`` after
    parsing.
    """

    observations: ImageObservations
    freeform_observations: Optional[str] = Field(
        None,
        description="Optional model-driven prose for anything off-taxonomy. "
        "Leave null when the structured fields cover everything.",
    )


# ---------- describe_video ----------


class VideoObservations(BaseModel):
    """Twelve fixed observation categories — eight base (shared with image)
    plus four video-specific. Each field is rich prose (1–3 sentences)."""

    composition: str = Field(
        ..., description="Framing, layout, scale relationships, hero placement"
    )
    subject_elements: str = Field(
        ..., description="What objects/figures are present and their actions"
    )
    color_and_palette: str = Field(
        ..., description="Dominant colors, accents, color relationships"
    )
    style_and_rendering: str = Field(
        ..., description="Medium, fidelity, technique signals"
    )
    lighting_and_atmosphere: str = Field(
        ..., description="Light direction, time of day, mood"
    )
    text_and_signage: str = Field(
        ..., description="Visible writing, branding, UI elements, or 'none'"
    )
    notable_or_unexpected: str = Field(
        ..., description="Anything surprising, distinctive, or off-brief"
    )
    artifacts_or_failures: str = Field(
        ...,
        description="Visible AI errors, anatomy issues, compositing seams, "
        "or 'none observed'",
    )
    # Video-specific:
    motion_and_camera: str = Field(
        ...,
        description="Camera movement (pan, push, orbit, static), subject "
        "motion, motion quality (smooth, judder, morph)",
    )
    pacing_and_timing: str = Field(
        ...,
        description="Beat rhythm, action timing, when key moments hit, "
        "perceived shot length",
    )
    frame_continuity: str = Field(
        ...,
        description="How well subject identity / style / lighting hold "
        "across frames; flicker, drift, morph artifacts",
    )
    audio_quality: str = Field(
        ...,
        description="Audio character if present (dialogue intelligibility, "
        "music, SFX, sync to visuals); 'no audio' if silent",
    )


class VideoDescriptionResult(BaseModel):
    """describe_video return shape (Gemini-produced fields only).

    Wrapper adds ``model``, ``video_path``, and ``context_used``.
    """

    observations: VideoObservations
    freeform_observations: Optional[str] = Field(
        None,
        description="Optional model-driven prose for anything off-taxonomy. "
        "Leave null when the structured fields cover everything.",
    )


# ---------- score_image ----------


class CriterionEvaluation(BaseModel):
    """One criterion's score + evidence. Scored 0-100, or null for N/A
    (e.g., preservation_fidelity on a Genesis text-to-image shot)."""

    name: str = Field(
        ...,
        description="Criterion name, exactly matching one of the values in the "
        "criteria list provided in the system prompt",
    )
    score: Optional[int] = Field(
        None,
        ge=0,
        le=100,
        description="0-100 integer score, or null for N/A. Calibrated such "
        "that 80+ = brief met, 60-79 = close with specific fix needed, "
        "below 60 = brief not met",
    )
    notes: str = Field(
        ...,
        description="1-3 sentences of grounded evidence — what specifically "
        "supports the score",
    )


DecisionHint = Literal["accept", "iterate", "reroll", "direction_gate"]


class ImageScoreResult(BaseModel):
    """score_image return shape (Gemini-produced fields only).

    The wrapper transforms ``evaluations`` from a list to a name-keyed dict
    and adds ``model``, ``image_path``, and ``context_used``.
    """

    evaluations: list[CriterionEvaluation] = Field(
        ...,
        description="One entry per criterion in the requested criteria list, "
        "in the same order",
    )
    summary: str = Field(
        ...,
        description="1-2 sentence overall summary tying scores to the "
        "decision_hint",
    )
    decision_hint: DecisionHint = Field(
        ...,
        description="Advisory next-action recommendation. Treat as the "
        "model's vote; the agent has final say.",
    )


# ---------- compare_images ----------


class ImageComparisonResult(BaseModel):
    """compare_images return shape (Gemini-produced fields only).

    The wrapper resolves ``best_index`` to a path and adds ``model``,
    ``image_paths``, and ``context_used``.
    """

    comparison: str = Field(
        ...,
        description="2-4 sentences walking through how the candidates differ "
        "across the requested criteria. Cite specific visual evidence.",
    )
    best_index: int = Field(
        ...,
        ge=1,
        description="1-indexed position of the chosen image in the input list. "
        "1 = first image, 2 = second, etc.",
    )
    reasoning: str = Field(
        ...,
        description="1-2 sentences naming the decisive factor that picked this "
        "candidate over the others.",
    )


# ---------- extract_visual_tokens ----------


class CategoryTokens(BaseModel):
    """Tokens for one category — short visual-vocabulary phrases, not prose."""

    category: str = Field(
        ...,
        description="Category name, exactly matching one of the values in the "
        "categories list provided in the system prompt",
    )
    tokens: list[str] = Field(
        ...,
        description="3-8 short token phrases (1-3 words each) capturing the "
        "category's signal in this image. Concrete visual vocabulary, not "
        "explanatory sentences.",
    )


class VisualTokensResult(BaseModel):
    """extract_visual_tokens return shape (Gemini-produced fields only).

    The wrapper transforms ``categories`` from a list to a category-keyed dict
    and adds ``model`` and ``image_path``.
    """

    categories: list[CategoryTokens] = Field(
        ...,
        description="One entry per category in the requested categories list, "
        "in the same order",
    )
