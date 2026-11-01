---
name: trellis-part
description: Turn one picked, cut-out part image (a body, a gear piece, a prop) into a textured GLB with TRELLIS-3 on the AMD laptop, with the cut-out step, the attention-budget presets and the known slowdowns. Use when generating a mesh from a Flux image.
---

# trellis-part

Wrapper: `tools/run_3d.py` (MCP tools `cutout(asset_id, image)` and `trellis_run(asset_id, image, preset)`). Settings: `config/config.json -> trellis` (env, presets) and `config.local.json` (venvs, dirs). Nothing else may be on the GPU.

## Evidence
Receipts that ran cutout then TRELLIS with status ok:
- `receipts/vampire-dracula-body/`: cutout 136 s, TRELLIS `master_reduced` (attn 28 GB) 377 s.
- `receipts/vampire-dracula-body-tpose/`: cutout 191 s, TRELLIS `master_reduced` 518 s.
- `receipts/vampire-dracula-attn24/`: attn 24 GB, 1749 s (dressed full figure).
- `receipts/vampire-dracula-front/`: preset `master`, no attention budget, 3183 s (dressed full figure, free RAM 0.0 GB).
- `receipts/robot-tokens98k/`: `--attn-auto-gb 24 --max-tokens 98304`, 336 s, free RAM 0.1 GB.
These are different inputs, so the speed-up is indicative, not a controlled benchmark. `docs/benchmarks.md` has the attention table.

## Steps
1. Input: one image of ONE part on a plain background (a body, a head, one gear piece, a prop; never a whole dressed figure), at least 1024 px on the short side and upscaled to 2048 px or more first (`character-sheet` step 9). For a body: the T-posed base-layer body, see `bind-humanoid`.
2. Start the guard: `powershell -NoProfile -File tools\gpu_guard.ps1 -Match generate -MinFreeRamGB 3 -MaxGpuGB 0`.
3. Run: `python tools\run_3d.py <asset_id> <picked_image.png> [--preset NAME]` (default `master_reduced`).
4. Stage 1, cut-out (CPU, 101-191 s): `tools/cutout_cpu.py` (BiRefNet) with `cutout_python`, the py3.11 venv (only one with einops), with `TRELLIS3_REMBG` removed from its environment. Output `<outputs_dir>/3d/<asset_id>/cutout.png`.
5. Stage 2, TRELLIS (GPU): `generate.py <cutout> --preset master --texture-size 2048 --bake-faces 500000 --device rocm --single-process --profile-memory --output <prefix> --attn-auto-gb 28`, run in the py3.12 venv with env from `trellis.env` (sdpa attention, flex_gemm, `TRELLIS3_REMBG=none`). Output `mesh.glb`, `mesh.receipt.json`; log in `logs_dir`.
6. Check `receipts/<asset_id>/cutout-*.json` and `trellis-*.json` both have `status: ok`; look at the GLB and stills before binding.

## Tiers (draft / master / hero)
The factory presets live in `config/config.json -> trellis.presets` and all call `generate.py --preset master` with the size and budget overridden. The base presets come from `Trellis-3-312/trellis3/presets.py`: `draft` (512 pipeline, 8 steps, no texture: throwaway), `final` (1024 cascade, 2048 texture), `master` (1024 cascade, 4096 texture, 1M-face bake, normal map), `hero` (1536 cascade, 8192 texture, 2M-face bake, normal map).

| Tier | Factory preset | Texture / faces | Use | Status |
|---|---|---|---|---|
| draft | `master_reduced` (default, attention 28 GB), `attn24`, `tokens98k`, `plain` (no budget) | 2048 / 500k | find out whether the input works, bind tests, proofs. A benchmark profile: never shown as a final | PROVEN, receipts above |
| master | none in config yet (a preset for `generate.py --preset master` at its own 4096 / 1M-face size would have to be added; no literals) | 4096 / 1M faces, normal map | stills and close-ups | NOT PROVEN, no receipt |
| hero | `hero_rebake` (re-decode saved latents at 8192 / 2M faces, normal map; skill `hero-rebake`). A fresh `--preset hero` run (1536 shape) has no config preset | 8192 / 2M faces | the final; only after the same input passes at draft and Dom has seen the stills (`docs/asset-standard.md` section 2) | NOT PROVEN, never run |

Only run a higher tier on an input that already passed the draft tier and that Dom picked. The repo's own naming overlaps (`docs/asset-standard.md` calls the 2M / 8K output the "MASTER asset" and runs it with `--preset hero`); this skill uses the table above.

## Time
Cut-out 101-191 s (CPU). TRELLIS 336-518 s for a body or part at 28 GB; 1749 s at 24 GB on a full dressed figure; 3183 s with no budget. Over the budget TRELLIS uses tiled attention, 6 to 10 times slower. Token cliff near 24k tokens (owner-measured; the receipts do not record token counts, so check the profile table before relying on it).

## Not proven
The master and hero tiers above. `--from-latents PATH --out-name STEM` (re-decode and re-bake, no sampling, needs the asset's existing `cutout.png`) is coded in `run_3d.py` but has no receipt, and `hero_rebake` has never run. `.latents.pt` files are gitignored; keep them if a hero rebake is wanted.

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
- Do put limits in a preset in `config.json`, not on the command line (the hook `no_literals.py` blocks inline numbers on `generate.py`).
- Do keep `mesh.glb`, the `.latents.pt` (written after every sampling stage) and the receipt of every run: the latents are what a hero re-bake needs.
- Do build hard characters in parts (body, each head, each gear piece) and assemble later; run the cleanup stage (`tools/run_clean.py`) on a part that fails the bind gate.
- Do check the receipt, not the exit message.

Don't
- Don't build characters or creatures as ONE mesh: build them in parts (body + gear parts; for creatures the body, head and each limb or attachment as parts). A fused mesh hides geometry (a cloaked coat hides the legs), cannot be rigged and fails the bind gate (the fused vampire bound with stretch 29.8; the T-posed body part bound with 1.31). Applies to humanoids, beasts and anything animated.
- Don't run two GPU jobs at once (two hard freezes on 2026-10-31); the hook `gpu_lock_guard.py` and the MCP tools refuse a second one.
- Don't trust 'done' without a receipt file, and don't trust a receipt without looking at the output.
- Don't put literals (prompts, sizes, seeds, budgets, paths) in a run: add or edit a kit row, a preset or config (the hook `no_literals.py` blocks inline numbers on `generate.py` and `flux_kit.py`).
- Don't call a proof a final, and don't show the 2048/500k benchmark profile as one.
- Don't pass `TRELLIS3_REMBG=none` to the cut-out step: `cutout_cpu.py` reads it as a model id and fails (early `vampire-dracula-front` cut-outs failed on model loading).
- Don't go over the default 28 GB attention budget: tiled attention is 6 to 10 times slower.
- Don't exceed the token cliff (about 24k) without a reason and a receipt.
- Don't run another GPU job, Flux or a Blender render while this runs.
- Don't re-run a failed seed hoping for luck; change the input.
- Don't feed it a 1024 px file when a 2048 px upscale exists, and never a dressed full figure (the dressed single-mesh run took 3183 s with no budget and bound with stretch 29.8).
