---
name: environment
description: DRAFT. Build a game environment: Flux backdrops and prop references, tileable materials, TRELLIS props, Infinigen filler, and assembly in Unity. Use for a set like the Hades gate. Only the Flux, upscale and one-prop TRELLIS steps have receipts.
---

> **STATUS: DRAFT, not yet run end to end.** Written from the repo's scripts, config and receipts; this stage has no complete run with a receipt.
>
> Unproven:
> - No environment has been taken through TRELLIS for every piece, placed in a scene and shown: the Hades gate has Flux references (all ten rows Dom-picked in `manifests/hades_gate.json`), two upscales and one TRELLIS prop only.
> - Infinigen is not in this repo. The only evidence is the laptop handoff note: minimal Infinigen runs on native Windows for rocks, plants and materials only, with an int32 patch, absolute paths and baked textures.
> - Unity assembly (placing props, URP lighting, stills) has no script, no receipt and no doc in this repo beyond 'Unity URP' in `docs/asset-standard.md` section 7.
> - The library row for an environment follows `docs/library-format.md`; the factory has never published one.
>
> Treat every step below as a plan to verify, and write the first end-to-end receipt before removing this banner.

# environment

## Evidence: the Hades gate (kit `kits/hades_gate_kit.json`, picks `manifests/hades_gate.json`)
- Kit: seed 42, ten rows: `gate_front_elevation` and `gate_arch_detail` (1536x1024), `causeway_floor_tile` and `ground_ash_tile` (tileable top-down materials, no subject line), props `wall_ruin_prop`, `pillar_fallen_prop`, `dead_tree_prop`, `brazier_prop`, `rock_cluster_prop` (1024x1024, plain neutral grey studio background), and `backdrop_valley_plate` (1664x928). The style line forbids orange or warm point lights and stray glowing spots.
- Flux receipts: `receipts/hades_gate-backdrop_valley_plate-f2ab77/flux-20261101T044119Z.json` (wall 137.7 s, peak GPU 21.5 GB, lowest free RAM 0.9 GB), `receipts/hades_gate-rock_cluster_prop-c8223b/flux-20261101T043841Z.json` (wall 89.5 s, free RAM 1.8 GB). Per `manifests/hades_gate.json`, the arch, floor and ash tiles were rendered before the warm-light prompt fix and carry small orange specks, and the brazier and earlier pieces have no receipt (old renderer). All ten rows were accepted by Dom in round 1.
- Upscales (Real-ESRGAN, 1 pass, colour matched): `receipts/hades_gate-gate_front_elevation/upscale-20261101T062005Z.json` (18.6 s) and `receipts/hades_gate-backdrop_valley_plate/upscale-20261101T062023Z.json` (17.9 s), both to a 6k PNG.
- One TRELLIS prop: `receipts/hades_gate-wall_ruin_prop/cutout-20261101T070801Z.json` (118.5 s) and `trellis-20261101T080829Z.json` (preset master at 2048 texture / 500k faces, attention 24 GB, 3627.6 s, lowest free RAM 0.0 GB). That is a draft-tier (benchmark profile) result.
- Failure on record (`LESSONS.md`, 2026-11-01): the black gate image and the black hall image were sent as done. Always look.

## Steps
1. **Reference set.** Make a kit for the set (same shape as `hades_gate_kit.json`): wide establishing shots, a backdrop plate, tileable materials, one row per prop. Render ONE row per process (`character-sheet` steps 4 and 6; max 1.5 MP; backdrop 1664x928 is 1.54 MP). Look at each image; a black or speckled image is re-rendered with a changed row, not a rerun of the same seed.
2. **Pick** per row with `tools/manifest.py ... --pick ... --by Dom`.
3. **Upscale** picks to 2048 px or more (`character-sheet` step 9); the plate and elevation went to 6k.
4. **Props through TRELLIS** (`trellis-part`): one cut-out image per prop, draft tier first. Tileable floor and ash tiles are materials, not meshes: they go straight to Unity as textures.
5. **Filler by Infinigen (unproven).** Rocks, plants and materials only, per the handoff note; bake textures; absolute paths; the int32 patch. Treat output as props to be inspected like any other mesh.
6. **Assemble in Unity URP (unproven).** Place props on the causeway and ground tiles, backdrop plate behind, cold ash-grey lighting per the style line; render stills from the game camera and a wide orbit. The set is rendered with lights and PBR, never a flat workbench view.
7. **Library row** (`publish-to-library`): group "Environment", `environment: true`, `rigged: false`, `envMedia.stills[]` as for `necropolis` (its 28,454,716-byte GLB is outside git, `tools/beast/MODELS.md`).

## Gates
Every picked image at least 1024 px (2048 after upscale), receipts `status: ok`, every image and the final set looked at, 9/10 for a final, 8/10 by Dom's eye to enter the library.

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
- Do keep the cold, desaturated palette the kit style line demands; check renders for stray orange specks (the first arch, floor and ash tiles have them).
- Do record picks as data in `manifests/<kit>.json`, never by renaming files.

Don't
- Don't build characters or creatures as ONE mesh: build them in parts (body + gear parts; for creatures the body, head and each limb or attachment as parts). A fused mesh hides geometry (a cloaked coat hides the legs), cannot be rigged and fails the bind gate (the fused vampire bound with stretch 29.8; the T-posed body part bound with 1.31). Applies to humanoids, beasts and anything animated.
- Don't run two GPU jobs at once (two hard freezes on 2026-10-31); the hook `gpu_lock_guard.py` and the MCP tools refuse a second one.
- Don't trust 'done' without a receipt file, and don't trust a receipt without looking at the output.
- Don't put literals (prompts, sizes, seeds, budgets, paths) in a run: add or edit a kit row, a preset or config (the hook `no_literals.py` blocks inline numbers on `generate.py` and `flux_kit.py`).
- Don't call a proof a final, and don't show the 2048/500k benchmark profile as one.
- Don't send an environment image as done without opening it (the black gate and hall).
- Don't treat a tileable floor image as a 3D asset.
- Don't run Infinigen, Unity or TRELLIS while another GPU job is running.
