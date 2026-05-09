from pathlib import Path

import pytest

from media_analysis_mcp import server


class _FakeClient:
    pass


class _FakeFileData:
    def __init__(self, *, file_uri, mime_type):
        self.file_uri = file_uri
        self.mime_type = mime_type


class _FakePart:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeVideoMetadata:
    def __init__(self, *, fps, start_offset):
        self.fps = fps
        self.start_offset = start_offset


class _FakeTypes:
    FileData = _FakeFileData
    Part = _FakePart
    VideoMetadata = _FakeVideoMetadata


class _FakeUploaded:
    name = "files/fake-upload"
    uri = "files/fake-upload"
    mime_type = "video/mp4"


@pytest.fixture
def video_path(tmp_path: Path) -> Path:
    """A file that just needs to exist — Files API upload is mocked."""
    p = tmp_path / "fake.mp4"
    p.write_bytes(b"not-a-real-mp4")
    return p


@pytest.fixture(autouse=True)
def fake_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server.gemini_media,
        "init_client",
        lambda: (_FakeClient(), _FakeTypes()),
    )
    monkeypatch.setattr(
        server.gemini_media,
        "upload_and_poll_video",
        lambda client, path, *, timeout_s=300: _FakeUploaded(),
    )
    monkeypatch.setattr(
        server.gemini_media,
        "cleanup_uploaded",
        lambda client, file_obj: None,
    )
    # require_pillow is called even when no ref images are passed; return a stub.
    monkeypatch.setattr(server.gemini_media, "require_pillow", lambda: object())


def test_analyze_video_returns_question_and_answer(
    monkeypatch: pytest.MonkeyPatch, video_path: Path
) -> None:
    captured: dict = {}

    def fake_call_unstructured(**kwargs):
        captured["model"] = kwargs["model"]
        captured["contents"] = kwargs["contents"]
        return "The camera is locked off; the glow ignites near midpoint."

    monkeypatch.setattr(
        server.gemini_media, "call_unstructured", fake_call_unstructured
    )

    result = server.analyze_video(
        video_path=str(video_path),
        question="describe the camera move",
        intent="ad-hoc motion read",
        model="test-model",
    )

    assert result["model"] == "test-model"
    assert result["video_path"] == str(video_path.resolve())
    assert result["question"] == "describe the camera move"
    assert result["answer"] == "The camera is locked off; the glow ignites near midpoint."
    assert result["context_used"]["question"] == "describe the camera move"
    assert result["context_used"]["prompt"] is None
    # Question is anchored in the first content block (context_block).
    assert "Question: describe the camera move" in captured["contents"][0]


def test_analyze_video_errors_on_missing_file(tmp_path: Path) -> None:
    bogus = tmp_path / "not-here.mp4"
    with pytest.raises(RuntimeError, match="^VIDEO_NOT_FOUND:"):
        server.analyze_video(video_path=str(bogus), question="anything")


def test_analyze_video_rejects_blank_question(video_path: Path) -> None:
    with pytest.raises(RuntimeError, match="^INVALID_INPUT: question is required"):
        server.analyze_video(video_path=str(video_path), question="\t\n")


def test_analyze_video_cleanup_runs_on_call_failure(
    monkeypatch: pytest.MonkeyPatch, video_path: Path
) -> None:
    """The Files API upload must be cleaned up even when the structured call
    raises — the same try/finally invariant that protects describe_video."""
    cleanup_calls: list = []

    monkeypatch.setattr(
        server.gemini_media,
        "cleanup_uploaded",
        lambda client, file_obj: cleanup_calls.append(file_obj.name),
    )

    def boom(**kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(server.gemini_media, "call_unstructured", boom)

    with pytest.raises(RuntimeError, match="synthetic failure"):
        server.analyze_video(video_path=str(video_path), question="anything")

    assert cleanup_calls == ["files/fake-upload"]
