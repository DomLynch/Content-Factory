---
name: hero-rebake
description: DRAFT. Re-decode and re-bake saved TRELLIS latents at the hero profile (8192 texture, 2M faces, normal map) with run_3d.py --from-latents, the path to a final. Use when Dom has picked a draft-tier part and wants the final bake.
---

> **STATUS: DRAFT, not yet run end to end.** Written from the repo's scripts, config and receipts; this stage has no complete run with a receipt.
>
> Unproven:
> - No hero re-bake has finished. It was tried twice on the `vampire-dracula-body-tpose` latents (`receipts/vampire-dracula-body-tpose/trellis-20261101T094928Z.json`: fail, wall 474.5 s, lowest free RAM 1.4 GB; `trellis-20261101T095247Z.json`: fail, wall 45.5 s, lowest free RAM 2.8 GB). `Documents\crash_monitor.csv` shows `GUARD_ABORT` for both (free RAM 1.3 GB at the 2 GB floor, then 2.2 GB at the 3 GB floor). The decode worked (2,031,898 vertices, 4,373,978 triangles in 11 s); the kills came during the 8192x8192 bake while simplifying to about 2,000,000 faces.
> - A hero bake of the Dracula front latents (`chain7.ps1`) was parked by the owner's queue ruling and has no receipt.
> - The known texture fault (speckle from the trilinear bake sampling and gamma path) is open; a re-bake may carry it. LESSONS.md calls fixing it the top fork engineering item.
> - The RAM the 8192 / 2M-face bake needs on this box is unmeasured: it went under the guard floors both times.
>
> Treat every step below as a plan to verify, and write the first end-to-end receipt before removing this banner.

# hero-rebake

Why: finals are the hero bake from the saved latents (2M faces, 8K textures, normal map, full PBR). The 2048 / 500k draft profile is a benchmark profile and is never shown as a final (`LESSONS.md`, 2026-11-01, lever 1). Wrapper: `tools/run_3d.py <asset_id> <image> --preset hero_rebake --from-latents <mesh.latents.pt> --out-name <stem>` (preset `hero_rebake`: base `master`, texture 8192, bake faces 2,000,000, no attention extras; `run_3d.py` adds `--normal-map` for `--from-latents`).

## Evidence: the first attempts (both killed by the RAM guard)
Log `Documents\flux_env\trellis_vampire-dracula-body-tpose_mesh_hero.txt`: preset `master`, pipeline `1024_cascade`, texture 8192, bake faces 2,000,000, normal map on; latents hold 4,249 tokens at 1024 and 985 voxels; "Loading pipeline (decode only): 2 checkpoints, ~1.9 GB", loaded in 26 s; decode 11 s; then "Baking PBR textures via cuda (8192x8192)" and "Simplifying mesh: 4,373,978 -> ~2,000,000 faces", where the guard killed the job (receipts above; GPU dedicated 18.1 GB at the first kill). So on this box a 2M-face / 8K bake does not yet fit under the guard floors with the other work running. Next try only with nothing else running and free RAM well above the floor, and record the peak in the receipt; do not lower the guard floor to get it through.

## What it does and does not do
`generate.py --from-latents` skips sampling and decodes in a fresh process that loads only the two decoders (docstring of `Trellis-3-312/generate.py`). So the SHAPE resolution stays what was sampled (the draft run used the 1024 cascade); only the bake (texture size, face count, normal map) changes. A real `--preset hero` (1536 cascade shape) needs a fresh sampling run and a config preset that does not exist yet; see `trellis-part`, tier table.

## Steps
1. **Precondition.** The part passed the draft tier, Dom picked it and has seen its stills (`docs/asset-standard.md` section 2), and its `mesh.latents.pt` exists. Latents are written beside the mesh after each sampling stage (`<output>.latents.pt`) and are gitignored. On disk now: `kits_out/3d/vampire-dracula-front`, `-body-tpose`, `-body`, `-coat`, `-attn24`, `-hands`, `robot-tokens98k` and `hades_gate-wall_ruin_prop`.
2. **State.** `factory_status`: GPU lock free, free RAM at least 6 GB. Nothing else on the GPU.
3. **Run.** `python tools\run_3d.py <asset_id> <picked_image.png> --preset hero_rebake --from-latents <outputs_dir>\3d\<asset_id>\mesh.latents.pt --out-name mesh_hero` under the GPU guard (`-Match generate`). `<asset_id>` must have its existing `cutout.png`; no cut-out is redone. Output `mesh_hero.glb` beside `mesh.glb`, one `trellis` receipt with `params.from_latents` set.
4. **Look.** Open the GLB and render it lit (`stills-and-turntable` step 5). Compare texture, face detail and speckle against the draft; `tools/latent_diff.py a.latents.pt b.latents.pt` compares two runs' features. Score honestly against 9/10.
5. **Texture repair** if speckled: `tools/run_textures.py <asset_id> <mesh.glb>` (receipts on `vampire-dracula-front`: pinhole texels 75,210 before, 354 still dark after, 9.3 s; and `vampire-dracula-body-tpose`: 73,053 before, 21 still dark after, 5.5 s; writes `textures/` with base colour, metal/roughness, a detail normal and a compare image; the GLB is never modified). A repair that needs a fork fix belongs in `LESSONS.md`.
6. **Size.** A 2M-face, 8K GLB is far over the 50 MB git limit; it goes to the library by path and sha256 (`publish-to-library`).

## Gates
Receipt `status: ok` with `from_latents`; GLB and normal map present; looked at lit; 9/10 for a final. If it fails or crashes, keep the draft and write the failure to `LESSONS.md`.

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
- Do keep every `.latents.pt` for a part Dom might want as a final.
- Do give a hero bake its own `--out-name` so the draft `mesh.glb` is never overwritten.

Don't
- Don't build characters or creatures as ONE mesh: build them in parts (body + gear parts; for creatures the body, head and each limb or attachment as parts). A fused mesh hides geometry (a cloaked coat hides the legs), cannot be rigged and fails the bind gate (the fused vampire bound with stretch 29.8; the T-posed body part bound with 1.31). Applies to humanoids, beasts and anything animated.
- Don't run two GPU jobs at once (two hard freezes on 2026-10-31); the hook `gpu_lock_guard.py` and the MCP tools refuse a second one.
- Don't trust 'done' without a receipt file, and don't trust a receipt without looking at the output.
- Don't put literals (prompts, sizes, seeds, budgets, paths) in a run: add or edit a kit row, a preset or config (the hook `no_literals.py` blocks inline numbers on `generate.py` and `flux_kit.py`).
- Don't call a proof a final, and don't show the 2048/500k benchmark profile as one.
- Don't run a hero bake on an input Dom has not picked.
- Don't call the re-bake a 1536-shape hero run; it keeps the sampled shape.
- Don't start it with less than 6 GB free or while another GPU job runs.
