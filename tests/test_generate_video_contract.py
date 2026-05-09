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
        {"prompt": "x", "duration": 3},
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


def test_start_video_job_writes_local_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image_path: Path
) -> None:
    def fake_create_seedance_prediction(**kwargs):
        assert kwargs["webhook_url"] is None
        return {
            "id": "pred-starting",
            "status": "starting",
            "version": "@latest",
            "created_at": "2026-05-09T10:00:00Z",
            "started_at": None,
            "completed_at": None,
            "output": None,
            "metrics": None,
            "error": None,
        }

    monkeypatch.setattr(
        gen_server.seedance,
        "create_seedance_prediction",
        fake_create_seedance_prediction,
    )

    result = gen_server.start_video_job(
        prompt="Use [Image1]",
        image=str(image_path),
        out_root=str(tmp_path / "out"),
    )

    assert result["status"] == "starting"
    assert result["prediction_id"] == "pred-starting"
    assert result["outputs_downloaded"] is False
    assert Path(result["status_path"]).is_file()
    assert Path(result["request_path"]).is_file()

    saved = json.loads(Path(result["status_path"]).read_text(encoding="utf-8"))
    assert saved["job_id"] == result["job_id"]
    assert saved["prediction_id"] == "pred-starting"


def test_start_video_job_uses_unique_job_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image_path: Path
) -> None:
    prediction_ids = iter(["pred-one", "pred-two"])

    monkeypatch.setattr(
        gen_server.seedance,
        "create_seedance_prediction",
        lambda **kwargs: {
            "id": next(prediction_ids),
            "status": "starting",
            "version": "@latest",
            "created_at": "2026-05-09T10:00:00Z",
            "started_at": None,
            "completed_at": None,
            "output": None,
            "metrics": None,
            "error": None,
        },
    )

    first = gen_server.start_video_job(
        prompt="Use [Image1]",
        image=str(image_path),
        out_root=str(tmp_path / "out"),
    )
    second = gen_server.start_video_job(
        prompt="Use [Image1]",
        image=str(image_path),
        out_root=str(tmp_path / "out"),
    )

    assert first["job_id"] != second["job_id"]
    assert first["job_dir"] != second["job_dir"]
    assert first["job_dir"].endswith(first["job_id"])
    assert second["job_dir"].endswith(second["job_id"])


def test_start_video_job_create_failure_leaves_no_local_job_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image_path: Path
) -> None:
    def fake_create_seedance_prediction(**kwargs):
        raise RuntimeError("REPLICATE_API_TOKEN_MISSING: missing token")

    monkeypatch.setattr(
        gen_server.seedance,
        "create_seedance_prediction",
        fake_create_seedance_prediction,
    )

    out_root = tmp_path / "out"
    with pytest.raises(RuntimeError, match="^REPLICATE_API_TOKEN_MISSING:"):
        gen_server.start_video_job(
            prompt="Use [Image1]",
            image=str(image_path),
            out_root=str(out_root),
        )

    assert not (out_root / "jobs").exists()


def test_get_video_job_poll_false_returns_local_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image_path: Path
) -> None:
    monkeypatch.setattr(
        gen_server.seedance,
        "create_seedance_prediction",
        lambda **kwargs: {
            "id": "pred-local",
            "status": "processing",
            "version": "@latest",
            "created_at": "2026-05-09T10:00:00Z",
            "started_at": "2026-05-09T10:00:01Z",
            "completed_at": None,
            "output": None,
            "metrics": None,
            "error": None,
        },
    )
    monkeypatch.setattr(
        gen_server.seedance,
        "get_seedance_prediction",
        lambda prediction_id: pytest.fail("poll=False should not call provider"),
    )

    started = gen_server.start_video_job(
        prompt="Use [Image1]",
        image=str(image_path),
        out_root=str(tmp_path / "out"),
    )
    result = gen_server.get_video_job(
        started["job_id"],
        out_root=str(tmp_path / "out"),
        poll=False,
    )

    assert result["status"] == "processing"
    assert result["prediction_id"] == "pred-local"


def test_get_video_job_unknown_job_id_is_coded(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="^JOB_NOT_FOUND: missing-job"):
        gen_server.get_video_job("missing-job", out_root=str(tmp_path / "out"))


def test_get_video_job_finalizes_succeeded_prediction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image_path: Path
) -> None:
    monkeypatch.setattr(
        gen_server.seedance,
        "create_seedance_prediction",
        lambda **kwargs: {
            "id": "pred-done",
            "status": "processing",
            "version": "@latest",
            "created_at": "2026-05-09T10:00:00Z",
            "started_at": "2026-05-09T10:00:01Z",
            "completed_at": None,
            "output": None,
            "metrics": None,
            "error": None,
        },
    )
    monkeypatch.setattr(
        gen_server.seedance,
        "get_seedance_prediction",
        lambda prediction_id: {
            "id": prediction_id,
            "status": "succeeded",
            "version": "@latest",
            "created_at": "2026-05-09T10:00:00Z",
            "started_at": "2026-05-09T10:00:01Z",
            "completed_at": "2026-05-09T10:00:30Z",
            "output": ["https://example.com/fake.mp4"],
            "metrics": {"predict_time": 1.5},
            "error": None,
        },
    )

    def fake_download_prediction_outputs(**kwargs):
        output_path = kwargs["out_dir"] / "fake_00.mp4"
        output_path.write_bytes(b"fake video")
        return [
            {
                "path": str(output_path),
                "url": kwargs["outputs"][0],
                "bytes": output_path.stat().st_size,
                "_metrics": {"download_time_s": 0.2},
            }
        ]

    monkeypatch.setattr(
        gen_server.seedance,
        "download_prediction_outputs",
        fake_download_prediction_outputs,
    )
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

    started = gen_server.start_video_job(
        prompt="Use [Image1]",
        image=str(image_path),
        out_root=str(tmp_path / "out"),
    )
    result = gen_server.get_video_job(
        started["job_id"],
        out_root=str(tmp_path / "out"),
    )

    assert result["status"] == "succeeded"
    assert result["outputs_downloaded"] is True
    assert result["result"]["status"] == "ok"
    assert result["result"]["outputs"][0]["path"].endswith(".mp4")
    assert result["result"]["metrics"]["predict_time_s"] == 1.5

    job_json = Path(result["job_dir"]) / "job.json"
    assert job_json.is_file()
    saved_job = json.loads(job_json.read_text(encoding="utf-8"))
    assert saved_job["prediction_id"] == "pred-done"


def test_get_video_job_finalizes_terminal_status_without_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image_path: Path
) -> None:
    monkeypatch.setattr(
        gen_server.seedance,
        "create_seedance_prediction",
        lambda **kwargs: {
            "id": "pred-terminal",
            "status": "succeeded",
            "version": "@latest",
            "created_at": "2026-05-09T10:00:00Z",
            "started_at": "2026-05-09T10:00:01Z",
            "completed_at": "2026-05-09T10:00:30Z",
            "output": ["https://example.com/fake.mp4"],
            "metrics": {"predict_time": 1.0},
            "error": None,
        },
    )

    monkeypatch.setattr(
        gen_server.seedance,
        "download_prediction_outputs",
        lambda **kwargs: [
            {
                "path": str(kwargs["out_dir"] / "fake_00.mp4"),
                "url": kwargs["outputs"][0],
                "bytes": 10,
                "_metrics": {"download_time_s": 0.1},
            }
        ],
    )
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

    started = gen_server.start_video_job(
        prompt="Use [Image1]",
        image=str(image_path),
        out_root=str(tmp_path / "out"),
    )
    saved_status = json.loads(Path(started["status_path"]).read_text(encoding="utf-8"))
    saved_status.pop("result", None)
    saved_status["outputs_downloaded"] = False
    Path(started["status_path"]).write_text(
        json.dumps(saved_status, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gen_server.get_video_job(
        started["job_id"],
        out_root=str(tmp_path / "out"),
    )

    assert result["status"] == "succeeded"
    assert result["outputs_downloaded"] is True
    assert result["result"]["prediction_id"] == "pred-terminal"


def test_cancel_video_job_updates_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image_path: Path
) -> None:
    monkeypatch.setattr(
        gen_server.seedance,
        "create_seedance_prediction",
        lambda **kwargs: {
            "id": "pred-cancel",
            "status": "processing",
            "version": "@latest",
            "created_at": "2026-05-09T10:00:00Z",
            "started_at": "2026-05-09T10:00:01Z",
            "completed_at": None,
            "output": None,
            "metrics": None,
            "error": None,
        },
    )
    monkeypatch.setattr(
        gen_server.seedance,
        "cancel_seedance_prediction",
        lambda prediction_id: {
            "id": prediction_id,
            "status": "canceled",
            "version": "@latest",
            "created_at": "2026-05-09T10:00:00Z",
            "started_at": "2026-05-09T10:00:01Z",
            "completed_at": "2026-05-09T10:00:03Z",
            "output": None,
            "metrics": None,
            "error": None,
        },
    )

    started = gen_server.start_video_job(
        prompt="Use [Image1]",
        image=str(image_path),
        out_root=str(tmp_path / "out"),
    )
    result = gen_server.cancel_video_job(
        started["job_id"],
        out_root=str(tmp_path / "out"),
    )

    assert result["status"] == "canceled"
    assert result["outputs_downloaded"] is False
    saved = json.loads(Path(result["status_path"]).read_text(encoding="utf-8"))
    assert saved["status"] == "canceled"
