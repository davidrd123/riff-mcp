# Gemini Video Prompts

Small standalone batch runner for Gemini image or video generation. The repo also hosts two companion MCP servers — `gemini-prompts-mcp` (wraps the CLI as MCP tools) and `media-analysis-mcp` (Gemini multimodal analysis + ffmpeg utilities). Each is its own console script + Python package; see [`MCP_DESIGN.md`](MCP_DESIGN.md) for the architecture.

It is built around the official `google-genai` Python SDK. Two generation flows
are supported:

- **Image** — `client.models.generate_content(...)` with image response
  modality, decoding inline image parts into PNGs.
- **Video** — `client.models.generate_videos(...)`, polling the long-running
  operation, then `client.files.download(...)` to save MP4s.

The defaults now follow two tracks:

- video default model: `veo-3.1-fast-generate-preview`
- image default model: `gemini-3-pro-image-preview`

The actual model is always configurable so a teammate with access to a newer
preview can swap in a different model code without editing the code.

## Install

Preferred with `uv`:

```bash
cd gemini-video-prompts
uv sync
cp .env.example .env
```

Then run with `uv run`.

Fallback with plain venv/pip:

```bash
cd gemini-video-prompts
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
cp .env.example .env
```

Then set `GEMINI_API_KEY` in `.env` or your shell.

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

## Defaults

Current defaults in the standalone CLI:

- mode: `video`
- video model: `veo-3.1-fast-generate-preview`
- image model: `gemini-3-pro-image-preview`
- image temperature: `0.7`
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
inline mode as in batch mode.

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
| `temperature` | image | |
| `system_prompt` | image | |
| `image_size` | image | |
| `image` | both | Single input image path |
| `images` | both | List or comma-separated string of image paths |
| `reference_images` | video | Explicit Veo 3.1 reference image entries with `reference_type` |
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

## Notes

- Google’s video generation flow is asynchronous, so jobs are run sequentially
  and polled until complete.
- Image generation uses the standard `generate_content(...)` flow: text (and
  optional input images) go in, inline image parts come out and are saved as
  PNGs.
- The tool is intentionally model-string driven. If your teammate gets access to
  a newer preview model, they can pass it with `--model` or `GEMINI_VIDEO_MODEL`.
- Image mode also supports `GEMINI_IMAGE_MODEL`.
- `images` is a convenience shorthand for Veo 3.1 reference images. Those paths
  are converted into `reference_images` entries with `reference_type="asset"`
  in video mode. In image mode, `image` and `images` are treated as edit inputs.
- Advanced model-specific parameters can go under YAML `config` or text headers
  as `config.<key>: value`.
