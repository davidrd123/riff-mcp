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
