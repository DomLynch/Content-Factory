---
name: character-sheet
description: Build and render the reference character sheet for a humanoid (body turnarounds, gear flats, heads, hands/feet, material swatches) with Flux, one cell per process, reference-locked to one identity image. Use when starting a new humanoid character or re-rendering sheet cells.
---

# character-sheet

Repo root: the Content-Factory checkout (`C:\Users\dom12\Documents\Content-Factory`). All paths and limits come from `config/config.json` plus the gitignored `config/config.local.json`. Python is `config.local.json -> python` (the Trellis-3-312 venv).

## Evidence level (read first)
- PROVEN with receipts: single Flux klein cells with a reference image, 1024x1536 and 1536x1024, for example `receipts/vampire-head_front-0b98b7/flux-20261101T045601Z.json` (wall 236 s, generate 202 s, peak GPU 21.8 GB, lowest free RAM 2.9 GB) and `receipts/vampire_parts-body_tpose-4d1d1d/flux-20261101T090039Z.json` (wall 354 s, generate 316 s, peak GPU 23.3 GB, free RAM 3.0 GB).
- PROVEN offline: `tools/make_kit.py` turned `characters/vampire-dracula-sheet.json` into `kits/vampire_sheet_kit.json` (23 rows).
- NOT PROVEN: the full 23-cell `vampire_sheet` kit has no Flux receipts yet (its output folder in `kits_out` was empty when this was written), and `tools/contact_sheet.py` has no receipt. Treat steps 4 to 7 as unrun for the full sheet.

## Steps
Tool shortcut: the MCP tool `flux_render(kit, row_id)` runs step 4 for one row (guard armed, refuses on a live GPU lock or under 6 GB free RAM) and returns the receipt path; its `dry_run` flag is step 3.

1. **Spec.** Copy `characters/vampire-dracula-sheet.json`. Set `id`, `description`, `seed`, `ref` (the identity image), `base_layer_ref` (plain fitted base layer, no coat or cloak), `character_noun`, `possessive`, `facial_hair`, `base_layer`, `three_quarter_posture`, `head_detail`, `hair_desc`, `gear_items[]`, `swatches[]`. All are required; the script exits on a missing field.
2. **Kit.** `python tools/make_kit.py characters/<spec>.json` writes `kits/<id>_sheet_kit.json`. It fails on any unfilled `{placeholder}`. Template: `config/character_sheet_template.json` (5 body views, one flat per gear item, 4 heads, hands, feet, one swatch per material). Rows are sorted by `group_priority` (body, gear, head, hands_feet, swatch), which is the GPU order. Sizes: body 1024x1536, gear/hands/feet 1536x1024, heads/swatches 1024x1024.
3. **Dry run, no GPU.** Set `CF_DRY=1`, then `python flux/flux_kit.py kits/<id>_sheet_kit.json <row_id>` prints the asset id, output path, size and whether the reference is used.
4. **Render ONE row per process, one GPU job at a time.** Check the lock first (`/factory-status`). Start the guard, then the row:
   - guard: `powershell -NoProfile -File tools\gpu_guard.ps1 -Match flux_kit -MinFreeRamGB 3 -MaxGpuGB 0` (values are `config.json -> limits`)
   - row: `python -u flux/flux_kit.py kits/<id>_sheet_kit.json <row_id>`
   - or every missing row, sequentially, one process each: `powershell -File bench\run_kit.ps1 kits\<id>_sheet_kit.json` (it arms the guard and skips rows whose png exists).
   Output: `<outputs_dir>/<kit>/<name>_<W>x<H>.png` and a receipt `receipts/<kit>-<row>-<hash>/flux-<utc>.json`. Exit 2 with `BLACK_IMAGE` means mean brightness under 3: the image is not saved and a fail receipt is written.
5. **Identity lock.** Every row sends the kit `ref` (or the row's own `ref`) plus its `ref_prefix` ("Edit instruction: keep this exact same ..."). Body rows use `base_layer_ref`, the clothes-free base layer, so the body can later be bound. Rows with `use_ref:false` (swatches) get no reference.
6. **Repair the black block.** After each 1024x1536 render run `python tools/fix_black_block.py <png>`. It prints the count of fully black 32 px cells; above 40 it fills them and saves the original to `orig/`. For symmetric subjects add `--mirror <centre column>`.
7. **Contact sheet.** `python tools/contact_sheet.py kits/<id>_sheet_kit.json [out.jpg]` (4096 px wide; a missing cell is a grey MISSING box). Look at it before any TRELLIS run, and score every cell against the identity image.
8. **Pick.** Dom picks per part. Record the picks as data, never by renaming a file: `python tools/manifest.py kits/<id>_sheet_kit.json --pick <row_id> ... --by Dom --note "..."` writes `manifests/<kit>.json` (candidates with sha256 plus the pick; example `manifests/hades_gate.json`).
9. **Upscale the picks.** `python tools\upscale\upscale_match.py <pick.png> <out.png> 1 <asset_id>` (Real-ESRGAN x4, then a Lab mean/std colour match back to the source; a second pass turns 2048 into 8192 on the long side; about 30 s per pass per its docstring, 15.8 to 18.6 s wall in `receipts/vampire-dracula-head/upscale-20261101T095015Z.json` and `receipts/hades_gate-gate_front_elevation/upscale-20261101T062005Z.json`). It writes an `upscale` receipt only when it is given the asset id. The MCP tool `upscale(src, dst)` wraps it. It is a GPU (Vulkan) job: one at a time. The executable path is hard-coded in the script (`~\Documents\pipeline_tools\realesrgan-full\app`). The upscaled file, not the 1024 px Flux file, goes to `trellis-part`.
10. **Version.** The sheet, its contact sheet, the picks and the receipts are one version (v1, v2, v3). A new Flux pass on a part makes the next version; do not overwrite the old one.

## Gates
- Input to 3D: each picked view at least 1024 px on the short side, measured with PIL (`docs/asset-standard.md`, section 1), and upscaled to 2048 px or more (step 9). The hook `input_gate.py` denies a TRELLIS or cut-out call under 1024 px.
- At most 1.6 MP per render (`limits.max_megapixels`); 1024x1536 is 1.57 MP.
- A cell counts only with a receipt whose `status` is `ok`.
- The body that goes to TRELLIS and the binder must be T-posed. The sheet template's body views (`kits/vampire_sheet_kit.json`, `body_front` and the others) are A-pose turnarounds; the T-pose body is the `body_tpose` row of `kits/vampire_parts_kit.json` (receipt `receipts/vampire_parts-body_tpose-4d1d1d/flux-20261101T090039Z.json`), the base layer with no coat or cloak.

## Time and memory (from receipts)
Load 34-38 s, generate 178-316 s, wall 214-354 s per cell. Peak GPU alloc 21.8-23.3 GB. Lowest free RAM 0.0-3.5 GB (`receipts/vampire-head_profile-615c32` touched 0.0 at 1.5 MP; the guard kills at 3 GB (limits.guard_min_free_ram_gb since c9c6260) and a start needs 6 GB free (limits.start_min_free_ram_gb)). A full 23-cell sheet is therefore about 1.5 to 2 hours of serial GPU time (estimate, not measured).

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
- Do render one row per process (`flux_kit.py` takes `<kit> <row_id>`).
- Do keep every cell of the kit; the sheet is the standard.
- Do run `fix_black_block.py` on 1024x1536 outputs and look at the image again.
- Do keep a plain base-layer body reference (`base_layer_ref`, T-posed, no coat or cloak) separate from the dressed hero image.

Don't
- Don't build characters or creatures as ONE mesh: build them in parts (body + gear parts; for creatures the body, head and each limb or attachment as parts). A fused mesh hides geometry (a cloaked coat hides the legs), cannot be rigged and fails the bind gate (the fused vampire bound with stretch 29.8; the T-posed body part bound with 1.31). Applies to humanoids, beasts and anything animated.
- Don't run two GPU jobs at once (two hard freezes on 2026-10-31); the hook `gpu_lock_guard.py` and the MCP tools refuse a second one.
- Don't trust 'done' without a receipt file, and don't trust a receipt without looking at the output.
- Don't put literals (prompts, sizes, seeds, budgets, paths) in a run: add or edit a kit row, a preset or config (the hook `no_literals.py` blocks inline numbers on `generate.py` and `flux_kit.py`).
- Don't call a proof a final, and don't show the 2048/500k benchmark profile as one.
- Don't generate a second image in the same process: it comes out black.
- Don't render above 1.5 MP on this box: 2048x1536 thrashed RAM to 0.1 GB free and took hours; at 1.5 MP free RAM still touches 0.4 to 2 GB, so the guard must be armed.
- Don't skip kit pieces to save time.
- Don't ask for a dressed character as one body image to feed 3D (see `bind-humanoid`).


## Spec fields are the only place character words live (GPT-ref sheet)
`config/character_sheet_template_gpt.json` is character-neutral: placeholders only, plus a `group_defaults` block (gear and swatch rows get `use_ref: false`) and a `required_fields` list. Every character word lives in the spec (`characters/<id>-gpt-sheet.json`). For a second character copy `characters/_example_second_character.json`, fill every field, then run `python tools/make_kit.py <spec> [out_kit.json] [--kit-name NAME]`. `make_kit.py` fails if a field is missing or a placeholder is left unfilled, and `{Field}` (capitalised first letter) is available for every string field.
Required spec fields: id, description, seed, ref, base_layer_ref, character_noun, possessive, facial_hair, base_layer, three_quarter_posture, head_detail, hair_desc, hair_style_desc, gear_items, swatches, eye_desc, pronoun, removed_gear, removed_gear_full, hand_accessory, outfit_desc, back_body_desc, front_body_desc, back_visible_desc, pose_rule, side_pose_rule, head_skin_detail, head_hair_front_desc, head_hair_profile_desc, head_facial_hair_desc, head_chin_front_desc, head_collar_desc, head_garment_exclude, profile_eye_desc, head_top_hair_colour, head_top_hair_desc, head_top_garment, head_top_exclude, hand_skin_desc, hand_wrist_rule, frame_rule, foot_desc, foot_sole_desc, foot_negative, gear_isolation_head, gear_isolation_negative, gear_person_negative, gear_closeup_suffix, head_hair_back_desc, head_chin_rule, head_collar_rule, head_accessory_exclude, hand_handedness_right, hand_ring_rule, hand_wrist_clean_rule.
The back and three-quarter body rows are text-only (row-level `use_ref: false` in the template, because the reference image overrides rotation) and are described by outfit_desc, back_body_desc, front_body_desc and back_visible_desc.
Per-item gear data: gear_items[] may set colour_desc (colour and material of that one garment, injected into its prompt), gear_isolation_negative (per-item override, empty string drops it), form (flat, described or closeup, see the template `forms`), gear_layout, gear_exclude and size. Hands and feet, gear and swatches are text-only (`group_defaults` use_ref false); body back and three-quarter rows are text-only per row.
Check a regeneration against an existing kit with `python tools/compare_kits.py <kit_a> <kit_b>` (row names, group, w, h, ref, use_ref, prompt, ref_prefix; prints every difference). Never hand-edit a generated kit: change the spec or template and regenerate.
Row-level data: the spec field row_seeds {row_id: {seed, note}} gives a documented re-render seed to a row whose old png is no longer on disk; template cells may carry mirror_of (the row is made by flipping another row's png with tools/mirror_cell.py and the queue skips it).
