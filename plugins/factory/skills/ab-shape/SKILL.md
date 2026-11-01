---
name: ab-shape
description: "A/B harness Pixal3D vs TRELLIS on one cut-out png and one seed (tool ab_shape): each half under the GPU lock, then bind gate, QC, matched lit turntables and a blind scorecard. Use when deciding whether Pixal3D replaces TRELLIS for a body or head cell."
---

> **STATUS: DRAFT, not yet run end to end.** The harness, hooks and offline tests exist; no engine half, bind, QC or Blender turntable has run through it and no scorecard exists yet. The decision below is a rule, not a result.

# A/B: Pixal3D vs TRELLIS (pipeline labels PIXAL3D and TRELLIS, never mixed)

## The rule (OpenTrident Dev)
Same cut-out PNG, same seed, fixed wall time of about 10 minutes each (reported against `ab.wall_budget_s`), TRELLIS first, one GPU job at a time under the lock. Score blind on lit PBR turntables, four criteria 0 to 10: face, hair, colour match to the sheet, bake faults (pinholes, speckle, seams); plus the bind-gate numbers. Pixal3D wins only if it beats TRELLIS by 1 point or more on two criteria AND its bind gate is no worse.

## How to run
1. `ab_shape(png, asset_id, seed, engines="trellis")` runs the TRELLIS half (preset tokens98k_fresh, `--max-tokens 98304`) under the lock; `engines="pixal3d"` runs the Pixal3D half (preset standard). `engines="both"` runs TRELLIS then Pixal3D and collects. Different sessions may run the halves back to back with the same png and the same seed.
2. `ab_shape(png, asset_id, seed, collect=true)` runs no GPU half: it gathers both finished halves from receipts (refusing if the png or seed differ), binds both meshes with the same donor, runs QC, renders matched turntables (same camera path, three-point rig, world, resolution and samples, Cycles on CPU), a contact sheet and `scorecard.json` + `scorecard.md`.
3. A GPU half REFUSES while the sheet queue is alive: a live stage process, a live GPU lock, or free RAM under the start limit.
4. Fill `blind_scores` (A and B, 0 to 10) without opening `blind_key`, then `python tools/run_ab.py --decide <scorecard.json>` prints WIN or NO-WIN.

## Bind needs a body-only T-pose mesh
`--bind auto` (default) binds only a mesh that looks like a T-pose body (bounding box check), says why it skipped otherwise, and a skipped bind means the rule cannot be met (NO-WIN). `--bind yes` refuses a mesh that is not T-pose-like. Feed the body cell.

## Do / Don't
- Do LOOK at every image and output yourself before calling a stage done: open the turntables and crops; a receipt and a scorecard only prove the tool ran.
- Do treat a 9/10 or higher result by Dom's eye as the bar for a final; the A/B only picks the engine, it does not make a final.
- Do treat the 98k-token TRELLIS preset and the standard Pixal3D preset (1024) as a BENCHMARK profile: never a final, never shown as one.
- Do give both halves the same input of 2048 px or more (upscaled, cut out, subject filling the frame) and the same seed.
- Do present work as lit PBR renders under a three-point light rig (key, fill, rim), never flat workbench renders.
- Do keep anything below 8/10 by Dom's eye out of the library: nothing enters the library below 8/10.
- Do version every character v1, v2, v3, each with a receipt, a contact sheet and Dom's pick per part.
- Do score blind: labels are A and B, the key stays unopened until all eight scores are filled.
- Don't build characters or creatures as ONE mesh: build them in parts (body, head, hands, gear) and run each through the pipeline.
- Don't run the A/B while the sheet queue or any other GPU job is alive, and don't run two GPU jobs at once.
- Don't compare a Pixal3D mesh with a TRELLIS one by eye alone, mix the PIXAL3D and TRELLIS labels, or call a run done without the receipt and a look at the output.
