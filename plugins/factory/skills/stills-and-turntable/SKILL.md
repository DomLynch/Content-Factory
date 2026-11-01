---
name: stills-and-turntable
description: DRAFT for the lit PBR presentation and the turntable. Render head, idle, walk and attack stills of a bound or assembled GLB with Blender on the CPU (proof-tier lighting today) and plan the three-point PBR stills and turntable. Use before showing Dom a character.
---

> **STATUS: DRAFT, not yet run end to end.** Written from the repo's scripts, config and receipts; this stage has no complete run with a receipt.
>
> Unproven:
> - `tools/run_stills.py` has receipts for single bound bodies only (`vampire-dracula-body-tpose`, `vampire-dracula-front`); no assembled, dressed character has been rendered end to end with a receipt.
> - Its lighting is ONE sun plus a flat world colour (`config/config.json -> stills`: `sun_energy` 3.0, `world_color`), not a three-point rig: its output is a proof, not a presentation.
> - No turntable script exists in the repo; a roll still is not rendered (config clips are idle, walk, attack only).
> - PBR presentation (normal map, roughness/metallic) in the stills has not been checked.
>
> Treat every step below as a plan to verify, and write the first end-to-end receipt before removing this banner.

# stills-and-turntable

Wrapper: `tools/run_stills.py <asset_id> <bound_or_assembled.glb>` (MCP tool `stills(asset_id, glb)`). Settings: `config/config.json -> stills` plus `config.local.json` (`blender_exe`, `mixamo_clips_dir`). CPU only: Cycles on CPU, 24 samples, no denoise, 1024x1024.

## Evidence
- `receipts/vampire-dracula-body-tpose/stills-20261101T093339Z.json`: ok, wall 15.2 s, lowest free RAM 7.8 GB, body margin 1.2. Earlier runs at margin 1.15 and the head cut off led to commit `6f31cca` (framing from the skinned meshes only, sensor height).
- `receipts/vampire-dracula-front/stills-20261101T074936Z.json`: ok, wall 47.7 s, margin 1.15.

## Steps (proof stills, proven)
1. Input: a bound GLB (objects `Rig` and skinned meshes). The helper bone-shape meshes the glTF importer adds are ignored for framing.
2. Clips are looked up by id as `<mixamo_clips_dir>/<id>.glb`: idle `113410901-idle`, walk `124540901-walk`, attack `9034009-zombie-neck-bite` (`stills.clips`); frame `mid`. A missing clip stops the run before Blender starts; nothing is downloaded.
3. Run it. Outputs in `<outputs_dir>/3d/<asset_id>/stills/`: `head.png` (head crop at `head_height_frac` 0.18 around the head bone), `idle.png`, `walk.png`, `attack.png`, and `thumb.png` (256 px, from idle). Camera: 50 mm lens, 36 mm sensor, body margin 1.2, camera height at 0.5 of the body.
4. **Look at every still.** Check head not cut off, body fully in frame, not black, textures not speckled, the right clip frame. Regenerate with changed config (never a literal) if the framing is wrong.

## Steps (presentation, not yet built)
5. **Three-point light rig.** Add key, fill and rim lights (and a neutral HDRI or world) in Blender; keep base colour, normal map and roughness/metallic wired; render Cycles stills of the same four views. Every light value goes into a `stills` config block, not into the script call. The single-sun stills of step 3 are proof only.
6. **Turntable.** A 360 degree orbit of the posed character under the same rig, to video or an image sequence. No script exists; write it as a config-driven Blender stage with its own receipt. (OpenTrident's Unity contest turntables are listed in `tools/beast/MODELS.md` for reference only.)
7. **Roll still** only if the library row shows it (`docs/library-format.md`: `roll.png` optional); the roll clip id is `120430902-sprinting-forward-roll` and is not in `stills.clips` today.

## Gates
Four stills plus thumb, receipt `status: ok`, every image looked at and judged by Dom's eye. Definition of done for a character (`docs/asset-standard.md` section 6): three clean engine stills (head crop, Idle, Walk, Attack at near camera) reviewed by Dom.

## Do / Don't

Do
- Do LOOK at every image and output yourself before calling a stage done: open the picture or render, check it is not black, cropped, mis-framed, speckled or mismatched to the reference, and check the numbers. A receipt only proves the tool ran. (Learned 2026-11-01: black images sent as done; stills with the head cut off; a speckled texture.)
- Do hold a quality bar of 9/10 or higher, comparable to the best of Unreal Engine 5.8 in 2026, for every FINAL asset, with NO compromise on quality, visuals or effects (9/10 AAA). Score it honestly against the reference sheet. Anything below 9 is a proof, not a final: label it as a proof, say what is wrong, and do not ship it as finished.
- Do treat the 2048 texture / 500k face profile (`master_reduced` and its attention variants) as a BENCHMARK profile: it is never shown to Dom as a final. Finals are the hero re-bake of the saved latents (2M faces, 8K textures, normal map, full PBR; preset `hero_rebake`, skill `hero-rebake`).
- Do upscale every picked sheet cell to 2048 px or more with Real-ESRGAN (colour-matched, `tools/upscale/upscale_match.py`) BEFORE it goes into TRELLIS, as the Demon 2 inputs were 2048 clean renders. The RAM rule limits Flux generation, not the file TRELLIS reads.
- Do present work as lit PBR renders: base colour, normal map and roughness/metallic under a three-point light rig (key, fill, rim) in Blender, or straight from Unity URP. Flat workbench-style renders are labelled proof and never shown as results.
- Do keep anything below 8/10 by Dom's eye out of the library: nothing enters the library below 8/10, and a proof is labelled a proof in its `label` and `source`.
- Do version every character v1, v2, v3 (like Demon 2 v1 to v18), each with a receipt, a contact sheet and Dom's pick per part.
- Do take every value from a kit row, a preset or config (`config/config.json`), never from the command line.
- Do check that the receipt exists, parses as JSON and says `status: ok`; one GPU job at a time.
- Do re-check framing after any change to the body margin, lens or head fraction; the head was cut off before `6f31cca`.
- Do label single-sun stills as proof when sending them.

Don't
- Don't build characters or creatures as ONE mesh: build them in parts (body + gear parts; for creatures the body, head and each limb or attachment as parts). A fused mesh hides geometry (a cloaked coat hides the legs), cannot be rigged and fails the bind gate (the fused vampire bound with stretch 29.8; the T-posed body part bound with 1.31). Applies to humanoids, beasts and anything animated.
- Don't run two GPU jobs at once (two hard freezes on 2026-10-31); the hook `gpu_lock_guard.py` and the MCP tools refuse a second one.
- Don't trust 'done' without a receipt file, and don't trust a receipt without looking at the output.
- Don't put literals (prompts, sizes, seeds, budgets, paths) in a run: add or edit a kit row, a preset or config (the hook `no_literals.py` blocks inline numbers on `generate.py` and `flux_kit.py`).
- Don't call a proof a final, and don't show the 2048/500k benchmark profile as one.
- Don't present single-sun or workbench stills as the result.
- Don't judge texture quality from a 256 px thumb.
