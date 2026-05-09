import json
from pathlib import Path

import pytest

from gemini_video_prompts_mcp import seedance
from gemini_video_prompts_mcp import server as gen_server


@pytest.fixture
def image_path(tmp_path: Path) -> Path:
    image = tmp_path / "first.png"
    image.write_bytes(b"not a real png; existence is enough for this contract")
    return image


def test_generate_video_dry_run_modes() -> None:
    text_only = gen_server.generate_video(prompt="A quiet establishing shot", dry_run=True)
    video_ref = gen_server.generate_video(
        prompt="Use [Video1] as motion reference",
        reference_videos=["/tmp/motion.mp4"],
        dry_run=True,
    )
    image_and_video = gen_server.generate_video(
        prompt="Use [Image1] and [Video1]",
        image="/tmp/first.png",
        reference_videos=["/tmp/motion.mp4"],
        dry_run=True,
    )

    assert text_only["mode"] == "text_to_video"
    assert video_ref["mode"] == "omni_reference"
    assert image_and_video["mode"] == "first_last_frames"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"prompt": "x", "image": "a.png", "reference_images": ["b.png"]},
        {"prompt": "x", "last_frame_image": "b.png"},
        {"prompt": "x", "duration": 0},
        {"prompt": "x", "reference_audios": ["a.wav"]},
        {"prompt": "x", "reference_videos": ["1.mp4", "2.mp4", "3.mp4", "4.mp4"]},
    ],
)
def test_seedance_validation_errors_are_coded(kwargs: dict) -> None:
    with pytest.raises(RuntimeError, match="^INVALID_INPUT:"):
        seedance.build_seedance_video_params(**kwargs)


def test_generate_video_preserves_coded_error_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image_path: Path
) -> None:
    def fake_run_seedance_job(**kwargs):
        return {
            "success": False,
            "error": {
                "message": "REPLICATE_TIMEOUT: prediction did not complete within 1s",
                "type": "RuntimeError",
            },
            "outputs": [],
            "metrics": {},
            "cold_start": False,
        }

    monkeypatch.setattr(gen_server.seedance, "run_seedance_job", fake_run_seedance_job)

    with pytest.raises(RuntimeError, match="^REPLICATE_TIMEOUT:"):
        gen_server.generate_video(
            prompt="Use [Image1]",
            image=str(image_path),
            out_root=str(tmp_path / "out"),
        )


def test_generate_video_writes_job_json_and_cold_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image_path: Path
) -> None:
    def fake_run_seedance_job(**kwargs):
        output_path = kwargs["out_dir"] / "fake_00.mp4"
        output_path.write_bytes(b"fake video")
        return {
            "success": True,
            "model": {"version": "@latest"},
            "outputs": [
                {
                    "path": str(output_path),
                    "url": "https://example.com/fake.mp4",
                    "bytes": output_path.stat().st_size,
                }
            ],
            "metrics": {
                "predict_time_s": 1.0,
                "download_time_s": 0.1,
                "elapsed_s": 1.1,
            },
            "cold_start": True,
        }

    monkeypatch.setattr(gen_server.seedance, "run_seedance_job", fake_run_seedance_job)
    monkeypatch.setattr(
        gen_server.seedance,
        "probe_media_info",
        lambda path: {
            "duration_s": None,
            "fps": None,
            "width": None,
            "height": None,
            "has_audio": None,
        },
    )

    result = gen_server.generate_video(
        prompt="Use [Image1]",
        image=str(image_path),
        out_root=str(tmp_path / "out"),
    )

    job_json = Path(result["job_dir"]) / "job.json"
    assert job_json.is_file()
    assert result["metrics"]["cold_start"] is True
    saved = json.loads(job_json.read_text(encoding="utf-8"))
    assert saved["status"] == "ok"
    assert saved["metrics"]["cold_start"] is True
