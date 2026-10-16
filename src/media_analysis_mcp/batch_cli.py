"""riff-batch: one Gemini request per file over a folder, on the batch (default), standard or flex tier.

    riff-batch run --sys PROMPT.md --user "Caption this window." --input-dir CLIPS --pattern "*.mp4" \\
        --out RUN_DIR [--tier batch|standard|flex] [--fps 8] [--thinking-level low] [--temperature 0.7] \\
        [--prompt-version v2] [--dry-run] [--detach]
    riff-batch resume RUN_DIR        # continue a run: poll and collect submitted jobs, run what is left
    riff-batch status RUN_DIR [--items]
    riff-batch cancel RUN_DIR        # the runner stops and cancels a running batch job
    riff-batch find-jobs PREFIX      # list batch jobs by display name (settles an uncertain create)

See media_analysis_mcp/batch.py for the run folder and the tiers. ``--dry-run`` plans and estimates the cost without a
call; the batch tier then runs ``--probe`` items (default 1) on the standard tier before submitting anything.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import batch


def _print(data: object) -> None:
    print(json.dumps(data, indent=1, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="riff-batch", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="plan and run a batch over a folder or a list of files")
    r.add_argument("--sys", required=True, help="the system prompt file (sent whole)")
    r.add_argument("--user", required=True, help="the user line sent with every file")
    r.add_argument("--input-dir")
    r.add_argument("--pattern", default="*")
    r.add_argument("--inputs", nargs="*", default=[])
    r.add_argument("--out", required=True, help="the run folder")
    r.add_argument("--tier", choices=batch.TIERS, default="batch")
    r.add_argument("--model", default="gemini-3.8-flash")
    r.add_argument("--fps", type=float)
    r.add_argument("--thinking-level", default="low", help="minimal, low, medium, high, or none to leave the model's default")
    r.add_argument("--temperature", type=float)
    r.add_argument("--max-output", type=int, default=65_536)
    r.add_argument("--media-resolution", help="e.g. MEDIA_RESOLUTION_HIGH")
    r.add_argument("--prompt-version", help="a name for this prompt text; reusing a name with different text is refused")
    r.add_argument("--concurrency", type=int, default=8)
    r.add_argument("--upload-concurrency", type=int, default=8)
    r.add_argument("--chunk-size", type=int, default=200, help="items per batch job (enqueued-token cap)")
    r.add_argument("--probe", type=int, default=1, help="batch tier: items run on standard first")
    r.add_argument("--flex-fallback", action="store_true", help="flex tier: finish refused items on standard (full price)")
    r.add_argument("--keep-uploads", action="store_true")
    r.add_argument("--poll-interval", type=float, default=30.0)
    r.add_argument("--max-wait-hours", type=float, default=47.0)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--detach", action="store_true", help="run as a background process and return at once")

    for name, help_ in (("resume", "continue a run from its folder"), ("cancel", "ask the runner to stop")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("out")
        if name == "resume":
            p.add_argument("--detach", action="store_true")
    s = sub.add_parser("status", help="a run's status, counts and cost")
    s.add_argument("out")
    s.add_argument("--items", action="store_true")
    s.add_argument("--limit", type=int, default=50)
    f = sub.add_parser("find-jobs", help="list batch jobs whose display name starts with PREFIX")
    f.add_argument("prefix")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "run":
            thinking = None if (args.thinking_level or "").lower() in ("", "none") else args.thinking_level
            spec = batch.make_spec(
                out_dir=args.out, system_prompt_path=args.sys, user_prompt=args.user, input_dir=args.input_dir,
                pattern=args.pattern, inputs=args.inputs, tier=args.tier, model=args.model, fps=args.fps,
                thinking_level=thinking, temperature=args.temperature, max_output_tokens=args.max_output,
                media_resolution=args.media_resolution, prompt_version=args.prompt_version, concurrency=args.concurrency,
                upload_concurrency=args.upload_concurrency, chunk_size=args.chunk_size, probe=args.probe,
                flex_fallback=args.flex_fallback, keep_uploads=args.keep_uploads, poll_interval_s=args.poll_interval,
                max_wait_hours=args.max_wait_hours)
            if args.dry_run:
                _print(batch.run(spec, dry_run=True))
                return 0
            if args.detach:
                plan = batch.run(spec, dry_run=True)
                batch.Run(spec.out_dir).save_request(spec)
                _print({**plan, "dry_run": False, "pid": batch.launch_detached(spec.out_dir)})
                return 0
            _print(batch.run(spec))
            return 0
        if args.cmd == "resume":
            if args.detach:
                _print({"pid": batch.launch_detached(args.out)})
                return 0
            _print(batch.run(batch.load_spec(args.out)))
            return 0
        if args.cmd == "status":
            _print(batch.status(args.out, items=args.items, limit=args.limit))
            return 0
        if args.cmd == "cancel":
            _print(batch.request_cancel(args.out))
            return 0
        if args.cmd == "find-jobs":
            client, _ = batch.make_client()
            _print(batch.find_jobs(client, args.prefix))
            return 0
    except (ValueError, RuntimeError) as exc:
        print(f"riff-batch: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
