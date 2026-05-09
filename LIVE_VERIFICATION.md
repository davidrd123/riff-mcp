# Live Verification Notes

What we learned by running the tools end-to-end during the v1 build. Everything here is grounded in actual API calls against real artifacts; nothing is hypothetical. The code itself, design doc, and git log capture *what we built*; this captures *how it behaves in practice* and *where the rough edges are*.

Updated 2026-05-08, immediately after Step 8 closed out v1; extended same day with the live Seedance fire (v2 item #1).

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

### `extract_video_frames` (Step 7)

- **Frame-accurate seek working** via `-ss` after `-i`. On the bridge video at timestamps `[3.5, 4.0, 4.083, 4.167, 4.5]`, all 5 PNGs land in `<video_dir>/frames/` with 3-decimal timestamp precision in the filenames.
- **Visual confirmation matches Gemini's `describe_video` analysis** of the same clip: at t=3.5 (pre-cut) the right side has dark buildings and a yellow circular sign; at t=4.5 (post-cut) the right side is completely different (lighter buildings, "FOOT 33" / partial coffee-shop text). The cut Gemini localized between t=4.083 and t=4.167 is visually obvious between the bracketing frames.
- ffmpeg 8.0.1 on Mac via Homebrew. No portability surprises.

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

Pro is roughly 5× more expensive than Flash on token cost; Flash is sufficient for `extract_visual_tokens`. Worth piloting Flash on `describe_image` for a few rounds to see if it holds the eight-category quality bar — would cut analysis cost ~5×.

---

## Outstanding items for v2

(These extend the design doc's "Future / v2" list with concrete items surfaced during verification.)

1. ~~**Live Seedance fire**~~ ✅ done 2026-05-08 — surfaced the `_get_url` shape-drift bug, validated cold-start latency (~154s), characterized Seedance's pen-and-ink preservation behavior. See `generate_video` per-tool section above.
1a. **Suppress `[Image1] not in prompt` warning for `first_last_frames` mode** — false positive when image role is implicit. `seedance.check_prompt_references` should accept a `mode` param and skip FIRST_FRAME / LAST_FRAME entries.
2. **`.mcp.json` wiring** — both servers (`gemini-prompts-mcp`, `media-analysis-mcp`) only invokable via direct Python today. Wiring into Claude Code's MCP config is the next operational step before agents can use them natively.
3. **Flash trial for `describe_image`** — Pro is the safe default; Flash *may* be sufficient for routine description, with ~5× cost reduction. Worth ~5 paired calls to see if the eight categories hold quality.
4. **`compare_images` mitigation** — current docstring caveat is the v1 fix. v2 ideas: (a) two-pass workflow that calls `describe_image` per candidate first, then asks Pro to reason across the descriptions (not the images); (b) explicit "verify which image you're describing" step in the system prompt.
5. ~~**Async/polling for `generate_video`**~~ ✅ local async plumbing is now mock-verified and live-smoked via `start_video_job(...)`, `get_video_job(job_id)`, and `cancel_video_job(job_id)`. It writes durable `request.json` / `status.json` files under `<out_root>/jobs/`, uses non-blocking Replicate prediction creation, and downloads outputs on terminal success.
   - First async smoke attempt (2026-05-08) used `duration=1`, which the live Seedance endpoint rejected despite the earlier schema note: "Duration must be between 4 and 15 seconds, or -1 for intelligent duration." Local validation now matches the live endpoint (`4..15` or `-1`).
   - Second async smoke attempt (2026-05-08) succeeded with `duration=4`, `resolution=480p`, `aspect_ratio=1:1`, text-only prompt. Job `de2f644b0f97`, prediction `hv932xrpv9rmr0cy19mafjakrw`, terminal status `succeeded` on poll 10. Output downloaded to `out/async-smoke/2026-05-08/bytedanceseedance-20/01_async-smoke-4s_0ed8e9ad_de2f644b0f97/async-smoke-4s_00.mp4` (114,096 bytes, 640×640, 4.04s, 24fps, no audio). This live-smoked `predictions.create(wait=False)`, `predictions.get`, local status merging, output download, `job.json` write, and ffprobe enrichment.
6. **Eval calibration loop** — for any tool where Gemini's score/judgment is consumed downstream (`score_image`, `score_video`, `compare_images`), periodically have a human re-score 10 outputs and check for drift. The `decision_hint` field is the most likely to drift.
7. **Standalone repo cleanup** — `~/Projects/LLM/media-analysis-mcp/` (the abandoned scaffold from before consolidation) is still on disk. David's call when to remove.

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
