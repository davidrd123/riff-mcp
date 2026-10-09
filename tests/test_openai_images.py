from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from gemini_video_prompts import cli, openai_images
from gemini_video_prompts_mcp import server
from riff_mcp_doctor import doctor


def image_bytes(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGBA" if fmt != "JPEG" else "RGB", (16, 16)).save(stream, fmt)
    return stream.getvalue()


def response(raw, count=1):
    return SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(raw).decode(), revised_prompt=None)
              for _ in range(count)],
        _request_id="req-test", usage=SimpleNamespace(model_dump=lambda **_: {"total_tokens": 12}),
        size="16x16", quality="low",
    )


def job(tmp_path, **kwargs):
    return cli.build_resolved_image_job(
        prompt="A small blue ceramic cup", provider="openai", out_root=str(tmp_path), **kwargs,
    )


def run(tmp_path, client, **kwargs):
    return openai_images.generate_openai_image_job(
        batch_path=tmp_path / "batch.yaml", job=job(tmp_path, **kwargs),
        run_day_dir=tmp_path / "day", client=client,
        allow_api_billing=True,
    )


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_generate_preserves_provider_bytes_metadata_and_multiple_outputs(tmp_path, fmt):
    raw = image_bytes(fmt)
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return response(raw, count=2)

    result = run(tmp_path, SimpleNamespace(images=SimpleNamespace(generate=generate)),
                 num_outputs=2, quality="low", size="1536x1024", output_format=fmt.lower())
    assert calls == [{"model": "gpt-image-2", "prompt": "A small blue ceramic cup", "n": 2,
                      "quality": "low", "size": "1536x1024", "output_format": fmt.lower(),
                      "background": "auto"}]
    assert result["provider"] == "openai" and result["access"] == "api_key"
    assert result["request_id"] == "req-test"
    assert result["usage"] == {"total_tokens": 12}
    assert result["response_metadata"]["size"] == "16x16"
    assert result["attempts"] == 1
    for output in result["outputs"]:
        assert Path(output["path"]).read_bytes() == raw
        assert Path(output["path"]).suffix == "." + fmt.lower()
    assert json.loads((Path(result["job_dir"]) / "job.json").read_text()) == result


def test_edit_resolves_ordered_references_relative_to_batch_and_closes_files(tmp_path):
    (tmp_path / "one.png").write_bytes(image_bytes())
    (tmp_path / "two.jpg").write_bytes(image_bytes("JPEG"))
    handles = []

    def edit(**kwargs):
        handles.extend(kwargs.pop("image"))
        assert [Path(f.name).name for f in handles] == ["one.png", "two.jpg"]
        assert all(not f.closed for f in handles)
        assert "input_fidelity" not in kwargs and "response_format" not in kwargs
        return response(image_bytes())

    result = run(tmp_path, SimpleNamespace(images=SimpleNamespace(edit=edit)),
                 image="one.png", images=["two.jpg", "one.png"])
    assert result["api"] == "images.edits"
    assert result["input_count"] == 2
    assert len(result["input_sha256"]) == 2
    assert all(f.closed for f in handles)


@pytest.mark.parametrize("options", [
    {"size": "1025x1024"}, {"size": "512x512"}, {"size": "4096x2048"},
    {"size": "3072x768"}, {"size": "3840x3840"}, {"quality": "best"},
    {"background": "transparent", "output_format": "jpeg"},
    {"output_compression": 10}, {"output_format": "webp", "output_compression": 101},
    {"temperature": 1.0}, {"aspect_ratio": "16:9"}, {"image_size": "2K"},
    {"system_prompt": "instructions"}, {"config": {"store": True}},
    {"config": {"api": "generate_content"}}, {"config": {"api": "interactions"}},
    {"config": {"thinking_level": "high"}}, {"config": {"previous_interaction_id": "parent"}},
    {"config": {"unknown": 1}}, {"model": "gemini-3-pro-image"},
    {"num_outputs": 5}, {"num_outputs": 0}, {"num_outputs": 1.5},
])
def test_invalid_openai_options_fail_during_resolution_without_writes(tmp_path, options):
    with pytest.raises(RuntimeError, match="INVALID_INPUT"):
        job(tmp_path, **options)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("size", ["auto", "1024x1024", "1536x864", "2048x1152", "3840x2160", "2160x3840"])
def test_valid_flexible_sizes(tmp_path, size):
    assert openai_images.validate_job(job(tmp_path, size=size))["size"] == size


def test_missing_reference_fails_before_client_initialization(tmp_path, monkeypatch):
    monkeypatch.setattr(openai_images, "init_openai_client", lambda: pytest.fail("client initialized"))
    with pytest.raises(RuntimeError, match="IMAGE_NOT_FOUND"):
        run(tmp_path, None, image="missing.png")
    assert not list(tmp_path.iterdir())


def test_empty_or_partial_response_retains_failure_record_without_retry(tmp_path):
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return response(image_bytes(), count=1)

    with pytest.raises(RuntimeError, match="OUTPUT_COUNT_MISMATCH"):
        run(tmp_path, SimpleNamespace(images=SimpleNamespace(generate=generate)), num_outputs=2)
    record = json.loads(next(tmp_path.rglob("job.json")).read_text())
    assert record["status"] == "failed"
    assert len(record["outputs"]) == 1 and len(calls) == 1


def test_missing_key_has_clear_error_and_no_gemini_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(openai_images, "load_dotenv_if_available", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(server, "init_client", lambda: pytest.fail("Gemini fallback"))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        server.generate_image(prompt="cup", provider="openai", out_root=str(tmp_path), allow_api_billing=True)


def test_mcp_dry_run_is_keyless_fileless_and_selects_openai_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "init_client", lambda: pytest.fail("Gemini initialized"))
    monkeypatch.setattr(openai_images, "init_openai_client", lambda: pytest.fail("OpenAI initialized"))
    result = server.generate_image(prompt="cup", provider="openai", image="missing.png",
                                   size="1536x1024", dry_run=True, out_root=str(tmp_path / "out"))
    assert result["provider"] == "openai" and result["model"] == "gpt-image-2"
    assert result["status"] == "planned"
    assert result["billing_confirmation_required"] is True
    assert "separate" in result["billing_notice"]
    assert not list(tmp_path.iterdir())


def test_cli_openai_only_and_mixed_batches_route_without_extra_credentials(tmp_path, monkeypatch):
    routes = []
    monkeypatch.setattr(cli, "init_client", lambda: (routes.append("gemini_client") or object(), object()))

    def worker(name):
        def generate(**kwargs):
            routes.append(name)
            return {"status": "ok", "outputs": [], "job_dir": str(tmp_path)}
        return generate

    monkeypatch.setattr(openai_images, "generate_openai_image_job", worker("openai"))
    monkeypatch.setattr(cli, "generate_image_job", worker("gemini"))
    monkeypatch.setattr(cli, "generate_job", worker("video"))
    assert cli.main(["--prompt", "cup", "--mode", "image", "--provider", "openai", "--out-root", str(tmp_path), "--allow-api-billing"]) == 0
    assert routes == ["openai"]
    routes.clear()
    batch = tmp_path / "batch.yaml"
    batch.write_text("defaults:\n  mode: image\njobs:\n  - prompt: cup\n    provider: openai\n  - prompt: saucer\n  - prompt: moving cup\n    mode: video\n")
    assert cli.main([str(batch), "--out-root", str(tmp_path), "--allow-api-billing"]) == 0
    assert routes == ["openai", "gemini_client", "gemini", "video"]


def test_cli_yaml_overrides_and_plan_no_writes(tmp_path, capsys):
    batch = tmp_path / "batch.yaml"
    batch.write_text("defaults:\n  provider: openai\n  mode: image\n  quality: high\njobs:\n  - prompt: cup\n")
    assert cli.main([str(batch), "--quality", "low", "--plan", "--out-root", str(tmp_path / "out")]) == 0
    planned = json.loads(capsys.readouterr().out)["jobs"][0]
    assert planned["quality"] == "low" and planned["model"] == "gpt-image-2"
    assert not (tmp_path / "out").exists()


def test_legacy_gemini_hashes_and_mcp_defaults_are_unchanged(monkeypatch):
    monkeypatch.delenv("GEMINI_IMAGE_MODEL", raising=False)
    legacy = cli.build_resolved_image_job(
        prompt="compatibility reference", model="gemini-3-pro-image",
    )
    assert "provider" not in legacy
    assert cli.build_job_hash(legacy) == "7ba1f6b4"  # captured from pre-change HEAD
    stateful = cli.build_resolved_image_job(
        prompt="compatibility reference", model="gemini-3.1-flash-image", image="ref.png",
        config={"api": "interactions", "store": True, "previous_interaction_id": "parent"},
    )
    assert cli.build_job_hash(stateful) == "23a4605c"
    assert cli.build_resolved_image_job(prompt="cup")["model"] == "gemini-nano-banana-2.1"
    monkeypatch.setenv("GEMINI_IMAGE_MODEL", "custom-cli-model")
    result = server.generate_image(prompt="cup", dry_run=True)
    assert result["model"] == "gemini-nano-banana-2.1"  # MCP historically ignores CLI env override
    assert result["config"]["api"] == "generate_content"
    assert "provider" not in result
    with pytest.raises(RuntimeError, match="require provider=openai"):
        server.generate_image(prompt="cup", size="1024x1024", dry_run=True)


def test_openai_quality_changes_job_hash(tmp_path):
    assert cli.build_job_hash(job(tmp_path, quality="low")) != cli.build_job_hash(job(tmp_path, quality="high"))


def test_billing_gate_blocks_worker_and_mcp_even_with_key_present(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(openai_images, "init_openai_client", lambda: pytest.fail("client initialized"))
    with pytest.raises(RuntimeError, match="API_BILLING_CONFIRMATION_REQUIRED"):
        openai_images.generate_openai_image_job(
            batch_path=tmp_path / "batch.yaml", job=job(tmp_path), run_day_dir=tmp_path / "day",
        )
    with pytest.raises(RuntimeError, match="API_BILLING_CONFIRMATION_REQUIRED"):
        server.generate_image(prompt="cup", provider="openai", out_root=str(tmp_path))
    assert not list(tmp_path.iterdir())


def test_batch_cannot_authorize_billing_and_gate_runs_before_any_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "init_client", lambda: pytest.fail("Gemini initialized"))
    monkeypatch.setattr(openai_images, "init_openai_client", lambda: pytest.fail("OpenAI initialized"))
    batch = tmp_path / "batch.yaml"
    batch.write_text("defaults:\n  mode: image\n  allow_api_billing: true\njobs:\n  - prompt: cup\n  - prompt: cup\n    provider: openai\n")
    assert cli.main([str(batch), "--out-root", str(tmp_path / "out")]) == 2
    assert "API_BILLING_CONFIRMATION_REQUIRED" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_doctor_openai_needs_no_gemini_or_replicate(monkeypatch):
    monkeypatch.setattr(doctor, "load_dotenv_if_available", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(doctor, "check_python_pkg", lambda name, users: doctor.CheckResult(name, "python", "ok", "", users))
    results = doctor.run_openai_checks(network=True)
    assert results[0].name == "OPENAI_API_KEY" and results[0].status == "fail"
    assert results[-1].status == "skipped"
    assert all(r.name not in {"GEMINI_API_KEY", "REPLICATE_API_TOKEN"} for r in results)


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("model,quality", [("gpt-image-2", "high"),
    ("gpt-image-2.5-sunburst", "xhigh"), ("gpt-image-2.5-flare", "max")])
def test_real_sdk_serializes_generation_and_edit_requests(tmp_path, editing, model, quality):
    openai = pytest.importorskip("openai")
    import httpx

    seen = []
    raw = image_bytes()

    def handler(request):
        seen.append(request)
        if editing:
            assert request.url.path == "/v1/images/edits"
            assert b'name="image[]"' in request.content
            assert raw in request.content
            assert model.encode() in request.content
            assert quality.encode() in request.content
        else:
            assert request.url.path == "/v1/images/generations"
            assert json.loads(request.content)["model"] == model
            assert json.loads(request.content)["quality"] == quality
        return httpx.Response(200, headers={"x-request-id": "req-sdk"}, json={
            "created": 1, "data": [{"b64_json": base64.b64encode(raw).decode()}],
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        })

    (tmp_path / "ref.png").write_bytes(raw)
    with openai.OpenAI(api_key="test-only", max_retries=0,
                       http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        result = run(tmp_path, client, model=model, quality=quality,
                     **({"image": "ref.png"} if editing else {}))
    assert result["request_id"] == "req-sdk" and len(seen) == 1


def test_real_sdk_quota_error_no_retry_and_safe_failure_record(tmp_path, monkeypatch):
    openai = pytest.importorskip("openai")
    import httpx

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(429, headers={"x-request-id": "req-quota"}, json={
            "error": {"message": "private body should not be logged", "type": "insufficient_quota", "code": "insufficient_quota"},
        })

    monkeypatch.setattr(openai_images, "load_dotenv_if_available", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-never-log")
    real_client = openai.OpenAI

    def create(**kwargs):
        assert kwargs["max_retries"] == 0 and kwargs["timeout"] == 300.0
        return real_client(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(openai, "OpenAI", create)
    with pytest.raises(RuntimeError, match="insufficient_quota"):
        run(tmp_path, None)
    record_text = next(tmp_path.rglob("job.json")).read_text()
    record = json.loads(record_text)
    assert record["status"] == "failed" and len(seen) == 1
    assert record["error"]["request_id"] == "req-quota"
    assert "test-secret-never-log" not in record_text
    assert "private body" not in record_text


@pytest.mark.parametrize('model', ['gpt-image-2', 'gpt-image-2-2026-04-21'])
@pytest.mark.parametrize('quality', ['xhigh', 'max'])
def test_legacy_model_rejects_new_quality(tmp_path, model, quality):
    with pytest.raises(RuntimeError, match='quality must be'):
        openai_images.validate_job(job(tmp_path, model=model, quality=quality))


def test_image_25_requires_exact_documented_id(tmp_path):
    with pytest.raises(RuntimeError, match='supports'):
        openai_images.validate_job(job(tmp_path, model='gpt-image-2.5'))


def test_real_sdk_mask_multipart_and_record(tmp_path):
    openai = pytest.importorskip('openai')
    import httpx
    raw = image_bytes()
    (tmp_path / 'ref.png').write_bytes(raw)
    (tmp_path / 'mask.png').write_bytes(raw)
    def handler(request):
        assert request.url.path == '/v1/images/edits'
        assert b'name="mask"' in request.content
        assert b'name="image[]"' in request.content
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(raw).decode()}]})
    with openai.OpenAI(api_key='fake', max_retries=0,
                      http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        result = run(tmp_path, client, image='ref.png', mask='mask.png')
    assert result['mask_sha256'] and result['elapsed_seconds'] >= 0
    assert json.loads((Path(result['job_dir']) / 'job.json').read_text())['mask_sha256'] == result['mask_sha256']
