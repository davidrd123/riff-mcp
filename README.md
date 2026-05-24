# riff-mcp

Toolkit for the *riff* workflow — iteratively generate, analyze, and refine AI-generated media. Three pieces in one repo:

- `gemini-video-prompts` — the original batch CLI for Gemini image/video generation (still the local entry point for batch runs and dry-runs).
- `gemini-prompts-mcp` — wraps the CLI as MCP tools (`generate_image` via Gemini, `generate_video` via Replicate-Seedance).
- `media-analysis-mcp` — Gemini multimodal analysis (`describe_*`, `score_*`, `compare_images`, `extract_visual_tokens`) + ffmpeg-based `extract_video_frames`.

The name comes from the `generation-review-loop` skill's vocabulary for iterative prompt work — *the riff loop*: generate → review → extract → iterate. See [`MCP_DESIGN.md`](MCP_DESIGN.md) for the architecture.

> **Preferred usage: wire the MCP servers into your agent** (see [MCP Servers](#mcp-servers)). The riff loop is designed to run from a chat agent calling the MCP tools directly. The standalone `gemini-video-prompts` CLI remains supported for batch runs and dry-runs, but day-to-day iteration is meant to go through MCP.

The repo now has two generation paths:

- **Standalone CLI** — built around the official `google-genai` Python SDK for
  Gemini image generation and the original Veo video batch flow.
- **MCP generation server** — `generate_image` reuses the Gemini image worker;
  `generate_video` uses Seedance 2.0 through Replicate.

Current defaults:

- CLI video default model: `veo-3.1-fast-generate-preview`
- CLI/MCP image default model: `gemini-3-pro-image-preview`
- MCP video default model: `bytedance/seedance-2.0`
- Media-analysis default model: `gemini-3.5-flash`

Model strings remain configurable so a teammate with access to a newer preview
or provider model can swap it in without editing the code.

## Install

Preferred with `uv`:

```bash
cd riff-mcp
uv sync
cp .env.example .env
```

Then run with `uv run`.

Fallback with plain venv/pip:

```bash
cd riff-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
cp .env.example .env
```

Then set the needed API tokens in `.env` or your shell:

- `GEMINI_API_KEY` for CLI image/video generation and `media-analysis-mcp`
- `REPLICATE_API_TOKEN` for MCP `generate_video`

## Quick Start

Preview an image batch plan without calling the API:

```bash
uv run gemini-video-prompts prompts/example_image_batch.txt --mode image --plan
```

Run an image batch:

```bash
uv run gemini-video-prompts prompts/example_image_batch.txt --mode image
```

Run a video batch:

```bash
uv run gemini-video-prompts prompts/example_batch.txt
```

Generate a single image inline (no batch file needed):

```bash
uv run gemini-video-prompts --prompt "A glowing jellyfish drifting through neon kelp." --mode image
```

Override the model for a teammate who has access to a newer one:

```bash
uv run gemini-video-prompts prompts/example_image_batch.txt --mode image --model gemini-2.5-flash-image
```

## MCP Servers

Run the generation server on stdio:

```bash
uv run gemini-prompts-mcp
```

Generation tools:

- `generate_image` — blocking Gemini image generation.
- `generate_video` — blocking Replicate-Seedance generation, preserved for simple one-shot calls.
- `start_video_job` — starts a Replicate-Seedance prediction and returns `{job_id, prediction_id, status, job_dir}` immediately.
- `get_video_job` — reads `<out_root>/jobs/<job_id>/status.json`, optionally polls Replicate, and downloads outputs when the prediction succeeds.
- `cancel_video_job` — cancels a running provider prediction and updates local status.

If you pass a custom `out_root` to `start_video_job`, pass the same `out_root`
to `get_video_job` / `cancel_video_job`. `start_video_job` can forward a
`webhook_url` to Replicate, but this repo does not yet include an HTTP webhook
receiver; polling remains the supported completion path.

Run the media-analysis server on stdio:

```bash
uv run media-analysis-mcp
```

Analysis tools:

- `analyze_image` / `analyze_video` — **preferred default.** Free-form Q&A: pass any question, get a prose answer. Same multimodal plumbing, no response schema.
- `describe_image` / `describe_video` — structured observation against a fixed taxonomy (8 categories for images, 12 for video). No scoring, no verdict — Claude is the judge. _Under review for deprecation_ — its baked-in taxonomy may not be justified vs. `analyze_*`; prefer `analyze_*` for new work.
- `score_image` / `score_video` — calibrated 0–100 scoring across criteria (default: the 6 dimensions from `generation-review-loop`). Gemini is the judge.
- `compare_images` — pick the best of N candidates against criteria; returns `best_index` + reasoning.
- `extract_visual_tokens` — deconstruct an image into reusable prompt tokens (lighting/atmosphere/palette/materials/spatial_grammar by default).
- `extract_video_frames` — ffmpeg-based frame extraction at custom timestamps; useful for feeding stills back into image tools.

All Gemini analysis tools share one default model (`gemini-3.5-flash`) and an
opt-in `temperature` (omitted unless you pass one — each model uses its own
tuned default).

#### When to use which: `analyze_*` vs `describe_*`

Both go to the same model with the same multimodal context. The difference is the *response* shape:

- **`analyze_*` (preferred default)** returns a single prose answer to whatever you ask. Reach for it first: it doesn't force your question through a fixed taxonomy, so it answers the thing you actually want to know ("how does the camera move?", "is the boy on the right's posture stable?", "rate just the lighting in 2 sentences"). No schema lock; no taxonomy decisions baked in.
- **`describe_*`** returns a fixed structured shape (8 / 12 named categories). Use it specifically when you want **repeatable, comparable** output across iterations — same axes every time, easy to diff between runs — e.g. a calibrated review pass where you're tracking the same dimensions run over run.

Rule of thumb: default to `analyze_*`. Switch to `describe_*` only when you need the structured, diffable taxonomy for side-by-side calibration — and note that `describe_*`'s fixed taxonomy is **under review for deprecation** (it bakes in structure that may not have been justified), so avoid building new workflows that depend on its exact shape.

Example MCP client config when the client launches from outside this repo:

```json
{
  "mcpServers": {
    "gemini-prompts": {
      "command": "uv",
      "args": ["--directory", "/Users/daviddickinson/Projects/Lora/riff-mcp", "run", "gemini-prompts-mcp"]
    },
    "media-analysis": {
      "command": "uv",
      "args": ["--directory", "/Users/daviddickinson/Projects/Lora/riff-mcp", "run", "media-analysis-mcp"]
    }
  }
}
```

A copy-paste-ready template lives at `.mcp.example.json` — replace `REPO_PATH`
with this directory and fill the env values to use it directly.

Running with `--directory` lets the servers find the repo-local `.env`. You can
also provide `GEMINI_API_KEY` and `REPLICATE_API_TOKEN` directly through the MCP
client's environment settings.

### Use the servers from another project (recommended for consumers)

The common case is calling these tools from a *different* project — you don't
work inside `riff-mcp`, you just want its tools available everywhere. For that,
register the servers at **user scope** so they load in every project:

```bash
# Run once from anywhere; --scope user writes to ~/.claude.json (global).
claude mcp add gemini-prompts --scope user \
  -- uv --directory /ABSOLUTE/PATH/TO/riff-mcp run gemini-prompts-mcp
claude mcp add media-analysis --scope user \
  -- uv --directory /ABSOLUTE/PATH/TO/riff-mcp run media-analysis-mcp
```

(Equivalently, hand-edit the top-level `mcpServers` block in `~/.claude.json`.)

**API keys.** You do not need to copy keys into each project. Because every
entry runs `uv --directory <riff-mcp> run …`, the server's working directory is
always the `riff-mcp` checkout, and the servers call `load_dotenv()` — so a
single `riff-mcp/.env` (with `GEMINI_API_KEY` and `REPLICATE_API_TOKEN`) feeds
the tools no matter which project you launch from. Alternatively, set the keys
in the server's `env` block (see `.mcp.example.json`).

### Skip the permission prompts (quick + iterable)

By default Claude Code asks before each tool call. To make the riff loop fast,
add the tools to a `permissions.allow` list. For a global setup put it in
`~/.claude/settings.json`; for a single project use that project's
`.claude/settings.local.json`:

```json
{
  "permissions": {
    "allow": [
      "mcp__gemini-prompts__generate_image",
      "mcp__gemini-prompts__start_video_job",
      "mcp__gemini-prompts__get_video_job",
      "mcp__media-analysis__analyze_image",
      "mcp__media-analysis__analyze_video",
      "mcp__media-analysis__score_image",
      "mcp__media-analysis__compare_images",
      "mcp__media-analysis__extract_visual_tokens",
      "mcp__media-analysis__extract_video_frames"
    ]
  }
}
```

The entry format is `mcp__<server-name>__<tool-name>`, where `<server-name>`
matches the key you registered above. List only the tools you want
auto-approved; anything omitted still prompts.

### Diagnose with `riff-mcp-doctor`

Before wiring the MCP servers (or after a "tool not working" report), run:

```bash
uv run riff-mcp-doctor          # env vars, Python packages, ffmpeg/ffprobe
uv run riff-mcp-doctor --network  # plus a cheap Gemini + Replicate auth check
uv run riff-mcp-doctor --json     # machine-readable output for scripts
```

Exits non-zero on any required failure. Network checks are skipped (not failed)
when their corresponding token is unset.

## Defaults

Current defaults in the standalone CLI:

- mode: `video`
- video model: `veo-3.1-fast-generate-preview`
- image model: `gemini-3-pro-image-preview`
- image temperature: omitted by default; `--temperature` is an opt-in override
- image num outputs: `1`
- video poll interval: `10` seconds
- output root: `out/`

## Input Formats

### 1. Plain text batch

For quick work, use one prompt per non-empty line:

```text
A neon hologram of a cat driving at top speed through a rainy city at night.
A handheld portrait video of a chef plating pasta in a loud, crowded kitchen.
```

For multiline prompts, separate jobs with `---`. Each block can optionally start
with simple metadata, followed by a blank line and then the prompt body:

```text
title: secret-code
aspect_ratio: 16:9
duration_seconds: 8

A close up of two people staring at a cryptic drawing on a wall, torchlight flickering.
A man murmurs, "This must be it. That's the secret code."
---
title: pizza-portrait
aspect_ratio: 9:16
config.resolution: 720p

A montage of pizza making with energetic camera movement and naturally generated kitchen sound.
```

Supported header keys (same set as YAML — see [Supported Keys](#supported-keys)
below). In text headers, nested fields use dot notation: `config.<key>: value`.

### 2. YAML batch

Use YAML when you want shared defaults and per-job overrides:

```yaml
defaults:
  model: veo-3.1-fast-generate-preview
  duration_seconds: 8
  aspect_ratio: "16:9"
  enhance_prompt: true
jobs:
  - title: "Torchlight wall"
    prompt: "A close up of two people staring at a cryptic drawing on a wall."

  - title: "Waterfall portrait"
    aspect_ratio: "9:16"
    config:
      resolution: "720p"
    prompt: "A majestic Hawaiian waterfall in a lush rainforest with drifting mist."
```

Top-level YAML keys:

- `defaults`: optional shared values
- `jobs`: required list of job objects

Each job (and `defaults`) accepts the keys listed in [Supported Keys](#supported-keys).

### 3. Inline prompt (no file required)

For a single prompt without a batch file — the typical "jamming" workflow when
you're iterating with a chat agent:

```bash
uv run gemini-video-prompts \
  --prompt "A glowing jellyfish drifting through neon kelp." \
  --mode image
```

With input images and overrides:

```bash
uv run gemini-video-prompts \
  --prompt "tighten the composition, more contrast" \
  --image ./refs/jelly.png \
  --mode image \
  --num-outputs 2 \
  --aspect-ratio "16:9"
```

Inline-only flags:

- `--prompt` — the prompt text. Mutually exclusive with the positional batch
  file argument.
- `--image` — single input image path; relative paths resolve against your
  current directory.
- `--images` — comma-separated input image paths.
- `--title` — optional job title (otherwise auto-derived from the first words
  of the prompt).

Every other CLI flag (`--mode`, `--model`, `--num-outputs`, `--temperature`,
`--system-prompt`, `--aspect-ratio`, `--out-root`, etc.) works the same in
inline mode as in batch mode. Leave `--temperature` unset for Gemini 3.x unless
you intentionally want to override the model's default sampling behavior.

## Supported Keys

These keys are accepted in text headers, YAML jobs, and YAML `defaults`. CLI
flags override them. Mode column shows where each key applies.

| Key | Mode | Notes |
|-----|------|-------|
| `mode` | both | `image` or `video` |
| `title` | both | Auto-derived from prompt if omitted |
| `prompt` | YAML only | Text format uses the block body for the prompt |
| `prompt_file` | both | Loads the prompt from a separate file (overrides `prompt`/body) |
| `model` | both | Model code, e.g. `gemini-3-pro-image-preview` |
| `aspect_ratio` | both | e.g. `"16:9"`, `"9:16"` |
| `duration_seconds` | video | |
| `enhance_prompt` | video | bool |
| `number_of_videos` | video | |
| `num_outputs` | image | 1–4 |
| `temperature` | image | Optional sampling override; omitted by default and generally left unset for Gemini 3.x |
| `system_prompt` | image | |
| `image_size` | image | |
| `image` | both | Single input image path |
| `images` | both | List or comma-separated string of image paths |
| `reference_images` | video | Explicit Veo 3.1 reference image entries with `reference_type` in the standalone CLI |
| `video` | video | Input video path |
| `video_uri` | video | Input video URI |
| `config` | both | Extra fields forwarded into the underlying generation config (`config.<key>: value` in text headers, nested mapping in YAML) |

CLI flags override YAML and text-file settings.

## Output Layout

Outputs are written under `out/` by default. Relative `--out-root` paths
(including the default `out/`) resolve against the repo root, not your current
working directory — so renders collect in `<repo>/out/...` regardless of where
you invoke the CLI from. Pass an absolute path or `~/...` to override.

```text
out/
  2026-04-15/
    run-20260415-235959.json
    veo-3.1-fast-generate-preview/
      01_secret-code_ab12cd34/
        secret-code_01.mp4
        job.json
```

Each job directory includes:

- generated `.mp4` files
- a `job.json` sidecar with prompt, config, status, and output paths

The run root also gets a manifest JSON for the whole batch.

## Useful Commands

Preview only:

```bash
uv run gemini-video-prompts prompts/example_batch.yaml --plan
uv run gemini-video-prompts prompts/example_image_batch.yaml --mode image --plan
```

Limit the batch:

```bash
uv run gemini-video-prompts prompts/example_batch.txt --limit 2
```

Use a different output root:

```bash
uv run gemini-video-prompts prompts/example_batch.yaml --out-root /tmp/gemini-videos
```

Stop on the first failed generation instead of continuing:

```bash
uv run gemini-video-prompts prompts/example_batch.yaml --fail-fast
```

Force the input format instead of inferring from the file extension:

```bash
uv run gemini-video-prompts my_prompts.dat --format yaml
```

## Notes

- Google’s video generation flow is asynchronous, so jobs are run sequentially
  and polled until complete in the standalone CLI.
- MCP `generate_video` uses Replicate-Seedance, requires `REPLICATE_API_TOKEN`,
  and blocks until the prediction completes or times out.
- MCP async video jobs write durable status files under `<out_root>/jobs/`.
  The generated media still lands under the normal dated output layout, with
  the async `job_id` appended to avoid collisions between identical prompts.
- Image generation uses the standard `generate_content(...)` flow: text (and
  optional input images) go in, inline image parts come out and are saved as
  PNGs.
- The tool is intentionally model-string driven. If your teammate gets access to
  a newer CLI preview model, they can pass it with `--model` or
  `GEMINI_VIDEO_MODEL`.
- Image mode also supports `GEMINI_IMAGE_MODEL`.
- `images` is a convenience shorthand for Veo 3.1 reference images. Those paths
  are converted into `reference_images` entries with `reference_type="asset"`
  in video mode. In image mode, `image` and `images` are treated as edit inputs.
- Advanced model-specific parameters can go under YAML `config` or text headers
  as `config.<key>: value`.

## Changelog

This project follows [semantic versioning](https://semver.org/). The current
version is set in [`pyproject.toml`](pyproject.toml).

### 0.2.0

- **`analyze_image` / `analyze_video`** — new free-form Q&A analysis tools, now
  the **preferred default** over the structured `describe_*` tools.
- **`describe_*` flagged for deprecation review** — its fixed taxonomy bakes in
  structure that may not be justified; prefer `analyze_*` for new work.
- **Unified analysis model** — all `media-analysis-mcp` Gemini tools now default
  to `gemini-3.5-flash` (previously a mix of `gemini-3.1-pro-preview` and
  `gemini-3-flash-preview`).
- **Opt-in `temperature`** — across the image CLI, `generate_image`, and all
  analysis tools, `temperature` is now omitted by default so each Gemini 3.x
  model uses its own tuned sampling default. Pass a value only to override.
- **`riff-mcp-doctor`** now loads a repo-root `.env` before running env checks,
  so it sees the same tokens the servers do.

### 0.1.0

- Initial release.
- `gemini-video-prompts` batch CLI (text / YAML / inline) for Gemini image and
  Veo video generation.
- `gemini-prompts-mcp` — `generate_image`, blocking `generate_video`, and the
  async `start_video_job` / `get_video_job` / `cancel_video_job` trio
  (Replicate-Seedance).
- `media-analysis-mcp` — `describe_*`, `score_*`, `compare_images`,
  `extract_visual_tokens`, and ffmpeg-based `extract_video_frames`.
- `riff-mcp-doctor` environment/dependency diagnostics.
