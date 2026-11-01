---
name: character-sheet
description: Build and render the reference character sheet for a humanoid (body turnarounds, gear flats, heads, hands/feet, material swatches) with Flux, one cell per process, reference-locked to one identity image. Use when starting a new humanoid character or re-rendering sheet cells.
---

# character-sheet (DRAFT, partly proven)

Repo root: the Content-Factory checkout (`C:\Users\dom12\Documents\Content-Factory`). All paths and limits come from `config/config.json` plus the gitignored `config/config.local.json`. Python is `config.local.json -> python` (the Trellis-3-312 venv).

## Evidence level (read first)
- PROVEN with receipts: single Flux klein cells with a reference image, 1024x1536 and 1536x1024, for example `receipts/vampire-head_front-0b98b7/flux-20261101T045601Z.json` (wall 236 s, generate 202 s, peak GPU 21.8 GB, lowest free RAM 2.9 GB) and `receipts/vampire_parts-body_tpose-4d1d1d/flux-20261101T090039Z.json` (wall 354 s, generate 316 s, peak GPU 23.3 GB, free RAM 3.0 GB).
- PROVEN offline: `tools/make_kit.py` turned `characters/vampire-dracula-sheet.json` into `kits/vampire_sheet_kit.json` (23 rows).
- NOT PROVEN: the full 23-cell `vampire_sheet` kit has no Flux receipts yet (its output folder in `kits_out` was empty when this was written), and `tools/contact_sheet.py` has no receipt. Treat steps 4 to 7 as unrun for the full sheet.

## Steps
1. **Spec.** Copy `characters/vampire-dracula-sheet.json`. Set `id`, `description`, `seed`, `ref` (the identity image), `base_layer_ref` (plain fitted base layer, no coat or cloak), `character_noun`, `possessive`, `facial_hair`, `base_layer`, `three_quarter_posture`, `head_detail`, `hair_desc`, `gear_items[]`, `swatches[]`. All are required; the script exits on a missing field.
2. **Kit.** `python tools/make_kit.py characters/<spec>.json` writes `kits/<id>_sheet_kit.json`. It fails on any unfilled `{placeholder}`. Template: `config/character_sheet_template.json` (5 body views, one flat per gear item, 4 heads, hands, feet, one swatch per material). Rows are sorted by `group_priority` (body, gear, head, hands_feet, swatch), which is the GPU order. Sizes: body 1024x1536, gear/hands/feet 1536x1024, heads/swatches 1024x1024.
3. **Dry run, no GPU.** Set `CF_DRY=1`, then `python flux/flux_kit.py kits/<id>_sheet_kit.json <row_id>` prints the asset id, output path, size and whether the reference is used.
4. **Render ONE row per process, one GPU job at a time.** Check the lock first (`/factory-status`). Start the guard, then the row:
   - guard: `powershell -NoProfile -File tools\gpu_guard.ps1 -Match flux_kit -MinFreeRamGB 2 -MaxGpuGB 0` (values are `config.json -> limits`)
   - row: `python -u flux/flux_kit.py kits/<id>_sheet_kit.json <row_id>`
   - or every missing row, sequentially, one process each: `powershell -File bench\run_kit.ps1 kits\<id>_sheet_kit.json` (it arms the guard and skips rows whose png exists).
   Output: `<outputs_dir>/<kit>/<name>_<W>x<H>.png` and a receipt `receipts/<kit>-<row>-<hash>/flux-<utc>.json`. Exit 2 with `BLACK_IMAGE` means mean brightness under 3: the image is not saved and a fail receipt is written.
5. **Identity lock.** Every row sends the kit `ref` (or the row's own `ref`) plus its `ref_prefix` ("Edit instruction: keep this exact same ..."). Body rows use `base_layer_ref`, the clothes-free base layer, so the body can later be bound. Rows with `use_ref:false` (swatches) get no reference.
6. **Repair the black block.** After each 1024x1536 render run `python tools/fix_black_block.py <png>`. It prints the count of fully black 32 px cells; above 40 it fills them and saves the original to `orig/`. For symmetric subjects add `--mirror <centre column>`.
7. **Contact sheet.** `python tools/contact_sheet.py kits/<id>_sheet_kit.json [out.jpg]` (4096 px wide; a missing cell is a grey MISSING box). Look at it before any TRELLIS run.

## Gates
- Input to 3D: each picked view at least 1024 px on the short side, measured with PIL (`docs/asset-standard.md`, section 1).
- At most 1.6 MP per render (`limits.max_megapixels`); 1024x1536 is 1.57 MP.
- A cell counts only with a receipt whose `status` is `ok`.

## Time and memory (from receipts)
Load 34-38 s, generate 178-316 s, wall 214-354 s per cell. Peak GPU alloc 21.8-23.3 GB. Lowest free RAM 0.0-3.5 GB (`receipts/vampire-head_profile-615c32` touched 0.0 at 1.5 MP; the guard kills at 2 GB). A full 23-cell sheet is therefore about 1.5 to 2 hours of serial GPU time (estimate, not measured).

## Do / Don't
Do
- Do LOOK at every image and output yourself before calling a stage done: open the picture or render, check it is not black, cropped, mis-framed, speckled or mismatched to the reference, and check the numbers. A receipt only proves the tool ran. (Learned 2026-11-01: black images sent as done; stills with the head cut off; a speckled texture.)
- Do hold a quality bar of 9/10 or higher, comparable to the best of Unreal Engine 5.8 in 2026, for every FINAL asset, with NO compromise on quality, visuals or effects. Score it honestly against the reference sheet. Anything below 9 is a proof, not a final: label it as a proof, say what is wrong, and do not ship it as finished.
- Do render one row per process (`flux_kit.py` takes `<kit> <row_id>`).
- Do keep every cell of the kit; the sheet is the standard.
- Do take every value from a kit row, the template or config.
- Do run `fix_black_block.py` on 1024x1536 outputs and look at the image again.
- Do keep a plain base-layer body reference separate from the dressed hero image.

Don't
- Don't generate a second image in the same process: it comes out black.
- Don't render above 1.5 MP on this box: 2048x1536 thrashed RAM to 0.1 GB free and took hours; at 1.5 MP free RAM still touches 0.4 to 2 GB, so the 2 GB guard must be armed.
- Don't run two GPU jobs at once (two hard freezes on 2026-10-31).
- Don't skip kit pieces to save time.
- Don't put literals (prompts, sizes, paths) in a run; add or edit a row, preset or config.
- Don't trust "done" without a receipt file.
- Don't ask for a dressed character as one body image to feed 3D (see `bind-humanoid`).
