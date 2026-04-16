# Gemini Video Prompts

Small standalone batch runner for Gemini image or video generation.

It is built around the official `google-genai` Python SDK and the Gemini video
API flow documented by Google:

- `client.models.generate_videos(...)`
- poll the long-running operation
- `client.files.download(...)`
- save the returned video bytes to `.mp4`

The defaults now follow two tracks:

- video default model: `veo-3.1-fast-generate-preview`
- image default model: `gemini-3.1-flash-image-preview`

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

Override the model for a teammate who has access to a newer one:

```bash
uv run gemini-video-prompts prompts/example_image_batch.txt --mode image --model gemini-2.5-flash-image
```

## Defaults

Current defaults in the standalone CLI:

- mode: `video`
- video model: `veo-3.1-fast-generate-preview`
- image model: `gemini-3.1-flash-image-preview`
- image temperature: `0.7`
- image num outputs: `1`
- video poll interval: `10` seconds
- output root: `out/`

Those image defaults were chosen to stay aligned with `tools/vision.py` in the
main repo.

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

Supported inline metadata keys:

- `mode`
- `title`
- `model`
- `aspect_ratio`
- `duration_seconds`
- `enhance_prompt`
- `num_outputs`
- `temperature`
- `system_prompt`
- `image_size`
- `image`
- `images`
- `reference_images`
- `video`
- `video_uri`
- `config.<key>` for raw `GenerateVideosConfig` fields

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

Supported job/default fields:

- `mode`
- `title`
- `prompt`
- `prompt_file`
- `model`
- `duration_seconds`
- `aspect_ratio`
- `enhance_prompt`
- `number_of_videos`
- `num_outputs`
- `temperature`
- `system_prompt`
- `image_size`
- `image`
- `images`
- `reference_images`
- `video`
- `video_uri`
- `config`: extra fields forwarded into `GenerateVideosConfig`

CLI flags override YAML and text-file settings.

## Output Layout

Outputs are written under `out/` by default:

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
gemini-video-prompts prompts/example_batch.txt --limit 2
```

Use a different output root:

```bash
gemini-video-prompts prompts/example_batch.yaml --out-root /tmp/gemini-videos
```

## Notes

- Google’s video generation flow is asynchronous, so jobs are run sequentially
  and polled until complete.
- Image generation uses the same Gemini pattern as `tools/vision.py`: text or
  input images go into `generate_content(...)`, and inline image parts are saved
  as PNGs.
- The tool is intentionally model-string driven. If your teammate gets access to
  a newer preview model, they can pass it with `--model` or `GEMINI_VIDEO_MODEL`.
- Image mode also supports `GEMINI_IMAGE_MODEL`.
- `images` is a convenience shorthand for Veo 3.1 reference images. Those paths
  are converted into `reference_images` entries with `reference_type="asset"`
  in video mode. In image mode, `image` and `images` are treated as edit inputs.
- Advanced model-specific parameters can go under YAML `config` or text headers
  as `config.<key>: value`.
