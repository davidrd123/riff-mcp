# Seedance Test — Espresso Pour (macro)

Pipeline test of `mcp__gemini-prompts__generate_video` (Seedance 2.0 via Replicate),
async path (`start_video_job` → `get_video_job`). Mode: text_to_video (no references).

Brief: prove the MCP video path end-to-end and judge Seedance's handling of fluid
physics + a slow macro push-in. Success = clip generates, downloads to `out/`, and
the pour/crema reads as believable liquid motion.

---

## v01 — espresso pour, macro

- **Date:** 2026-05-20
- **Model:** bytedance/seedance-2.0
- **Mode:** text_to_video | **Duration:** 5s | **Res:** 720p | **AR:** 16:9 | **Audio:** off
- **Sent:** ✓ 2026-05-20 (job_id `943be26258f5`, prediction `e3vt958xg1rmw0cy94vvk9spsc`)
- **Prompt:**

> Macro food cinematography on a fast prime, shallow depth of field. Imperfections show —
> faint scratches on the glass, the uneven melt of the ice. Nothing is over-rendered.
> A slow push-in on a glass of cracked ice. A thread of dark espresso falls from above,
> threading through the cubes and blooming into amber clouds that curl downward. Pale gold
> crema froths at the surface, settling into a fine ring against the glass. Warm tungsten
> key from the left, deep shadows opposite. Steam ghosts upward. Patient, intimate — the
> quiet ritual before the first sip.

- **Result notes:** ✓ Succeeded in 206s. Output `seedance_test_espresso_00.mp4` (5.04s, 24fps,
  1280x720, no audio, 1.94MB). Seed 221220757. Async MCP path worked end-to-end
  (start → poll → auto-download → frame extraction). Three-beat arc landed: empty iced glass →
  espresso thread falling/threading through cubes → near-full amber glass, with a smooth slow
  push-in throughout. Fluid physics believable from text alone (no reference frames). Misses:
  pale-gold crema froth ring (surface reads as plain dark liquid) and visible steam (absent).
  Style slightly cleaner/more commercial than the "imperfections show" briefing requested.
- **Next iteration:** Pass at ~80%; surfaced to human. If pursued: add crema/foam emphasis
  ("amber crema foams pale gold at the meniscus, holding a ring of bubbles") and a steam cue,
  and reinforce the imperfection briefing. Reuse seed 221220757 to hold composition while nudging.
