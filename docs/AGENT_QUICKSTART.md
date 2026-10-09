# Run riff-mcp from a fresh agent session

Read the repository's [AGENTS.md](../AGENTS.md) first. This guide requires no
earlier conversation, private vault, or separately installed prompting skill.
Commands below run from the root of a checkout containing this feature.

If using a source archive rather than a Git checkout, skip `git status`.
Read only the relevant sections of the generated tool reference; the live
tool description/schema is sufficient when the MCP server is already connected.

## Choose the correct tool and billing account

| User's task | Tool or command | Access |
|---|---|---|
| Generate/edit an image with the host's built-in ImageGen | Use the host's own tool, outside riff | Host account and limits; riff cannot control this route |
| Generate/edit with GPT-Image-2 through riff | `generate_image`, explicit `provider="openai"` | `OPENAI_API_KEY`, separate API billing and explicit confirmation |
| Generate/edit with Gemini | `generate_image` with the default provider | `GEMINI_API_KEY`, Gemini API billing |
| Generate H3 Max video through fal | `start_fal_video_job`, then `get_video_job` | `FAL_KEY`, fal API billing and explicit confirmation; [guide](FAL_H3_MAX.md) |
| Generate Seedance video through MCP | `generate_video`, or `start_video_job` followed by `get_video_job` | `REPLICATE_API_TOKEN`, Replicate billing |
| Generate video through the batch CLI | `gemini-video-prompts --mode video` | `GEMINI_API_KEY`, Veo API billing |
| Ask about an image, video, or audio file | `analyze_image`, `analyze_video`, `analyze_audio` | `GEMINI_API_KEY`, Gemini API billing |
| Ask across multiple images/videos | `analyze_images`, `analyze_videos` | Gemini; ordered paths and optional labels |
| Structured observation, scoring, comparison | `describe_*`, `score_*`, `compare_images`, `extract_visual_tokens` | Gemini; use only when that output shape fits the task |
| Extract video frames | `extract_video_frames` | Local ffmpeg operation |
| Read an existing async video record | `get_generation`, `list_generations`, or `get_video_job` with `poll=false` | Local reads |

Every provider-backed riff generation/analysis call uses API access. Putting an
OpenAI key in this checkout does not change the host agent's subscription
sign-in. Installing an SDK or possessing a key is not permission to spend.
The explicit billing gate applies to OpenAI images and fal video submissions; the legacy
Gemini and Replicate tools do not implement that argument. Follow the user's
authorization for those tools as well.

## Install and check the checkout

Use Python 3.10+ and an installed `uv` executable. If either is unavailable,
use the [pip installation instructions](../README.md#install) or have the user
install the prerequisite. Do not replace an existing environment as a workaround.

```bash
pwd
git status --short --branch
uv sync --locked --extra openai
uv run --extra openai gemini-video-prompts --help
```

The help must list `--provider`, `--size`, and `--allow-api-billing`. If it does
not, this is an older checkout or the wrong executable. The package version
alone is insufficient: unreleased changes still use version `0.2.0`. Obtain the
intended revision from the maintainer; do not assume these changes are on a
remote default branch. Preserve local changes when updating.

Omit `--extra openai` if only Gemini/Replicate/fal is needed. Keep it on OpenAI-enabled
launch commands. `uv sync` is an exact environment sync and can remove unused
extras; stop running servers before changing their shared environment, then
restart them. For verification that must not touch the existing environment,
set `UV_PROJECT_ENVIRONMENT` to a separate absolute directory when running uv.

## Configure credentials without exposing them

If `.env` does not exist, create it from `.env.example` without overwriting an
existing file. Ask the user to edit it locally. For OpenAI images, the only key
needed is:

```dotenv
OPENAI_API_KEY=<the user's API key>
```

This is a placeholder, not a usable key. Remove the placeholder and supply a
real value locally. Never read the entire `.env` into a chat, log, or tool
output. Leave unused credentials commented out. An inherited environment value
takes precedence over `.env`; an empty client `env` entry can shadow a valid key.

A restricted OpenAI key needs access to `POST /v1/images/generations` and
`POST /v1/images/edits`. The optional doctor network check also needs model-read
access. Edits send reference images directly; a Files API upload is not needed.
Key permissions and available API billing/quota are separate requirements.

```bash
uv run --extra openai riff-mcp-doctor --provider openai
```

Expected: all checks pass. This only checks local configuration/imports; a
placeholder can pass the presence check. If useful, add `--network` for a
non-generating model lookup. A successful lookup does not prove image-generation
or billing permission. The default doctor checks Gemini/Replicate and video
binaries, so use `--provider openai` for an OpenAI-only setup.

## Connect the MCP server

Merge [`.mcp.example.json`](../.mcp.example.json) into the client's MCP
configuration and replace `REPO_PATH` with this checkout's absolute path.
Do not overwrite the user's other server entries or permission settings.
For an OpenAI-only workflow, register only `gemini-prompts`; `media-analysis`
is an optional second server and uses Gemini.

The generation command is:

```bash
uv --directory /ABSOLUTE/PATH/TO/riff-mcp run --extra openai gemini-prompts-mcp
```

This is a long-running stdio server, not a one-shot generation command. Let the
MCP client own the process. Restart/reconnect it after installation, code, or
configuration changes. In the client's tool list, verify `generate_image` has
`provider`, `size`, `quality`, and `allow_api_billing` (default false).
Use the actual discovered tool name; the client may prefix it with the alias
`gemini-prompts`. Do not confuse it with a host tool named ImageGen.

Use absolute input/output paths in MCP calls. With `--directory`, a relative
reference path resolves against riff-mcp, not the user's other project.

For fal H3 Max, follow [the fal guide](FAL_H3_MAX.md): `FAL_KEY`, the dedicated
`start_fal_video_job` tool, 480p/768p, explicit text-to-video routes, and scoped API approval. Its reference
tokens and parameters differ from Seedance; do not translate them implicitly.

## Preview, approve, and generate one OpenAI image

Call the MCP tool `generate_image` with these arguments:

```json
{
  "provider": "openai",
  "model": "gpt-image-2",
  "prompt": "A blue ceramic cup on a cream background, soft side lighting.",
  "size": "1024x1024",
  "quality": "low",
  "num_outputs": 1,
  "title": "blue-cup",
  "dry_run": true
}
```

Expected: `status="planned"`, `billing_confirmation_required=true`, a billing
notice, and `projected_job_dir`. No provider request or output file is created.
Dry runs do not establish key access or that referenced files exist.

Tell the user: "This uses separately billed OpenAI API access, not your
ChatGPT/Codex subscription. May I generate one low-quality 1024×1024 image?"
If the user has already approved this exact request or a bounded batch that
includes it, use that authorization without asking again.

After approval, repeat the same call with `dry_run=false` and
`allow_api_billing=true`. Do not change its count, quality, size, or scope beyond
the approval. Do not set the flag just to silence an error. The flag records
caller confirmation; it is not independent proof of human consent or a spend cap.

Expected: `status="ok"`, `outputs[]`, `job_dir`, `request_id`, and available
`usage`. Open/view `outputs[0].path`, check the actual width/height and image,
and read `<job_dir>/job.json` for the record. A successful API response is not
the user's creative approval.

## Select GPT Image 2.5

In the OpenAI preview above, set `model` to `gpt-image-2.5-sunburst` or
`gpt-image-2.5-flare`. Both add `xhigh` and `max` quality; these values are
rejected for GPT Image 2. Existing defaults and billing gates are unchanged.
The unsuffixed `gpt-image-2.5` is not a documented model ID.

Use `dry_run=true` first. Model lookup checks visibility; only a successful
image request proves generation access. Do not silently fall back to another
model after an error. Restart the MCP server after updating.
See the [official image guide](https://developers.openai.com/api/docs/guides/image-generation).

## Edit the returned image

Use the exact path from `outputs[0].path`, not a guessed filename. Call
`generate_image` with the same provider/size/quality, set `image` to that path,
and give a bounded edit prompt such as "Change only the cup glaze to green."
Preview with `dry_run=true`; execute with `allow_api_billing=true` only after
approval that includes this additional billable edit.

`image` plus an `images` list supplies ordered references (duplicate paths are
deduplicated). OpenAI accepts PNG/JPEG/WebP inputs; the adapter validates up to
16 references, each below 50 MiB. The first request without references selects
generation; adding references selects editing automatically.

OpenAI Images API calls are stateless here. Do not pass Gemini
`previous_interaction_id`, `store`, `thinking_level`, `temperature`,
`system_prompt`, `aspect_ratio`, or `image_size`. Use `size` for dimensions and
put instructions in `prompt`. Start with direct edit instructions and omit
`mask`, including for local changes such as recoloring a jacket. A mask is not
required to identify an object or preserve the surrounding image.

### Regional edits with a mask

Masks are optional. Use one for a user-supplied selection or after a direct edit
shows a targeting problem. Do not routinely hand-trace objects before editing:
imperfect boundaries can introduce missed edges and constrain the desired change.

Pass `image` for the base PNG and `mask` for a PNG of identical dimensions,
with an alpha channel. Fully transparent pixels mark the editable region.
Riff currently requires masks smaller than 4 MiB and PNG base images; it does
not convert assets automatically. With multiple references, the mask applies
to the first resolved image. Mask guidance does not guarantee unchanged pixels
elsewhere; inspect the result. Paths and content hashes are saved with the job.

Use `generate_image` with `provider="openai"`, `image`, `mask`, and the edit
prompt, or supply `--mask /absolute/path/mask.png` in CLI image mode. YAML jobs
accept `mask`; relative paths resolve against the batch file. Preview first,
then obtain approval for the specified image count, model, quality, and size.

### Compare models or quality settings

Call `compare_image_variants` with a shared prompt and 2–8 variants. Each makes
one image; every variant must specify model, quality, and size (no auto values).
For example:

```json
{
  "prompt": "A blue ceramic cup on a cream background",
  "variants": [
    {"label": "Flare medium", "model": "gpt-image-2.5-flare", "quality": "medium", "size": "1024x1024"},
    {"label": "Sunburst medium", "model": "gpt-image-2.5-sunburst", "quality": "medium", "size": "1024x1024"}
  ],
  "dry_run": true
}
```

Show the preview and billing notice. After approval for the entire batch, pass
`dry_run=false` and `allow_api_billing=true`. Optional `image`, `images`, and
`mask` apply to all variants. Start model/quality comparisons without a mask;
test whether masking helps separately, keeping the model and quality fixed.
Execution stops at the first failure without retry.
All assets are checked before the first submission. Save and inspect the result:
`review_path` is the side-by-side HTML, `record_path` contains complete JSON,
and each successful entry retains the original job record and outputs.

Cost estimates use returned token usage and dated uncached list prices, excluding
discounts and taxes. Missing usage means unknown cost, not zero. Latency includes
the API call and local output handling. There is no seed matching or automatic
winner: one sample is for visual comparison, not a reliable model benchmark.
Keep the entire comparison directory when sharing the HTML so images resolve.
This workflow is an MCP tool; the existing YAML CLI can still run ordinary batches.

For repeatable settings, select quality explicitly; `auto` lets the provider
choose. PNG is the default output format; JPEG/WebP and compression are
selectable. The returned `response_metadata` and decoded image dimensions
describe the actual result. Built-in host ImageGen controls may differ; do not
infer its quality tier from the appearance or dimensions of an image.

## Run a batch or use the CLI instead

```bash
# Free preview: two low-quality 1536x1024 images.
uv run gemini-video-prompts prompts/example_openai_image_batch.yaml --plan

# Only after user approval for that two-image batch; this makes billable calls.
uv run --extra openai gemini-video-prompts prompts/example_openai_image_batch.yaml \
  --allow-api-billing --fail-fast
```

For a single CLI image, use `--prompt`, `--mode image`, `--provider openai`,
`--size`, `--quality`, and `--plan`; replace `--plan` with `--allow-api-billing`
after approval. The CLI otherwise defaults to video. Add `--image` for an edit.
`--image`/`--images` are inline-prompt options; in a batch, put reference paths
inside each job. Batch reference paths resolve relative to the batch file.

The CLI flag approves only the current invocation. Batch contents cannot
authorize billing. `--limit N` limits jobs, not images; sum each job's
`num_outputs` when requesting approval. `--fail-fast` stops after a failure.
Without it, the CLI continues to later jobs. See the [supported keys](../README.md#supported-keys).

## Find results and recover from errors

Image calls are blocking and return their output paths directly. There is no
OpenAI image job ID to poll. `get_generation` and `list_generations` cover
async video jobs, not image records. For images, retain the returned result
and read its `job.json`; CLI batches also write a run manifest.

Relative `out_root` resolves against the checkout root. Use an explicit absolute
`out_root` for project-owned media. Existing image job names are deterministic:
the same resolved request, title, date, and output root can reuse its directory
and overwrite outputs. Use a distinct `title` or output root to retain another
take. A matching hash is not a cache hit or an idempotency guarantee.

| Symptom | Next action |
|---|---|
| Tool lacks `provider` or billing argument | Check the checkout and restart the MCP server; refresh its tool list |
| `API_BILLING_CONFIRMATION_REQUIRED` | Surface the billing notice and obtain scoped approval; do not auto-enable the flag |
| `OPENAI_AUTH_ERROR` | Have the user set the key locally; check for an empty inherited env override without printing secrets |
| `OPENAI_SETUP_ERROR` | Install/run with `--extra openai`; reconnect the MCP client |
| `INVALID_INPUT` | Correct the named parameter; use the OpenAI fields, not Gemini options |
| `IMAGE_NOT_FOUND` | Use the real absolute path; a successful dry run does not check file existence |
| HTTP 401/403 | Check key validity, endpoint permissions, and project access; do not switch accounts/providers silently |
| HTTP 429 / insufficient quota | Report the API quota/billing issue; do not retry or fall back to another account |
| Timeout or connection failure | Completion may be uncertain. Inspect the saved record and provider dashboard; another submission can incur another charge |
| Empty, malformed, or partial output | Inspect any saved outputs and the failure record; do not report full success or silently top up missing images |

OpenAI requests have a five-minute timeout and no automatic SDK retries. Once
execution begins, a job record is written; early validation or billing-gate
failures do not write one. Successful records contain usage, not a dollar invoice.
Do not print full provider errors or credentials while troubleshooting.

## Verify documentation and understand its evidence

```bash
uv run python scripts/update_tool_reference.py --check
uv run --extra openai pytest -q
uv run --extra openai ruff check src tests scripts
```

These are offline checks. The [tool reference](TOOL_REFERENCE.md) contains all
registered tool input schemas and descriptions. [README.md](../README.md) is
the broader usage guide. [MCP_DESIGN.md](../MCP_DESIGN.md) retains historical
design stages, not copy/paste setup instructions. [LIVE_VERIFICATION.md](../LIVE_VERIFICATION.md)
records what was actually tested; success at one size/quality does not verify
all combinations or grant authorization for another paid test.

## Adaptive video inspection

Use `analyze_video` or `analyze_videos` with `processing="agentic"` and omit
`fps`. For example (Gemini API billing applies):

```json
{
  "video_path": "/absolute/path/to/clip.mp4",
  "question": "Locate the final shot transition and state timing uncertainty.",
  "model": "gemini-3.8-flash",
  "processing": "agentic",
  "max_output_tokens": 12000
}
```

`processing="static"` is the default and preserves the existing request path.
Omitting FPS alone does not enable adaptive inspection. Agentic mode uses
Interactions; the same mode applies to every video in a multi-video request.
Riff rejects explicit FPS with agentic mode to avoid conflicting controls.

Save the complete tool result as JSON in the task's output directory. It includes
`processing_trace`, `agentic_processing_verified`, and the raw `interaction`
response (including usage when supplied). Verification requires a matching
processing call/result pair; an answer alone is insufficient. The SDK's typed
parser can omit these steps, so Riff retains raw response JSON. This evidence
confirms navigation, not exact-frame accuracy or creative acceptance.

These are synchronous single-turn calls with `store=false`; streaming,
background execution, and follow-up interaction IDs are not exposed here.
Uploaded videos are cleaned up after success or failure. Riff does not silently
fall back to static mode. Restart the media-analysis MCP server after updating
and verify that its tool schema exposes `processing`.

See [Google's processing-mode documentation](https://ai.google.dev/gemini-api/docs/video-understanding#agentic-video-understanding)
for supported models and provider limitations.
