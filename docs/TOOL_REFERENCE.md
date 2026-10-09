# MCP tool reference

Generated from the registered tools in this checkout. Do not edit by hand.

Start with [the agent quickstart](AGENT_QUICKSTART.md). These are local MCP
tool names, not OpenAI built-in ImageGen or provider REST endpoints. Your
client may prefix names with its configured server alias. The live
`tools/list` response is authoritative for the running server; restart
the server if it differs from this checkout.

Update: `uv run python scripts/update_tool_reference.py`.
Check for drift: `uv run python scripts/update_tool_reference.py --check`.
Neither command calls a provider, reads credentials, or generates media.

The JSON blocks are input schemas, not example calls. `required` lists
mandatory arguments. A nullable model/API default can be resolved inside
the tool; see its description for the effective provider-specific default.

## gemini-prompts-mcp

9 tools.

- [compare_image_variants](#compare_image_variants)
- [generate_image](#generate_image)
- [generate_video](#generate_video)
- [start_video_job](#start_video_job)
- [start_fal_video_job](#start_fal_video_job)
- [get_video_job](#get_video_job)
- [cancel_video_job](#cancel_video_job)
- [get_generation](#get_generation)
- [list_generations](#list_generations)

### compare_image_variants

Compare 2..8 OpenAI model/quality/size variants of one shared prompt.

Each variant requires explicit model, quality, size and optional label.
No auto values; exactly one image per variant. References and optional PNG
alpha mask are shared. dry_run=True previews the full billable batch for free.
Start edit comparisons without a mask: use the same reference and direct
instruction for every variant. Do not introduce a hand-drawn mask merely
to compare models. Test masking separately if targeting errors warrant it.
Obtain approval for every variant before setting allow_api_billing=True:
these are OpenAI API charges, separate from ChatGPT/Codex subscriptions.
Execution stops on the first failure, with no retries or model fallback.

Returns comparison_dir, review_path (local side-by-side HTML), record_path
(JSON), and results containing original outputs, latency, usage, and dated
uncached list-price cost estimates when token breakdowns are available.
Estimates are not invoices; missing usage is unknown, not zero. No matched
seed is available and one sample does not establish a model ranking.
Inspect saved job records after a failure before approving another attempt.

```json
{
  "properties": {
    "prompt": {
      "title": "Prompt",
      "type": "string"
    },
    "variants": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Variants",
      "type": "array"
    },
    "image": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Image"
    },
    "images": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Images"
    },
    "mask": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Mask"
    },
    "out_root": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Out Root"
    },
    "dry_run": {
      "default": true,
      "title": "Dry Run",
      "type": "boolean"
    },
    "allow_api_billing": {
      "default": false,
      "title": "Allow Api Billing",
      "type": "boolean"
    }
  },
  "required": [
    "prompt",
    "variants"
  ],
  "title": "compare_image_variantsArguments",
  "type": "object"
}
```

### generate_image

Generate one or more images with Gemini (default) or OpenAI GPT Image 2.

Wraps the gemini-video-prompts CLI's image generation worker. Outputs
land at ``<out_root>/<today>/<model>/<seq>_<title>_<hash>/``, matching the
CLI layout. Gemini saves PNG; OpenAI saves the returned PNG/JPEG/WebP bytes.

Args:
    prompt: The image generation prompt.
    system_prompt: Style / behavior instruction sent as system_instruction.
    model: Defaults to ``gemini-nano-banana-2.1`` for Gemini or ``gpt-image-2`` for OpenAI.
        Pass ``gemini-3-pro-image`` explicitly for Nano Banana Pro.
        OpenAI also accepts explicit ``gpt-image-2.5-sunburst`` or
        ``gpt-image-2.5-flare``; account availability is provider-controlled.
    image: Path to a single reference image (img2img).
    images: List of additional reference image paths.
    mask: Optional OpenAI-only PNG alpha mask; omit by default. For an
        identifiable object, first use image plus a direct edit instruction.
        Use a mask for a user-supplied selection or a demonstrated targeting
        problem, not as routine preparation. Imperfect boundaries can harm
        edits. The mask applies to the first reference image.
        Transparent areas indicate edits. Requires a PNG first reference of
        matching dimensions and a mask smaller than 4 MiB. Mask guidance
        does not guarantee pixel-exact preservation outside the region.
    aspect_ratio: e.g. ``"16:9"``, ``"9:16"``, ``"1:1"``, ``"3:4"``.
        Requires google-genai with ImageConfig support.
    image_size: e.g. ``"1K"``, ``"2K"``. Same caveat as aspect_ratio.
    temperature: Optional override; omitted by default for Gemini 3.x models.
    num_outputs: 1..4 images per call.
    api: Gemini: ``generate_content`` (default) or ``interactions``.
        OpenAI: ``images`` (default); references automatically select edits.
    thinking_level: Optional ``minimal``/``low``/``medium``/``high``.
        Model support varies; Nano Banana 2.1 documents minimal/medium/high
        and Gemini 3.1 Flash Image minimal/high.
    store: Persist an Interactions request so it can be continued later.
    previous_interaction_id: Continue a stored interaction. Requires
        ``api="interactions"`` and sends ``prompt`` as the next instruction.
    title: Optional human-readable title; defaults to first words of prompt.
    out_root: Override output root; default ``<cli-repo>/out``.
    dry_run: If True, return the resolved job + projected_job_dir without
        calling the model or creating files.
    provider: ``gemini`` (default) or ``openai``. OpenAI requires
        ``uv sync --extra openai`` and ``OPENAI_API_KEY``; API billing applies.
    size: OpenAI pixel dimensions, e.g. ``1536x1024``, or ``auto``.
    quality: OpenAI ``auto``, ``low``, ``medium``, or ``high``; GPT Image 2.5
        also supports ``xhigh`` and ``max``.
    output_format: OpenAI ``png`` (default), ``jpeg``, or ``webp``.
    background: OpenAI ``auto``, ``opaque``, or ``transparent`` (PNG/WebP).
    output_compression: OpenAI JPEG/WebP compression, integer 0..100.
    allow_api_billing: Required True for a billable OpenAI request; default False.
        OpenAI API charges are separate from ChatGPT/Codex subscription limits.
        Ask the user to approve this request or a bounded batch before setting
        True. An installed key, provider selection, or auto-approved MCP tool
        does not establish spending authorization. Do not silently retry or
        switch from subscription generation to API billing. Dry runs are free.

    OpenAI does not accept Gemini aspect_ratio, image_size, temperature,
    system_prompt, thinking_level, or stored interaction options. Include
    all instructions in prompt and pass saved output paths as references
    for subsequent edits. OpenAI results also record provider, access,
    request_id, usage, response_metadata, and original image byte hashes.

Returns:
    On success: the full result dict from ``generate_image_job`` with
    ``status``, ``title``, ``model``, ``prompt``, ``resolved_params``,
    ``input_count``, ``inputs``, ``attempts``, ``interaction_ids``,
    ``text``, ``job_dir``, and ``outputs[]`` (each with ``index``, ``path``,
    ``width``, ``height``). For the Interactions path,
    ``interaction_ids`` contains the provider IDs that can be supplied as
    ``previous_interaction_id`` on a later call.

    On dry_run: the summarized job plus ``status: "planned"`` and
    ``projected_job_dir``.

Raises:
    RuntimeError: with codes ``IMAGE_NOT_FOUND``, ``INVALID_INPUT``,
        ``NO_IMAGE_RETURNED``, ``IMAGE_CONFIG_UNSUPPORTED``, or others
        propagated from the underlying generate_image_job.

```json
{
  "properties": {
    "prompt": {
      "title": "Prompt",
      "type": "string"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    },
    "model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Model"
    },
    "image": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Image"
    },
    "images": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Images"
    },
    "aspect_ratio": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Aspect Ratio"
    },
    "image_size": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Image Size"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "num_outputs": {
      "default": 1,
      "title": "Num Outputs",
      "type": "integer"
    },
    "api": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Api"
    },
    "thinking_level": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Thinking Level"
    },
    "store": {
      "default": false,
      "title": "Store",
      "type": "boolean"
    },
    "previous_interaction_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Previous Interaction Id"
    },
    "title": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Title"
    },
    "out_root": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Out Root"
    },
    "dry_run": {
      "default": false,
      "title": "Dry Run",
      "type": "boolean"
    },
    "provider": {
      "default": "gemini",
      "title": "Provider",
      "type": "string"
    },
    "size": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Size"
    },
    "quality": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Quality"
    },
    "output_format": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Output Format"
    },
    "background": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Background"
    },
    "output_compression": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Output Compression"
    },
    "allow_api_billing": {
      "default": false,
      "title": "Allow Api Billing",
      "type": "boolean"
    },
    "mask": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Mask"
    }
  },
  "required": [
    "prompt"
  ],
  "title": "generate_imageArguments",
  "type": "object"
}
```

### generate_video

Generate a video with Seedance 2.5 (or 2.0) via Replicate.

Wraps the Seedance adapter (``seedance.py``) — param mapping, multi-file
handle lifecycle, sidecar sanitization, ffprobe ``media_info`` per output.
Outputs land at
``<out_root>/<today>/<model_slug>/01_<title_slug>_<hash>/<title>_NN.mp4``.

Mode discriminator (returned in ``mode``): ``text_to_video`` |
``first_last_frames`` | ``omni_reference`` per
``seedance-prompting-guide.md:25``.

Reference token convention: bracket syntax (``[Image1]``, ``[Video1]``,
``[Audio1]``). The ``references[]`` return field carries provider-truthful
tokens — paste them verbatim into the prompt rather than translating.

Args:
    prompt: Text prompt for video generation. Put dialogue in double
        quotes; cite references by token (``[Image1]``, ``[Video1]``).
    model: Replicate model_ref. Default ``bytedance/seedance-2.5``;
        ``bytedance/seedance-2.0`` is still supported (it is the only one
        with 1080p/4k). Limits below are 2.5's; 2.0's are in parentheses.
    image: First frame (img2vid). On 2.5 mutually exclusive with ALL
        ``reference_*`` inputs (2.0: only with reference_images).
    last_frame_image: Last frame; requires image. Same exclusivity as image.
    reference_images: Up to 30 (2.0: 9) reference image paths (identity,
        style, or composition). Mut.ex. with image/last_frame_image.
    reference_videos: Up to 10 (2.0: 3) reference video paths; combined
        ≤ 30s (2.0: 15s). Motion transfer, style, editing, extension.
        Editing mode wants ``duration=-1``.
    reference_audios: Up to 10 (2.0: 3) reference audio paths; combined
        ≤ 30s (2.0: 15s). Requires an anchor (reference_images /
        reference_videos; on 2.0 also image).
    duration: 4..30 (2.0: 4..15) seconds, or -1 for "intelligent" length.
    resolution: ``"480p"`` | ``"720p"`` (2.0 also ``"1080p"`` | ``"4k"``).
    aspect_ratio: ``16:9``/``4:3``/``1:1``/``3:4``/``9:16``/``21:9``/
        ``adaptive`` (2.0 also ``9:21``). Leave unset to get ``"adaptive"``
        when a first frame is given (the frame's own ratio wins) and
        ``"16:9"`` otherwise. Explicit values are passed through as-is.
    generate_audio: If True, Seedance generates synchronized audio.
        Default False (production typically replaces with edited score).
    seed: Optional reproducibility seed.
    title: Optional human-readable title; defaults to first words of prompt.
    out_root: Override output root; default ``<cli-repo>/out``.
    timeout_s: Replicate poll timeout. Default 600.
    dry_run: If True, validate inputs + return ``status: "planned"`` plus
        ``projected_job_dir`` without firing or touching files.

Returns:
    Real run: ``{ status, created_at, started_at, title, model,
    model_version, mode, prompt, resolved_params, references[],
    validation_warnings[], job_dir, outputs[], metrics }``.
    Dry run: same minus ``outputs``/``metrics``/``created_at``/
    ``model_version``, plus ``status: "planned"`` and ``projected_job_dir``.

Raises:
    RuntimeError: with codes ``INVALID_INPUT`` (unsupported model,
    mut.ex., per-type cap, range, enum, anchor), ``FILE_NOT_FOUND``
    (real run only),
    ``REPLICATE_ERROR`` (Replicate API failure),
    ``REPLICATE_NOT_INSTALLED`` / ``REPLICATE_API_TOKEN_MISSING``.

```json
{
  "properties": {
    "prompt": {
      "title": "Prompt",
      "type": "string"
    },
    "model": {
      "default": "bytedance/seedance-2.5",
      "title": "Model",
      "type": "string"
    },
    "image": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Image"
    },
    "last_frame_image": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Last Frame Image"
    },
    "reference_images": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Reference Images"
    },
    "reference_videos": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Reference Videos"
    },
    "reference_audios": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Reference Audios"
    },
    "duration": {
      "default": 5,
      "title": "Duration",
      "type": "integer"
    },
    "resolution": {
      "default": "720p",
      "title": "Resolution",
      "type": "string"
    },
    "aspect_ratio": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Aspect Ratio"
    },
    "generate_audio": {
      "default": false,
      "title": "Generate Audio",
      "type": "boolean"
    },
    "seed": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Seed"
    },
    "title": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Title"
    },
    "out_root": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Out Root"
    },
    "timeout_s": {
      "default": 600,
      "title": "Timeout S",
      "type": "integer"
    },
    "dry_run": {
      "default": false,
      "title": "Dry Run",
      "type": "boolean"
    }
  },
  "required": [
    "prompt"
  ],
  "title": "generate_videoArguments",
  "type": "object"
}
```

### start_video_job

Start a Seedance video job and return immediately.

This is the local-async counterpart to ``generate_video`` — same inputs,
same per-model validation (see ``generate_video`` for the 2.5 / 2.0
limits). It validates inputs, creates a durable local job record under
``<out_root>/jobs/<job_id>``,
starts a non-blocking Replicate prediction, and returns the current status.
Use ``get_video_job(job_id)`` to poll or collect completed outputs.

If ``out_root`` is set here, pass the same ``out_root`` to ``get_video_job``
and ``cancel_video_job``. ``webhook_url`` is forwarded to Replicate for
future HTTP receiver workflows; this stdio MCP still relies on polling.

```json
{
  "properties": {
    "prompt": {
      "title": "Prompt",
      "type": "string"
    },
    "model": {
      "default": "bytedance/seedance-2.5",
      "title": "Model",
      "type": "string"
    },
    "image": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Image"
    },
    "last_frame_image": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Last Frame Image"
    },
    "reference_images": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Reference Images"
    },
    "reference_videos": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Reference Videos"
    },
    "reference_audios": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Reference Audios"
    },
    "duration": {
      "default": 5,
      "title": "Duration",
      "type": "integer"
    },
    "resolution": {
      "default": "720p",
      "title": "Resolution",
      "type": "string"
    },
    "aspect_ratio": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Aspect Ratio"
    },
    "generate_audio": {
      "default": false,
      "title": "Generate Audio",
      "type": "boolean"
    },
    "seed": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Seed"
    },
    "title": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Title"
    },
    "out_root": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Out Root"
    },
    "webhook_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Webhook Url"
    }
  },
  "required": [
    "prompt"
  ],
  "title": "start_video_jobArguments",
  "type": "object"
}
```

### start_fal_video_job

Submit one H3 Max video to fal's durable queue, then return a local job_id.

Uses separately billed FAL_KEY API access, not a subscription. A key or
auto-approved tool is not spending authorization. Before executing, obtain
user approval for endpoint, clip count, duration, resolution, and references;
then set allow_api_billing=true. Honor existing approval within that scope.
Reference-to-video may bill input references in addition to output seconds.
Confirmation is caller acknowledgement, not an independently enforced cap.

Args:
    prompt: 1..50000 characters. Reference tokens are Image 1, Video 1,
        Audio 1, etc., numbered independently in each ordered list.
    model: minimax/h3-max/image-to-video (default),
        minimax/h3-max/reference-to-video,
        minimax/h3-max/text-to-video, or
        minimax/h3-max-turbo/text-to-video. No automatic provider fallback.
    image: Local first frame, required for image-to-video only. Output
        inherits its aspect ratio. Remote URLs are not accepted by this adapter.
    last_frame_image: Optional local last frame for image-to-video.
    reference_images: Reference-to-video only; up to 9 local images.
    reference_videos: Up to 3 local video clips, each 2..15s, total <=15s.
    reference_audios: Up to 3 local audio clips, each 2..15s, total <=15s.
        Reference-to-video needs an image or video anchor; max 12 files
        across modalities. ffprobe is required for video/audio validation.
    duration: Integer 5..15 seconds. One video per submission, with native
        model audio; no separate generate_audio switch on these endpoints.
    resolution: 480p or 768p (default); uppercase accepted too.
    aspect_ratio: Reference-to-video: adaptive (default), 21:9, 16:9,
        4:3, 1:1, 3:4, or 9:16. Text-to-video: 16:9 (default), 21:9,
        4:3, 1:1, 3:4, or 9:16. Image-to-video does not accept it.
    seed: Optional integer; provider-returned seed is recorded separately.
    prompt_expansion_mode: balanced (default, about 1s rewrite) or quality
        (up to about 30s rewrite). This is prompt preprocessing effort.
        Both submitted prompt and returned expanded_prompt are preserved.
    enable_safety_checker: fal's safety checker; default true.
    title: Human-readable label. Each real submission gets a unique folder.
    out_root: Absolute output root recommended; default repository out/.
    dry_run: Validate parameter shape and preview references without reading
        credentials, reading media, writing files, or making requests.
    allow_api_billing: Explicit approval acknowledgement, default false.

Use get_video_job(job_id, out_root) to poll and collect the video;
poll=false and get_generation are local reads. list_generations finds jobs.
cancel_video_job requests cancellation, which may not stop running work.
References are sent as inline data URIs after approval (100 MiB total local
limit). Job records keep hashes and paths, not those large encoded inputs.
No automatic HTTP retries or resubmissions. fal can retry work within its
own queue. If submission is uncertain, inspect the local record and fal
dashboard before authorizing another billable attempt.

```json
{
  "properties": {
    "prompt": {
      "title": "Prompt",
      "type": "string"
    },
    "model": {
      "default": "minimax/h3-max/image-to-video",
      "title": "Model",
      "type": "string"
    },
    "image": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Image"
    },
    "last_frame_image": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Last Frame Image"
    },
    "reference_images": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Reference Images"
    },
    "reference_videos": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Reference Videos"
    },
    "reference_audios": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Reference Audios"
    },
    "duration": {
      "default": 5,
      "title": "Duration",
      "type": "integer"
    },
    "resolution": {
      "default": "768p",
      "title": "Resolution",
      "type": "string"
    },
    "aspect_ratio": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Aspect Ratio"
    },
    "seed": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Seed"
    },
    "prompt_expansion_mode": {
      "default": "balanced",
      "title": "Prompt Expansion Mode",
      "type": "string"
    },
    "enable_safety_checker": {
      "default": true,
      "title": "Enable Safety Checker",
      "type": "boolean"
    },
    "title": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Title"
    },
    "out_root": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Out Root"
    },
    "dry_run": {
      "default": false,
      "title": "Dry Run",
      "type": "boolean"
    },
    "allow_api_billing": {
      "default": false,
      "title": "Allow Api Billing",
      "type": "boolean"
    }
  },
  "required": [
    "prompt"
  ],
  "title": "start_fal_video_jobArguments",
  "type": "object"
}
```

### get_video_job

Return an async video job; optionally poll its saved provider (Replicate or fal).

fal completion downloads the video and records the expanded prompt. Polling
never resubmits inference. With poll=false this only reads the local record.

```json
{
  "properties": {
    "job_id": {
      "title": "Job Id",
      "type": "string"
    },
    "out_root": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Out Root"
    },
    "poll": {
      "default": true,
      "title": "Poll",
      "type": "boolean"
    }
  },
  "required": [
    "job_id"
  ],
  "title": "get_video_jobArguments",
  "type": "object"
}
```

### cancel_video_job

Request cancellation from the saved provider and persist its response.

fal acceptance does not guarantee running work stops or charges are avoided.
Continue polling the existing job; never resubmit it to check cancellation.

```json
{
  "properties": {
    "job_id": {
      "title": "Job Id",
      "type": "string"
    },
    "out_root": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Out Root"
    }
  },
  "required": [
    "job_id"
  ],
  "title": "cancel_video_jobArguments",
  "type": "object"
}
```

### get_generation

Return the durable request, status, and result records for one job.

This is a read-only provenance lookup. It never polls the provider or
modifies the generation. For a running job, ``result`` remains ``None``;
call ``get_video_job`` first when provider polling is desired.

```json
{
  "properties": {
    "job_id": {
      "title": "Job Id",
      "type": "string"
    },
    "out_root": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Out Root"
    }
  },
  "required": [
    "job_id"
  ],
  "title": "get_generationArguments",
  "type": "object"
}
```

### list_generations

List and search durable async generation records.

New jobs append lifecycle summaries to ``jobs/index.ndjson``. The lookup
also scans existing ``jobs/*/status.json`` records so generations created
before the index existed are immediately discoverable.

```json
{
  "properties": {
    "out_root": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Out Root"
    },
    "query": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Query"
    },
    "status_filter": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Status Filter"
    },
    "limit": {
      "default": 50,
      "title": "Limit",
      "type": "integer"
    }
  },
  "title": "list_generationsArguments",
  "type": "object"
}
```

## media-analysis-mcp

12 tools.

- [describe_image](#describe_image)
- [score_image](#score_image)
- [analyze_image](#analyze_image)
- [analyze_images](#analyze_images)
- [analyze_audio](#analyze_audio)
- [describe_video](#describe_video)
- [score_video](#score_video)
- [analyze_video](#analyze_video)
- [analyze_videos](#analyze_videos)
- [compare_images](#compare_images)
- [extract_visual_tokens](#extract_visual_tokens)
- [extract_video_frames](#extract_video_frames)

### describe_image

Rich structured observations of an image. No scoring, no verdict —
Claude is the judge.

Returns the eight fixed observation categories (composition,
subject_elements, color_and_palette, style_and_rendering,
lighting_and_atmosphere, text_and_signage, notable_or_unexpected,
artifacts_or_failures) plus optional freeform_observations and
context_used echo.

Args:
    image_path: Absolute path to the image to describe.
    prompt: The gen prompt that produced the image (or empty if not
        available — describe-mode is still useful as a generic visual
        read).
    intent: The creative brief — what this generation was trying to
        solve. Routed into the description as "Brief: ...".
    context: Per-call freeform notes — prior iterations, what to focus
        on this round. Routed as "Context for this evaluation: ...".
    base_plate_path: Optional reference image; the source plate the
        output was mutated from. Helps Gemini call out what changed.
    identity_refs: Optional list of character/asset reference paths.
        Useful for evaluating identity carry-through across shot types.
    style_refs: Optional list of style anchor paths.
    model: Gemini model id. Default ``gemini-3.8-flash``.
    temperature: Optional sampling override; omitted by default.
    system_prompt: Override the default observation-mode system
        instruction. Rare.

Raises:
    RuntimeError: ``IMAGE_NOT_FOUND``, ``API_KEY_MISSING``, or other
    coded errors from gemini_media helpers.

```json
{
  "properties": {
    "image_path": {
      "title": "Image Path",
      "type": "string"
    },
    "prompt": {
      "title": "Prompt",
      "type": "string"
    },
    "intent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Intent"
    },
    "context": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Context"
    },
    "base_plate_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Base Plate Path"
    },
    "identity_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Identity Refs"
    },
    "style_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Style Refs"
    },
    "model": {
      "default": "gemini-3.8-flash",
      "title": "Model",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    }
  },
  "required": [
    "image_path",
    "prompt"
  ],
  "title": "describe_imageArguments",
  "type": "object"
}
```

### score_image

Calibrated scored evaluation against criteria. Gemini is the judge.

Default criteria are the 6 dimensions from the global
``generation-review-loop`` skill (prompt_fidelity, preservation_fidelity,
style_lock, scene_hierarchy, story_service, creative_brief_fidelity).
Override via the ``criteria`` arg for non-Patrick workflows.

Returns per-criterion ``{score, notes}``, a 1-2 sentence summary, and a
``decision_hint`` (advisory; agent has final say).

Args:
    image_path: Absolute path to the image to score.
    prompt: The gen prompt that produced the image.
    intent: The creative brief — feeds creative_brief_fidelity dim.
    context: Per-call freeform notes — directs attention to the
        specific dim being checked this iteration.
    base_plate_path: Reference for preservation_fidelity dim.
    identity_refs: References for identity-carry checks within
        scene_hierarchy / creative_brief_fidelity dims.
    style_refs: References for style_lock dim.
    criteria: Override the default 6-dim list. Each entry becomes one
        evaluation in the response.
    model: Gemini model id. Default ``gemini-3.8-flash``.
    temperature: Optional sampling override; omitted by default.
    system_prompt: Override the default scoring-mode system
        instruction. Rare.

Raises:
    RuntimeError: ``IMAGE_NOT_FOUND``, ``API_KEY_MISSING``, or other
    coded errors from gemini_media helpers.

```json
{
  "properties": {
    "image_path": {
      "title": "Image Path",
      "type": "string"
    },
    "prompt": {
      "title": "Prompt",
      "type": "string"
    },
    "intent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Intent"
    },
    "context": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Context"
    },
    "base_plate_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Base Plate Path"
    },
    "identity_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Identity Refs"
    },
    "style_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Style Refs"
    },
    "criteria": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Criteria"
    },
    "model": {
      "default": "gemini-3.8-flash",
      "title": "Model",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    }
  },
  "required": [
    "image_path",
    "prompt"
  ],
  "title": "score_imageArguments",
  "type": "object"
}
```

### analyze_image

Free-form image analysis. Same multimodal plumbing as describe_image,
but no response schema — Gemini answers ``question`` in prose.

Use when describe_image's 8-category taxonomy is too rigid or off-axis
for what you actually want to know. For repeatable structured
observation across iterations, prefer describe_image.

Args:
    image_path: Absolute path to the image to analyze.
    question: The agent's question for Gemini to answer.
    prompt: Optional gen prompt that produced this image.
    intent: Optional creative brief.
    context: Optional per-call notes.
    base_plate_path: Optional reference image (source plate).
    identity_refs: Optional character/asset reference paths.
    style_refs: Optional style anchor paths.
    model: Gemini model id. Default ``gemini-3.8-flash``.
    temperature: Optional sampling override; omitted by default.
    system_prompt: Override the default analyze-mode instruction.
    thinking_level: Gemini reasoning depth. Omitted by default, so the
        model's own default applies. Thinking tokens are billed as
        output and are usually most of a call's cost.

Returns:
    The model, resolved image path, question, free-form answer, an echo
    of the context, and ``usage``: the token counts Gemini reported
    (``None`` when it reported none). ``usage`` carries no prices.

Raises:
    RuntimeError: ``IMAGE_NOT_FOUND``, ``API_KEY_MISSING``,
    ``NO_RESPONSE``, or other coded errors.

```json
{
  "properties": {
    "image_path": {
      "title": "Image Path",
      "type": "string"
    },
    "question": {
      "title": "Question",
      "type": "string"
    },
    "prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Prompt"
    },
    "intent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Intent"
    },
    "context": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Context"
    },
    "base_plate_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Base Plate Path"
    },
    "identity_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Identity Refs"
    },
    "style_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Style Refs"
    },
    "model": {
      "default": "gemini-3.8-flash",
      "title": "Model",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    },
    "thinking_level": {
      "anyOf": [
        {
          "enum": [
            "minimal",
            "low",
            "medium",
            "high"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Thinking Level"
    }
  },
  "required": [
    "image_path",
    "question"
  ],
  "title": "analyze_imageArguments",
  "type": "object"
}
```

### analyze_images

Ask one open-ended question across two to ten equal-role images.

Every image is inserted into one Gemini request behind an explicit
``IMAGE N — label`` marker. Unlike ``compare_images``, this tool has no
response schema, candidate roles, criteria, forced ranking, or winner.
It is intended for collection reading, pattern discovery, mode and
counterexample analysis, and other questions that depend on jointly
seeing several images.

Args:
    image_paths: Ordered list of two to ten distinct local image paths.
    question: Cross-image question for Gemini to answer in prose.
    labels: Optional ordered labels. Defaults to each file's basename.
    prompt: Optional shared generation prompt or source note.
    intent: Optional shared creative brief.
    context: Optional per-call notes.
    model: Gemini model id. Default ``gemini-3.8-flash``.
    temperature: Optional sampling override.
    system_prompt: Override the neutral multi-image instruction.
    thinking_level: Gemini reasoning depth. Defaults to ``high``. Pass
        ``None`` to use the model/API default.
    max_output_tokens: Maximum output budget. Defaults to 65,536. Pass
        ``None`` to use the model/API default.

Returns:
    Resolved ordered paths and labels, reasoning controls, the original
    question, a free-form answer, an echo of shared context, and
    ``usage``: the token counts Gemini reported (``None`` when it
    reported none; no prices applied).

Raises:
    RuntimeError: ``INVALID_INPUT`` for list/label errors,
    ``IMAGE_NOT_FOUND`` for a missing source, ``API_KEY_MISSING``,
    ``NO_RESPONSE``, or other provider errors.

```json
{
  "properties": {
    "image_paths": {
      "items": {
        "type": "string"
      },
      "title": "Image Paths",
      "type": "array"
    },
    "question": {
      "title": "Question",
      "type": "string"
    },
    "labels": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Labels"
    },
    "prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Prompt"
    },
    "intent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Intent"
    },
    "context": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Context"
    },
    "model": {
      "default": "gemini-3.8-flash",
      "title": "Model",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    },
    "thinking_level": {
      "anyOf": [
        {
          "enum": [
            "minimal",
            "low",
            "medium",
            "high"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": "high",
      "title": "Thinking Level"
    },
    "max_output_tokens": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": 65536,
      "title": "Max Output Tokens"
    }
  },
  "required": [
    "image_paths",
    "question"
  ],
  "title": "analyze_imagesArguments",
  "type": "object"
}
```

### analyze_audio

Free-form audio analysis and transcription with Gemini.

The file is uploaded through Gemini's Files API, polled until ACTIVE,
analyzed, and deleted in a ``finally`` block. The Files API's detected
MIME type is passed through unchanged, which makes this safe for M4A/AAC
recordings that cannot be routed through ``analyze_video``.

Args:
    audio_path: Absolute path to a local audio file.
    question: The analysis or transcription request.
    prompt: Optional provenance or production prompt.
    intent: Optional creative or analytical brief.
    context: Optional per-call notes.
    model: Gemini model id. Default ``gemini-3.8-flash``.
    temperature: Optional sampling override; omitted by default.
    system_prompt: Optional audio-analysis system instruction override.
    upload_timeout_s: Bounds the Files API upload+processing wait.

Raises:
    RuntimeError: ``AUDIO_NOT_FOUND``, ``AUDIO_UPLOAD_FAILED``,
    ``AUDIO_PROCESSING_TIMEOUT``, ``AUDIO_PROCESSING_FAILED``,
    ``AUDIO_MIME_MISMATCH``, ``API_KEY_MISSING``, ``INVALID_INPUT``,
    or ``NO_RESPONSE``.

```json
{
  "properties": {
    "audio_path": {
      "title": "Audio Path",
      "type": "string"
    },
    "question": {
      "title": "Question",
      "type": "string"
    },
    "prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Prompt"
    },
    "intent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Intent"
    },
    "context": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Context"
    },
    "model": {
      "default": "gemini-3.8-flash",
      "title": "Model",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    },
    "upload_timeout_s": {
      "default": 300,
      "title": "Upload Timeout S",
      "type": "integer"
    }
  },
  "required": [
    "audio_path",
    "question"
  ],
  "title": "analyze_audioArguments",
  "type": "object"
}
```

### describe_video

Rich structured observations of a video. No scoring, no verdict —
Claude is the judge.

Returns the eight image-shared observation categories plus four
video-specific (motion_and_camera, pacing_and_timing, frame_continuity,
audio_quality), an optional freeform field, and ``context_used`` echo.

Video upload via Gemini's Files API — the file is uploaded, polled until
``state == ACTIVE``, used in the multimodal call, then deleted in a
``finally`` block. ``upload_timeout_s`` bounds the upload+process wait.

Args:
    video_path: Absolute path to the video file (.mp4 / .mov / .webm).
    prompt: The gen prompt that produced the video.
    intent: The creative brief.
    context: Per-call freeform notes.
    base_plate_path: Optional reference frame the video was generated
        from. Useful for comparing how motion / continuity drift from
        the start frame.
    identity_refs: Optional list of character/asset reference paths.
    style_refs: Optional list of style anchor paths.
    model: Gemini model id. Default ``gemini-3.8-flash``.
    temperature: Optional sampling override; omitted by default.
    system_prompt: Override the default observation-mode instruction.
    upload_timeout_s: How long to wait for Files API to mark the upload
        ACTIVE before raising ``VIDEO_PROCESSING_TIMEOUT``.

Raises:
    RuntimeError: ``VIDEO_NOT_FOUND``, ``VIDEO_UPLOAD_FAILED``,
    ``VIDEO_PROCESSING_TIMEOUT``, ``VIDEO_PROCESSING_FAILED``,
    ``API_KEY_MISSING``, or ``NO_RESPONSE``.

```json
{
  "properties": {
    "video_path": {
      "title": "Video Path",
      "type": "string"
    },
    "prompt": {
      "title": "Prompt",
      "type": "string"
    },
    "intent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Intent"
    },
    "context": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Context"
    },
    "base_plate_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Base Plate Path"
    },
    "identity_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Identity Refs"
    },
    "style_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Style Refs"
    },
    "fps": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Fps"
    },
    "model": {
      "default": "gemini-3.8-flash",
      "title": "Model",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    },
    "upload_timeout_s": {
      "default": 300,
      "title": "Upload Timeout S",
      "type": "integer"
    }
  },
  "required": [
    "video_path",
    "prompt"
  ],
  "title": "describe_videoArguments",
  "type": "object"
}
```

### score_video

Calibrated scored evaluation of a video against criteria.

Same six default dimensions as ``score_image``, but with video-adapted
prompt language per ``generation-review-loop`` SKILL.md §Generalizing
to Video. Reuses ``ImageScoreResult`` schema — shape is identical to
image scoring; only the system prompt and lifecycle differ.

Args mirror ``describe_video`` plus ``criteria`` to override the default
six-dim list. ``upload_timeout_s`` bounds the Files API upload wait.

Raises:
    Same coded errors as ``describe_video`` plus ``SCHEMA_MISMATCH`` if
    Gemini returns criterion names that don't match the request.

```json
{
  "properties": {
    "video_path": {
      "title": "Video Path",
      "type": "string"
    },
    "prompt": {
      "title": "Prompt",
      "type": "string"
    },
    "intent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Intent"
    },
    "context": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Context"
    },
    "base_plate_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Base Plate Path"
    },
    "identity_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Identity Refs"
    },
    "style_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Style Refs"
    },
    "criteria": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Criteria"
    },
    "fps": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Fps"
    },
    "model": {
      "default": "gemini-3.8-flash",
      "title": "Model",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    },
    "upload_timeout_s": {
      "default": 300,
      "title": "Upload Timeout S",
      "type": "integer"
    }
  },
  "required": [
    "video_path",
    "prompt"
  ],
  "title": "score_videoArguments",
  "type": "object"
}
```

### analyze_video

Free-form video analysis. Same Files API plumbing as describe_video,
but no response schema — Gemini answers ``question`` in prose.

Use when describe_video's 12-category taxonomy is too rigid or off-axis
for what you actually want to know (e.g., "how does the camera move
in the second half?", "is the boy on the right's posture stable?").
For repeatable structured observation, prefer describe_video.

Args:
    video_path: Absolute path to the video file (.mp4 / .mov / .webm).
    question: The agent's question for Gemini to answer.
    prompt: Optional gen prompt that produced this video.
    intent: Optional creative brief.
    context: Optional per-call notes.
    base_plate_path: Optional reference frame.
    identity_refs: Optional character/asset reference paths.
    style_refs: Optional style anchor paths.
    fps: Sampling rate Gemini uses when reading the video. Default
        None uses static sampling at the API default (1 fps).
    processing: ``static`` preserves the existing request path. ``agentic``
        uses Interactions for adaptive inspection; omit fps. Supported by
        Gemini 3.8/3.7/3.6 Flash and 3.5 Flash Lite. Returns processing_trace,
        agentic_processing_verified (matched call/result evidence), and full
        interaction metadata. Save the returned result to retain the trace.
        This is a synchronous, single-turn API call; no automatic fallback.
    model: Gemini model id. Default ``gemini-3.8-flash``.
    temperature: Optional sampling override; omitted by default.
    system_prompt: Override the default analyze-mode instruction.
    upload_timeout_s: Bounds the Files API upload+process wait.
    thinking_level: Gemini reasoning depth. Defaults to ``high``. Pass
        ``None`` to use the model/API default.
    max_output_tokens: Maximum output budget; thinking can consume this
        budget on thinking models. Defaults to 65,536. Pass ``None`` to
        use the model/API default.

Raises:
    RuntimeError: ``VIDEO_NOT_FOUND``, ``VIDEO_UPLOAD_FAILED``,
    ``VIDEO_PROCESSING_TIMEOUT``, ``VIDEO_PROCESSING_FAILED``,
    ``API_KEY_MISSING``, ``INVALID_INPUT``, or ``NO_RESPONSE``.

```json
{
  "properties": {
    "video_path": {
      "title": "Video Path",
      "type": "string"
    },
    "question": {
      "title": "Question",
      "type": "string"
    },
    "prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Prompt"
    },
    "intent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Intent"
    },
    "context": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Context"
    },
    "base_plate_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Base Plate Path"
    },
    "identity_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Identity Refs"
    },
    "style_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Style Refs"
    },
    "fps": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Fps"
    },
    "model": {
      "default": "gemini-3.8-flash",
      "title": "Model",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    },
    "upload_timeout_s": {
      "default": 300,
      "title": "Upload Timeout S",
      "type": "integer"
    },
    "thinking_level": {
      "anyOf": [
        {
          "enum": [
            "minimal",
            "low",
            "medium",
            "high"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": "high",
      "title": "Thinking Level"
    },
    "max_output_tokens": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": 65536,
      "title": "Max Output Tokens"
    },
    "processing": {
      "default": "static",
      "enum": [
        "static",
        "agentic"
      ],
      "title": "Processing",
      "type": "string"
    }
  },
  "required": [
    "video_path",
    "question"
  ],
  "title": "analyze_videoArguments",
  "type": "object"
}
```

### analyze_videos

Ask one grounded question across two to ten ordered videos.

Each video is uploaded through Gemini's Files API and inserted into one
multimodal request behind an explicit ``VIDEO N — label`` marker. This is
intended for edit comparisons, continuity checks, candidate ranking, and
other questions whose answer depends on seeing multiple clips together.
``analyze_video`` remains the lower-cost choice for one source.

Args:
    video_paths: Ordered list of two to ten distinct local video paths.
    question: Cross-video question for Gemini to answer.
    labels: Optional ordered labels. Defaults to each file's basename.
    prompt: Optional shared generation prompt or editorial instruction.
    intent: Optional shared creative brief.
    context: Optional per-call notes.
    base_plate_path: Optional reference image shared by all videos.
    identity_refs: Optional shared character/asset image references.
    style_refs: Optional shared style image references.
    fps: Sampling rate applied independently to every video. Default None
        uses static sampling at 1 fps; explicit range is (0, 24].
    processing: ``static`` preserves the existing request path. ``agentic``
        uses Interactions for adaptive inspection; omit fps. Supported by
        Gemini 3.8/3.7/3.6 Flash and 3.5 Flash Lite. Returns processing_trace,
        agentic_processing_verified (matched call/result evidence), and full
        interaction metadata. Save the returned result to retain the trace.
        This is a synchronous, single-turn API call; no automatic fallback.
    model: Gemini model id. Default ``gemini-3.8-flash``.
    temperature: Optional sampling override.
    system_prompt: Override the default analyze-mode instruction.
    upload_timeout_s: Per-file Files API processing timeout.
    thinking_level: Gemini reasoning depth. Defaults to ``high``. Pass
        ``None`` to use the model/API default.
    max_output_tokens: Maximum output budget; thinking can consume this
        budget on thinking models. Defaults to 65,536. Pass ``None`` to
        use the model/API default.

Returns:
    The resolved ordered paths and labels, model/fps, original question,
    free-form answer, and an echo of the evaluation context.

Raises:
    RuntimeError: ``INVALID_INPUT`` for list/label/fps errors,
    ``VIDEO_NOT_FOUND`` for any missing source, Files API errors from any
    upload, ``API_KEY_MISSING``, or ``NO_RESPONSE``.

```json
{
  "properties": {
    "video_paths": {
      "items": {
        "type": "string"
      },
      "title": "Video Paths",
      "type": "array"
    },
    "question": {
      "title": "Question",
      "type": "string"
    },
    "labels": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Labels"
    },
    "prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Prompt"
    },
    "intent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Intent"
    },
    "context": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Context"
    },
    "base_plate_path": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Base Plate Path"
    },
    "identity_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Identity Refs"
    },
    "style_refs": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Style Refs"
    },
    "fps": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Fps"
    },
    "model": {
      "default": "gemini-3.8-flash",
      "title": "Model",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    },
    "upload_timeout_s": {
      "default": 300,
      "title": "Upload Timeout S",
      "type": "integer"
    },
    "thinking_level": {
      "anyOf": [
        {
          "enum": [
            "minimal",
            "low",
            "medium",
            "high"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": "high",
      "title": "Thinking Level"
    },
    "max_output_tokens": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": 65536,
      "title": "Max Output Tokens"
    },
    "processing": {
      "default": "static",
      "enum": [
        "static",
        "agentic"
      ],
      "title": "Processing",
      "type": "string"
    }
  },
  "required": [
    "video_paths",
    "question"
  ],
  "title": "analyze_videosArguments",
  "type": "object"
}
```

### compare_images

Pick the best of N candidate images against a brief.

Returns a comparison narrative + the chosen candidate (resolved to its
1-indexed position and absolute path) + a 1-2 sentence reasoning string.

Default criteria are the 6 dimensions from ``generation-review-loop``;
override for non-Patrick workflows.

**Calibration caveat.** Cross-image grounding is harder than single-image
analysis — the model can correctly *pick* the better candidate while
misattributing which image holds which specific detail in the
``comparison`` text. The ``pick.best_index`` is the most reliable field;
the ``comparison`` and ``reasoning`` strings should be read as
directional, not authoritative on sub-image specifics. For
detail-sensitive cross-image reasoning, prefer ``describe_image`` on
each candidate separately and let the agent reason across the
descriptions.

Args:
    image_paths: List of 2+ candidate image paths. Order matters —
        ``best_index`` in the return is 1-indexed against this list.
    prompt: The shared gen prompt that produced these candidates (or
        empty if the candidates came from different prompts).
    intent: The creative brief — what the candidates are competing to
        satisfy.
    context: Per-call freeform notes — what specifically you want the
        comparison to weight (e.g., "I'm only worried about style_lock
        this round; ignore composition variance").
    criteria: Override the default 6-dim list.
    model: Gemini model id. Default ``gemini-3.8-flash``.
    temperature: Optional sampling override; omitted by default.
    system_prompt: Override the default comparison-mode instruction.

Raises:
    RuntimeError: ``INVALID_INPUT`` (fewer than 2 candidates),
    ``IMAGE_NOT_FOUND``, ``API_KEY_MISSING``, or other coded errors.
    ``SCHEMA_MISMATCH`` if Gemini returns a ``best_index`` outside the
    valid range [1, len(image_paths)].

```json
{
  "properties": {
    "image_paths": {
      "items": {
        "type": "string"
      },
      "title": "Image Paths",
      "type": "array"
    },
    "prompt": {
      "title": "Prompt",
      "type": "string"
    },
    "intent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Intent"
    },
    "context": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Context"
    },
    "criteria": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Criteria"
    },
    "model": {
      "default": "gemini-3.8-flash",
      "title": "Model",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    }
  },
  "required": [
    "image_paths",
    "prompt"
  ],
  "title": "compare_imagesArguments",
  "type": "object"
}
```

### extract_visual_tokens

Deconstruct an image into reusable prompt tokens by category.

Default categories are the env-coverage workflow's five (lighting,
atmosphere, palette, materials, spatial_grammar) per
``vault_gml/CLAUDE.md:164``. Output is short token phrases per category
(1–3 words each) — concrete visual vocabulary another genesis prompt
can paste in verbatim.

Defaults to Gemini 3.5 Flash because this is a descriptive lane where
the stable Flash model is enough and Pro image generation is not needed.

Args:
    image_path: Absolute path to the image to deconstruct.
    categories: Override the default 5-category list.
    intent: Optional brief — focuses the extraction (e.g., "I'm only
        interested in tokens relevant to teal-orange grade and
        anamorphic optics, skip color-palette specifics").
    model: Gemini model id. Default ``gemini-3.8-flash``.
    temperature: Optional sampling override; omitted by default.
    system_prompt: Override the default extraction instruction.

Raises:
    RuntimeError: ``IMAGE_NOT_FOUND``, ``API_KEY_MISSING``,
    ``SCHEMA_MISMATCH`` (Gemini returned categories that don't match
    the request), or other coded errors.

```json
{
  "properties": {
    "image_path": {
      "title": "Image Path",
      "type": "string"
    },
    "categories": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Categories"
    },
    "intent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Intent"
    },
    "model": {
      "default": "gemini-3.8-flash",
      "title": "Model",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Temperature"
    },
    "system_prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "System Prompt"
    }
  },
  "required": [
    "image_path"
  ],
  "title": "extract_visual_tokensArguments",
  "type": "object"
}
```

### extract_video_frames

Extract one PNG frame per timestamp via ffmpeg.

Frame-accurate seek (``-ss`` after ``-i``) — slower than fast seek but
lands on the exact target frame, which matters for cut-detection
workflows where frames are sampled at sub-second resolution.

Args:
    video_path: Absolute path to the video file.
    timestamps: List of seconds (``5.5``) or HH:MM:SS / MM:SS strings
        (``"00:00:05.500"``). Order is preserved in the returned list.
    out_dir: Default ``<video_dir>/frames/``.
    title_prefix: Default ``<video_basename_without_ext>``.

Returns:
    ``{video_path, frame_count, frames: [{timestamp_s, path, width,
    height}]}`` with one entry per input timestamp.

Raises:
    RuntimeError: ``VIDEO_NOT_FOUND``, ``FFMPEG_NOT_INSTALLED``,
    ``FFMPEG_FAILED`` (e.g., timestamp past the video end), or
    ``INVALID_INPUT`` (malformed timestamp).

```json
{
  "properties": {
    "video_path": {
      "title": "Video Path",
      "type": "string"
    },
    "timestamps": {
      "items": {
        "anyOf": [
          {
            "type": "number"
          },
          {
            "type": "integer"
          },
          {
            "type": "string"
          }
        ]
      },
      "title": "Timestamps",
      "type": "array"
    },
    "out_dir": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Out Dir"
    },
    "title_prefix": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Title Prefix"
    }
  },
  "required": [
    "video_path",
    "timestamps"
  ],
  "title": "extract_video_framesArguments",
  "type": "object"
}
```
