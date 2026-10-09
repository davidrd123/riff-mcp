"""Bounded OpenAI image comparisons and local review artifacts."""
from __future__ import annotations

import html
import json
import uuid
from pathlib import Path
from typing import Any

from .cli import (
    build_job_hash, build_resolved_image_job, now_iso, resolve_output_root,
    slugify, summarize_job, write_json,
)
from .openai_images import BILLING_NOTICE, generate_openai_image_job, validate_assets
from .review_zoom import ZOOM_VIEWER


PRICING = {
    "as_of": "2026-09-08",
    "source": "https://developers.openai.com/api/docs/guides/image-generation",
    "usd_per_million_tokens": {"text_input": 5, "image_input": 8, "image_output": 30},
}


def estimate_cost(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Conservative list-price estimate; do not infer missing token categories."""
    usage = usage or {}
    details = usage.get("input_tokens_details") or {}
    values = [details.get("text_tokens"), details.get("image_tokens"), usage.get("output_tokens")]
    known = all(type(v) is int and v >= 0 for v in values)
    return {
        "usd": round(sum(v * r for v, r in zip(values, (5, 8, 30))) / 1_000_000, 8) if known else None,
        "basis": "Uncached list-price estimate; excludes discounts, tax, and account adjustments. Not an invoice.",
        "pricing": PRICING,
    }


def render_review(root: Path, manifest: dict[str, Any]) -> None:
    cards = []
    for entry in manifest["results"]:
        result = entry.get("result", {})
        images = "".join(
            '<img alt="Generated candidate" src="' + html.escape(
                Path(output["path"]).relative_to(root).as_posix(), quote=True) + '">'
            for output in result.get("outputs", [])
        )
        details = {key: entry.get(key) for key in ("model", "quality", "size", "status", "error_type", "elapsed_seconds", "cost_estimate")}
        cards.append('<article><h2>' + html.escape(entry["label"]) + '</h2>' + images +
                     '<pre>' + html.escape(json.dumps(details, indent=2)) + '</pre></article>')
    page = ('<!doctype html><meta charset="utf-8"><title>Image comparison</title>'
            '<style>body{font:16px system-ui;background:#171717;color:#eee;margin:2rem}'
            'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1rem}'
            'article{background:#242424;padding:1rem;border-radius:12px}img{width:100%;height:auto}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#9cd}</style>'
            '<h1>Image comparison</h1><p>' + html.escape(manifest["prompt"]) + '</p>'
            '<p>Status: ' + html.escape(manifest["status"]) +
            '. One independent sample per variant; no matched seed or automatic winner.</p>'
            '<p>Click an image to zoom, pan, and switch candidates at the same position.</p>'
            '<p><a href="comparison.json">Full record and usage</a></p><main>' + ''.join(cards) + '</main>')
    (root / "index.html").write_text(page + ZOOM_VIEWER, encoding="utf-8")


def compare_images(
    *, prompt: str, variants: list[dict[str, Any]], image: str | None = None,
    images: list[str] | None = None, mask: str | None = None, out_root: str | None = None,
    dry_run: bool = True, allow_api_billing: bool = False,
) -> dict[str, Any]:
    if not isinstance(variants, list) or not 2 <= len(variants) <= 8:
        raise RuntimeError("INVALID_INPUT: provide 2 to 8 variants, one output each")
    batch_path = (Path.cwd() / "<comparison>").resolve()
    jobs, labels = [], []
    for index, variant in enumerate(variants, 1):
        if not isinstance(variant, dict) or set(variant) - {"label", "model", "quality", "size"}:
            raise RuntimeError("INVALID_INPUT: variant keys are label, model, quality, size")
        if not all(isinstance(variant.get(k), str) and variant[k].strip() for k in ("model", "quality", "size")):
            raise RuntimeError("INVALID_INPUT: each variant needs explicit model, quality, size")
        if variant["quality"] == "auto" or variant["size"] == "auto":
            raise RuntimeError("INVALID_INPUT: comparisons require fixed quality and size")
        label = variant.get("label", f"Variant {index}")
        if not isinstance(label, str) or not label.strip():
            raise RuntimeError("INVALID_INPUT: label must be non-empty text")
        labels.append(label)
        jobs.append(build_resolved_image_job(
            prompt=prompt, title=f"variant-{index}", provider="openai", image=image,
            images=images, mask=mask, num_outputs=1, source_index=index,
            model=variant["model"], quality=variant["quality"], size=variant["size"],
            output_format="png", out_root=out_root,
        ))
    plan = {"status": "planned", "output_count": len(jobs), "billing_notice": BILLING_NOTICE,
            "billing_confirmation_required": True,
            "variants": [dict(summarize_job(i, job), label=label)
                         for i, (job, label) in enumerate(zip(jobs, labels), 1)]}
    if dry_run:
        return plan
    if allow_api_billing is not True:
        raise RuntimeError(f"API_BILLING_CONFIRMATION_REQUIRED: {BILLING_NOTICE}")
    # Validate the entire batch before any client is initialized or request submitted.
    for job in jobs:
        validate_assets(job, batch_path)
    root = resolve_output_root(out_root or "out") / "comparisons" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    manifest = {"status": "running", "created_at": now_iso(), "prompt": prompt,
                "output_count": len(jobs), "plan": plan, "results": [],
                "comparison_dir": str(root), "review_path": str(root / "index.html"),
                "record_path": str(root / "comparison.json")}
    write_json(root / "comparison.json", manifest)
    for job, label in zip(jobs, labels):
        job_record = (root / slugify(job["model"], max_len=80) /
                      f"{job['source_index']:02d}_{slugify(job['title'])}_{build_job_hash(job)}" / "job.json")
        entry = {"label": label, "job_record_path": str(job_record),
                 **{k: job[k] for k in ("model", "quality", "size")}}
        manifest["results"].append(entry)
        try:
            result = generate_openai_image_job(batch_path=batch_path, job=job, run_day_dir=root,
                                               allow_api_billing=True)
            entry.update(status="ok", result=result, elapsed_seconds=result.get("elapsed_seconds"),
                         cost_estimate=estimate_cost(result.get("usage")))
        except Exception as exc:
            # No retries, including uncertain timeouts. Leave existing job records intact.
            entry.update(status="failed", error_type=type(exc).__name__)
            if job_record.is_file():
                result = json.loads(job_record.read_text())
                entry.update(result=result, elapsed_seconds=result.get("elapsed_seconds"))
            manifest["status"] = "failed"
            break
        finally:
            write_json(root / "comparison.json", manifest)
            render_review(root, manifest)
    else:
        manifest["status"] = "ok"
    manifest["completed_at"] = now_iso()
    manifest["unsubmitted_count"] = len(jobs) - len(manifest["results"])
    estimates = [entry.get("cost_estimate", {}).get("usd") for entry in manifest["results"]]
    manifest["total_estimated_usd"] = round(sum(estimates), 8) if all(v is not None for v in estimates) else None
    write_json(root / "comparison.json", manifest)
    render_review(root, manifest)
    return manifest
