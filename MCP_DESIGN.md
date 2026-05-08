# MCP Layer — Design

**Status:** Draft, pre-implementation — v5
**Author:** David Dickinson + Claude
**Last updated:** 2026-05-08

Wrap the existing `gemini-video-prompts` CLI as an MCP server, and build a separate media-analysis MCP, so Claude Code can invoke generation and evaluation as typed tool calls instead of shelling out to `Bash → uv run`.

## v5 changes from v4

- **Fixed reference token syntax** to bracket form (`[Image1]`, `[Video1]`, `[Audio1]`) per Replicate Seedance schema descriptions. Earlier `@Image1` in the return example was inconsistent with the convention paragraph.
- **Provider-truthful tokens.** The `references[].token` field is the provider's actual syntax — Replicate-Seedance returns `[Image1]`; a future Fal adapter returns whatever Fal accepts (likely `@Image1`). No translation across providers; the agent pastes the token verbatim.
- **Fixed mode/exclusivity grouping.** Only `reference_images` is mutually exclusive with `image`/`last_frame_image`. `reference_videos` and `reference_audios` can layer on top of any mode. Signature comments + validation now agree.
- **Added 12-file total reference cap** per `seedance-prompting-guide.md:23` (per-category caps sum to 15 but 12 is the total ceiling; not enforced by Replicate schema, enforced by us).
- **Made Replicate sidecar sanitization explicit** in `seedance.py`. `replicate_min.generate()`'s built-in filter only excludes legacy keys; Seedance handles live under different keys (`last_frame_image`, `reference_images`, etc.) and would leak as file handles into returned JSON without a separate clean-params dict.
- **Defined `dry_run=True` return shape** for both `generate_image` and `generate_video`. Status becomes `"planned"`; `outputs`/`metrics` omitted; `projected_job_dir` provided.

## v4 changes from v3

- **Renamed analysis MCP** from `image-analysis-mcp` to `media-analysis-mcp` — video analysis is first-class per `vault_graffito/open-items.md:51` ("missing link for a fully closed evaluation loop... priority: high").
- **Added video tools:** `describe_video`, `score_video`, `extract_video_frames`. The frame-extraction tool is named in the vault as "the key bottleneck" (`vault_graffito/open-items.md:50`).
- **Split image analysis into two tools:** `describe_image` (Gemini observes, Claude judges) and `score_image` (Gemini judges via 6-dim eval). Same split for video. Lets us A/B which judgment locus produces better real-world iteration.
- **Added `intent` + `context` inputs** to all four describe/score tools. Lets Claude pass conversation-private context (the brief, prior iterations, what specifically to check this round) into Gemini's evaluation.
- **Added three named reference args** (`base_plate_path`, `identity_refs`, `style_refs`) to image/video analysis. Each routes into a distinct dimension prompt.
- **Added `references` map + `mode` discriminator** to `generate_video` return shape — surfaces Seedance's upload-order/role contract per `seedance-prompting-guide.md:31-42`.
- **Added `media_info` (`ffprobe`-derived)** to `generate_video` outputs.
- **Echoed `system_prompt`** in `generate_image` `resolved_params` for completeness.
- **Added Portability Principle** — explicit non-coupling to Patrick's vault markdown shape; tools are usable by any agent / any project.

---

## Goals

1. **Replace `Bash → uv run gemini-video-prompts ...` with a typed MCP tool call.** Today the agent assembles a long shell command; the MCP shifts the contract to function arguments with default-handling and validation.
2. **Make media evaluation a typed tool call.** Replace the manual "read image, reason about 6 dimensions, write notes" loop. Provide both *describe-only* (Claude judges) and *scored* (Gemini judges) modes so we can A/B which produces better iteration outcomes.
3. **Swap video generation from Veo to Seedance via Replicate (later Fal).** The existing CLI's Veo path is being retired in favor of Seedance.
4. **Stay a thin layer where the layer is genuinely thin, and admit when it isn't.** The image generation tool is plumbing over the existing CLI worker. The video generation tool is a new adapter for a new provider — that's not plumbing, it's adapter code, and the doc should say so. Analysis tools are thin Gemini wrappers with a structured response schema.
5. **Stay portable.** These MCPs are designed for Patrick's vault workflow but also for David's other projects, and for any future user. No vault-specific shapes leak into the tool surface (see Portability Principle below).

## Non-goals

- Not replacing the CLI. It stays as the local entry point for batch runs and dry-runs.
- Not vault-aware. The MCPs do not edit prompt logs, update Approved Keyframes tables, or write `Sent:` markers — that's the agent's job, working from MCP outputs.
- Not multi-provider routing in v1. One backend per modality. Provider abstraction is a v2 problem.
- Not handling the long video-poll problem in v1. Block-and-wait. Async/polling is a v2 decision.

---

## Portability Principle

These MCPs are designed for use by Patrick (GML / Graffito vaults), by David (DMPOST AE workflow, other projects), and by future users with no vault at all. Concrete rules:

- **Field names stay modality-natural**, not vault-natural. `outputs`, `references`, `prompt`, `model` — yes. `Sent`, `ResultNotes`, `NextIteration`, `BreadcrumbEntry` — never.
- **No vault-shape inputs.** No `vault_path`, no `prompts_file`, no `scene_id`. The MCP doesn't know what a vault is.
- **Return JSON, never Markdown.** Logging into a vault is the agent's job; the MCP's job is to return data.
- **Defaults are domain-natural, not project-natural.** The 6-dim eval criteria default comes from the global `generation-review-loop` skill (which any agent can load), not from Patrick's specific vault. The `criteria` arg is fully overridable so non-Patrick users pass their own dimensions.
- **Vault-logging contract is an audit, not a constraint.** The MCP return shape happens to contain everything Patrick's prompt-entry markdown needs (audit at end of doc). It is not *shaped* to match the markdown, and it should not drift toward such shaping in future revisions.

If a future tool or input feels vault-specific, it doesn't belong in these MCPs. Build a separate vault-aware MCP for that.

---

## Background

### Today's flow

1. Agent constructs a `cd /Users/.../gemini-video-prompts && uv run gemini-video-prompts --prompt "..." --system-prompt "..."` Bash command from project context.
2. Backgrounds it via `run_in_background=true` so the conversation stays responsive.
3. On completion, reads the captured stdout, parses the "saved N output(s) to X" line, and Reads the resulting PNG path.
4. Reasons about the image inline against six named dimensions (`generation-review-loop` SKILL.md), writes a freeform evaluation, surfaces it.

The two friction points are step 1 (long bespoke shell strings) and step 4 (unstructured evaluation that varies per session, with no way to pass conversation-private context into the eval).

### Prior art

- **`gemini-video-prompts/src/gemini_video_prompts/cli.py`** — clean separation between argparse plumbing and `generate_image_job(...)` / `generate_job(...)` workers. The image worker (`cli.py:622-733`) returns a well-shaped result dict and is reusable. **However**, it expects a *fully resolved* job dict (`cli.py:309-375` is the resolver), so the MCP can't call it directly without first reproducing or refactoring that resolver. See §Step 0 below.
- **`/Users/daviddickinson/Projects/LLM/DMPOST31/ae-mcp-dmpost/dmpost-gemini-mcp/server.py`** — FastMCP-based server using the same Python/Gemini stack. Demonstrates `@mcp.tool()` shape, error-code idiom, date-stamped output dirs, and Replicate integration via `vendor/replicate_min.py`. The `nano_segment` tool (`server.py:298-439`) is a working example of a Replicate-backed tool, but it's single-file-input — Seedance needs multi-file. The `nano_analyze_media` tool (`server.py:443-514`) demonstrates Gemini multimodal analysis with video-upload + poll; we lift its `upload_and_poll_video` / `cleanup_uploaded` helpers for `describe_video` / `score_video`.
- **`DMPOST31/.../vendor/replicate_min.py`** — minimal `replicate.run()` wrapper with file-handle lifecycle, sidecar dict, metrics. The lower-level `generate()` (line 107) takes a pure params dict and is the right entry point for Seedance; the higher-level `edit()` (line 173) only handles a single image and is too narrow for our needs.
- **Seedance prompting guide** (`vault_gml/visual/seedance-prompting-guide.md:23-42`) — three named modes (`text_to_video`, `first_last_frames`, `omni_reference`); upload-order @reference syntax; explicit role assignment required because "the model does not infer purpose."

---

## Architecture overview

Two independent MCP servers, each registered in `~/.claude.json` (or per-project `.mcp.json`):

| MCP | Repo | Purpose | Backends |
|-----|------|---------|----------|
| `gemini-prompts-mcp` | sibling package inside `gemini-video-prompts/` | Generate images and videos | Gemini (image), Replicate-Seedance (video) |
| `media-analysis-mcp` | new repo at `~/Projects/LLM/media-analysis-mcp/` | Describe / score / compare images and videos; extract frames; extract visual tokens | Gemini multimodal (analysis); ffmpeg/ffprobe (frame extraction, media info) |

### Why one MCP for both image + video gen (not split)

- Single responsibility is "generate media." Different providers under the hood is an implementation detail, not a UX boundary.
- Single venv, single `.mcp.json` entry, single restart on dep updates.
- Cost of the extra dep (`replicate`) is trivial — pure Python, no native bits.
- If/when Fal arrives, prefer adding `provider="fal"` arg or a sibling `generate_video_fal` tool over a third MCP.

Reconsider the split if the server file grows beyond ~500 lines or if backend-specific failure modes start polluting the shared code paths.

### Why one MCP for both image + video analysis

- Same Gemini multimodal backend, same response-schema pattern, same six default criteria.
- Frame extraction is conceptually a media-inspection tool — fits the same MCP even though it's ffmpeg-driven, not Gemini-driven. Tiny tool (~30 lines); not worth splitting until more transformation tools accumulate.
- Vault open-items name analysis + frame extraction as a single workflow ("the missing link in the loop: generate → review → extract frame → iterate" — `vault_graffito/open-items.md:51`).

---

## MCP #1 — `gemini-prompts-mcp`

### File layout

```
gemini-video-prompts/
├── src/
│   ├── gemini_video_prompts/         # existing CLI package
│   │   └── cli.py                    # MODIFIED — extract build_resolved_image_job()
│   └── gemini_video_prompts_mcp/     # NEW
│       ├── __init__.py
│       ├── __main__.py               # python -m gemini_video_prompts_mcp
│       ├── server.py                 # FastMCP entry + tool definitions
│       ├── seedance.py               # Seedance adapter (param mapping, file handles, validation)
│       └── replicate_min.py          # vendored from DMPOST31, +.mp4/.mov/.webm exts
├── pyproject.toml                    # MODIFIED — see diff below
└── ...
```

### `pyproject.toml` diff

```diff
 dependencies = [
     "google-genai>=1.47.0",
     "pillow>=10.4.0",
     "PyYAML>=6.0.2",
     "python-dotenv>=1.0.1",
+    "mcp[cli]>=1.0.0",
+    "replicate>=0.34.0",
 ]

 [project.scripts]
 gemini-video-prompts = "gemini_video_prompts.cli:main"
+gemini-prompts-mcp   = "gemini_video_prompts_mcp.server:main"

 [tool.hatch.build.targets.wheel]
-packages = ["src/gemini_video_prompts"]
+packages = ["src/gemini_video_prompts", "src/gemini_video_prompts_mcp"]

 [tool.hatch.build.targets.sdist]
 include = [
   "src/gemini_video_prompts",
+  "src/gemini_video_prompts_mcp",
   "pyproject.toml",
   "README.md",
   ".env.example",
   "prompts",
 ]
```

**Why main deps, not extras.** Putting `mcp[cli]` and `replicate` behind `[project.optional-dependencies]` would mean `pip install -e .` produces a `gemini-prompts-mcp` console script that crashes on first import. Both packages are pure-Python and small (~5MB combined). The dep boundary purity isn't worth the broken-on-default-install footgun.

### Step 0 — Refactor `cli.py`

**Why.** `generate_image_job(...)` (`cli.py:622`) takes a fully *resolved* job dict — meaning batch_path, source_index, source_format, and the merged-defaults+overrides+job key set. That resolution lives in `resolved_job()` (`cli.py:309-375`), which presupposes a CLI-shaped input (defaults dict, cli_overrides dict, batch source). For the MCP to call the worker, it would have to fake all four — or we extract the actual logic.

**Action.** Add `build_resolved_image_job()` to `cli.py`:

```python
def build_resolved_image_job(
    *,
    prompt: str,
    title: Optional[str] = None,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    image: Optional[str] = None,
    images: Optional[list[str]] = None,
    aspect_ratio: Optional[str] = None,
    image_size: Optional[str] = None,
    temperature: Optional[float] = None,
    num_outputs: int = 1,
    out_root: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Build a resolved image-mode job dict from inline parameters.

    Used by both the CLI inline-mode (--prompt) branch and the MCP generate_image tool.
    Mirrors the shape produced by resolved_job() for image-mode jobs.
    """
    ...
```

The CLI inline branch (`cli.py:826-841` + `resolved_job` call at 861) collapses to a single call into this helper. The MCP `generate_image` calls the same helper. One source of truth.

**Optional.** Same treatment for the video path is *not* needed in v1 — Veo's `generate_job()` is being retired. The new Seedance path is its own adapter (see below) and never touches the CLI's video resolver.

### Tools

#### `generate_image` (Gemini)

```python
def generate_image(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = "gemini-3-pro-image-preview",
    image: Optional[str] = None,                # single ref image path
    images: Optional[List[str]] = None,         # multiple ref image paths
    aspect_ratio: Optional[str] = None,         # "16:9", "9:16", "1:1", "3:4", ...
    image_size: Optional[str] = None,           # "1K", "2K", ...
    temperature: float = 0.7,
    num_outputs: int = 1,                       # 1..4
    title: Optional[str] = None,
    out_root: Optional[str] = None,             # default: <repo>/out
    dry_run: bool = False,                      # if True, return resolved job without firing
) -> dict
```

**Implementation.** With `build_resolved_image_job()` extracted, this is genuinely thin:

```python
@mcp.tool()
def generate_image(...) -> dict:
    job = build_resolved_image_job(prompt=prompt, model=model, system_prompt=system_prompt, ...)
    if dry_run:
        return summarize_job(1, job)
    client, gtypes = init_client()
    day_dir = ensure_dir(Path(job["out_root"]) / dt.date.today().isoformat())
    return generate_image_job(client=client, gtypes=gtypes, batch_path=...,
                              job=job, run_day_dir=day_dir)
```

Returns the result dict from `generate_image_job` verbatim — with `system_prompt` echoed in `resolved_params` so vault-logging callers can record it without re-deriving:

```jsonc
{
  "status": "ok",
  "created_at": "2026-05-08T...",
  "title": "sc04-v13-wide-establishing",
  "model": "gemini-3-pro-image-preview",
  "prompt": "...",
  "resolved_params": {
    "system_prompt": "...",
    "aspect_ratio": "16:9",
    "image_size": null,
    "temperature": 0.7,
    "num_outputs": 1,
    "image": null,
    "images": null
  },
  "input_count": 0,
  "inputs": [],
  "attempts": 1,
  "text": null,
  "job_dir": "/Users/.../out/2026-05-08/gemini-3-pro-image-preview/01_<title>_<hash>",
  "outputs": [
    { "index": 1, "path": "<job_dir>/<title>_01.png", "width": 1280, "height": 720 }
  ]
}
```

#### `generate_video` (Seedance via Replicate) — new adapter

This is **not a wrapper around `cli.py:generate_job`.** Veo and Seedance share neither parameter shape nor output handling. The video tool is a new Seedance-specific adapter living in `gemini_video_prompts_mcp/seedance.py`.

**Signature** (matching the Seedance 2.0 Replicate input schema, with mutual-exclusivity validated at tool entry):

```python
def generate_video(
    prompt: str,
    model: str = "bytedance/seedance-2.0",
    # First/last-frame inputs (mutually exclusive with reference_images)
    image: Optional[str] = None,                    # first frame
    last_frame_image: Optional[str] = None,         # last frame; requires image
    # Identity/style references (mutually exclusive with image/last_frame_image)
    reference_images: Optional[List[str]] = None,   # ≤9, prompt tokens [Image1]..[Image9]
    # Motion/audio references (layerable on either mode above; not mutually exclusive)
    reference_videos: Optional[List[str]] = None,   # ≤3, total ≤15s, [Video1]..[Video3]
    reference_audios: Optional[List[str]] = None,   # ≤3, total ≤15s, [Audio1]..[Audio3]
    # Output controls
    duration: int = 5,                              # 1..15, or -1 for "intelligent"
    resolution: str = "720p",                       # "480p" | "720p" | "1080p"
    aspect_ratio: str = "16:9",                     # "16:9"|"4:3"|"1:1"|"3:4"|"9:16"|"21:9"|"9:21"|"adaptive"
    generate_audio: bool = False,                   # NB: schema default true; we override to false
    seed: Optional[int] = None,
    title: Optional[str] = None,
    out_root: Optional[str] = None,
    timeout_s: int = 600,
    dry_run: bool = False,
) -> dict
```

**Validation at tool entry** (raise `INVALID_INPUT: <message>`):

- `image` or `last_frame_image` set AND any of `reference_images` set → mutually exclusive per schema
- `last_frame_image` set without `image` → "last_frame_image requires a first frame"
- `len(reference_images) > 9`, `len(reference_videos) > 3`, `len(reference_audios) > 3` → per-type cap exceeded
- `len(reference_images) + len(reference_videos) + len(reference_audios) > 12` → total reference cap exceeded (per `seedance-prompting-guide.md:23`)
- `duration` not in `{-1, 1..15}` → out of range
- `reference_audios` set but no `image`/`reference_images`/`reference_videos` → schema requires anchor
- `resolution` not in `{"480p","720p","1080p"}` → invalid
- `aspect_ratio` not in the schema enum → invalid

Strictness here is deliberate: Replicate rejects invalid combos with opaque API errors. We want a clean MCP error code instead.

**Why `generate_audio=False` default vs. schema's `True`.** Production usage tends to replace gen-audio with edited score in post; firing every job with audio wastes time and credits. Override on the call when audio is wanted.

**Reference token convention.** Seedance correlates prompt mentions to inputs via `[Image1]..[Image9]`, `[Video1]..[Video3]`, `[Audio1]..[Audio3]`. The agent uses the `seedance-prompting` skill to write the prompt; the MCP tool just passes the array order through. The return shape includes a soft validation warning if any uploaded slot is not named in prompt text.

**Provider-truthful tokens.** The `references[].token` field is the provider's actual syntax — Replicate-Seedance returns bracket form (`[Image1]`); a future Fal adapter would return whatever Fal accepts (likely `@Image1`). The agent reads `token` and pastes it into the prompt — never translates between providers. The `role` field stays provider-agnostic (`FIRST_FRAME`, `LAST_FRAME`, `REFERENCE_IMAGE`, `REFERENCE_VIDEO`, `REFERENCE_AUDIO`).

**Implementation outline.** `seedance.py`:

```python
def build_seedance_video_params(
    *, prompt, image, last_frame_image, reference_images, reference_videos,
    reference_audios, duration, resolution, aspect_ratio, generate_audio, seed,
) -> dict:
    """Pure function: validate inputs, return Replicate-shaped params dict.
    No I/O. Easily unit-testable."""
    ...

def derive_mode(*, image, last_frame_image, reference_images, reference_videos, reference_audios) -> str:
    """Returns one of 'text_to_video', 'first_last_frames', 'omni_reference' per
    seedance-prompting-guide.md:25."""
    ...

def build_references_map(...) -> list[dict]:
    """[{token, path, role}] in upload order. Roles: FIRST_FRAME, LAST_FRAME,
    REFERENCE_IMAGE, REFERENCE_VIDEO, REFERENCE_AUDIO."""
    ...

def check_prompt_references(prompt: str, references: list[dict]) -> list[str]:
    """Soft regex check: warn if any uploaded slot isn't named in prompt text."""
    ...

def run_seedance_job(
    *, api_params, return_params, image_path, last_frame_path,
    ref_image_paths, ref_video_paths, ref_audio_paths,
    out_dir, base_name, timeout_s,
) -> dict:
    """Build api_params (with file handles for image/last_frame_image/
    reference_images/reference_videos/reference_audios), call
    replicate_min.generate(), then replace the sidecar's `inputs` and
    `resolved_params` with `return_params` (string paths only — never file
    handles). Owns the file-handle lifecycle (try/finally close on every
    handle).

    Why explicit sanitization is required: replicate_min.generate()'s built-in
    filter (replicate_min.py:160-161) only excludes legacy keys (image,
    image_input, input_images). Seedance handles live under last_frame_image,
    reference_images, reference_videos, reference_audios — those would leak as
    file handles into returned JSON without this step."""
    ...
```

The `replicate_min.py` borrowed from DMPOST31 is *only* extended in one place: `_ext_from_url()` line 73 — add `.mp4`, `.mov`, `.webm` to the allowed-extensions set. Otherwise it stays pristine for parity with DMPOST31.

**Return shape** (normalized to mirror `generate_image`, plus video-specific fields):

```jsonc
{
  "status": "ok",
  "created_at": "2026-05-08T...",
  "title": "...",
  "model": "bytedance/seedance-2.0",
  "model_version": "@latest",
  "mode": "first_last_frames",
  "prompt": "...",
  "resolved_params": {
    "duration": 5, "resolution": "720p", "aspect_ratio": "16:9",
    "generate_audio": false, "seed": null
  },
  "references": [
    { "token": "[Image1]", "path": "/abs/first.png",  "role": "FIRST_FRAME" },
    { "token": "[Image2]", "path": "/abs/last.png",   "role": "LAST_FRAME" }
  ],
  "validation_warnings": [],
  "job_dir": "/Users/.../out/2026-05-08/bytedance-seedance-2.0/01_<title>_<hash>",
  "outputs": [
    {
      "index": 1,
      "path": "<job_dir>/<title>_01.mp4",
      "url": "https://replicate.delivery/...",
      "bytes": 8421337,
      "media_info": {
        "duration_s": 5.0,
        "fps": 24,
        "width": 1280,
        "height": 720,
        "has_audio": false
      }
    }
  ],
  "metrics": { "predict_time_s": 47.3, "download_time_s": 1.2,
               "elapsed_s": 49.1, "cold_start": false }
}
```

`media_info` is probed via `ffprobe` after download. `ffprobe` is a system dep — same as MCP #2's frame-extraction tool, so no new install pain.

**`dry_run=True` return shape.** When `dry_run=True`, no API call fires, no file handles open, no output writes. Status becomes `"planned"`; `outputs`/`metrics`/`created_at`/`model_version` are omitted; `projected_job_dir` is the path that *would* be created on a real run:

```jsonc
{
  "status": "planned",
  "title": "...",
  "model": "bytedance/seedance-2.0",
  "mode": "first_last_frames",
  "prompt": "...",
  "resolved_params": {
    "duration": 5, "resolution": "720p", "aspect_ratio": "16:9",
    "generate_audio": false, "seed": null
  },
  "references": [
    { "token": "[Image1]", "path": "/abs/first.png", "role": "FIRST_FRAME" }
  ],
  "validation_warnings": [],
  "projected_job_dir": "/Users/.../out/2026-05-08/bytedance-seedance-2.0/01_<title>_<hash>"
}
```

`generate_image` `dry_run=True` returns the resolved job summary from the existing CLI helper `summarize_job()` (`cli.py:402-423`) plus `status: "planned"` and `projected_job_dir`. Same shape contract: API call suppressed, paths projected, no file writes.

### Borrow map

| What | From | Action |
|------|------|--------|
| FastMCP scaffold + `mcp.run()` | `dmpost-gemini-mcp/server.py:25, 53, 517-518` | Copy verbatim |
| `_slugify`, `_prompt_stem` | `cli.py:30-45` | Import from `gemini_video_prompts.cli` |
| Output date-dir helpers (`_output_date_dir`, `_next_seq`) | `server.py:144-164` | Copy, retarget base from `~/Documents/dmpost-ae-mcp/generated/` to the existing `out/` convention |
| SDK version guard (`_google_genai_version`, `_supports_image_config`, `_require_image_config_support`) | `server.py:56-76` | Copy as-is |
| Error-code idiom: `raise RuntimeError("CODE: msg")` with codes like `IMAGE_NOT_FOUND`, `NO_IMAGE_RETURNED`, `OUTPUT_WRITE_FAILED`, `REPLICATE_ERROR`, `IMAGE_CONFIG_UNSUPPORTED`, `INVALID_INPUT`, `FFPROBE_FAILED` | `server.py` throughout | Adopt convention; add codes as new failure modes appear |
| `replicate_min.generate()` (lower-level entry — pure params, no implicit single-image handling) | `vendor/replicate_min.py:107-170` | Use as the call site for Seedance. Do **not** use `edit()` (single-file only). |
| `replicate_min._write_outputs` and `_ext_from_url` | `vendor/replicate_min.py:65-104` | Copy verbatim, edit one line: extend `_ext_from_url` allowed-set with `.mp4`, `.mov`, `.webm` (currently line 73) |
| `init_client()`, `generate_image_job()`, `slugify()`, `ensure_dir()`, `now_iso()`, `write_json()`, `summarize_job()`, the new `build_resolved_image_job()` | `gemini_video_prompts/cli.py` | Import from existing CLI package — do not duplicate |
| **New code (no borrow):** Seedance adapter — param validation, multi-file handle lifecycle, mode derivation, references map, prompt-text check | `gemini_video_prompts_mcp/seedance.py` | ~150 lines, owns its own correctness |
| **Drop:** WSL/Windows path normalization (`_windows_to_wsl`, `_wsl_to_windows`, `_resolve_path`, `_discover_base_dir`) | `server.py:97-142` | Mac-only, no AE bridge |
| **Drop:** `nano_segment` (SAM3) and `nano_analyze_media` (Gemini media analysis) tools | `server.py:298-514` | Out of scope for this MCP; analysis lives in MCP #2 |
| **Drop:** `replicate_min.edit()` | `vendor/replicate_min.py:173-223` | Single-file only; Seedance needs multi-file. Use `generate()` directly. |

### Configuration

Reads from environment (with `.env` fallback via `python-dotenv`, already loaded by `cli.py:62-68`):

- `GEMINI_API_KEY` — required
- `REPLICATE_API_TOKEN` — required when `generate_video` is called
- `GEMINI_IMAGE_MODEL` — optional default override (already honored by `cli.py:339`)

`GEMINI_VIDEO_MODEL` is no longer consulted (Veo retired); Seedance model_ref is `bytedance/seedance-2.0` constant unless overridden via the tool's `model` arg.

System dep: `ffprobe` (typically installed alongside `ffmpeg` — `brew install ffmpeg` on Mac).

---

## MCP #2 — `media-analysis-mcp`

### File layout

```
media-analysis-mcp/
├── pyproject.toml
├── README.md
├── .env.example
└── src/
    └── media_analysis_mcp/
        ├── __init__.py
        ├── __main__.py
        ├── server.py                  # FastMCP entry + tool definitions
        ├── schemas.py                 # Pydantic response schemas for Gemini structured output
        ├── prompts.py                 # describe / score / token-extract / compare templates
        ├── gemini_media.py            # video upload+poll, image loading, multimodal call dispatch
        └── ffmpeg_utils.py            # frame extraction (ffmpeg) + media probe (ffprobe)
```

`pyproject.toml`:

```toml
[project]
name = "media-analysis-mcp"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "mcp[cli]>=1.0.0",
    "google-genai>=1.49.0",
    "pillow>=10.4.0",
    "python-dotenv>=1.0.1",
    "pydantic>=2.0",
]

[project.scripts]
media-analysis-mcp = "media_analysis_mcp.server:main"
```

System dep: `ffmpeg` + `ffprobe` (`brew install ffmpeg` on Mac). README documents this as a prereq.

### Tools

| Tool | Default model | Purpose | Who judges? |
|------|---------------|---------|-------------|
| `describe_image` | `gemini-3.1-pro-preview` | Rich structured observations of one image; no scores | **Claude** — consumes the description, applies the rubric herself |
| `score_image` | `gemini-3.1-pro-preview` | 6-dim scored eval of one image; advisory `decision_hint` | **Gemini** — Claude can override |
| `describe_video` | `gemini-3.1-pro-preview` | Rich structured observations of one video; video-aware fields | **Claude** |
| `score_video` | `gemini-3.1-pro-preview` | 6-dim scored eval of one video, dims adapted per skill SKILL.md:290-300 | **Gemini** |
| `compare_images` | `gemini-3.1-pro-preview` | "Which is better"; pick + reasoning | **Gemini** |
| `extract_visual_tokens` | `gemini-3-flash-preview` | Categorized token deconstruct for env-coverage genesis workflow | **Gemini (descriptive)** |
| `extract_video_frames` | — (no model) | ffmpeg subprocess; timestamp → PNG list | — |

**Why both `describe` and `score`.** `describe_image` lets Claude do the judgment using conversation-private context. `score_image` lets Gemini do the judgment when Claude wants a fast structured verdict. They wrap the same Gemini call internally — only the system instruction + response schema differs. A/B testing which produces better real-world iteration outcomes is the empirical question this design supports.

**Why option (a) — no `analyze_image` wrapper.** Naming forces the choice between describe and score, and the choice is meaningful. A wrapper that picks for you (or accepts a `mode` arg) hides what's actually happening.

### Common inputs across describe/score tools

All four tools (`describe_image`, `score_image`, `describe_video`, `score_video`) accept:

```python
prompt: str                           # the gen prompt (what the gen model was asked to do)
intent: Optional[str] = None          # the brief — what this generation was trying to solve
context: Optional[str] = None         # case-specific freeform notes — prior iterations, what to focus on this round
base_plate_path: Optional[str] = None # for preservation_fidelity (mutation eval)
identity_refs: Optional[List[str]] = None   # for identity carry-through eval
style_refs: Optional[List[str]] = None      # for style_lock comparison
model: str = "gemini-3.1-pro-preview"
temperature: float = 0.3                    # low — we want consistent eval
system_prompt: Optional[str] = None         # rare override for model behavior
```

**Why `intent` and `context` are split (not one freeform field):**

- `intent` is durable — Claude probably has it once and reuses it across many calls on related shots.
- `context` is per-call, ephemeral — "this is iteration 4, prior failed on X, check whether Y was fixed."
- They route into different parts of the eval template: `intent` feeds Creative Brief Fidelity (dim 6); `context` directs attention to the specific dimension being checked this iteration.

**Why three named ref args (not one `references` list):**

- Each routes into a distinct part of the eval template. `base_plate_path` triggers the preservation-fidelity sub-prompt; `identity_refs` trigger identity-carry sub-prompts; `style_refs` trigger a style-lock comparison.
- Discoverability: an agent picking the tool sees three named args and knows which to populate per use case.
- Trade-off: less generic than a flat list, but covers the vault use cases (`vault_gml/CLAUDE.md:162, 166`) and is easy to extend.

### `describe_image`

Input: common inputs + `image_path: str`.

Returns:

```jsonc
{
  "model": "gemini-3.1-pro-preview",
  "image_path": "/path/to/image.png",
  "observations": {
    "composition": "Wide isometric establishing shot. Central machine dominates the frame; supermarket facade in background. Cars arranged in tidy rows along the left and right edges; the foreground asphalt is mostly empty.",
    "subject_elements": "...",
    "color_and_palette": "...",
    "style_and_rendering": "...",
    "lighting_and_atmosphere": "...",
    "text_and_signage": "...",
    "notable_or_unexpected": "...",
    "artifacts_or_failures": "..."
  },
  "freeform_observations": "Optional model-driven prose — anything that didn't fit the structured fields.",
  "context_used": {
    "prompt": "...",
    "intent": "...",
    "context": "...",
    "base_plate_path": null,
    "identity_refs": [],
    "style_refs": []
  }
}
```

Eight fixed observation categories — predictable for the agent to consume. `freeform_observations` catches anything off-taxonomy. `context_used` echoes inputs so the agent can audit what the eval had to work with.

### `score_image`

Input: common inputs + `image_path: str` + `criteria: Optional[List[str]] = None` (default = SIX_DIMENSIONS from `~/.claude/skills/generation-review-loop/SKILL.md:74-131`).

Returns:

```jsonc
{
  "model": "gemini-3.1-pro-preview",
  "image_path": "/path/to/image.png",
  "evaluations": {
    "prompt_fidelity":         { "score": 75, "notes": "Wide lot ✓, machine ✓..." },
    "preservation_fidelity":   { "score": null, "notes": "N/A — Genesis t2i" },
    "style_lock":              { "score": 80, "notes": "..." },
    "scene_hierarchy":         { "score": 90, "notes": "..." },
    "story_service":           { "score": 80, "notes": "..." },
    "creative_brief_fidelity": { "score": 70, "notes": "..." }
  },
  "summary": "~80% pass. Direction Gate trigger: time-of-day register drift.",
  "decision_hint": "direction_gate",
  "context_used": { "...": "same shape as describe_image" }
}
```

`decision_hint` is one of `accept`, `iterate`, `reroll`, `direction_gate` — derived from dimension scores via the decision tree at `generation-review-loop/SKILL.md:135-155`. **Treat as the model's vote, not a verdict.** The agent has final say. We expect drift before calibration.

### `describe_video` / `score_video`

Same shapes as image versions, plus video-specific observation fields:

- `describe_video.observations` adds: `motion_and_camera`, `pacing_and_timing`, `frame_continuity`, `audio_quality` (when audio present).
- `score_video.evaluations` uses the same six dim names but with video-adapted prompt language per `generation-review-loop/SKILL.md:290-300`.

Implementation: video upload via Gemini Files API + poll, lifted from `dmpost-gemini-mcp/server.py:481-494` (`upload_and_poll_video`, `cleanup_uploaded`). Wrapped in `gemini_media.py` so the tool functions stay clean.

### `compare_images`

```python
def compare_images(
    image_paths: List[str],
    prompt: str,
    intent: Optional[str] = None,
    context: Optional[str] = None,
    criteria: Optional[List[str]] = None,
    model: str = "gemini-3.1-pro-preview",
) -> dict
```

Returns:

```jsonc
{
  "model": "...",
  "image_paths": ["..."],
  "comparison": "Image 1 holds style lock better; image 2 has stronger composition...",
  "pick": { "best_index": 1, "best_path": "...", "reasoning": "..." }
}
```

### `extract_visual_tokens`

```python
def extract_visual_tokens(
    image_path: str,
    categories: Optional[List[str]] = None,    # default: TOKEN_CATEGORIES
    intent: Optional[str] = None,              # focuses the extraction
    model: str = "gemini-3-flash-preview",
) -> dict
```

Default categories (from `vault_gml/CLAUDE.md:164` env-coverage workflow):

```python
TOKEN_CATEGORIES = ["lighting", "atmosphere", "palette", "materials", "spatial_grammar"]
```

Returns:

```jsonc
{
  "model": "gemini-3-flash-preview",
  "image_path": "/path/to/image.png",
  "tokens": {
    "lighting":        ["high-key commercial", "warm key light", "no cast shadows"],
    "atmosphere":      ["pristine", "midday", "no haze"],
    "palette":         ["candy pink", "electric mint", "warm cream"],
    "materials":       ["glossy lacquer", "matte fabric"],
    "spatial_grammar": ["isometric 3/4", "flat horizontal plane"]
  }
}
```

These tokens drop directly into the genesis-prompt-from-tokens workflow.

### `extract_video_frames`

```python
def extract_video_frames(
    video_path: str,
    timestamps: List[Union[float, str]],   # seconds (5.5) or HH:MM:SS strings
    out_dir: Optional[str] = None,         # default: <video_dir>/frames/
    title_prefix: Optional[str] = None,    # default: <video_basename>
) -> dict
```

ffmpeg subprocess per timestamp: `ffmpeg -ss <ts> -i <video> -frames:v 1 -q:v 1 <out>`.

Returns:

```jsonc
{
  "video_path": "/abs/path/clip.mp4",
  "frame_count": 3,
  "frames": [
    { "timestamp_s": 0.0,  "path": "/.../clip_t0.000.png", "width": 1280, "height": 720 },
    { "timestamp_s": 2.5,  "path": "/.../clip_t2.500.png", "width": 1280, "height": 720 },
    { "timestamp_s": 4.99, "path": "/.../clip_t4.990.png", "width": 1280, "height": 720 }
  ]
}
```

### Configuration

- `GEMINI_API_KEY` — required
- No Replicate dep — Gemini-only
- System deps: `ffmpeg` + `ffprobe`

### Implementation notes

- **Use Gemini `response_schema`** (Pydantic class) to force JSON conformance for describe/score/extract tools. Falls back to tolerant text parsing only on schema-rejection errors.
- **Image loading:** Pillow `Image.open(path).convert("RGB")` (matches both `cli.py:101` and `server.py:478`).
- **Video upload:** `client.files.upload(...)` + poll until `state == "ACTIVE"`, then pass as `gtypes.FileData(file_uri=..., mime_type=...)`. Always `cleanup_uploaded()` in `finally`.
- **Prompt templates:** one shared template per mode in `prompts.py`. Each template interpolates `{intent}`, `{context}`, `{prompt}`, the criterion list (for score), and reference role descriptions (for ref-aware tools).

---

## A/B comparison plan

The describe/score split exists because we don't yet know which produces better real-world iteration. The doc supports the experiment but doesn't prejudge it.

Once both modes are live, three comparisons:

| Comparison | Setup | Measures |
|------------|-------|----------|
| **Cold A/B** | Both modes, no `intent`/`context` | Raw model judgment quality |
| **Briefed A/B** | Both modes, `intent`+`context` populated | Model judgment with Claude's context |
| **Cross A/B** | Per-job: which mode produced the better next-iteration prompt? | Real-world workflow win-rate |

The empirical question: does briefed describe-mode produce text rich enough that Claude's downstream judgment dominates anyway? Does briefed score-mode become accurate enough to trust directly? Both are possible; only running tells.

Tracking method: log MCP calls to a side-file with `mode`, `decision_hint` (when score-mode), and the agent's actual next action. Manual review every 20 calls.

---

## Sequencing

| # | Step | Verify by |
|---|------|-----------|
| 0 | Refactor `cli.py`: extract `build_resolved_image_job()`. CLI inline-mode branch (`cli.py:826-869`) collapses to a single helper call. | Existing CLI inline run produces byte-identical output. |
| 1 | Scaffold MCP #1 package; `pyproject.toml` updates per the diff above. | `uv run gemini-prompts-mcp` starts cleanly on stdio. |
| 2 | Implement `generate_image` (Gemini path). | Wire into Claude Code's `.mcp.json`, fire today's v13 prompt via the MCP, compare output to direct CLI run. |
| 3 | Copy `replicate_min.py`, edit `_ext_from_url`. Implement `seedance.py` (`build_seedance_video_params`, `derive_mode`, `build_references_map`, `check_prompt_references`, `run_seedance_job`). Implement `generate_video` calling into `seedance.py`. Add `ffprobe` `media_info` probe. | Smoke test with a known-good Seedance prompt + start frame. Inspect job_dir, manifest, references map, media_info, validation_warnings. |
| 4 | Scaffold MCP #2 repo at `~/Projects/LLM/media-analysis-mcp/`. | `uv run media-analysis-mcp` starts on stdio. |
| 5 | Implement `gemini_media.py` (image load, video upload+poll). Implement `prompts.py` (describe / score / token templates). Implement `describe_image` and `score_image` with default criteria. | Pipe today's v13 PNG through both tools, compare outputs to my hand-written eval from this session. A/B note. |
| 6 | Implement `describe_video` and `score_video`. | Pipe a known Seedance .mp4 through both. Compare to manual eval. |
| 7 | Implement `ffmpeg_utils.py` and `extract_video_frames`. | Verify timestamp precision against a video with a known cut at 2.5s. |
| 8 | Implement `compare_images` and `extract_visual_tokens`. | Round-trip: `generate_image` → `describe_image` (or `score_image`) → `extract_visual_tokens` → seed a new genesis prompt. |

Step 0 is small but critical — without it, Step 2 has to fake CLI internals. Step 2 remains the natural pause point: once the MCP `generate_image` matches the CLI byte-for-byte, the rest is repetition of the pattern.

---

## Open decisions

| # | Question | Status |
|---|----------|--------|
| 1 | Sibling package layout vs. separate repo for MCP #1 | **Closed** — sibling |
| 2 | One gen MCP vs. split image/video | **Closed** — one |
| 3 | Seedance default | **Closed** — `bytedance/seedance-2.0` |
| 4 | Whether `generate_video` blocks or returns `prediction_id` | **Open** — block in v1 |
| 5 | `decision_hint` opinionated or omit | **Closed** — include, advisory-only |
| 6 | Embed Patrick's `seedance-prompting` skill in `generate_video` docstring | **Closed** — no, skill loads on its own |
| 7 | `generate_audio` default | **Closed** — `False` (override per call) |
| 8 | MCP deps — main or extras | **Closed** — main |
| 9 | `plan_job` standalone vs. `dry_run` arg | **Closed** — `dry_run` arg |
| 10 | Image-only analysis vs. media (image+video) analysis | **Closed** — media (rename to `media-analysis-mcp`) |
| 11 | Reference shape: flat list vs. three named args | **Closed** — three named args (`base_plate_path`, `identity_refs`, `style_refs`) |
| 12 | Single `analyze_image` tool vs. `describe`/`score` split | **Closed** — split, with shared backend |
| 13 | Convenience `analyze_image` wrapper | **Closed** — no, force explicit choice |
| 14 | Default model for analysis tools | **Closed** — Pro (`gemini-3.1-pro-preview`) for all except `extract_visual_tokens` (Flash) |
| 15 | `intent` + `context` as separate inputs vs. single freeform field | **Closed** — separate (different lifetimes, different routing) |
| 16 | Frame extraction tool location | **Closed** — inside `media-analysis-mcp` |

---

## Future / v2

- **Provider abstraction** for video: once Fal lands, factor `provider` arg or sibling tool. Defer until a second provider exists.
- **Async/polling** for `generate_video`. Split into `start_video_job` / `poll_video_job` if blocking annoys.
- **`plan_job` standalone tool.** If a workflow emerges where the agent wants pre-flight without a tool call to a gen function, revisit. For now `dry_run` covers it.
- **Streaming results** for analysis tools. Useful for long describe-mode outputs.
- **Vault-aware tools** (`log_prompt_entry`, `mark_keyframe_approved`). Tempting but reverses the Portability Principle. Build a separate vault-aware MCP if desired.
- **A/B telemetry tooling.** Side-file logging is manual in v1; a dedicated `analysis_log` field in returns + a small reporter script would scale better.
- **`structured_intent`** as a typed alternative to freeform `intent` — captures brief in fields like `genre`, `register`, `must_preserve`, `must_change`. Defer; freeform is more flexible until patterns emerge.
- **`compare_videos`.** Same as `compare_images` but for clips. Add when first asked for.
- **Fal video adapter** as a sibling `fal.py` module.

---

## Schema reference — Seedance 2.0 (Replicate)

Captured 2026-05-08 from `bytedance/seedance-2.0` Replicate input schema. Source of truth for the `generate_video` tool signature.

**Required:** `prompt`

**Mutually exclusive groups:**
- Group A: `image`, `last_frame_image` (`last_frame_image` requires `image`)
- Group B: `reference_images`
- A and B cannot both be set.

**Anchored requirement:** `reference_audios` requires at least one of `image` / `reference_images` / `reference_videos`.

**Total cap (vault, not schema):** `len(reference_images) + len(reference_videos) + len(reference_audios) ≤ 12` per `seedance-prompting-guide.md:23`. Per-type caps sum to 15 (9+3+3); 12 is the working ceiling. Replicate's schema does not enforce this — we do.

**Field summary:**

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `prompt` | string | required | — |
| `seed` | int? | nullable | null |
| `image` | uri? | first frame; mut.ex. with `reference_images` | null |
| `last_frame_image` | uri? | requires `image`; mut.ex. with `reference_images` | null |
| `reference_images` | uri[] | ≤9; mut.ex. with `image`/`last_frame_image`; correlated as `[Image1]..[Image9]` | `[]` |
| `reference_videos` | uri[] | ≤3, total ≤15s; correlated as `[Video1]..[Video3]` | `[]` |
| `reference_audios` | uri[] | ≤3, total ≤15s; needs anchor; `[Audio1]..[Audio3]` | `[]` |
| `duration` | int | -1..15 (-1 = "intelligent") | 5 |
| `resolution` | enum | `480p` / `720p` / `1080p` | `720p` |
| `aspect_ratio` | enum | `16:9`/`4:3`/`1:1`/`3:4`/`9:16`/`21:9`/`9:21`/`adaptive` | `16:9` |
| `generate_audio` | bool | dialogue (double-quoted in prompt) + SFX + music | **`true`** (we override to `false`) |

---

## Vault-logging contract — audit, not constraint

For Patrick's GML/Graffito vaults specifically, the agent logs each generation into a Markdown prompt-entry. This audit confirms the MCP return shapes contain everything that workflow needs — **without coupling MCP design to the markdown shape**. Any field not in the list below either (a) is generic enough to map cleanly anyway, or (b) belongs in the vault's own metadata/notes rather than the MCP return.

| Vault prompt-entry field | image return path | video return path |
|--------------------------|-------------------|-------------------|
| Date / Sent | `created_at` | `created_at` |
| Source plate / refs | `inputs[]` | `references[]` |
| Model | `model` | `model` |
| System instruction | `resolved_params.system_prompt` | N/A (Seedance) |
| Job addenda | (text in prompt; agent's responsibility) | (same) |
| Prompt | `prompt` | `prompt` |
| Output path | `outputs[].path` | `outputs[].path` |
| Dimensions | `outputs[].{width, height}` | `outputs[].media_info.{width, height}` |
| Duration | N/A | `outputs[].media_info.duration_s` |
| FPS | N/A | `outputs[].media_info.fps` |
| Has audio | N/A | `outputs[].media_info.has_audio` |
| Seed | (Gemini doesn't expose) | `resolved_params.seed` |
| Generation timing | (Gemini doesn't expose) | `metrics.predict_time_s` |
| Mode | N/A | `mode` |
| Validation warnings | N/A | `validation_warnings[]` |

This audit informs *what fields the MCP returns* but does not *shape the field names*. Field naming is modality-natural per Portability Principle.

---

## File reference index

- This doc: `/Users/daviddickinson/Projects/Lora/gemini-video-prompts/MCP_DESIGN.md`
- CLI we're wrapping: `/Users/daviddickinson/Projects/Lora/gemini-video-prompts/src/gemini_video_prompts/cli.py`
- DMPOST31 server prior art: `/Users/daviddickinson/Projects/LLM/DMPOST31/ae-mcp-dmpost/dmpost-gemini-mcp/server.py`
- DMPOST31 Replicate vendor: `/Users/daviddickinson/Projects/LLM/DMPOST31/ae-mcp-dmpost/dmpost-gemini-mcp/vendor/replicate_min.py`
- Generation review loop skill: `~/.claude/skills/generation-review-loop/SKILL.md`
- Seedance prompting guide: `~/Projects/Lora/ComfyPromptByAPI-patrick/WorkingSpace/patrick/vault_gml/visual/seedance-prompting-guide.md`
- Vault MCP-relevant guidance: `~/Projects/Lora/ComfyPromptByAPI-patrick/WorkingSpace/patrick/vault_gml/CLAUDE.md`
- Vault open-items (frame extraction + video MCP priority): `~/Projects/Lora/ComfyPromptByAPI-patrick/WorkingSpace/patrick/vault_graffito/open-items.md`
