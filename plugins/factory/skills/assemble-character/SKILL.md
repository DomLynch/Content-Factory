---
name: assemble-character
description: DRAFT. Bind each gear part (coat, cloak, boots) to the bound body's skeleton and assemble body plus gear under one 106-joint armature into one GLB. Use after bind-humanoid, when a character has gear parts.
---

> **STATUS: DRAFT, not yet run end to end.** Written from the repo's scripts, config and receipts; this stage has no complete run with a receipt.
>
> Unproven:
> - `tools/run_gear.py` and `tools/assemble_character.py` have no receipt for any asset: no `gear` or `assemble` stage receipt exists under `receipts/`.
> - No gear part has been through TRELLIS and the gear gate end to end; `vampire-dracula-coat` has only cutout and trellis receipts.
> - The shipped gear parameters (fit mode `none`, helper bones, smoothing) have never been judged by eye on a posed result.
>
> Treat every step below as a plan to verify, and write the first end-to-end receipt before removing this banner.

# assemble-character

Wrappers: `tools/run_gear.py <asset_id> <part> <gear_mesh.glb> <bound_body.glb>` then `tools/assemble_character.py <asset_id>`. Both are CPU only (Blender `--background`, no render). Settings: `config/config.json -> gear` and `-> assemble`. `tools/run_character.py` runs both inside the full pipeline (stages `gear`, `assemble`).

## Steps
1. **Inputs.** A bound body from `bind-humanoid` (`<outputs_dir>/3d/<asset_id>/bound.glb`, object `Rig` plus `Body`) and one TRELLIS mesh per gear part from `trellis-part` (a coat, a cloak, boots, each from its own picture, never fused with the body). Asset ids for parts follow `run_character.py`: `<character_id>-<part>`.
2. **Bind each gear part.** `python tools\run_gear.py <asset_id> <part> <gear_mesh.glb> <bound_body.glb>`. It transfers weights from the nearest body surface point (`bind/bind_gear.py`), smooths (`smooth_passes` 2, factor 0.5), caps influences at 4, then measures stretch over the idle, walk and attack clips (6 frames each) against the Mixamo clips in `mixamo_clips_dir`. Output `<outputs_dir>/3d/<asset_id>/gear_<part>.glb`, one receipt (stage `gear`).
3. **Gear gate** (`gear.gate`, looser than the body): zero-weight vertices 0, at most 4 influences, weight sums within 1e-3, p99 stretch at most 3.0, edges of 5 mm or more only. Gear hangs rather than deforms.
4. **Optional secondary motion** (`gear.helpers`): cloak vertices below 0.8 of body height move up to 0.8 of their weight to the nearest helper bone containing `cape`; coat below 0.55 moves up to 0.6 to a `skirt` bone. Fit mode is `none`: scale and placement of the incoming mesh are trusted (`z_frac` mode exists, per-part ranges in `gear.fit.parts`).
5. **Assemble.** `python tools\assemble_character.py <asset_id>` imports `bound.glb` and every `gear_*.glb` beside it into one scene, merges them under the single armature, expects `assemble.expected_bones` = 106, and exports `<asset_id>.glb` (skins on, animations and morphs off, no Draco so meshopt can be applied afterwards by gltf-transform; unknown exporter keywords for this Blender are skipped and recorded).
6. **Look.** Open the assembled GLB in Blender or render it (`stills-and-turntable`): check no gear is hidden or missing, nothing penetrates the body in idle, walk and attack, and the legs are visible under a coat.

## Gates
- Gear: step 3. Assembled file: 106 bones, one armature, every gear part present, size under the 50 MB git limit or recorded by path and sha256 (`publish-to-library`).
- A part that fails its gate is re-made as a better picture, not forced through.

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
- Do bind and judge each gear part separately before assembling.
- Do run `stills_dressed` (stills of the assembled file) and look at them before calling the character assembled.

Don't
- Don't build characters or creatures as ONE mesh: build them in parts (body + gear parts; for creatures the body, head and each limb or attachment as parts). A fused mesh hides geometry (a cloaked coat hides the legs), cannot be rigged and fails the bind gate (the fused vampire bound with stretch 29.8; the T-posed body part bound with 1.31). Applies to humanoids, beasts and anything animated.
- Don't run two GPU jobs at once (two hard freezes on 2026-10-31); the hook `gpu_lock_guard.py` and the MCP tools refuse a second one.
- Don't trust 'done' without a receipt file, and don't trust a receipt without looking at the output.
- Don't put literals (prompts, sizes, seeds, budgets, paths) in a run: add or edit a kit row, a preset or config (the hook `no_literals.py` blocks inline numbers on `generate.py` and `flux_kit.py`).
- Don't call a proof a final, and don't show the 2048/500k benchmark profile as one.
- Don't assemble before the body bind passed (`bind-humanoid`).
- Don't lower the gear gate to make a part pass; change the part or the fit.
- Don't bind gear to a different skeleton than the body's.
