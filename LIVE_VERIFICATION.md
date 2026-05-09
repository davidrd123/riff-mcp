# Live Verification Notes

What we learned by running the tools end-to-end during the v1 build. Everything here is grounded in actual API calls against real artifacts; nothing is hypothetical. The code itself, design doc, and git log capture *what we built*; this captures *how it behaves in practice* and *where the rough edges are*.

Updated 2026-05-08. v1 closed out (Step 8); v2 #1 (live Seedance fire) added the same day; v2 #5 (local async API) implemented + mock-verified + live-smoked; v2 #2 (`.mcp.example.json` + `riff-mcp-doctor`) wired the surface for agent use; project `.mcp.json` wired in this repo and exercised end-to-end against vault-grounded inputs from the sibling GML 2026 Closing Film vault — surfaced the **input-sensitivity finding** that `intent` text is the load-bearing variable for `score_image` (same image / same prompt, ~65pt swing on `creative_brief_fidelity` based purely on intent composition). Also live-fired the full mutation→describe→score riff loop end-to-end through MCP for the first time. **What is NOT yet validated**: whether the scores Gemini returns track production judgment. That requires a human-in-the-loop calibration pass (see v2 outstanding #13). See "Vault-grounded input-sensitivity characterization" section.

---

## Targets used for verification

| Artifact | Path | Used by |
|----------|------|---------|
| Scene-04 v13 PNG (apocalyptic supermarket, pixel art) | `out/2026-05-07/gemini-3-pro-image-preview/01_sc04-v13-wide-establishing-test_3ec0da36/sc04-v13-wide-establishing-test_01.png` | `generate_image` (Step 2 fire), `describe_image` + `score_image` (Step 5), `extract_visual_tokens` (Step 8) |
| Apple PNG (1:1, default temp) | `out/2026-05-08/gemini-3-pro-image-preview/01_step-2-real-fire-test_ed6bb7e4/step-2-real-fire-test_01.png` | `generate_image` (Step 2 default-path verification) |
| Bridge video (.mp4, 3MB, 720p, ~5s, known camera cut at ~4.1s) | `~/Downloads/bridge_attempt_wimageref_badcameracut.mp4` (not in repo) | `describe_video` + `score_video` at fps=12 (Step 6 + FPS feature), `extract_video_frames` (Step 7), `compare_images` on extracted frames (Step 8) |
| Bridge cut-bracket frames | `~/Downloads/frames/bridge_attempt_wimageref_badcameracut_t{03.500,04.000,04.083,04.167,04.500}.png` | `compare_images` (Step 8) |

| Grothendieck seed-cluster PNG (pen-and-ink, 1403×1121, ~5:4) | `local/Images/Grothendieck/ChatGPT Image May 8, 2026, 06_15_10 PM.png` | `generate_video` (Seedance live fire, v2 #1) |
| Seedance fire output (.mp4, 1112×834, 5.04s, 24fps) | `out/2026-05-08/bytedanceseedance-20/01_seedance-fire-grothendieck-seed-cluster_5b943597/seedance-fire-grothendieck-seed-cluster_00.mp4` | `describe_video` + `extract_video_frames` (closing the riff loop on the fire) |

---

## Per-tool behavioral findings

### `generate_image` (Step 2)

- **Default path matches CLI byte-for-byte** when fired with same params. Step 0's `build_resolved_image_job()` extraction did its job — single source of truth for image-mode resolution.
- **Job artifacts persist correctly** — `job.json` written alongside the PNG, content matches the return dict.
- **Output dir layout**: `<out_root>/<today>/<model_slug>/<seq>_<title>_<hash>/<title>_NN.png` is honored by both CLI and MCP paths.
- Latency: ~30s for a single 1024×1024 1:1 default-temp image on `gemini-3-pro-image-preview`.

### `generate_video` (Step 3) — **live-fired 2026-05-08**

- **Live fire succeeded** on the Grothendieck seed-cluster PNG. 5s, 720p, 4:3, `first_last_frames` mode. End-to-end **153.9s** (cold start; predict_time 153.3s, download 0.35s). Output: 3.1MB h.264 mp4, 1112×834, 24fps, 5.04s. Sidecar `job.json` and `media_info` populated correctly via ffprobe.
- **Latent `_get_url` bug surfaced** — file saved as `.bin` instead of `.mp4`. Root cause: `_get_url` only handled callable `url()` (DMPOST31's older Replicate SDK shape), but modern `FileOutput` exposes `.url` as a string property. We silently fell through to `None`, then `_ext_from_url(None) → ".bin"`. Fixed: now handles both callable and string-attribute forms (additional dict-form handling pre-staged by separate work). The `.bin` content was a valid mp4 throughout — failure was *graceful*, which is exactly why it didn't surface in dry-run / import / mock testing.
- **Style-preservation result is the surprise**. Pen-and-ink line work held *perfectly* across all 121 frames, white background stayed clean, no boiling, no drift to photorealism. This is the opposite of what I expected — pixel art and pen-and-ink were both predicted to be hard for Seedance, but pen-and-ink turned out to be the easier case (perhaps because illustrated white-bg has unambiguous "this is a drawing" signal).
- **Motion interpretation**: Seedance treated the figures as *part of the still drawing* and only animated what the prompt explicitly named as moving (the seed pulse, the drifting puffs). Children + background figure: zero motion. Worth understanding as a *bias to leverage*, not a bug — when style preservation matters, framing the prompt as "the [styled] image, with the [specific element] doing [specific action]" gets you locked-style + targeted motion.
- **Color drift** — the prompted "warm yellow-white glow" drifted toward orange/red over the 5s. Subtle, not catastrophic. Color stability is a separate axis from style stability and worth tracking in v2 calibration.
- **Anatomy artifacts at t=0 are source-frame issues, not Seedance issues** — the elongated boy and faceless background man are present in the ChatGPT-generated source. Seedance faithfully propagated them. Calibration needs to distinguish "Seedance drift" from "Seedance faithful reproduction of source flaws."
- **Soft warning false positive**: the `[Image1] not in prompt` warning fires for `first_last_frames` mode where the image's role is implicit. v2 polish — `check_prompt_references` should be mode-aware and skip first/last-frame entries.
- **15-case dry_run matrix from v1 still holds** — mode discrimination, hard validation, soft warnings, edge values all verified pre-fire.
- **Thread-watchdog and coded-prefix preservation** untriggered by this fire (no timeout, no error path) — those mock-verified guarantees still apply.

### `describe_image` (Step 5)

- **Eight-category structured output works first try** on Pro — no schema-rejection retries, no JSON parsing issues. `response_schema` + `response.parsed` flow is reliable.
- **Sharper than the hand-written eval on the same image.** On v13, Gemini's `freeform_observations` flagged the **prompt-vs-brief structural conflict** that my register-drift framing missed: *"the prompt explicitly requested a 'blue/cyan palette', which the model followed perfectly, but this directly contradicts the brief's requirement for a 'high-key sunny commercial-Americana' look."* That's the actionable diagnosis (rewrite the prompt) vs. the symptomatic one (image is too blue).
- Caught a **perspective-mismatch artifact** I missed entirely: *"parking lot lines and building converge in standard one-point perspective, while the cars and carts are drawn from a fixed isometric angle"*.
- Latency: ~25–30s on Pro for a 1024-side image.

### `score_image` (Step 5)

- **Same six dim names match my hand-eval; calibration is sharper.** On v13, Gemini scored `creative_brief_fidelity = 30` where I'd written ~70. Gemini's diagnosis caught the structural conflict; my soft 70 was over-generous. The brief explicitly said "not dystopian-blue"; the image was dystopian-blue.
- **`style_lock` calibration anchor working as designed**. On the bridge video, Gemini scored `style_lock = 80` — recognizing that the 2.5D-collage aesthetic survived the cut even though the cut wrecked everything else. A miscalibrated model would have dragged style_lock down with the rest.
- **`decision_hint`** consistently lined up with the dimension scores in our tests: `direction_gate` for v13, `reroll` for the bridge cut.
- **`SCHEMA_MISMATCH` post-parse validation** verified via 5 mock cases (commit `c0bb88d`): catches missing dims, bogus dims, duplicates; passes correct responses and custom-criteria responses.
- **`intent` is the load-bearing variable** (2026-05-08 vault-grounded run) — re-running on the v13 PNG with vault-grounded scene-04-V3-direction `intent` (vs the earlier improvised intent that referenced the overarching candy-coated brief) flipped `creative_brief_fidelity` from **30** to **95** and `decision_hint` from `direction_gate` to `accept`. Same image, same prompt, same dimensions. The score is a function of (image, intent), not image alone. See "Vault-grounded input-sensitivity characterization" section below for the full table and production implication.

### `describe_video` (Step 6) at fps=12

- **The headline result.** On the bridge video with a known cut at ~4.1s, Gemini reported the cut at **`00:04.083 to 00:04.167`** — exactly 1/12s apart. That's direct evidence the FPS plumbing works: at default 1 fps, the closest precision Gemini could give is "around 4 seconds." With our `VideoMetadata(fps=12, start_offset='0s')`, it reaches frame-pair precision (~83ms).
- **Cross-time observation** caught the SALON BARBER → COFFEE SHOP signage swap as evidence for the cut. Specific, actionable.
- **Twelve-category schema (8 image + 4 video-specific) holds** — every category populated with grounded prose, no schema slippage.
- **`audio_quality` correctly returns "No audio is observed"** for silent video. Validates our default `generate_audio=False` choice in `generate_video`: agents calling describe_video on Seedance output won't get a hallucinated audio track read.
- Latency: ~45s including upload + Files-API processing + analysis. Upload phase dominates for a 3MB file.

### `score_video` (Step 6) at fps=12

- **Same calibration signal as `score_image`**. On the bridge cut: `scene_hierarchy=30` ("camera grammar fundamentally broken by an unmotivated jump cut"), `creative_brief_fidelity=30` ("fails to provide a usable transition"), `style_lock=80` (style survived the cut), `decision_hint=reroll`. Sharp and actionable.
- Schema-validation invariants from `score_image` carry over.

### `compare_images` (Step 8) — **caveat documented in docstring**

- **Picks correctly but can misattribute details across images.** On bridge cut-bracket frames (t=3.500 vs t=4.500), `pick.best_index=1` is the right answer (pre-cut frame is the better base). But the `comparison` text claimed Image 1 (pre-cut) had legible "3 Star Coffee Shop" signage when the actual visual + the prior `describe_video` on the same clip both put that signage in Image 2 (post-cut). The model fumbled which image carries which detail.
- **Implication for the A/B plan**: when sub-image detail accuracy matters, prefer `describe_image` per candidate + Claude reasons across the descriptions, vs. `compare_images` doing it in one shot. The pick decision is reliable; the supporting prose is directional.
- **`SCHEMA_MISMATCH` raises on out-of-range `best_index`** — server-side guard catches Gemini hallucinating an index outside [1, N].

### `extract_visual_tokens` (Step 8) — **strongest single result**

- On the v13 PNG with default 5 categories on **Flash** (`gemini-3-flash-preview`):
  - lighting: `["flat global illumination", "neon sign glow", "no cast shadows", "uniform brightness"]`
  - atmosphere: `["retro arcade vibe", "consumerist satire", "busy digital landscape", "CRT scanline effect"]`
  - palette: `["electric blue", "cyan dominant", "magenta accents", "neon yellow", "16-bit palette"]`
  - materials: `["chunky pixel art", "matte plastic", "dithered gradients", "aliased edges"]`
  - spatial_grammar: `["isometric 3/4 view", "grid-based layout", "fisheye lens distortion", "repeating geometric patterns"]`
- **"Consumerist satire" in `atmosphere` is the surprise** — Flash, given a generic category, surfaced a token that captures the *intent* of the subject matter (abandoned shopping carts), not just the visual surface. Token extraction can carry brief-level signal, not just style-grammar.
- **"Fisheye lens distortion" in `spatial_grammar`** caught the CRT vignette I noticed visually as a structural property.
- **Flash is sufficient** for this tool. Pro would be overkill.
- Latency: ~10–15s on Flash.
- **`intent` is the steering wheel** (2026-05-08 vault-grounded run) — no-intent runs surface "interesting observations" tokens (`satirical consumerism`, `limited digital palette`); vault-grounded `intent` shifts output to brief-aligned vocabulary (`searing cyan highlights`, `chunky 16-bit pixels, ordered dithering, hard black outlines`, `frictionless digital world` — last one pulled from the vault's `scene-key-beats.md` Takeaway 2 narrative description). The `intent` arg shifts the model from "what's interesting about this image" to "what brief-aligned vocabulary is in this image." See "Vault-grounded input-sensitivity characterization" section.

### `start_video_job` / `get_video_job` / `cancel_video_job` (v2 #5, async API)

- **Two-tier file system** — durable async state under `<out_root>/jobs/<job_id>/{request.json, status.json}` plus the actual generated media under the existing `<out_root>/<today>/<model>/01_<title>_<hash>_<job_id>/` layout. The `_<job_id>` suffix on the media dir prevents collisions when the same prompt fires multiple times.
- **`request.json` is immutable, `status.json` is mutable** — captured intent vs. running state. If `status.json` gets corrupted, `request.json` survives. Two-file split is journaled state without over-engineering.
- **Half-built record protection** — `start_video_job` writes local state *only after* `create_seedance_prediction` returns successfully. If creation fails (e.g., REPLICATE_API_TOKEN missing), no orphan local job record. Tested via `test_start_video_job_create_failure_leaves_no_local_job_record`.
- **Idempotent finalization** — `get_video_job` re-finalizes if local status is "succeeded" but the result/outputs haven't been generated yet. Important for crash recovery: if the process dies between Replicate-success and disk-write, the next poll completes the work.
- **Cancel-races-with-complete handled implicitly** — `_merge_prediction_status` checks the provider status regardless of which entry point called it, so a cancel that returns "succeeded" still downloads outputs.
- **Live smoke (v2 #5)** — first attempt with `duration=1` rejected by live endpoint despite docs saying 1..15; this surfaced the **second case of schema-vs-live drift** in this surface (after `_get_url` in v2 #1) and was fixed to `4..15` or `-1`. Second smoke succeeded: 4s @ 480p @ 1:1 text-only, job `de2f644b0f97`, terminal "succeeded" on poll 10. Output: `out/async-smoke/2026-05-08/bytedanceseedance-20/01_async-smoke-4s_0ed8e9ad_de2f644b0f97/async-smoke-4s_00.mp4` (114KB, 640×640, 4.04s, 24fps).
- **Mock test coverage**: 17 tests covering 3 modes, 5 validation paths, coded-prefix preservation, sync sidecar, async start/poll/cancel, JOB_NOT_FOUND, poll=False, create-failure no-record. All green.

### `riff-mcp-doctor` (v2 #2)

- **Console script in a new `riff_mcp_doctor` package** — separate from existing CLIs to keep their argparse-positional shape intact. Adds ~70 LOC plus tests.
- **Categories**: env vars, Python packages (by import name, not pip name), binaries on PATH (ffmpeg/ffprobe), and optional `--network` (cheap Gemini `models.list()` + Replicate `models.list()` calls — verifies tokens are valid, not just present).
- **Skip-not-fail for network when keys are missing** — a doctor that says "fix env first, then re-run with --network" is more useful than one that fails twice. One root cause per surface, no cascade.
- **`--json` flag** for scriptability; exits 1 on any required failure.
- **Live verification on this machine**: all checks pass — 50 Gemini models reachable, Replicate auth ok, both binaries on PATH at `/opt/homebrew/bin/`.
- **Tests**: 13 unit-level tests (env present/missing, pkg present/missing, binary present/missing, no-network, network-skip-when-no-keys, formatting, exit codes).

### `extract_video_frames` (Step 7)

- **Frame-accurate seek working** via `-ss` after `-i`. On the bridge video at timestamps `[3.5, 4.0, 4.083, 4.167, 4.5]`, all 5 PNGs land in `<video_dir>/frames/` with 3-decimal timestamp precision in the filenames.
- **Visual confirmation matches Gemini's `describe_video` analysis** of the same clip: at t=3.5 (pre-cut) the right side has dark buildings and a yellow circular sign; at t=4.5 (post-cut) the right side is completely different (lighter buildings, "FOOT 33" / partial coffee-shop text). The cut Gemini localized between t=4.083 and t=4.167 is visually obvious between the bracketing frames.
- ffmpeg 8.0.1 on Mac via Homebrew. No portability surprises.

---

## Vault-grounded input-sensitivity characterization (2026-05-08)

> **Honest framing.** This section documents how the tools *respond* to vault-grounded inputs — not whether their outputs are *right*. What follows is *infrastructure validated* and *input-output behavior characterized*; what follows is **not** ground-truthed against production judgment. The original section header read "calibration"; that overclaimed. True calibration requires comparing tool scores to scores assigned by a production reviewer (Patrick, or equivalent) blind to the tool's output. Until that pass happens (see v2 outstanding #13), every "Gemini scored X" finding here is internally coherent but circular — Gemini measured against itself, with the rubric Gemini applies determined by the `intent` text the caller composes. Treat the findings as *characterization of the surface area*, not *proof of fitness*.

First end-to-end exercise of the wired MCP servers, run against real production artifacts from the sibling GML 2026 Closing Film vault (`~/Projects/Lora/ComfyPromptByAPI-patrick/WorkingSpace/patrick/vault_gml/`). The vault's evaluation rubric — six dimensions, named: `prompt_fidelity`, `preservation_fidelity`, `style_lock`, `scene_hierarchy`, `story_service`, `creative_brief_fidelity` — is the same rubric `score_image`/`score_video` default to. So the *rubric is shared by design* with the production team. Whether the tool's *scores within that rubric* match the production team's *scores within that rubric* is the open question; the rubric itself is not.

**Methodology.** Pulled brief text and prompt verbatim from the vault → drove `score_image`, `describe_image`, `extract_visual_tokens`, and `generate_image` (mutation) on the v13 PNG and one mutation iteration → compared input/output behavior across runs that varied only in their `intent` and `base_plate_path` arguments. No human ground-truth scoring has been collected.

### Headline: `intent` is the load-bearing variable for `score_image`

Same image, same prompt, same six dimensions — only the `intent` text changed:

| Dimension | Earlier eval (improvised intent referencing overarching brief) | 2026-05-08 (vault-grounded scene-04 V3 intent) |
|-----------|---------------------------------------------------------------|------------------------------------------------|
| prompt_fidelity | (not surfaced) | **95** |
| style_lock | 80 | **95** |
| scene_hierarchy | (not surfaced) | **90** |
| story_service | (not surfaced) | **90** |
| **creative_brief_fidelity** | **30** | **95** |
| decision_hint | `direction_gate` | **`accept`** |

Both diagnoses are internally coherent — they're scoring against different briefs. Earlier: *"prompt requested 'blue/cyan palette' which directly contradicts the brief's requirement for a 'high-key sunny commercial-Americana' look."* 2026-05-08: *"successfully translates the structural requirements of the Scene 04 style anchor into the updated blue/cyan sunny daytime palette required by the current direction."* The image hasn't changed; the score is a function of `(image, intent)`, not `image` alone.

**Production implication — multi-layered briefs.** The vault has multi-layered briefs: overarching aesthetic in `look-overarching.md` (V2 — candy-coated luxury, pinks/mints/golds) plus a scene-specific override in `look-scene-04-search-shopping.md` (V3 — blue/cyan-dominant, sunny Bay Area daytime, pixel art). The current single-string `intent` parameter has no structure to express *"primary criterion: scene direction; secondary check: overarching, flag conflicts."* Whichever layer the calling agent puts in `intent` is the brief Gemini scores against — invisibly. Today's vault-grounded `intent` (scene-04 V3 specific) hides the overarching-vs-scene conflict that the earlier improvised intent surfaced. Both are "right" given their respective inputs; neither is sufficient for production. Intent composition is now a first-class production decision, not a free-text afterthought. See v2 outstanding item #11.

### `extract_visual_tokens` — `intent` shifts what the model surfaces

Side-by-side on the v13 PNG, default 5 categories on Flash, identical except `intent`:

| Category | No-intent | Vault-grounded `intent` (scene-04 V3 vocabulary) |
|----------|-----------|--------------------------------------------------|
| lighting | flat digital light, uniform glow, no cast shadows, screen-emitted light | **searing cyan highlights**, **deep flat shadows**, hard-edged sunlight, uniform global illumination, no soft falloff |
| atmosphere | retro digital, pixel art aesthetic, **satirical consumerism**, surreal commercial, arcade game vibe | **hyper-saturated commercial**, **16-bit arcade**, busy retail chaos, **frictionless digital world**, sunny daytime |
| palette | monochromatic blue base, electric cyan, neon pink accents, vibrant primary pops, limited digital palette | **electric blue dominant**, **cyan midtones**, magenta accents, neon pink highlights, saturated primary yellow |
| materials | pixelated textures, aliased edges, flat color blocks, digital dithered gradients | **chunky 16-bit pixels**, **ordered dithering**, **hard black outlines**, **CRT scan lines**, aliased edges |
| spatial_grammar | high-angle wide shot, symmetrical composition, central focal point, isometric perspective, repetitive grid layout | **isometric 3/4 view**, **grid-aligned sprites**, **flat horizontal plane**, repeating car assets, centered focal machine |

**Bold = matches vault vocabulary verbatim or near-verbatim** (e.g. `"searing cyan highlights"` and `"deep flat shadows"` are taken straight from the V3 system instruction; `"frictionless digital world"` comes from the Takeaway 2 narrative description in `scene-key-beats.md`). The intent run is dramatically more aligned with the production team's codified style language — without intent, Flash gives you "what's interesting about this image"; with intent, it gives you "brief-aligned vocabulary you can paste into the next genesis prompt."

This is the strongest single argument for the `intent` parameter design within the *characterization* frame — without `intent`, Flash gives you "what's interesting about this image"; with `intent`, it gives you brief-aligned vocabulary. Whether that brief-aligned vocabulary is *production-usable* is a Patrick judgment call, not provable from this run alone.

### Iteration mock — full riff loop end-to-end (mutation path)

Exercised the canonical generate→describe→score loop end-to-end through MCP for the first time. Mutation: pass v13 PNG as `image:` ref, prompt for the addition of the AI Sean pixel-art figures the v13 prompt deliberately omitted (per the 2026-04-16 vault note: figures were to be added separately, not in the wide establishing shot).

**Infrastructure exercised for the first time through MCP:**
- `generate_image` live (not dry_run), with `image:` ref → 22s, ~$0.05, 1376×768 output
- `describe_image` with `base_plate_path` set → Gemini returned an explicit "what changed vs base plate" read
- `score_image` with `base_plate_path` set → `preservation_fidelity` returned a non-null score (98) for the first time through MCP; previously null on v13 since v13 was a genesis with no base plate

**Tool-output behavior observed (NOT validated against ground truth):**
- Mutation result: 8 figures added (vs ~6 requested in the prompt). Gemini counted them — my own visual quick-read of the thumbnail said "fewer than 6"; Gemini's count is more reliable for this kind of pixel-figure inventory than my eyeballs at thumbnail scale.
- `describe_image` surfaced specific real artifacts: hands of cart-pushers don't perfectly align with cart handles; figure at machine slightly overlaps the conveyor belt. Whether these are production-acceptable artifacts or revision-triggers is a Patrick call.
- `score_image` returned: prompt_fidelity=95, preservation_fidelity=98, style_lock=95, scene_hierarchy=90, story_service=95, creative_brief_fidelity=85, decision_hint=`accept`. The lone partial-credit was on `creative_brief_fidelity` with the diagnosis *"sparse compared to brief's call for an 'army' in such a massive parking lot"* — Gemini independently caught a tension between *prompt language* ("approximately six", "small swarm") and *brief language* ("army of identical figures") that the caller had encoded without noticing. Whether 85 is the right number for that tension, or whether 60 / 95 / something else would better track production judgment, is unverified.

**What this round did *not* exercise.** No `compare_images` call (the documented v1 caveat about cross-image detail attribution still applies; the recommended two-pass workflow per v2 outstanding #4 remains untested). No fresh `generate_image` genesis through MCP (only the mutation was fired). No video tools.

### Test-condition notes

**Caller-side string hygiene.** Multiple tool calls in this session had stray closing tags (`</intent>`, `</system_prompt>`) and even a malformed `<parameter name="title">` block leaked into string parameter values — a Claude scaffolding bug, not a server one (wrong namespace on closing tags). The MCP boundary doesn't lint string params; the malformed text passed through verbatim to Gemini, which was robust to it (treated as noise, scored coherently). One real downstream cost was observed: in the iteration-mock generation, the malformed `<parameter name="title">` block meant the `title` arg was never applied; the job dir was auto-named from the prompt instead of carrying the intended `sc04-v13mut-add-ai-sean-figures` searchable identifier. **Production implication:** when an agent drives MCP tools that feed strings to LLMs, half the caller-side bugs fail-quietly (model copes) and half fail-loudly (param drops). The MCP server can't and shouldn't catch this — strings are arbitrary user data — but production callers need their own input validation pass.

**Asymmetric failure modes.** Caller-side bugs against AI models tend to fail-quietly (model just copes); caller-side bugs against APIs (like dropping a `title` arg) fail-loudly. The MCP layer here is pure-API, so half the bugs got dropped and half passed through. Worth knowing for production: the model-side leg of the call gives almost no validation signal back to the caller about input quality.

---

## Cross-tool calibration observations

**Where Gemini was sharper than my hand-written eval:**
- `describe_image` on v13: caught the prompt-vs-brief structural conflict and a perspective-mismatch artifact.
- `score_image` on v13: harsher and more honest on `creative_brief_fidelity` (30 vs my 70).
- `describe_video` on bridge cut: localized cut to ~83ms precision; cross-referenced signage as evidence.

**Where Gemini was weaker than the hand-written eval:**
- `compare_images` cross-image detail attribution. The pick is right; the supporting reasoning can be wrong about which image holds which detail.

**Where calibration was equivalent:**
- `decision_hint` matched my hand-eval on every case tested (`direction_gate` for v13, `reroll` for the bridge cut).

**Pattern:** Single-image / single-video tools are reliably sharper than my baseline. Cross-image is the weakest surface. This validates the design doc's A/B comparison plan as the right experiment to actually run, with the prior that single-modality > cross-modality.

---

## Schema-vs-live drift (live fire as a permanent fixture)

Live fire has now caught **two for two** schema-vs-live mismatches that mock testing couldn't:

| Round | Drift caught |
|-------|--------------|
| v2 #1 fire | `replicate.helpers.FileOutput.url` is a string property in the modern SDK; vendored `_get_url` only handled the older `url()` callable form, so outputs silently wrote as `.bin` (file content was valid mp4 throughout — graceful failure, invisible to dry-run/import/mock). |
| v2 #5 first smoke | Live Seedance endpoint rejects `duration < 4` despite the published schema/docs saying 1..15. Local validation tightened to `4..15` or `-1`. |

**Pattern**: published schemas underspecify; production endpoints over-constrain. They diverge whenever the implementation tightens for stability/cost reasons (4s minimum is probably an artifact-reduction heuristic). Vendored adapter code rots whenever the upstream SDK shape changes (callable→string property in this case).

**Implication**: every external-service adapter benefits from a **schema-conformance smoke test** as a permanent fixture — a small `pytest -m live` lane that runs the cheapest possible live call on every adapter and asserts the response shape matches our types. Doesn't need to be CI; just runnable on demand. Cost: ~$0.10–0.30 per provider per check. Without it, schema drift accumulates silently between SDK upgrades.

---

## Codex review pattern

Five rounds of codex review across the project found small-but-real bugs that static reading wouldn't catch:

| Round | What was caught |
|-------|-----------------|
| Step 5 review | `score_image` accepted arbitrary criterion names; stale `__init__.py` doc → commit `c0bb88d` |
| Step 6 review (1) | Replicate sidecar handle leak (file handles in returned JSON), 4 design points in `replicate_min` / `seedance.py` → commit `b8b9f8f` |
| Step 6 review (2) | Coded error prefixes lost at MCP boundary (`REPLICATE_TIMEOUT` → `REPLICATE_ERROR: REPLICATE_TIMEOUT:`) → commit `f928bc1` |
| Step 6 review (3) | FAILED upload not cleaned up; `fps=True` accepted as 1 (bool subclasses int); `context_block` said "image" reused for video → commit `21cdce0` |
| Implicit Step 8 | `compare_images` cross-image grounding caveat documented in docstring (no commit needed beyond the original Step 8 commit) |
| Live Seedance fire (v2 #1) | `_get_url` only handled callable `url()` — modern `FileOutput.url` is a string property, so outputs were silently saved as `.bin` instead of `.mp4` (file content remained valid mp4 throughout — graceful failure mode that none of the static / mock / import tests would catch) |

**Lesson for v2**: every adapter wrapping an external service deserves at least one "what happens when the service misbehaves" review pass *and* at least one "what happens with real bytes flowing through real shapes" live pass. Static review surfaces structural issues; mock testing surfaces failure-mode handling; live fire surfaces shape-evolution drift between vendored code and current SDK shapes. The `.bin` bug is the canonical example — invisible to all three of (a) reading the code, (b) mocking the bad path, (c) running dry-runs — only surfaces when real Replicate `FileOutput` flows in.

---

## Cost / latency rough notes

| Operation | Latency | Cost (rough) |
|-----------|---------|--------------|
| `generate_image` on Pro, 1024-side, 1 output | ~30s | ~$0.05 |
| `describe_image` / `score_image` on Pro | ~25–30s | ~$0.10 |
| `describe_video` / `score_video` on Pro at fps=12, 5s clip 720p | ~45s | ~$0.15–0.20 |
| `compare_images` on Pro, 2 candidates | ~30s | ~$0.10 |
| `extract_visual_tokens` on Flash | ~10–15s | ~$0.01 |
| `extract_video_frames` (ffmpeg, 5 timestamps) | <1s | $0 |
| `generate_video` on Seedance (live, cold start, 5s @720p, 4:3) | **153.9s actual** (predict 153.3s, download 0.35s) | ~$0.30 actual |
| `start_video_job` + 10× `get_video_job` polling to terminal (live, 4s @480p @1:1) | similar Seedance latency, polling is local file reads | ~$0.10 actual |
| `riff-mcp-doctor` (no network) | <1s | $0 |
| `riff-mcp-doctor --network` | ~2–3s (Gemini models.list + Replicate models.list) | $0 (free GETs) |

Pro is roughly 5× more expensive than Flash on token cost; Flash is sufficient for `extract_visual_tokens`. Worth piloting Flash on `describe_image` for a few rounds to see if it holds the eight-category quality bar — would cut analysis cost ~5×.

---

## Outstanding items for v2

(These extend the design doc's "Future / v2" list with concrete items surfaced during verification.)

1. ~~**Live Seedance fire**~~ ✅ done 2026-05-08 — surfaced the `_get_url` shape-drift bug, validated cold-start latency (~154s), characterized Seedance's pen-and-ink preservation behavior. See `generate_video` per-tool section above.
1a. **Suppress `[Image1] not in prompt` warning for `first_last_frames` mode** — false positive when image role is implicit. `seedance.check_prompt_references` should accept a `mode` param and skip FIRST_FRAME / LAST_FRAME entries.
2. ~~**`.mcp.json` wiring**~~ ✅ done 2026-05-08 — `.mcp.example.json` at repo root with `REPO_PATH` placeholder and env scaffolding for both servers. Companion `riff-mcp-doctor` console script verifies env vars, Python packages, ffmpeg/ffprobe on PATH; `--network` adds Gemini + Replicate auth pings.
3. **Flash trial for `describe_image`** — Pro is the safe default; Flash *may* be sufficient for routine description, with ~5× cost reduction. Worth ~5 paired calls to see if the eight categories hold quality.
4. **`compare_images` mitigation** — current docstring caveat is the v1 fix. v2 ideas: (a) two-pass workflow that calls `describe_image` per candidate first, then asks Pro to reason across the descriptions (not the images); (b) explicit "verify which image you're describing" step in the system prompt.
5. ~~**Async/polling for `generate_video`**~~ ✅ local async plumbing is now mock-verified and live-smoked via `start_video_job(...)`, `get_video_job(job_id)`, and `cancel_video_job(job_id)`. It writes durable `request.json` / `status.json` files under `<out_root>/jobs/`, uses non-blocking Replicate prediction creation, and downloads outputs on terminal success.
   - First async smoke attempt (2026-05-08) used `duration=1`, which the live Seedance endpoint rejected despite the earlier schema note: "Duration must be between 4 and 15 seconds, or -1 for intelligent duration." Local validation now matches the live endpoint (`4..15` or `-1`).
   - Second async smoke attempt (2026-05-08) succeeded with `duration=4`, `resolution=480p`, `aspect_ratio=1:1`, text-only prompt. Job `de2f644b0f97`, prediction `hv932xrpv9rmr0cy19mafjakrw`, terminal status `succeeded` on poll 10. Output downloaded to `out/async-smoke/2026-05-08/bytedanceseedance-20/01_async-smoke-4s_0ed8e9ad_de2f644b0f97/async-smoke-4s_00.mp4` (114,096 bytes, 640×640, 4.04s, 24fps, no audio). This live-smoked `predictions.create(wait=False)`, `predictions.get`, local status merging, output download, `job.json` write, and ffprobe enrichment.
6. **Eval calibration loop** — for any tool where Gemini's score/judgment is consumed downstream (`score_image`, `score_video`, `compare_images`), periodically have a human re-score 10 outputs and check for drift. The `decision_hint` field is the most likely to drift.
7. **Standalone repo cleanup** — `~/Projects/LLM/media-analysis-mcp/` (the abandoned scaffold from before consolidation) is still on disk. David's call when to remove.
8. **Schema-conformance live lane** — small `pytest -m live` fixture that runs the cheapest possible call against each adapter (Seedance 4s @480p, Gemini one-image describe, etc.) and asserts response shapes match our types. ~$0.30–0.40/full run; runnable on demand. Justified by the two schema-drift catches in v2 #1 and v2 #5 — without it, drift accumulates silently between SDK upgrades.
9. **Pytest as a dev dep** — currently `uv run pytest` requires `--with pytest` since pytest isn't in `[dependency-groups] dev`. Small papercut, easy fix when convenient.
10. **`compare_videos`** (codex follow-on) — same shape as `compare_images` but for clips. Natural after v2 #4 (`compare_images` mitigation) lands so both share the two-pass pattern.
11. **Intent composition for multi-layered briefs** (surfaced 2026-05-08 vault-grounded run) — the single-string `intent` parameter on `score_image` / `score_video` / `extract_visual_tokens` is the load-bearing variable for the score (see "Vault-grounded calibration" section). For projects with multi-layered briefs (overarching + scene-specific override), today's API forces the calling agent to choose one layer to put in `intent`, hiding any conflict with the other. A structured intent shape — e.g., `intent: {primary: "...", overarching: "...", flag_conflicts: bool}` — would let the caller express *"score against this direction but tell me if it conflicts with the broader brief."* Lower-cost alternative: a separate `overarching_intent` arg that adds a second creative-brief check dimension to the response. Either approach makes the conflict visible in the tool output instead of silenced by the caller's choice.
12. **MCP wire smoke as permanent fixture** — the inline JSON-RPC smoke run on 2026-05-08 (spawn each server, `initialize` + `tools/list`, assert all 12 tools present with valid schemas) caught zero issues but is precisely the kind of check that catches "tool silently fails to register due to bad type annotation" — invisible to running tests, only surfaces at agent call time. Worth lifting into a small `tests/test_mcp_smoke.py` so it runs in the suite, not just from a heredoc.
13. **Human-in-the-loop calibration with Patrick** (the only thing in this list that turns input-sensitivity findings into actual validation) — pick ~5 production outputs Patrick has already reviewed, run `score_image` on each with vault-grounded `intent` (scene-specific direction), ask him to spot-rate each on the same six dimensions blind to the tool's output, then compare per-dimension. Compute: numerical agreement (within ±10? ±20?), rank-order agreement (does the tool agree with Patrick on which output is best?), and `decision_hint` agreement (does `accept`/`direction_gate`/`reroll` match Patrick's verdict?). Cost: ~30 min of Patrick's time + ~$0.50 in API calls. Without this pass, every "Gemini scored X" finding above is internally coherent but circular. The `decision_hint` field is the most likely to drift (already flagged in #6); this is the concrete experiment that would show whether it has. Outputs: a small `calibration-results-YYYY-MM-DD.md` showing where the tool agrees/disagrees with production judgment and what the systematic biases are.

---

## File reference index

- This doc: `/Users/daviddickinson/Projects/Lora/riff-mcp/LIVE_VERIFICATION.md`
- Design doc: `/Users/daviddickinson/Projects/Lora/riff-mcp/MCP_DESIGN.md`
- Bridge video: `~/Downloads/bridge_attempt_wimageref_badcameracut.mp4`
- Extracted bridge frames: `~/Downloads/frames/`
- v13 PNG: `out/2026-05-07/gemini-3-pro-image-preview/01_sc04-v13-wide-establishing-test_3ec0da36/sc04-v13-wide-establishing-test_01.png`
- Apple PNG: `out/2026-05-08/gemini-3-pro-image-preview/01_step-2-real-fire-test_ed6bb7e4/step-2-real-fire-test_01.png`
- Grothendieck source PNG: `local/Images/Grothendieck/ChatGPT Image May 8, 2026, 06_15_10 PM.png`
- Seedance fire output: `out/2026-05-08/bytedanceseedance-20/01_seedance-fire-grothendieck-seed-cluster_5b943597/seedance-fire-grothendieck-seed-cluster_00.mp4`
- Seedance fire frames: same dir, `frames/seedance-fire-grothendieck-seed-cluster_00_t{00.000,02.500,04.900}.png`
- Async smoke output: `out/async-smoke/2026-05-08/bytedanceseedance-20/01_async-smoke-4s_0ed8e9ad_de2f644b0f97/async-smoke-4s_00.mp4`
- Async job records: `out/async-smoke/jobs/de2f644b0f97/{request.json, status.json}`
- MCP wiring template: `.mcp.example.json` (repo root)
- Project MCP config (gitignored, local): `.mcp.json` (repo root)
- Doctor source: `src/riff_mcp_doctor/doctor.py`
- Doctor tests: `tests/test_doctor.py`
- GML 2026 Closing Film vault (sibling project, source of vault-grounded calibration inputs): `~/Projects/Lora/ComfyPromptByAPI-patrick/WorkingSpace/patrick/vault_gml/`
- v13 prompt entry (vault): `vault_gml/visual/scene-04/prompts-scene-04-search-shopping.md` (look for `### v13`)
- v13 brief sources (vault): `vault_gml/visual/look-overarching.md` (overarching V2) + `vault_gml/visual/scene-04/look-scene-04-search-shopping.md` (scene 04 V3 — what the v13 prompt actually followed) + `vault_gml/story/scene-key-beats.md` (Takeaway 2 narrative subtext)

## Recent commits (this session)

- `1fffa69` — Add `.mcp.example.json` + `riff-mcp-doctor` (v2 #2)
- `cc38254` — Validate async video jobs against live Seedance (v2 #5 live smoke + duration-min fix)
- `6744318` — Implement local async video jobs (v2 #5 main implementation, mock-verified)
- `c486b47` — Capture v2 #1 live Seedance fire findings (this doc)
- `db27c51` — Capture v1 live-verification findings before context compact

For future-you after compact: read `MCP_DESIGN.md` (architecture) + this doc (behavior, current state, v2 progress) + `git log --oneline -20`. Test suite: `uv run --with pytest pytest tests/ -v` → 35 passing. Doctor sanity: `uv run riff-mcp-doctor --network`.
