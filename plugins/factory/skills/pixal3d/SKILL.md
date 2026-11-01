---
name: pixal3d
description: "Image to textured GLB with Pixal3D (TencentARC, MIT) through the pixal3d_run tool: a second shape pipeline next to TRELLIS, compared with it only by the A/B on the same input and seed. Use when a cut-out picked image should be run through Pixal3D."
---

> **STATUS: DRAFT, not yet run end to end.** Weights are downloaded and checksummed and the tool, hooks and tests exist, but the model has not been run in the factory and no receipt exists yet. The A/B against TRELLIS has not run.
>
> Unproven: that Pixal3D runs on this ROCm stack at all, its wall time and peak memory, and whether it beats TRELLIS on the vampire body cell.

# Pixal3D (pipeline label PIXAL3D)

## What it is
TencentARC Pixal3D, MIT (code, weights and NOTICE re-read 2026-11-01). Single image in, PBR textured GLB out. It builds on the TRELLIS.2 stack the factory already runs, so it uses the same native ROCm wheels in its own venv `Pixal3D-312` next to `Trellis-3-312`.

## How to run
1. The input is the factory's cut-out PNG (short side at least 1024 px, subject at least 60% of the frame height; the input gate applies).
2. Call the MCP tool `pixal3d_run(asset_id, image, preset)`. Presets are in `config/config.json` under `pixal3d.presets`: draft and standard (1024, low VRAM), master (1536, low VRAM).
3. It refuses while another GPU job holds the lock, while free RAM is under the start limit, or on a small input. It writes a `pixal3d` receipt; the asset's pipeline label is PIXAL3D.
4. Look at the GLB and a lit turntable before saying it is done.

## What the entry script changes (in memory only)
- DINOv3 and MoGe-2 are read from local folders (no network, no token).
- The background remover in the weights' `pipeline.json` is rewritten once from briaai/RMBG-2.0 (licence bria-rmbg-2.0, non-commercial) to ZhengPeng7/BiRefNet (MIT); the original is kept as `pipeline.json.orig`.
- Attention is sdpa. flash-attn and natten are CUDA-only; upstream does not import natten.

## The A/B (decision rule from OpenTrident Dev)
Run it with the `ab_shape` tool (skill ab-shape). Same cut-out PNG, same seed, about 10 minutes each, one GPU job at a time under the lock, TRELLIS first. Score blind on lit turntables, four criteria 0 to 10: face, hair, colour match to the sheet, bake faults. Run the bind gate on both. Pixal3D wins only if it beats TRELLIS by 1 point or more on two criteria and the bind gate is no worse.

## Do / Don't
- Do LOOK at every image and output yourself before calling a stage done: open the GLB, the turntable and the stills, check for black, cropped or speckled results; a receipt only proves the tool ran.
- Do treat a 9/10 or higher result by Dom's eye as the bar for a final; anything below is a proof and is labelled a proof.
- Do treat the draft and standard presets (1024) as a BENCHMARK profile: never a final, never shown as one.
- Do give the tool an input of 2048 px or more (upscaled, cut out, subject filling the frame); the gate refuses under 1024 px.
- Do present work as lit PBR renders under a three-point light rig (key, fill, rim), never flat workbench renders.
- Do keep anything below 8/10 by Dom's eye out of the library: nothing enters the library below 8/10.
- Do version every character v1, v2, v3, each with a receipt, a contact sheet and Dom's pick per part.
- Do label the asset PIXAL3D; a Pixal3D mesh is compared with a TRELLIS output only through the A/B above.
- Don't build characters or creatures as ONE mesh: build them in parts (body, head, hands, gear) and run each through the pipeline.
- Don't use RMBG-2.0 (non-commercial) or run two GPU jobs at once.
- Don't call a run done without the receipt and a look at the output.
