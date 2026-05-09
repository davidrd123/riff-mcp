from pathlib import Path

import pytest

from media_analysis_mcp import schemas, server


class _FakeClient:
    pass


class _FakeTypes:
    pass


@pytest.fixture
def image_path(tmp_path: Path) -> Path:
    image = tmp_path / "target.png"
    pil_image = server.gemini_media.require_pillow()
    pil_image.new("RGB", (8, 8), color=(200, 10, 20)).save(image)
    return image


@pytest.fixture(autouse=True)
def fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server.gemini_media,
        "init_client",
        lambda: (_FakeClient(), _FakeTypes()),
    )


def _description_result() -> schemas.ImageDescriptionResult:
    return schemas.ImageDescriptionResult(
        observations=schemas.ImageObservations(
            composition="Centered subject with a tight crop.",
            subject_elements="A red square fills the frame.",
            color_and_palette="Dominant red with no accents.",
            style_and_rendering="Flat raster test image.",
            lighting_and_atmosphere="Even synthetic lighting.",
            text_and_signage="none observed",
            notable_or_unexpected="No unexpected elements.",
            artifacts_or_failures="none observed",
        ),
        freeform_observations=None,
    )


def _score_result(names: list[str]) -> schemas.ImageScoreResult:
    return schemas.ImageScoreResult(
        evaluations=[
            schemas.CriterionEvaluation(
                name=name,
                score=80,
                notes=f"{name} is adequately satisfied.",
            )
            for name in names
        ],
        summary="The image is usable.",
        decision_hint="accept",
    )


def test_describe_image_returns_observations_and_context(
    monkeypatch: pytest.MonkeyPatch, image_path: Path
) -> None:
    def fake_call_structured(**kwargs):
        assert kwargs["response_schema"] is schemas.ImageDescriptionResult
        assert kwargs["model"] == "test-model"
        return _description_result()

    monkeypatch.setattr(server.gemini_media, "call_structured", fake_call_structured)

    result = server.describe_image(
        image_path=str(image_path),
        prompt="make a red test image",
        intent="verify contract shape",
        context="unit test",
        model="test-model",
    )

    assert result["model"] == "test-model"
    assert result["image_path"] == str(image_path.resolve())
    assert result["observations"]["composition"] == "Centered subject with a tight crop."
    assert result["context_used"] == {
        "prompt": "make a red test image",
        "intent": "verify contract shape",
        "context": "unit test",
        "base_plate_path": None,
        "identity_refs": [],
        "style_refs": [],
    }


def test_score_image_returns_evaluations_keyed_by_requested_criteria(
    monkeypatch: pytest.MonkeyPatch, image_path: Path
) -> None:
    criteria = ["prompt_fidelity", "style_lock"]

    def fake_call_structured(**kwargs):
        assert kwargs["response_schema"] is schemas.ImageScoreResult
        return _score_result(criteria)

    monkeypatch.setattr(server.gemini_media, "call_structured", fake_call_structured)

    result = server.score_image(
        image_path=str(image_path),
        prompt="make a red test image",
        criteria=criteria,
    )

    assert list(result["evaluations"].keys()) == criteria
    assert result["evaluations"]["prompt_fidelity"]["score"] == 80
    assert result["decision_hint"] == "accept"


@pytest.mark.parametrize(
    ("criteria", "returned_names"),
    [
        (["prompt_fidelity", "style_lock"], ["prompt_fidelity"]),
        (["prompt_fidelity"], ["prompt_fidelity", "unexpected"]),
        (["prompt_fidelity"], ["prompt_fidelity", "prompt_fidelity"]),
    ],
)
def test_score_image_rejects_criterion_name_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    image_path: Path,
    criteria: list[str],
    returned_names: list[str],
) -> None:
    monkeypatch.setattr(
        server.gemini_media,
        "call_structured",
        lambda **kwargs: _score_result(returned_names),
    )

    with pytest.raises(RuntimeError, match="^SCHEMA_MISMATCH:"):
        server.score_image(
            image_path=str(image_path),
            prompt="make a red test image",
            criteria=criteria,
        )


def test_analyze_image_returns_question_and_answer(
    monkeypatch: pytest.MonkeyPatch, image_path: Path
) -> None:
    def fake_call_unstructured(**kwargs):
        assert kwargs["model"] == "test-model"
        # The contents list should embed the question via context_block.
        first_text = kwargs["contents"][0]
        assert "Question: how saturated is the red?" in first_text
        return "The red is fully saturated; no desaturation visible."

    monkeypatch.setattr(
        server.gemini_media, "call_unstructured", fake_call_unstructured
    )

    result = server.analyze_image(
        image_path=str(image_path),
        question="how saturated is the red?",
        intent="ad-hoc inspection",
        model="test-model",
    )

    assert result["model"] == "test-model"
    assert result["image_path"] == str(image_path.resolve())
    assert result["question"] == "how saturated is the red?"
    assert result["answer"] == "The red is fully saturated; no desaturation visible."
    assert result["context_used"]["question"] == "how saturated is the red?"
    assert result["context_used"]["intent"] == "ad-hoc inspection"
    assert result["context_used"]["prompt"] is None


def test_analyze_image_errors_on_missing_file(tmp_path: Path) -> None:
    bogus = tmp_path / "not-here.png"
    with pytest.raises(RuntimeError, match="^IMAGE_NOT_FOUND:"):
        server.analyze_image(image_path=str(bogus), question="anything")
