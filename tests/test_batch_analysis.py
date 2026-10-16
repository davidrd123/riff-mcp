"""Offline tests for the batch analysis engine (media_analysis_mcp.batch) with a fake Gemini client."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import types as gtypes

from media_analysis_mcp import batch, server


class FakeFile(SimpleNamespace):
    pass


class CodedError(Exception):
    def __init__(self, code: int, msg: str = "boom"):
        super().__init__(f"{code} {msg}")
        self.code = code


class FakeFiles:
    def __init__(self):
        self.uploaded: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.blobs: dict[str, bytes] = {}

    def upload(self, file, config=None):
        name = f"files/{len(self.uploaded)}"
        self.uploaded.append((file, dict(config or {})))
        return FakeFile(name=name, uri=f"https://x/{name}", mime_type=(config or {}).get("mime_type"),
                        state=SimpleNamespace(name="ACTIVE"))

    def get(self, name):
        return FakeFile(name=name, uri=f"https://x/{name}", mime_type=None, state=SimpleNamespace(name="ACTIVE"))

    def delete(self, name):
        self.deleted.append(name)

    def download(self, file):
        return self.blobs[file]


class FakeModels:
    def __init__(self, errors=None):
        self.calls: list[dict] = []
        self.errors = list(errors or [])

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.errors:
            raise self.errors.pop(0)
        return SimpleNamespace(text=f"caption {len(self.calls)}",
                               candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
                               usage_metadata=SimpleNamespace(prompt_token_count=1000, candidates_token_count=100,
                                                              thoughts_token_count=10, cached_content_token_count=0,
                                                              total_token_count=1110))


class FakeBatches:
    def __init__(self, files: FakeFiles, states=("JOB_STATE_RUNNING", "JOB_STATE_SUCCEEDED"), fail_create=False,
                 drop_key=None, on_get=None):
        self.files, self.states, self.fail_create, self.drop_key, self.on_get = files, states, fail_create, drop_key, on_get
        self.created: list[dict] = []
        self.cancelled: list[str] = []
        self.polls: dict[str, int] = {}

    def create(self, model, src, config):
        self.created.append({"model": model, "src": src, "config": config})
        if self.fail_create:
            raise ConnectionError("reply lost")
        return SimpleNamespace(name=f"batches/{len(self.created)}", state=SimpleNamespace(name="JOB_STATE_PENDING"))

    def get(self, name):
        n = self.polls.get(name, 0)
        self.polls[name] = n + 1
        if self.on_get:
            self.on_get(name, n)
        state = "JOB_STATE_CANCELLED" if name in self.cancelled else self.states[min(n, len(self.states) - 1)]
        dest = None
        if state == "JOB_STATE_SUCCEEDED":
            idx = int(name.split("/")[1]) - 1
            src_name = self.created[idx]["src"]
            local = next(f for (f, _), i in zip(self.files.uploaded, range(10**6)) if f"files/{i}" == src_name)
            out = []
            for line in Path(local).read_text().splitlines():
                key = json.loads(line)["key"]
                if key == self.drop_key:
                    continue
                out.append(json.dumps({"key": key, "response": {
                    "candidates": [{"content": {"parts": [{"text": "thinking", "thought": True}, {"text": f"batch caption {key}"}]},
                                    "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 100, "thoughtsTokenCount": 20,
                                      "totalTokenCount": 1120}}}))
            self.files.blobs[f"files/results-{idx}"] = ("\n".join(out) + "\n").encode()
            dest = SimpleNamespace(file_name=f"files/results-{idx}")
        return SimpleNamespace(name=name, state=SimpleNamespace(name=state), dest=dest, error=None,
                               create_time=None, end_time=None, update_time=None)

    def cancel(self, name):
        self.cancelled.append(name)

    def list(self, config=None):
        return [SimpleNamespace(name=f"batches/{i + 1}", display_name=c["config"]["display_name"],
                                state=SimpleNamespace(name="JOB_STATE_RUNNING")) for i, c in enumerate(self.created)]


def fake_client(**batch_kwargs):
    files = FakeFiles()
    client = SimpleNamespace(files=files, models=FakeModels(batch_kwargs.pop("errors", None)),
                             batches=FakeBatches(files, **batch_kwargs))
    return client, (lambda timeout=None: (client, gtypes))


@pytest.fixture
def media(tmp_path: Path) -> Path:
    d = tmp_path / "clips"
    d.mkdir()
    for i in range(5):
        (d / f"clip-{i}.mp4").write_bytes(f"video {i}".encode())
    (tmp_path / "prompt.md").write_text("You caption clips.\n")
    return d


def spec_for(media: Path, tmp_path: Path, **kw):
    kw.setdefault("tier", "standard")
    return batch.make_spec(out_dir=str(tmp_path / "run"), system_prompt_path=str(tmp_path / "prompt.md"),
                           user_prompt="Caption this.", input_dir=str(media), pattern="*.mp4", fps=8,
                           poll_interval_s=0, **kw)


def nosleep(_s):
    return None


def test_standard_run_then_rerun_skips_done(media, tmp_path):
    client, factory = fake_client()
    out = batch.run(spec_for(media, tmp_path), client_factory=factory, sleep=nosleep)
    assert out["status"] == "succeeded" and out["summary"]["succeeded"] == 5
    rec = json.loads((tmp_path / "run/items/clip-0.json").read_text())
    assert rec["ok"] and rec["tier"] == "standard" and rec["usage"]["prompt"] == 1000 and rec["finish_reason"] == "STOP"
    assert (tmp_path / "run/texts/clip-0.txt").read_text().startswith("caption")
    assert len(client.files.deleted) == 5
    call = client.models.calls[0]
    part = call["contents"][0]
    assert part.video_metadata.fps == 8 and call["config"].thinking_config.thinking_level.name == "LOW"
    batch.run(spec_for(media, tmp_path), client_factory=factory, sleep=nosleep)
    assert len(client.models.calls) == 5  # nothing re-run


def test_changed_setting_reruns_and_supersedes(media, tmp_path):
    client, factory = fake_client()
    batch.run(spec_for(media, tmp_path), client_factory=factory, sleep=nosleep)
    s2 = spec_for(media, tmp_path)
    s2.user_prompt = "Caption this, briefly."
    plan = batch.run(s2, dry_run=True)
    assert plan["stale"] == 5 and plan["done"] == 0
    batch.run(s2, client_factory=factory, sleep=nosleep)
    assert len(client.models.calls) == 10
    assert len(list((tmp_path / "run/superseded").glob("*.json"))) == 5


def test_prompt_version_is_frozen(media, tmp_path):
    client, factory = fake_client()
    batch.run(spec_for(media, tmp_path, prompt_version="v1"), client_factory=factory, sleep=nosleep)
    (tmp_path / "prompt.md").write_text("You caption clips differently.\n")
    with pytest.raises(ValueError, match="PROMPT_VERSION_CHANGED"):
        batch.run(spec_for(media, tmp_path, prompt_version="v1"), client_factory=factory, sleep=nosleep)


def test_retries_on_503_then_records_attempts(media, tmp_path):
    client, factory = fake_client(errors=[CodedError(503), CodedError(503)])
    s = spec_for(media, tmp_path, concurrency=1)
    s.inputs = s.inputs[:1]
    batch.run(s, client_factory=factory, sleep=nosleep)
    rec = json.loads((tmp_path / "run/items/clip-0.json").read_text())
    assert rec["ok"] and len(rec["attempts"]) == 3 and rec["attempts"][0]["error"].startswith("503")


def test_non_retryable_error_fails_once(media, tmp_path):
    client, factory = fake_client(errors=[CodedError(400, "INVALID_ARGUMENT")])
    s = spec_for(media, tmp_path, concurrency=1)
    s.inputs = s.inputs[:1]
    out = batch.run(s, client_factory=factory, sleep=nosleep)
    rec = json.loads((tmp_path / "run/items/clip-0.json").read_text())
    assert not rec["ok"] and len(rec["attempts"]) == 1 and out["status"] == "partial"


def test_flex_sets_service_tier(media, tmp_path):
    client, factory = fake_client()
    batch.run(spec_for(media, tmp_path, tier="flex"), client_factory=factory, sleep=nosleep)
    assert all(c["config"].service_tier.value == "flex" for c in client.models.calls)


def test_batch_flow_probe_chunks_and_request_shape(media, tmp_path):
    client, factory = fake_client()
    s = spec_for(media, tmp_path, tier="batch", chunk_size=2, probe=1, temperature=0.7)
    out = batch.run(s, client_factory=factory, sleep=nosleep)
    assert out["status"] == "succeeded" and out["summary"]["by_tier"] == {"standard": 1, "batch": 4}
    assert len(client.models.calls) == 1 and len(client.batches.created) == 2
    line = json.loads((tmp_path / "run/batches/001/requests.jsonl").read_text().splitlines()[0])
    req = line["request"]
    assert req["system_instruction"] == {"parts": [{"text": "You caption clips."}]}
    part = req["contents"][0]["parts"][0]
    assert set(part) == {"file_data", "video_metadata"} and part["video_metadata"] == {"fps": 8, "start_offset": "0s"}
    assert req["contents"][0]["parts"][1] == {"text": "Caption this."}
    assert req["generationConfig"] == {"responseMimeType": "text/plain", "maxOutputTokens": 65536, "temperature": 0.7,
                                       "thinkingConfig": {"thinkingLevel": "LOW"}}
    job = json.loads((tmp_path / "run/batches/001/job.json").read_text())
    assert job["job"] == "batches/1" and job["state"] == "JOB_STATE_SUCCEEDED" and job["report"]["missing"] == []
    rec = json.loads((tmp_path / "run/items/clip-2.json").read_text())
    assert rec["tier"] == "batch" and rec["text"] == "batch caption clip-2" and rec["usage"]["thoughts"] == 20
    assert (tmp_path / "run/batches/001/results.jsonl").exists()
    state = json.loads((tmp_path / "run/run.json").read_text())
    assert state["probe"]["remaining"] == 4 and state["summary"]["cost_usd"] > 0


def test_batch_missing_key_fails_that_item(media, tmp_path):
    client, factory = fake_client(drop_key="clip-3")
    out = batch.run(spec_for(media, tmp_path, tier="batch", probe=0), client_factory=factory, sleep=nosleep)
    rec = json.loads((tmp_path / "run/items/clip-3.json").read_text())
    assert not rec["ok"] and rec["error"] == "MISSING_FROM_RESULTS" and out["status"] == "partial"


def test_batch_create_error_is_never_retried(media, tmp_path):
    client, factory = fake_client(fail_create=True)
    with pytest.raises(RuntimeError, match="BATCH_CREATE_UNCERTAIN"):
        batch.run(spec_for(media, tmp_path, tier="batch", probe=0), client_factory=factory, sleep=nosleep)
    assert len(client.batches.created) == 1
    assert json.loads((tmp_path / "run/batches/001/job.json").read_text())["state"] == "CREATE_UNCERTAIN"
    with pytest.raises(RuntimeError, match="BATCH_CREATE_UNCERTAIN"):
        batch.run(spec_for(media, tmp_path, tier="batch", probe=0), client_factory=factory, sleep=nosleep)
    assert len(client.batches.created) == 1
    assert batch.find_jobs(client, "riff-batch run")[0]["job"] == "batches/1"


def test_cancel_cancels_the_running_job(media, tmp_path):
    def on_get(name, n):
        if n == 0:
            (tmp_path / "run/CANCEL").write_text("now")
    client, factory = fake_client(states=("JOB_STATE_RUNNING",), on_get=on_get)
    out = batch.run(spec_for(media, tmp_path, tier="batch", probe=0), client_factory=factory, sleep=nosleep)
    assert client.batches.cancelled == ["batches/1"] and out["status"] == "cancelled"


def test_resume_collects_a_submitted_job(media, tmp_path):
    client, factory = fake_client(states=("JOB_STATE_RUNNING", "JOB_STATE_RUNNING", "JOB_STATE_SUCCEEDED"))
    s = spec_for(media, tmp_path, tier="batch", probe=0, max_wait_hours=0)
    assert batch.run(s, client_factory=factory, sleep=nosleep)["status"] == "waiting"
    s.max_wait_hours = 1
    out = batch.run(s, client_factory=factory, sleep=nosleep)
    assert out["status"] == "succeeded" and len(client.batches.created) == 1


def test_dry_run_makes_no_call(media, tmp_path):
    def boom(timeout=None):
        raise AssertionError("no client in a dry run")
    plan = batch.run(spec_for(media, tmp_path, tier="batch"), dry_run=True, client_factory=boom)
    assert plan["todo"] == 5 and plan["estimate"]["cost_usd"] > 0 and not (tmp_path / "run/request.json").exists()


def test_parse_results_reports_problems():
    raw = b'{"key": "a", "response": {"candidates": [{"content": {"parts": [{"text": "A"}]}}]}}\n' \
          b'{"key": "a", "response": {}}\n{"key": "z", "response": {}}\n{"key": "b", "error": {"code": 400}}\n'
    res, rep = batch.parse_results(raw, ["a", "b", "c"])
    assert res["a"]["text"] == "A" and not res["b"]["ok"]
    assert rep == {"duplicate": ["a"], "unknown": ["z"], "missing": ["c"]}


def test_mcp_tools_dry_run_status_and_cancel(media, tmp_path):
    plan = server.start_batch_analysis(system_prompt_path=str(tmp_path / "prompt.md"), user_prompt="Caption this.",
                                       out_dir=str(tmp_path / "run"), input_dir=str(media), pattern="*.mp4", fps=8,
                                       dry_run=True)
    assert plan["dry_run"] and plan["tier"] == "batch" and plan["todo"] == 5
    client, factory = fake_client()
    batch.run(spec_for(media, tmp_path), client_factory=factory, sleep=nosleep)
    st = server.get_batch_analysis(str(tmp_path / "run"), include_items=True)
    assert st["status"] == "succeeded" and st["summary"]["succeeded"] == 5 and len(st["items"]) == 5
    assert server.cancel_batch_analysis(str(tmp_path / "run"))["cancel_requested"]
