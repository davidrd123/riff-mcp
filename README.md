# riff-mcp

**New agent or teammate:** start with the [agent quickstart](docs/AGENT_QUICKSTART.md).
It includes the setup, billing boundary, first image, edit, and error recovery.
The [generated tool reference](docs/TOOL_REFERENCE.md) lists the actual MCP
schemas. For fal video, use the [H3 Max guide](docs/FAL_H3_MAX.md).
No private vault or external prompting skill is required to run riff.

Toolkit for the *riff* workflow — iteratively generate, analyze, and refine AI-generated media. Three pieces in one repo:

- `gemini-video-prompts` — batch CLI for Gemini/OpenAI images and Gemini video generation, including dry-runs.
- `gemini-prompts-mcp` — wraps generation as MCP tools (`generate_image` via Gemini or opt-in OpenAI, `generate_video` via Replicate-Seedance, and `start_fal_video_job` via fal H3 Max).
- `media-analysis-mcp` — Gemini multimodal analysis (`analyze_*`, `describe_*`, `score_*`, `compare_images`, `extract_visual_tokens`) + ffmpeg-based `extract_video_frames`.

The name comes from the `generation-review-loop` skill's vocabulary for iterative prompt work — *the riff loop*: generate → review → extract → iterate. See [`MCP_DESIGN.md`](MCP_DESIGN.md) for the architecture.

> **Preferred usage: wire the MCP servers into your agent** (see [MCP Servers](#mcp-servers)). The riff loop is designed to run from a chat agent calling the MCP tools directly. The standalone `gemini-video-prompts` CLI remains supported for batch runs and dry-runs, but day-to-day iteration is meant to go through MCP.

The repo now has two generation paths:

- **Standalone CLI** — built around the official `google-genai` Python SDK for
  Gemini image generation and the original Veo video batch flow, with an optional
  OpenAI Images API adapter.
- **MCP generation server** — `generate_image` shares the CLI's image workers;
  `generate_video` uses Seedance 2.5 through Replicate (2.0 remains selectable
  via the `model` arg — it is the only one with 1080p/4k output).
  `start_fal_video_job` separately submits H3 Max image/reference/text-to-video to fal.

Current defaults:

- CLI video default model: `veo-3.1-fast-generate-preview`
- CLI/MCP image default model: `gemini-nano-banana-2.1` (Nano Banana 2.1); pass
  `gemini-3-pro-image` explicitly for Nano Banana Pro
- Opt-in OpenAI image models (`provider="openai"`): `gpt-image-2` (default),
  `gpt-image-2.5-sunburst`, `gpt-image-2.5-flare`
- MCP Seedance video default model: `bytedance/seedance-2.5`
- Opt-in fal video tool: `start_fal_video_job`, H3 Max at 768p (480p selectable)
- Media-analysis image default model: `gemini-3.8-flash`
- Media-analysis video default model: `gemini-3.8-flash`
- Opt-in media-analysis model: `gemini-4-argon` (Gemini 4 Argon), passed as
  `model` to any analysis tool; paid tier only, about 2.7× Flash's price

Gemini model strings are configurable. The OpenAI adapter also accepts the two
GPT Image 2.5 IDs above, alongside `gpt-image-2`
and dated GPT-Image-2 snapshots; it does not route arbitrary model names.

## Install

Requires Python 3.10+. Video probing and frame extraction also need `ffprobe`
and `ffmpeg` on PATH. Direct image generation does not require those binaries.

Preferred with `uv`:

```bash
cd riff-mcp
uv sync --locked
cp -n .env.example .env
```

Then run with `uv run`. `cp -n` preserves an existing `.env`; uncomment and fill
only the credentials for services you intend to use. Placeholder values are
not valid credentials even if the doctor's presence check reports them as set.

For optional OpenAI image support:

```bash
uv sync --locked --extra openai
```

Use `uv run --extra openai ...` for OpenAI-enabled CLI/server invocations.
The extra installs the SDK; it does not select OpenAI or make API requests.

Fallback with plain venv/pip:

```bash
cd riff-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
cp -n .env.example .env
```

For OpenAI with pip, use `python -m pip install -e '.[openai]'` instead.
Then set the needed API tokens in `.env` or the MCP server's environment:

- `GEMINI_API_KEY` for Gemini image/video generation and `media-analysis-mcp`
- `REPLICATE_API_TOKEN` for MCP `generate_video`
- `OPENAI_API_KEY` only for image calls explicitly selecting `provider="openai"`

Each teammate uses their own untracked `.env`. OpenAI-only image generation
does not require Gemini or Replicate keys. This configuration does not change
the host agent's subscription sign-in. API calls have separate billing;
Gemini/Replicate tools also use their respective APIs. Do not configure an
OpenAI key on a machine that should not make OpenAI API calls.

To update an existing checkout, inspect its branch and local changes, obtain
the intended revision, then rerun the matching locked sync command. Stop running
MCP servers before changing their shared environment and reconnect afterward
so they load the new code and tool schema. `uv sync` can remove unselected
extras; include `--extra openai` when retaining OpenAI support. Existing server
names remain valid. Check `--help` for `--provider` and `--allow-api-billing`;
the unchanged package version `0.2.0` alone does not identify this feature.

## Quick Start

Preview an image batch plan without calling the API:

```bash
uv run gemini-video-prompts prompts/example_image_batch.txt --mode image --plan
```

Run an image batch (billable Gemini API calls):

```bash
uv run gemini-video-prompts prompts/example_image_batch.txt --mode image
```

Run a video batch (billable Veo API calls; this is not MCP Seedance):

```bash
uv run gemini-video-prompts prompts/example_batch.txt
```

Generate a single Gemini image inline (billable; no batch file needed):

```bash
uv run gemini-video-prompts --prompt "A glowing jellyfish drifting through neon kelp." --mode image
```

Override the Gemini image model explicitly:

```bash
uv run gemini-video-prompts prompts/example_image_batch.txt --mode image --model gemini-3.1-flash-image
```

## MCP Servers

Run the generation server on stdio:

```bash
uv run gemini-prompts-mcp
```

Generation tools:

- `generate_image` — blocking Gemini or OpenAI image generation. Gemini can use the
  one-shot `generate_content` path or a stored Interactions path for
  multi-turn image editing.
- `generate_video` — blocking Replicate-Seedance generation, preserved for simple one-shot calls.
- `start_video_job` — starts a Replicate-Seedance prediction and returns `{job_id, prediction_id, status, job_dir}` immediately.
- `start_fal_video_job` — submits an explicitly approved H3 Max image/reference/text-to-video request to fal, or previews it with `dry_run=true`.
- `get_video_job` — reads the local record, optionally polls its saved provider (Replicate or fal), and collects completed outputs.
- `cancel_video_job` — requests cancellation from the saved provider and records the response; fal acceptance does not guarantee running work stops.
- `get_generation` — returns the durable request, status, result, paths, and
  normalized seed provenance for one async job without polling.
- `list_generations` — searches current and historical async video records by prompt,
  title, model, status, or job ID.

### fal H3 Max image, reference, and text-to-video

Use `start_fal_video_job` with `minimax/h3-max/image-to-video` or
`minimax/h3-max/reference-to-video`, or the explicit prompt-only routes
`minimax/h3-max/text-to-video` and `minimax/h3-max-turbo/text-to-video`, 5–15
seconds, and `resolution="480p"` or `"768p"` (default). Configure `FAL_KEY` in the untracked `.env`; no additional
SDK extra is needed. This uses separately billed fal API access. Preview with
`dry_run=true`, obtain scoped approval, then set `allow_api_billing=true`.
Reference inputs can affect charges as well as output duration.

Use `get_video_job` to poll the returned job ID and collect the output;
`get_generation` and `list_generations` provide local provenance lookup. The
original and expanded prompts are preserved. `prompt_expansion_mode` defaults
to `balanced`; `quality` is an explicit, slower prompt-rewriting option.
See the [complete fal guide](docs/FAL_H3_MAX.md) for working examples, reference
limits, the prompt-only 480p style-sweep preview, prompt expansion research,
current pricing links, and timeout recovery.
Existing Seedance tools and CLI video routing retain their behavior.

### OpenAI GPT-Image-2 generation and editing

Install the `openai` extra and put `OPENAI_API_KEY=...` in your own `riff-mcp/.env`.
Run the generation server with `uv run --extra openai gemini-prompts-mcp`.
This example is a free MCP `generate_image` dry run:

```json
{
  "provider": "openai",
  "prompt": "A blue ceramic cup on a cream background, soft side lighting.",
  "size": "1536x1024",
  "quality": "low",
  "dry_run": true
}
```

**Billing boundary:** OpenAI API charges are separate from a ChatGPT/Codex
subscription. An installed key does not authorize spending. The calling agent must ask
the user to approve the request or a bounded batch before setting
`allow_api_billing=true`. To execute the example after approval, remove
`dry_run` and add `"allow_api_billing": true`. Without that explicit argument,
OpenAI execution stops with `API_BILLING_CONFIRMATION_REQUIRED` before a call.
The dry-run response includes the same billing notice for the agent to surface.

The default OpenAI model is `gpt-image-2`; a dated snapshot can be selected
explicitly. Add `image` or an ordered `images` list of local PNG/JPEG/WebP paths
for reference-guided editing. For the next edit, pass the exact returned output path and
the new instruction. Gemini interaction IDs cannot be used with OpenAI.

CLI equivalents:

```bash
# Preview without credentials, SDK calls, or output files.
uv run gemini-video-prompts prompts/example_openai_image_batch.yaml --plan

# After user approval: generate using your API key (billable).
uv run --extra openai gemini-video-prompts --mode image --provider openai \
  --prompt "A blue ceramic cup on a cream background." --quality low --size 1536x1024 \
  --allow-api-billing

# After user approval: edit a local image (billable).
uv run --extra openai gemini-video-prompts --mode image --provider openai \
  --image /absolute/path/to/cup.png --prompt "Make the cup green; preserve everything else." \
  --allow-api-billing
```

OpenAI controls are `size`, `quality`, `output_format`, `background`, and
`output_compression`. Defaults are `auto` size/quality/background and PNG.
Use `size` for pixel dimensions; Gemini `aspect_ratio`, `image_size`,
`temperature`, `system_prompt`, thinking, and stored-interaction options are
rejected. Put all instructions in `prompt`. PNG and WebP preserve transparency.
See the [OpenAI image guide](https://developers.openai.com/api/docs/guides/image-generation)
for model constraints, preview capabilities, and current pricing.

The adapter validates size before execution: both edges must be multiples of
16 and at most 3840, long/short edge ratio at most 3:1, and total pixels between
655,360 and 8,294,400. Explicit `low`, `medium`, or `high` avoids leaving quality
selection to `auto`; inspect returned `response_metadata` and output dimensions
for the actual settings. The host's built-in ImageGen is a separate interface;
riff cannot declare its quality tier or control its resolution.

OpenAI is never selected automatically, and a failed Gemini request never
falls back to OpenAI. Each call requests 1–4 outputs. Requests have a five-minute
timeout and no automatic retries; a timeout can leave provider completion
uncertain. Inspect the record before deliberately resubmitting.

The CLI flag confirms API billing for that invocation and cannot be enabled by
batch-file contents. There is no global "always allow" environment toggle.
The MCP boolean records caller confirmation; it cannot independently prove a
human approved it. If `generate_image` is auto-approved in your agent, keep
the agent instructions in AGENTS.md and the tool description in force. Keep
normal tool approval enabled when reviewing API spend per call matters. Neither
the boolean nor the local output limit is a cumulative spending cap.

### Stateful Gemini image generation

The following examples use the Gemini API and can incur charges. They are
unrelated to OpenAI's Images API or the host's subscription image tool.

Use `api="interactions"` and `store=true` when a generated image may become the
parent of a later edit. The result includes `interaction_ids`; pass the relevant
ID back as `previous_interaction_id` with a bounded next instruction. The
provider then carries the prior image and thought signatures without requiring
you to resend the earlier turn.

For a fresh Nano Banana 2 root:

```json
{
  "prompt": "One low rear source; keep camera-facing planes near-black.",
  "model": "gemini-3.1-flash-image",
  "api": "interactions",
  "thinking_level": "high",
  "temperature": 1.0,
  "store": true,
  "aspect_ratio": "21:9",
  "image_size": "1K"
}
```

For one continuation from the returned interaction ID:

```json
{
  "prompt": "Add only a thin horizontal optical streak. Preserve the lighting, geometry, camera, and shadows.",
  "model": "gemini-3.1-flash-image",
  "api": "interactions",
  "thinking_level": "high",
  "temperature": 1.0,
  "store": true,
  "previous_interaction_id": "<INTERACTION_ID>"
}
```

`thinking_level` accepts `minimal`, `low`, `medium`, or `high` when the selected
model supports that level. Gemini 3 models are tuned for a temperature of
`1.0`; omitting `temperature` uses the model default. Setting `store=true`
persists provider-side interaction state, so use it only when that retention is
appropriate for the material.

If you pass a custom `out_root` to `start_video_job`, pass the same `out_root`
to `get_video_job`, `cancel_video_job`, `get_generation`, and
`list_generations`. `start_video_job` can forward a `webhook_url` to Replicate,
but this repo does not yet include an HTTP webhook receiver; polling remains
the supported completion path.

Run the media-analysis server on stdio:

```bash
uv run media-analysis-mcp
```

Analysis tools:

- `analyze_image` / `analyze_video` — **preferred single-source default.** Free-form Q&A: pass any question, get a prose answer. Same multimodal plumbing, no response schema. Video analysis defaults to `thinking_level=high` and `max_output_tokens=65536`; both are explicit per-call overrides and may be set to `null` to restore model/API defaults.
- `analyze_images` — one open-ended question across 2–10 ordered, explicitly labeled, equal-role images. It has no candidate hierarchy, fixed criteria, response schema, or forced winner. Defaults to `thinking_level=high` and `max_output_tokens=65536`, with `null` restoring model/API defaults.
- `analyze_audio` — free-form audio Q&A and detailed transcription. Preserves the Files API's detected `audio/*` MIME type, including M4A/AAC files that must not be routed through `analyze_video`. Defaults to `gemini-3.8-flash`.
- `analyze_videos` — one grounded free-form question across 2–10 ordered,
  explicitly labeled videos. Useful for edit comparisons, continuity checks,
  and candidate ranking without first concatenating a review reel. It shares
  the high-thinking, 65,536-token video defaults and override behavior.
- `describe_image` / `describe_video` — structured observation against a fixed taxonomy (8 categories for images, 12 for video). No scoring, no verdict — Claude is the judge. _Under review for deprecation_ — its baked-in taxonomy may not be justified vs. `analyze_*`; prefer `analyze_*` for new work.
- `score_image` / `score_video` — model-generated 0–100 scores across criteria
  (six built-in dimensions by default). Scores are advisory; agreement with
  human production judgment has not been established.
- `compare_images` — pick the best of N candidates against criteria; returns `best_index` + reasoning.
- `extract_visual_tokens` — deconstruct an image into reusable prompt tokens (lighting/atmosphere/palette/materials/spatial_grammar by default).
- `extract_video_frames` — ffmpeg-based frame extraction at custom timestamps; useful for feeding stills back into image tools.

All Gemini image-, video-, and audio-analysis tools default to
`gemini-3.8-flash`. All retain an opt-in `temperature`
(omitted unless you pass one — each model uses its own tuned default).

`gemini-4-argon` (Gemini 4 Argon) is available to every analysis tool as an
explicit `model`; the default stays Flash. It is billed on the paid tier only:
introductory list prices are $2 per 1M input tokens and $10 per 1M output
tokens (thinking included) through 2026-11-30, then $4 and $20, against Flash's
$0.75 and $3.75 through 2026-12-31 (AI Studio pricing page, read 2026-10-13).
The Models API lists `generateContent`, `countTokens` and
`batchGenerateContent` for it (no explicit context caching), a 1,048,576-token
input limit and a 32,768-token output limit. Agentic video processing has not
been checked with it. Use it where Flash's answer is known to fall short and the
better answer is worth the price; see LIVE_VERIFICATION.md for what was checked.

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
      "args": ["--directory", "/ABSOLUTE/PATH/TO/riff-mcp", "run", "gemini-prompts-mcp"]
    },
    "media-analysis": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/TO/riff-mcp", "run", "media-analysis-mcp"]
    }
  }
}
```

A copy-paste-ready template lives at `.mcp.example.json` — replace `REPO_PATH`
with this directory. The template enables the optional OpenAI SDK on the
generation server; this alone does not select OpenAI or incur API costs.
For an existing generation-server entry, add `"--extra", "openai"` after
`"run"` to enable its SDK.

Running with `--directory` lets the servers find the repo-local `.env`. You can
also provide the needed API keys directly through the MCP client's environment
settings. Omit unused env entries: an empty value can override a valid `.env`
value. Keep secrets out of tracked example files.

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
single `riff-mcp/.env` (with only the keys you use) feeds
the tools no matter which project you launch from. Alternatively, set the keys
in the server's `env` block. For OpenAI-enabled registration, use
`run --extra openai gemini-prompts-mcp` in the generation command above.

### Configure tool permissions deliberately

The `generate_image` permission covers both Gemini and OpenAI modes. Only
auto-approve it if you intend the agent to submit billable image requests
without per-call review.

Keep the client's normal approval behavior unless the user requests otherwise.
Do not copy a blanket allowlist as part of installation. If the user wants local
record lookups auto-approved in Claude Code, an example `permissions.allow`
list is below. For a global setup it belongs in
`~/.claude/settings.json`; for a single project use that project's
`.claude/settings.local.json`:

```json
{
  "permissions": {
    "allow": [
      "mcp__gemini-prompts__get_generation",
      "mcp__gemini-prompts__list_generations"
    ]
  }
}
```

`get_generation` and `list_generations` are trusted-local provenance tools.
Allowlisting them lets an agent read prompts, absolute reference/output paths,
raw provider responses, and temporary provider URLs across the selected output
root. Grant that access only to trusted local clients, and never place API
tokens or other credentials inside prompts or provider parameters.

The entry format is `mcp__<server-name>__<tool-name>`, where `<server-name>`
matches the key you registered above. List only the tools you want
auto-approved. Other calls follow the client's remaining permission settings;
this example does not override other allow rules or the user's current mode.

### Diagnose with `riff-mcp-doctor`

Before wiring the MCP servers (or after a "tool not working" report), run:

```bash
uv run riff-mcp-doctor          # env vars, Python packages, ffmpeg/ffprobe
uv run riff-mcp-doctor --network  # plus a cheap Gemini + Replicate auth check
uv run riff-mcp-doctor --json     # machine-readable output for scripts
uv run riff-mcp-doctor --provider fal  # local fal key/import readiness; no inference
uv run --extra openai riff-mcp-doctor --provider openai
uv run --extra openai riff-mcp-doctor --provider openai --network
```

Exits non-zero on any required failure. Network checks are skipped (not failed)
when their corresponding token is unset.
The default doctor retains the Gemini/Replicate checks. `--provider openai`
checks only the OpenAI key and required imports, so unrelated service keys or
video binaries are not required. Its optional network check retrieves the
`gpt-image-2` model; it does not generate an image or establish billing access.

## Batch analysis (riff-batch)

One Gemini request per file over a folder or a list of files: dataset captioning (a versioned system-prompt file
sent whole, one user line, a folder of clips, stills or audio) or any per-file question. Three tiers:

| tier | price | how | when |
|---|---|---|---|
| `batch` (default) | half of standard | the Gemini Batch API: jobs of up to `--chunk-size` files, uploaded just before submission | most runs; Google targets 24 h (48 h expiry), our runs took minutes |
| `standard` | full | ordinary calls, `--concurrency` at a time | when you need it now |
| `flex` | half | ordinary calls with `service_tier: flex`; refused (503/429) under load, retried with backoff | optional; `--flex-fallback` finishes refusals on standard |

The batch tier first runs `--probe` files (default 1) on standard, so a refused setting fails in seconds and the
measured tokens price the rest. A job is created once and never re-created after an error (a lost reply may still
have made a billed job); `riff-batch find-jobs` settles it.

```bash
uv run riff-batch run --sys caption-prompt-v2.md --user "Caption this window." \
    --input-dir clips/ --pattern "*.mp4" --fps 8 --thinking-level low --temperature 0.7 \
    --prompt-version v2 --out runs/captions-v2 --dry-run      # plan and estimate; no call
uv run riff-batch run ... --out runs/captions-v2               # run (or --detach for a background process)
uv run riff-batch status runs/captions-v2 --items
uv run riff-batch resume runs/captions-v2                      # after an interruption: collect, then the rest
uv run riff-batch cancel runs/captions-v2
```

The run folder holds `request.json`, `run.json` (status, counts, tokens, cost from
`src/media_analysis_mcp/prices.json`), `events.ndjson`, `items/<key>.json` (text, finish reason, tokens, attempts,
tier), `texts/<key>.txt`, and `batches/<n>/` (`requests.jsonl`, `job.json`, the raw `results.jsonl`). A rerun skips
files already done under the same file sha256 and settings and re-runs changed ones (their old records go to
`superseded/`); a `--prompt-version` name reused with different text is refused. The MCP tools
`start_batch_analysis`, `get_batch_analysis` and `cancel_batch_analysis` do the same from a client; start launches
`riff-batch resume` as its own process, so a run outlives the server.

## Defaults

Current defaults in the standalone CLI:

- mode: `video`
- video model: `veo-3.1-fast-generate-preview`
- image model: `gemini-nano-banana-2.1`
- image provider: `gemini`; explicit `openai` selects `gpt-image-2`
- image temperature: omitted by default; `--temperature` is an opt-in override
- image num outputs: `1`
- video poll interval: `10` seconds
- output root: `out/`

For reliable model selection, use `model` in a batch/MCP call or `--model` in
the CLI. CLI model environment overrides (`GEMINI_IMAGE_MODEL`,
`GEMINI_VIDEO_MODEL`) are read while resolving jobs, before execution loads
`.env`; export them in the shell if using them. The MCP image default does not
read `GEMINI_IMAGE_MODEL`. `.env` is the credentials setup path.

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

Some Gemini/Veo examples contain `refs/...` placeholders. They are useful for
`--plan`, but replace those paths with real images before executing the whole
batch. Paths resolve relative to the prompt file, not the checkout root.

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
| `model` | both | Model code, e.g. `gemini-nano-banana-2.1` or `gemini-3-pro-image` |
| `provider` | image | `gemini` (default) or explicit `openai` |
| `size` | OpenAI image | Pixel dimensions, e.g. `1536x1024`, or `auto` |
| `quality` | OpenAI image | `auto`, `low`, `medium`, `high`; GPT Image 2.5 also accepts `xhigh`, `max` |
| `mask` | OpenAI image edit | PNG alpha mask for the first reference; same dimensions, under 4 MiB |
| `output_format` | OpenAI image | `png`, `jpeg`, `webp` |
| `background` | OpenAI image | `auto`, `opaque`, `transparent` (PNG/WebP) |
| `output_compression` | OpenAI image | Integer 0–100 for JPEG/WebP only |
| `aspect_ratio` | Gemini | e.g. `"16:9"`, `"9:16"` |
| `duration_seconds` | video | |
| `enhance_prompt` | video | bool |
| `number_of_videos` | video | |
| `num_outputs` | image | 1–4 |
| `temperature` | Gemini image | Optional sampling override; omitted by default |
| `system_prompt` | Gemini image | For OpenAI, include instructions in `prompt` |
| `image_size` | Gemini image | For OpenAI, use `size` |
| `image` | both | Single input image path |
| `images` | both | List or comma-separated string of image paths |
| `reference_images` | video | Explicit Veo 3.1 reference image entries with `reference_type` in the standalone CLI |
| `video` | video | Input video path |
| `video_uri` | video | Input video URI |
| `config` | Gemini | Extra fields forwarded into generation config (`config.<key>` in text headers, nested mapping in YAML). For Gemini images, reserved keys are `api`, `thinking_level`, `store`, and `previous_interaction_id`. OpenAI accepts only `api: images` and inactive Gemini defaults; unsupported options fail before a call. |

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

- generated images (Gemini PNG; OpenAI PNG/JPEG/WebP) or `.mp4` video files
- a `job.json` sidecar with prompt, config, status, and output paths

The CLI run root also gets a manifest JSON for the whole batch. OpenAI records
include access mode, request ID, available usage, actual response metadata,
and input/output SHA-256 hashes. Original output bytes are preserved. Failed
requests retain a failure record; any saved partial outputs remain listed.

Image requests are blocking and have no async polling ID. `get_generation`
and `list_generations` index async video jobs only; read an image's returned
`job_dir` and `job.json` directly. Repeating an identical image request on the
same date can reuse its output directory and overwrite files. Use a distinct
title or output root for another take; a matching hash does not skip billing.

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
- Blocking `generate_video` can preserve a seed supplied by the caller, but
  Replicate's blocking helper does not expose the prediction log needed to
  recover an auto-selected seed. Use `start_video_job` + `get_video_job` when
  complete provider-observed seed provenance matters.
- MCP async video jobs write durable status files under `<out_root>/jobs/`.
  Each request has a stable `generation_id` equal to its `job_id`; completed
  provider-selected seeds are normalized into `seed_provenance.effective_seed`.
  Reference and downloaded output records include byte size and SHA-256.
  Reference digests are calculated from the same opened file streams passed to
  the provider; output digests are calculated from the downloaded files.
  Lifecycle summaries append to `<out_root>/jobs/index.ndjson`, while
  `list_generations` also discovers pre-index `status.json` records.
  The generated media still lands under the normal dated output layout, with
  the async `job_id` appended to avoid collisions between identical prompts.
- Gemini image generation uses `generate_content(...)` by default or the
  explicit Interactions path. OpenAI uses Images API generation/edit endpoints
  and preserves the returned PNG/JPEG/WebP bytes.
- The tool is intentionally model-string driven. If your teammate gets access to
  a newer CLI preview model, they can pass it with `--model` or
  `GEMINI_VIDEO_MODEL`.
- Gemini CLI image mode supports the exported `GEMINI_IMAGE_MODEL` variable;
  use explicit `model` in MCP calls.
- `images` is a convenience shorthand for Veo 3.1 reference images. Those paths
  are converted into `reference_images` entries with `reference_type="asset"`
  in video mode. In image mode, `image` and `images` are treated as edit inputs.
- Advanced Gemini parameters can go under YAML `config` or text headers as
  `config.<key>: value`. The OpenAI adapter rejects unsupported config fields.

## Maintain the documentation

The registered MCP schema is exported into `docs/TOOL_REFERENCE.md`. After
changing a tool signature or description, regenerate it and check for drift:

```bash
uv run python scripts/update_tool_reference.py
uv run python scripts/update_tool_reference.py --check
```

These commands do not invoke tools or call providers. Keep
`docs/AGENT_QUICKSTART.md`, examples, CLI help, and this README consistent.
Historical designs and live-test receipts must retain their dates and evidence
boundaries; they are not the current setup reference.

## Changelog

This project follows [semantic versioning](https://semver.org/). The current
version is set in [`pyproject.toml`](pyproject.toml).

### Unreleased

- **Batch analysis** — `riff-batch` and `start_batch_analysis` / `get_batch_analysis` /
  `cancel_batch_analysis`: one request per file over a folder on the batch (default, half price), standard or
  flex tier, with resume by file hash and settings, frozen prompt versions, a standard-tier probe before any batch
  job, per-file tokens and a cost record.

- **Gemini 4 Argon, opt-in for media analysis** — `gemini-4-argon` passes
  through the existing `model` argument of every analysis tool; no code change
  was needed. The live Models API and synthetic image, audio, and video calls
  through the real tools were checked on 2026-10-13. Flash stays the default.

- **fal H3 Max video** — explicit image/reference-to-video queue submission,
  480p/768p, billing confirmation, ordered references, durable recovery, and
  original/expanded prompt records. See [the fal guide](docs/FAL_H3_MAX.md).

- **Opt-in OpenAI GPT-Image-2 images** — direct API generation and reference
  edits through MCP, CLI, and YAML/text batches. Optional SDK extra, independent
  doctor checks, explicit per-invocation billing confirmation, format/quality
  controls, original output bytes, and usage records.
  Existing Gemini defaults and job hashes are preserved. Local and SDK transport
  tests cover the integration. One low-quality 1024×1024 generation and one
  reference edit succeeded on 2026-09-04; see LIVE_VERIFICATION.md for scope.

- **Gemini 3.8 Flash for media analysis** — image, video, multi-image,
  multi-video, and audio analysis tools now share the `gemini-3.8-flash`
  default. The live Gemini Models API and synthetic image, audio, and video
  calls (video at `thinking_level=high`) were verified before changing the
  default. `gemini-3.7-flash` remains available as an explicit `model`.

- **Consistent generation timestamps** — video results preserve the original
  request time as `created_at` and record local result finalization separately
  as `collected_at`.
- **Durable Seedance provenance** — async generation records now distinguish
  requested and effective seeds, recover provider-selected seeds from Replicate
  prediction logs, hash reference/output artifacts, append idempotent lifecycle
  summaries to `jobs/index.ndjson`, and expose `get_generation` /
  `list_generations` for MCP retrieval.
- **Gemini 3.7 Flash for media analysis** — image, video, multi-video, and
  audio analysis tools now share the `gemini-3.7-flash` default. The live
  Gemini Models API and synthetic image, audio, and video calls were verified
  before changing the default.
- **Native multi-video analysis** — `analyze_videos` accepts 2–10 distinct
  local videos plus optional ordered labels, uploads them into one Gemini
  request, and cleans up every Files API resource even after partial failure.
- **Native audio analysis** — `analyze_audio` preserves the Files API's
  detected `audio/*` MIME type for free-form analysis and transcription,
  including M4A/AAC recordings that must not be routed as video.

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

### Agentic video analysis

`analyze_video` and `analyze_videos` accept `processing="agentic"` for adaptive
video inspection through Gemini Interactions. Omit `fps`; static remains the
default. Results preserve the raw processing trace and report whether a linked
call/result pair was observed. See the [agent quickstart](docs/AGENT_QUICKSTART.md#adaptive-video-inspection)
for an example, result handling, and restart instructions.

### Regional edits and model comparisons

OpenAI image edits accept `mask` through MCP, CLI `--mask`, and YAML jobs.
Start with a reference image and direct instructions; omit the mask by default.
Use masks for user-supplied selections or demonstrated targeting problems.
Hand-drawn boundaries can introduce errors and are not routine edit preparation.
Use a PNG base and matching PNG alpha mask; transparent pixels identify the
region to edit. Riff validates assets and records mask hashes.

The `compare_image_variants` MCP tool previews 2–8 explicit model/quality/size
variants of one prompt. After bounded billing approval, it generates one image
per variant and saves a side-by-side HTML review with JSON records, latency,
usage, and dated list-price estimates. It stops on the first failure and never
retries automatically. See the [workflow examples](docs/AGENT_QUICKSTART.md#regional-edits-with-a-mask).
