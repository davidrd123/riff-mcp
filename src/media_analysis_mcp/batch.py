"""Batch media analysis: one Gemini request per file over a set of files, in one of three tiers.

Built for dataset captioning (a versioned system-prompt file, a fixed user line, a folder of clips, stills or audio),
usable for any per-file question. One engine behind three doors: the ``riff-batch`` console script, the MCP tools
``start_batch_analysis`` / ``get_batch_analysis`` / ``cancel_batch_analysis`` (which launch the console script as its
own process), and :func:`run` for callers in Python.

Tiers
-----
``batch`` (default)
    The Gemini Batch API: half the standard price, asynchronous. Google's target is 24 hours and a job expires after
    48; our runs (3,400+ items, 2025-11 to 2026-10) came back in minutes. The first ``probe`` items run on the standard
    tier first, so a refused setting fails in seconds instead of after the wait, and their tokens price the rest.
    Media is uploaded per chunk just before its job is created (uploaded files also expire after 48 hours); the job is
    created once and never re-created on an error (a lost reply may still have made a billed job).
``standard``
    Ordinary calls, ``concurrency`` at a time.
``flex``
    Ordinary calls with ``service_tier: flex``: half price, synchronous, a 1 to 15 minute target, and refusals (503,
    429) under load; retried with backoff, and optionally finished on standard (``flex_fallback``, which costs full
    price).

Run folder
----------
::

    request.json      the settings (rewritten on a rerun; earlier ones kept in request-history.ndjson)
    run.json          status, counts, tokens, cost, the batch jobs, the runner's pid
    events.ndjson     an append-only log
    items/<key>.json  one record per input: text, finish reason, tokens, attempts, tier, resume key
    texts/<key>.txt   the reply of each item that succeeded
    batches/<n>/      requests.jsonl, job.json (written at submit), results.jsonl (the raw results)
    superseded/       item records replaced because their resume key changed
    CANCEL            written by cancel; the runner stops and cancels a running batch job

An item is done when its record succeeded under the same resume key: the file's sha256 and every setting that shapes
the reply (model, fps, system prompt sha256, user prompt, thinking level, temperature, max output, media resolution).
A rerun on any day skips done items and re-runs changed ones. A prompt version name already used in a folder with
different text is refused.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import mimetypes
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from . import gemini_media

TIERS = ("batch", "standard", "flex")
THINKING_LEVELS = ("minimal", "low", "medium", "high")
RETRY_CODES = (429, 500, 503, 504)
TERMINAL = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}
PRICES = json.loads((Path(__file__).with_name("prices.json")).read_text())
# Rough token rates for the dry-run estimate only (measured on gemini-3.8-flash captioning, 2026-10): about 66 a
# sampled video frame, 32 a second of audio, about 4 characters a text token. The batch tier's probe measures them.
VIDEO_TOKENS_PER_FRAME, AUDIO_TOKENS_PER_S, IMAGE_TOKENS, CHARS_PER_TOKEN = 66, 32, 1100, 4
OUTPUT_TOKENS_GUESS = 400


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, data: Any) -> None:
    """Write JSON atomically (a temp file in the same folder, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def media_kind(path: Path) -> tuple[str, str]:
    mime, _ = mimetypes.guess_type(path.name)
    if not mime:
        raise ValueError(f"UNSUPPORTED_MEDIA: cannot tell the type of {path.name}")
    kind = mime.split("/")[0]
    if kind not in ("video", "image", "audio"):
        raise ValueError(f"UNSUPPORTED_MEDIA: {path.name} is {mime}")
    return kind, mime


@dataclass
class Spec:
    """A run's settings. Paths are stored absolute."""

    out_dir: str
    system_prompt_path: str
    user_prompt: str
    inputs: list[str]
    input_root: Optional[str] = None
    tier: str = "batch"
    model: str = "gemini-3.8-flash"
    fps: Optional[float] = None
    thinking_level: Optional[str] = "low"
    temperature: Optional[float] = None
    max_output_tokens: int = 65_536
    media_resolution: Optional[str] = None
    prompt_version: Optional[str] = None
    concurrency: int = 8
    upload_concurrency: int = 8
    chunk_size: int = 200
    probe: int = 1
    max_attempts_standard: int = 3
    max_attempts_flex: int = 6
    flex_fallback: bool = False
    keep_uploads: bool = False
    poll_interval_s: float = 30.0
    max_wait_hours: float = 47.0
    system_prompt_sha256: str = ""
    created_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if self.tier not in TIERS:
            raise ValueError(f"INVALID_TIER: {self.tier!r}; one of {', '.join(TIERS)}")
        if self.thinking_level is not None and self.thinking_level.lower() not in THINKING_LEVELS:
            raise ValueError(f"INVALID_THINKING_LEVEL: {self.thinking_level!r}")
        if self.fps is not None and not (0 < self.fps <= 24):
            raise ValueError("INVALID_FPS: fps must be in (0, 24]")
        if not self.inputs:
            raise ValueError("NO_INPUTS: no files to analyse")
        if self.chunk_size < 1 or self.concurrency < 1 or self.probe < 0:
            raise ValueError("INVALID_LIMITS: chunk_size and concurrency must be at least 1, probe at least 0")

    def settings_for_key(self) -> dict[str, Any]:
        return {"model": self.model, "fps": self.fps, "system_prompt_sha256": self.system_prompt_sha256,
                "user_prompt_sha256": sha256_text(self.user_prompt),
                "thinking_level": (self.thinking_level or "").lower() or None, "temperature": self.temperature,
                "max_output_tokens": self.max_output_tokens, "media_resolution": self.media_resolution}


def make_spec(*, out_dir: str, system_prompt_path: str, user_prompt: str, input_dir: Optional[str] = None,
              pattern: str = "*", inputs: Optional[list[str]] = None, **settings: Any) -> Spec:
    """Resolve the inputs and the prompt file into a validated :class:`Spec`."""
    sys_path = Path(system_prompt_path).expanduser().resolve()
    if not sys_path.is_file():
        raise ValueError(f"SYSTEM_PROMPT_NOT_FOUND: {sys_path}")
    files: list[Path] = []
    root = None
    if input_dir:
        root = Path(input_dir).expanduser().absolute()
        files += sorted(p for p in root.glob(pattern) if p.is_file() and not p.name.startswith("."))
    files += [Path(p).expanduser().absolute() for p in (inputs or [])]
    clean = {k: v for k, v in settings.items() if v is not None or k in ("fps", "temperature", "media_resolution",
                                                                           "prompt_version", "thinking_level")}
    spec = Spec(out_dir=str(Path(out_dir).expanduser().absolute()), system_prompt_path=str(sys_path),
                user_prompt=user_prompt, inputs=[str(p) for p in files], input_root=str(root) if root else None,
                system_prompt_sha256=sha256_file(sys_path), **clean)
    spec.validate()
    return spec


def item_key(spec: Spec, path: Path) -> str:
    if spec.input_root:
        try:
            rel = path.relative_to(spec.input_root)
            return rel.with_suffix("").as_posix().replace("/", "__")
        except ValueError:
            pass
    return path.stem


class Run:
    """A run folder: its records, its log and its plan."""

    def __init__(self, out_dir: str | Path):
        self.dir = Path(out_dir)
        self.lock = threading.Lock()

    # -- files
    @property
    def request_path(self) -> Path:
        return self.dir / "request.json"

    @property
    def run_path(self) -> Path:
        return self.dir / "run.json"

    def item_path(self, key: str) -> Path:
        return self.dir / "items" / f"{key}.json"

    def event(self, kind: str, **data: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": utc_now(), "event": kind, **data}, ensure_ascii=False) + "\n"
        with self.lock, open(self.dir / "events.ndjson", "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line)

    def state(self) -> dict[str, Any]:
        return read_json(self.run_path, {}) or {}

    def update_state(self, **changes: Any) -> dict[str, Any]:
        with self.lock:
            st = read_json(self.run_path, {}) or {}
            st.update(changes, updated_at=utc_now())
            write_json(self.run_path, st)
            return st

    def cancelled(self) -> bool:
        return (self.dir / "CANCEL").exists()

    def write_item(self, rec: dict[str, Any]) -> None:
        write_json(self.item_path(rec["key"]), rec)
        if rec.get("ok") and rec.get("text") is not None:
            p = self.dir / "texts" / f"{rec['key']}.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f".{p.name}.tmp")
            tmp.write_text(rec["text"].rstrip() + "\n", encoding="utf-8")
            os.replace(tmp, p)

    # -- the plan
    def save_request(self, spec: Spec) -> None:
        old = read_json(self.request_path)
        if old == asdict(spec):
            return
        if old:
            if (spec.prompt_version and old.get("prompt_version") == spec.prompt_version
                    and old.get("system_prompt_sha256") != spec.system_prompt_sha256):
                raise ValueError(f"PROMPT_VERSION_CHANGED: version {spec.prompt_version!r} was used in this folder "
                                 "with different text; give the new text a new version name")
            with open(self.dir / "request-history.ndjson", "a", encoding="utf-8") as f:
                f.write(json.dumps(old, ensure_ascii=False) + "\n")
        write_json(self.request_path, asdict(spec))

    def plan(self, spec: Spec) -> list[dict[str, Any]]:
        """Each input with its key, sha256, resume key and status: done, todo or stale (done under other settings)."""
        settings = spec.settings_for_key()
        items, seen = [], {}
        for raw in spec.inputs:
            path = Path(raw)
            if not path.is_file():
                raise ValueError(f"INPUT_NOT_FOUND: {path}")
            key = item_key(spec, path)
            if key in seen:
                raise ValueError(f"DUPLICATE_KEY: {seen[key]} and {path} both map to {key!r}")
            seen[key] = path
            kind, mime = media_kind(path)
            digest = sha256_file(path)
            resume = sha256_text(json.dumps({"media_sha256": digest, **settings}, sort_keys=True))
            old = read_json(self.item_path(key))
            status = "todo"
            if old and old.get("ok"):
                status = "done" if old.get("resume_key") == resume else "stale"
            items.append({"key": key, "path": str(path), "kind": kind, "mime": mime, "sha256": digest,
                          "resume_key": resume, "status": status})
        return items

    def supersede(self, key: str) -> None:
        src = self.item_path(key)
        if src.exists():
            dst = self.dir / "superseded" / f"{key}.{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), dst)


# -- estimates and costs


def _duration_s(path: str) -> Optional[float]:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out) if out else None
    except Exception:
        return None


def rates(model: str, tier: str) -> Optional[tuple[float, float]]:
    """USD per 1M input and output tokens for a tier (flex and batch at the batch rate), or None if unpriced."""
    p = PRICES["models"].get(model)
    if not p:
        return None
    return (p["input"], p["output"]) if tier == "standard" else (p["batch_input"], p["batch_output"])


def cost_usd(usage: dict[str, int], model: str, tier: str) -> Optional[float]:
    r = rates(model, tier)
    if r is None:
        return None
    return (usage.get("prompt", 0) * r[0] + (usage.get("output", 0) + usage.get("thoughts", 0)) * r[1]) / 1e6


def estimate(spec: Spec, todo: list[dict[str, Any]]) -> dict[str, Any]:
    """A rough cost estimate before any call: text by length, video by sampled frames, audio by seconds."""
    text_tokens = (len(Path(spec.system_prompt_path).read_text(encoding="utf-8")) + len(spec.user_prompt)) / CHARS_PER_TOKEN
    total_in = 0.0
    for it in todo:
        media = IMAGE_TOKENS
        if it["kind"] in ("video", "audio"):
            dur = _duration_s(it["path"]) or 10.0
            media = dur * AUDIO_TOKENS_PER_S if it["kind"] == "audio" else dur * (spec.fps or 1.0) * VIDEO_TOKENS_PER_FRAME + dur * AUDIO_TOKENS_PER_S
        total_in += text_tokens + media
    usage = {"prompt": int(total_in), "output": OUTPUT_TOKENS_GUESS * len(todo), "thoughts": 0}
    c = cost_usd(usage, spec.model, spec.tier)
    return {"items": len(todo), "input_tokens": usage["prompt"], "output_tokens_guess": usage["output"],
            "cost_usd": None if c is None else round(c, 4), "tier": spec.tier, "model": spec.model,
            "note": "rough: text by length, video at ~66 tokens a sampled frame, audio 32 a second; output guessed. "
                    "The batch tier's probe measures real tokens before submitting."}


def usage_from(obj: Any) -> dict[str, int]:
    """Token counts from an SDK usage object or a REST usageMetadata dict."""
    if obj is None:
        return {"prompt": 0, "output": 0, "thoughts": 0, "cached": 0, "total": 0}
    pairs = {"prompt": ("prompt_token_count", "promptTokenCount"), "output": ("candidates_token_count", "candidatesTokenCount"),
             "thoughts": ("thoughts_token_count", "thoughtsTokenCount"),
             "cached": ("cached_content_token_count", "cachedContentTokenCount"), "total": ("total_token_count", "totalTokenCount")}
    out = {}
    for k, (snake, camel) in pairs.items():
        v = obj.get(camel, obj.get(snake)) if isinstance(obj, dict) else getattr(obj, snake, None)
        out[k] = int(v or 0)
    return out


# -- Gemini


def make_client(timeout_s: Optional[float] = None) -> tuple[Any, Any]:
    """A google-genai client (key from GEMINI_API_KEY or riff's .env) with an optional request timeout."""
    gemini_media._load_dotenv_if_available()
    from google import genai  # type: ignore
    from google.genai import types as gtypes  # type: ignore

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("API_KEY_MISSING: GEMINI_API_KEY is not set (riff's .env or the environment)")
    opts = gtypes.HttpOptions(timeout=int(timeout_s * 1000)) if timeout_s else None
    return genai.Client(api_key=key, http_options=opts), gtypes


def upload(client: Any, it: dict[str, Any]) -> Any:
    f = client.files.upload(file=str(Path(it["path"]).resolve()), config={"mime_type": it["mime"], "display_name": it["key"][:100]})
    deadline = time.time() + 600
    while True:
        state = (getattr(getattr(f, "state", None), "name", None) or str(getattr(f, "state", ""))).upper().split(".")[-1]
        if state in ("ACTIVE", ""):
            return f
        if state == "FAILED" or time.time() > deadline:
            gemini_media.cleanup_uploaded(client, f)
            raise RuntimeError(f"UPLOAD_FAILED: {it['key']} ended {state}")
        time.sleep(2)
        f = client.files.get(name=f.name)


def file_part_rest(spec: Spec, it: dict[str, Any], f: Any) -> dict[str, Any]:
    part: dict[str, Any] = {"file_data": {"file_uri": f.uri, "mime_type": getattr(f, "mime_type", None) or it["mime"]}}
    if it["kind"] == "video" and spec.fps is not None:
        part["video_metadata"] = {"fps": spec.fps, "start_offset": "0s"}
    return part


def batch_line(spec: Spec, system_text: str, it: dict[str, Any], f: Any) -> dict[str, Any]:
    """One JSONL line, in the shape that works (quixotron-f, 2025-11): system instruction as parts, snake_case content
    parts, camelCase generationConfig, upper-case thinking level."""
    gen: dict[str, Any] = {"responseMimeType": "text/plain", "maxOutputTokens": spec.max_output_tokens}
    if spec.temperature is not None:
        gen["temperature"] = spec.temperature
    if spec.thinking_level:
        gen["thinkingConfig"] = {"thinkingLevel": spec.thinking_level.upper()}
    if spec.media_resolution:
        gen["mediaResolution"] = spec.media_resolution
    return {"key": it["key"], "request": {
        "system_instruction": {"parts": [{"text": system_text}]},
        "contents": [{"role": "user", "parts": [file_part_rest(spec, it, f), {"text": spec.user_prompt}]}],
        "generationConfig": gen}}


def call_one(client: Any, gtypes: Any, spec: Spec, system_text: str, it: dict[str, Any], f: Any, tier: str,
             max_attempts: int, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """One standard or flex request with bounded retries on 429/5xx; every attempt is recorded."""
    cfg: dict[str, Any] = {"system_instruction": system_text, "response_mime_type": "text/plain",
                           "max_output_tokens": spec.max_output_tokens}
    if spec.temperature is not None:
        cfg["temperature"] = spec.temperature
    if spec.thinking_level:
        cfg["thinking_config"] = gtypes.ThinkingConfig(thinking_level=spec.thinking_level.upper())
    if spec.media_resolution:
        cfg["media_resolution"] = spec.media_resolution
    if tier == "flex":
        cfg["service_tier"] = "flex"
    kwargs: dict[str, Any] = {"file_data": gtypes.FileData(file_uri=f.uri, mime_type=getattr(f, "mime_type", None) or it["mime"])}
    if it["kind"] == "video" and spec.fps is not None:
        kwargs["video_metadata"] = gtypes.VideoMetadata(fps=spec.fps, start_offset="0s")
    contents = [gtypes.Part(**kwargs), spec.user_prompt]
    attempts: list[dict[str, Any]] = []
    t0 = time.time()
    for n in range(1, max_attempts + 1):
        a0 = time.time()
        try:
            r = client.models.generate_content(model=spec.model, contents=contents, config=gtypes.GenerateContentConfig(**cfg))
        except Exception as exc:  # noqa: BLE001 - the code decides whether to retry
            code = getattr(exc, "code", None)
            attempts.append({"at": utc_now(), "tier": tier, "seconds": round(time.time() - a0, 1),
                             "error": f"{code}: {str(exc)[:300]}"})
            if code not in RETRY_CODES or n == max_attempts:
                break
            sleep(min(300.0, 10.0 * 2 ** (n - 1)) + random.uniform(0, 5))
            continue
        attempts.append({"at": utc_now(), "tier": tier, "seconds": round(time.time() - a0, 1), "result": "ok"})
        cand = (getattr(r, "candidates", None) or [None])[0]
        finish = getattr(getattr(cand, "finish_reason", None), "name", None) or (str(cand.finish_reason) if cand is not None and getattr(cand, "finish_reason", None) else None)
        text = getattr(r, "text", None)
        return {"ok": bool(text and text.strip()), "text": text.strip() if text else None, "finish_reason": finish,
                "usage": usage_from(getattr(r, "usage_metadata", None)), "seconds": round(time.time() - t0, 1),
                "attempts": attempts, "tier": tier, **({} if text and text.strip() else {"error": "EMPTY_RESPONSE"})}
    return {"ok": False, "text": None, "usage": usage_from(None), "seconds": round(time.time() - t0, 1), "attempts": attempts,
            "tier": tier, "error": attempts[-1].get("error") if attempts else "NO_ATTEMPT"}


def parse_results(raw: bytes, keys: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Map a batch results JSONL to per-key results; report duplicate, unknown and missing keys."""
    out: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {"duplicate": [], "unknown": [], "missing": []}
    wanted = set(keys)
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = row.get("key") or (row.get("metadata") or {}).get("key")
        if key not in wanted:
            report["unknown"].append(key)
            continue
        if key in out:
            report["duplicate"].append(key)
            continue
        resp = row.get("response") or {}
        cand = (resp.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought")).strip()
        rec = {"ok": bool(text), "text": text or None, "finish_reason": cand.get("finishReason"),
               "usage": usage_from(resp.get("usageMetadata"))}
        if row.get("error") or not text:
            rec["ok"] = False
            rec["error"] = json.dumps(row.get("error"))[:500] if row.get("error") else "EMPTY_RESPONSE"
        out[key] = rec
    report["missing"] = sorted(wanted - set(out))
    return out, report


# -- the runner


def _record(it: dict[str, Any], result: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"key": it["key"], "media": it["path"], "media_sha256": it["sha256"], "resume_key": it["resume_key"],
            "at": utc_now(), **result, **extra}


def _run_interactive(run: Run, spec: Spec, items: list[dict[str, Any]], tier: str, client: Any, gtypes: Any,
                     system_text: str, fallback: Optional[tuple[Any, Any]] = None,
                     sleep: Callable[[float], None] = time.sleep) -> None:
    max_attempts = spec.max_attempts_flex if tier == "flex" else spec.max_attempts_standard

    def one(it: dict[str, Any]) -> None:
        if run.cancelled():
            return
        try:
            f = upload(client, it)
        except Exception as exc:  # noqa: BLE001
            run.write_item(_record(it, {"ok": False, "tier": tier, "error": f"UPLOAD_FAILED: {exc}"[:500],
                                        "usage": usage_from(None), "attempts": []}))
            return
        try:
            res = call_one(client, gtypes, spec, system_text, it, f, tier, max_attempts, sleep)
            if not res["ok"] and tier == "flex" and spec.flex_fallback and fallback is not None:
                fb = call_one(fallback[0], fallback[1], spec, system_text, it, f, "standard", spec.max_attempts_standard, sleep)
                fb["attempts"] = res["attempts"] + fb["attempts"]
                res = fb
            run.write_item(_record(it, res))
            run.event("item", key=it["key"], tier=res["tier"], ok=res["ok"], attempts=len(res["attempts"]))
        finally:
            if not spec.keep_uploads:
                gemini_media.cleanup_uploaded(client, f)

    with ThreadPoolExecutor(spec.concurrency) as ex:
        list(ex.map(one, items))


def _poll_and_collect(run: Run, spec: Spec, client: Any, bdir: Path, sleep: Callable[[float], None]) -> str:
    job = read_json(bdir / "job.json")
    started = job.get("submitted_epoch", time.time())
    while True:
        if run.cancelled() and job.get("state") not in TERMINAL and not job.get("cancel_sent"):
            job["cancel_sent"] = utc_now()
            write_json(bdir / "job.json", job)
            try:
                client.batches.cancel(name=job["job"])
                run.event("batch_cancel_requested", job=job["job"])
            except Exception as exc:  # noqa: BLE001
                run.event("batch_cancel_failed", job=job["job"], error=str(exc)[:300])
        b = client.batches.get(name=job["job"])
        state = getattr(getattr(b, "state", None), "name", None) or str(getattr(b, "state", ""))
        state = state.replace("BATCH_STATE_", "JOB_STATE_")
        if state != job.get("state"):
            job.setdefault("states", []).append({"at": utc_now(), "state": state, "after_s": round(time.time() - started)})
            job["state"] = state
            write_json(bdir / "job.json", job)
            run.event("batch_state", job=job["job"], state=state)
            _sync_batches(run)
        if state in TERMINAL:
            break
        if time.time() - started > spec.max_wait_hours * 3600:
            run.event("batch_wait_limit", job=job["job"])
            return "waiting"
        sleep(spec.poll_interval_s)
    for k in ("create_time", "end_time", "update_time"):
        v = getattr(b, k, None)
        if v is not None:
            job[k] = v.isoformat() if hasattr(v, "isoformat") else str(v)
    keys = job["keys"]
    items = {it["key"]: it for it in job["items"]}
    if state == "JOB_STATE_SUCCEEDED" and getattr(getattr(b, "dest", None), "file_name", None):
        raw = client.files.download(file=b.dest.file_name)
        (bdir / "results.jsonl").write_bytes(raw)
        results, report = parse_results(raw, keys)
        job["report"] = report
        for key in keys:
            res = results.get(key) or {"ok": False, "text": None, "usage": usage_from(None), "error": "MISSING_FROM_RESULTS"}
            run.write_item(_record(items[key], {**res, "tier": "batch", "attempts": [], "batch_job": job["job"]}))
    else:
        job["error"] = str(getattr(b, "error", None))[:500]
        for key in keys:
            run.write_item(_record(items[key], {"ok": False, "text": None, "usage": usage_from(None), "tier": "batch",
                                                "attempts": [], "batch_job": job["job"], "error": state}))
    job["collected_at"] = utc_now()
    write_json(bdir / "job.json", job)
    if not spec.keep_uploads:
        for name in job.get("uploads", []) + ([job["requests_file"]] if job.get("requests_file") else []):
            gemini_media.cleanup_uploaded(client, type("F", (), {"name": name})())
    run.event("batch_collected", job=job["job"], state=state, report=job.get("report"))
    return state


def _sync_batches(run: Run) -> None:
    rows = []
    for p in sorted((run.dir / "batches").glob("*/job.json")):
        j = read_json(p, {})
        rows.append({"chunk": p.parent.name, "job": j.get("job"), "state": j.get("state"), "items": len(j.get("keys", [])),
                     "submitted_at": j.get("submitted_at"), "collected": bool(j.get("collected_at"))})
    run.update_state(batches=rows)


def _submit_chunk(run: Run, spec: Spec, client: Any, system_text: str, chunk: list[dict[str, Any]], n: int) -> Path:
    bdir = run.dir / "batches" / f"{n:03d}"
    bdir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(spec.upload_concurrency) as ex:
        uploaded = list(ex.map(lambda it: (it, _try_upload(client, it)), chunk))
    failed = [it for it, f in uploaded if isinstance(f, Exception)]
    for it, f in uploaded:
        if isinstance(f, Exception):
            run.write_item(_record(it, {"ok": False, "tier": "batch", "error": f"UPLOAD_FAILED: {f}"[:500],
                                        "usage": usage_from(None), "attempts": []}))
    good = [(it, f) for it, f in uploaded if not isinstance(f, Exception)]
    if failed:
        run.event("upload_failures", chunk=n, keys=[it["key"] for it in failed])
    if not good:
        raise RuntimeError("UPLOAD_FAILED: no file in this chunk uploaded")
    src = bdir / "requests.jsonl"
    src.write_text("".join(json.dumps(batch_line(spec, system_text, it, f), ensure_ascii=False) + "\n" for it, f in good),
                   encoding="utf-8")
    req_file = client.files.upload(file=str(src), config={"mime_type": "jsonl", "display_name": f"{run.dir.name}-{n:03d}"[:100]})
    display = f"riff-batch {run.dir.name} {n:03d}"[:120]
    job = {"display_name": display, "requests_file": req_file.name, "uploads": [f.name for _, f in good],
           "keys": [it["key"] for it, _ in good], "items": [it for it, _ in good], "model": spec.model,
           "prepared_at": utc_now(), "state": "CREATING"}
    write_json(bdir / "job.json", job)
    try:
        b = client.batches.create(model=spec.model, src=req_file.name, config={"display_name": display})
    except Exception as exc:  # noqa: BLE001 - never re-create: a lost reply may still have made a billed job
        job.update(state="CREATE_UNCERTAIN", error=str(exc)[:500])
        write_json(bdir / "job.json", job)
        run.event("batch_create_uncertain", chunk=n, error=str(exc)[:300])
        raise RuntimeError(f"BATCH_CREATE_UNCERTAIN: chunk {n:03d}: {exc}. Not re-created; check `riff-batch status "
                           f"{run.dir}` (it lists jobs named {display!r}) before running again.") from exc
    job.update(job=b.name, state=(getattr(getattr(b, "state", None), "name", None) or "JOB_STATE_PENDING"),
               submitted_at=utc_now(), submitted_epoch=time.time(), states=[{"at": utc_now(), "state": "SUBMITTED"}])
    write_json(bdir / "job.json", job)
    run.event("batch_submitted", chunk=n, job=b.name, items=len(good))
    _sync_batches(run)
    return bdir


def _try_upload(client: Any, it: dict[str, Any]) -> Any:
    try:
        return upload(client, it)
    except Exception as exc:  # noqa: BLE001
        return exc


def find_jobs(client: Any, display_prefix: str, limit: int = 100) -> list[dict[str, Any]]:
    """Batch jobs whose display name starts with the prefix (to settle an uncertain create)."""
    found = []
    for b in client.batches.list(config={"page_size": limit}):
        name = getattr(b, "display_name", "") or ""
        if name.startswith(display_prefix):
            found.append({"job": b.name, "display_name": name, "state": getattr(getattr(b, "state", None), "name", None)})
    return found


def summarize(run: Run, spec: Spec) -> dict[str, Any]:
    recs = [read_json(p) for p in sorted((run.dir / "items").glob("*.json"))]
    wanted = {item_key(spec, Path(p)) for p in spec.inputs}
    recs = [r for r in recs if r and r["key"] in wanted]
    tokens = {k: sum(r.get("usage", {}).get(k, 0) for r in recs) for k in ("prompt", "output", "thoughts", "cached")}
    cost, unpriced = 0.0, False
    by_tier: dict[str, int] = {}
    for r in recs:
        by_tier[r.get("tier", "?")] = by_tier.get(r.get("tier", "?"), 0) + 1
        c = cost_usd(r.get("usage", {}), spec.model, r.get("tier", spec.tier))
        if c is None:
            unpriced = True
        else:
            cost += c
    ok = sum(1 for r in recs if r.get("ok"))
    return {"total": len(wanted), "succeeded": ok, "failed": sum(1 for r in recs if not r.get("ok")),
            "not_run": len(wanted) - len(recs), "by_tier": by_tier, "tokens": tokens,
            "cost_usd": None if unpriced else round(cost, 4),
            "cost_note": "tokens x the tier's rate in prices.json (flex and batch at the batch rate); cached tokens at the full input rate"}


def run(spec: Spec, *, dry_run: bool = False, client_factory: Callable[..., tuple[Any, Any]] = make_client,
        sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Plan and run (or, with ``dry_run``, only plan and estimate). Re-entrant: rerun on the same folder to resume,
    including polling and collecting batch jobs already submitted."""
    spec.validate()
    rdir = Path(spec.out_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    r = Run(rdir)
    items = r.plan(spec)
    todo = [it for it in items if it["status"] != "done"]
    est = estimate(spec, todo)
    plan_info = {"items": len(items), "done": len(items) - len(todo), "todo": sum(1 for it in todo if it["status"] == "todo"),
                 "stale": sum(1 for it in todo if it["status"] == "stale"), "estimate": est}
    if dry_run:
        return {"dry_run": True, "out_dir": str(rdir), "tier": spec.tier, "model": spec.model, **plan_info}
    lockf = open(rdir / "runner.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError(f"RUN_BUSY: another runner holds {rdir / 'runner.lock'}")
    try:
        r.save_request(spec)
        r.update_state(status="running", pid=os.getpid(), started_at=utc_now(), tier=spec.tier, model=spec.model,
                       plan=plan_info, error=None)
        r.event("run_start", tier=spec.tier, model=spec.model, **{k: v for k, v in plan_info.items() if k != "estimate"})
        system_text = Path(spec.system_prompt_path).read_text(encoding="utf-8").strip()
        client, gtypes = client_factory(900 if spec.tier == "flex" else 600)
        status = "succeeded"
        # Batch jobs a previous runner submitted and did not collect come first.
        for jp in sorted((rdir / "batches").glob("*/job.json")):
            j = read_json(jp, {})
            if j.get("job") and not j.get("collected_at"):
                r.event("batch_resume", job=j["job"])
                if _poll_and_collect(r, spec, client, jp.parent, sleep) == "waiting":
                    status = "waiting"
            elif j.get("state") in ("CREATING", "CREATE_UNCERTAIN"):
                raise RuntimeError(f"BATCH_CREATE_UNCERTAIN: {jp.parent.name} has no job id recorded; check "
                                   f"`riff-batch status {rdir}` before running again")
        if status == "waiting":
            r.update_state(status="waiting", summary=summarize(r, spec))
            return {"out_dir": str(rdir), "status": "waiting", **r.state()}
        items = r.plan(spec)
        todo = [it for it in items if it["status"] != "done"]
        for it in todo:
            if it["status"] == "stale":
                r.supersede(it["key"])
        if spec.tier in ("standard", "flex"):
            fallback = client_factory(600) if (spec.tier == "flex" and spec.flex_fallback) else None
            _run_interactive(r, spec, todo, spec.tier, client, gtypes, system_text, fallback, sleep)
        else:
            probe = todo[: spec.probe]
            if probe:
                _run_interactive(r, spec, probe, "standard", client, gtypes, system_text, None, sleep)
                probed = [read_json(r.item_path(it["key"])) for it in probe]
                if not all(p and p.get("ok") for p in probed):
                    errs = [p.get("error") for p in probed if p and not p.get("ok")]
                    raise RuntimeError(f"PROBE_FAILED: the standard-tier probe failed ({errs}); nothing was submitted")
                per = [cost_usd(p["usage"], spec.model, "batch") for p in probed]
                rest = len(todo) - len(probe)
                if all(c is not None for c in per) and per:
                    r.update_state(probe={"items": len(probe), "measured_batch_cost_each_usd": round(sum(per) / len(per), 6),
                                          "remaining": rest, "remaining_cost_usd": round(sum(per) / len(per) * rest, 4)})
            rest_items = todo[spec.probe:]
            for n0 in range(0, len(rest_items), spec.chunk_size):
                if r.cancelled():
                    status = "cancelled"
                    break
                chunk = rest_items[n0: n0 + spec.chunk_size]
                n = len(list((rdir / "batches").glob("*/job.json"))) + 1
                bdir = _submit_chunk(r, spec, client, system_text, chunk, n)
                st = _poll_and_collect(r, spec, client, bdir, sleep)
                if st == "waiting":
                    status = "waiting"
                    break
                if st == "JOB_STATE_CANCELLED":
                    status = "cancelled"
                    break
        summary = summarize(r, spec)
        if r.cancelled():
            status = "cancelled"
        elif status == "succeeded" and summary["succeeded"] < summary["total"]:
            status = "partial"
        r.update_state(status=status, finished_at=utc_now(), summary=summary, pid=None)
        r.event("run_end", status=status, **{k: summary[k] for k in ("succeeded", "failed", "not_run")})
        return {"out_dir": str(rdir), "status": status, "summary": summary}
    except Exception as exc:
        r.update_state(status="failed", error=str(exc)[:1000], pid=None, summary=summarize(r, spec))
        r.event("run_failed", error=str(exc)[:500])
        raise
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()


def load_spec(out_dir: str | Path) -> Spec:
    data = read_json(Path(out_dir) / "request.json")
    if not data:
        raise ValueError(f"NO_REQUEST: {out_dir} has no request.json")
    return Spec(**data)


def status(out_dir: str | Path, *, items: bool = False, limit: int = 50) -> dict[str, Any]:
    r = Run(out_dir)
    st = r.state()
    if not st and not r.request_path.exists():
        raise ValueError(f"NO_RUN: {out_dir} is not a run folder")
    pid = st.get("pid")
    alive = False
    if pid:
        try:
            os.kill(int(pid), 0)
            alive = True
        except (OSError, ValueError):
            alive = False
    out = {"out_dir": str(Path(out_dir)), **st, "runner_alive": alive, "cancel_requested": r.cancelled()}
    if r.request_path.exists():
        out["summary"] = summarize(r, load_spec(out_dir))
    if items:
        rows = []
        for p in sorted((r.dir / "items").glob("*.json"))[:limit]:
            rec = read_json(p, {})
            rows.append({"key": rec.get("key"), "ok": rec.get("ok"), "tier": rec.get("tier"),
                         "finish_reason": rec.get("finish_reason"), "error": rec.get("error"),
                         "text": (rec.get("text") or "")[:300]})
        out["items"] = rows
    return out


def request_cancel(out_dir: str | Path) -> dict[str, Any]:
    r = Run(out_dir)
    if not r.request_path.exists():
        raise ValueError(f"NO_RUN: {out_dir} is not a run folder")
    (r.dir / "CANCEL").write_text(utc_now() + "\n")
    r.event("cancel_requested")
    return status(out_dir)


def launch_detached(out_dir: str | Path) -> int:
    """Start ``riff-batch resume <out_dir>`` as its own process (it outlives the caller); return its pid."""
    rdir = Path(out_dir)
    log = open(rdir / "runner.log", "a")
    proc = subprocess.Popen([sys.executable, "-m", "media_analysis_mcp.batch_cli", "resume", str(rdir)],
                            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                            env=os.environ.copy())
    Run(rdir).update_state(status="starting", pid=proc.pid, launched_at=utc_now())
    return proc.pid


__all__ = ["Spec", "TIERS", "make_spec", "run", "status", "request_cancel", "launch_detached", "load_spec",
           "estimate", "parse_results", "batch_line", "find_jobs"]
