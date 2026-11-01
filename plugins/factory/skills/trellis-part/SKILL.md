---
name: trellis-part
description: Turn one picked, cut-out part image (a body, a gear piece, a prop) into a textured GLB with TRELLIS-3 on the AMD laptop, with the cut-out step, the attention-budget presets and the known slowdowns. Use when generating a mesh from a Flux image.
---

# trellis-part (DRAFT, proven for the reduced master)

Wrapper: `tools/run_3d.py`. Settings: `config/config.json -> trellis` (env, presets) and `config.local.json` (venvs, dirs). Nothing else may be on the GPU.

## Evidence
Receipts that ran cutout then TRELLIS with status ok:
- `receipts/vampire-dracula-body/`: cutout 136 s, TRELLIS `master_reduced` (attn 28 GB) 377 s.
- `receipts/vampire-dracula-body-tpose/`: cutout 191 s, TRELLIS `master_reduced` 518 s.
- `receipts/vampire-dracula-attn24/`: attn 24 GB, 1749 s (dressed full figure).
- `receipts/vampire-dracula-front/`: preset `master`, no attention budget, 3183 s (dressed full figure, free RAM 0.0 GB).
- `receipts/robot-tokens98k/`: `--attn-auto-gb 24 --max-tokens 98304`, 336 s, free RAM 0.1 GB.
These are different inputs, so the speed-up is indicative, not a controlled benchmark. `docs/benchmarks.md` has the attention table.

## Steps
1. Input: one image, at least 1024 px on the short side, one part on a plain background (for a body: the T-posed base-layer body, see `bind-humanoid`).
2. Start the guard: `powershell -NoProfile -File tools\gpu_guard.ps1 -Match generate -MinFreeRamGB 2 -MaxGpuGB 0`.
3. Run: `python tools\run_3d.py <asset_id> <picked_image.png> [--preset NAME]` (default `master_reduced`).
4. Stage 1, cut-out (CPU, 101-191 s): `tools/cutout_cpu.py` (BiRefNet) with `cutout_python`, the py3.11 venv (only one with einops), with `TRELLIS3_REMBG` removed from its environment. Output `<outputs_dir>/3d/<asset_id>/cutout.png`.
5. Stage 2, TRELLIS (GPU): `generate.py <cutout> --preset master --texture-size 2048 --bake-faces 500000 --device rocm --single-process --profile-memory --output <prefix> --attn-auto-gb 28`, run in the py3.12 venv with env from `trellis.env` (sdpa attention, flex_gemm, `TRELLIS3_REMBG=none`). Output `mesh.glb`, `mesh.receipt.json`; log in `logs_dir`.
6. Check `receipts/<asset_id>/cutout-*.json` and `trellis-*.json` both have `status: ok`; look at the GLB and stills before binding.

## Presets (`trellis.presets`)
`master_reduced` (2048 tex, 500k faces, attn 28 GB, default), `attn24` (24 GB), `tokens98k` (24 GB plus `--max-tokens 98304`), `plain` (no budget), `hero_rebake` (8192 tex, 2M faces, for `--from-latents`).

## Time
Cut-out 101-191 s (CPU). TRELLIS 336-518 s for a body or part at 28 GB; 1749 s at 24 GB on a full dressed figure; 3183 s with no budget. Over the budget TRELLIS uses tiled attention, 6 to 10 times slower. Token cliff near 24k tokens (owner-measured; the receipts do not record token counts, so check the profile table before relying on it).

## Not proven
`--from-latents PATH --out-name STEM` (re-decode and re-bake, no sampling, needs the asset's existing `cutout.png`) is coded in `run_3d.py` but has no receipt, and `hero_rebake` has never run. `.latents.pt` files are gitignored; keep them if a hero rebake is wanted.

## Do / Don't
Do
- Do put limits in a preset in `config.json`, not on the command line.
- Do keep `mesh.glb`, the latents and the receipt of every run.
- Do build hard characters in parts (body, each gear piece) and assemble later.
- Do check the receipt, not the exit message.

Don't
- Don't pass `TRELLIS3_REMBG=none` to the cut-out step: `cutout_cpu.py` reads it as a model id and fails (early `vampire-dracula-front` cut-outs failed on model loading).
- Don't go over the default 28 GB attention budget: tiled attention is 6 to 10 times slower.
- Don't exceed the token cliff (about 24k) without a reason and a receipt.
- Don't run another GPU job, Flux or a Blender render while this runs (two hard freezes on 2026-10-31).
- Don't build a cloaked or dressed character as ONE mesh: no leg geometry, cannot be rigged (the fused vampire bound with stretch 29.8).
- Don't re-run a failed seed hoping for luck; change the input.
- Don't trust "done" without a receipt.
