---
name: bind-beast
description: "DRAFT. Rig a creature (quadruped, winged quadruped, serpent) with the OpenTrident beast rig standard: bone rows with roles, a role map, headless Blender builds. Only what the repo and the OpenTrident handover docs say; no script yet binds a TRELLIS mesh to a beast rig. Use when a creature needs a skeleton."
---

> **STATUS: DRAFT, not yet run end to end.** Written from the repo's scripts, config and receipts; this stage has no complete run with a receipt.
>
> Unproven:
> - No beast or quadruped bind has ever run in this repo (`skills/STATUS.md`; `bind/` holds only the humanoid donor binder). There is no factory receipt for any beast stage.
> - No script in either repo binds a TRELLIS mesh to a beast rig. The three `build_*.py` scripts skin the CC0 Quaternius Wolf, Dragon and a test tube, not a Flux/TRELLIS creature.
> - The beast rig file itself says `PROPOSAL v2 ... not verified in Blender or on any clip source`, and the README says the proposal is not shipped.
> - There is no beast gate in `config/config.json`: its `bind.gate` is the humanoid psyche-106 gate. Do not reuse its numbers for a beast without Dom's ruling.
>
> Treat every step below as a plan to verify, and write the first end-to-end receipt before removing this banner.

# bind-beast

Source of every fact below: the OpenTrident beast tooling `C:\HANDOVER-Macbook\extracted\3-opentrident-demon-threejs\dev\opentrident-steam\tools\beast` (read-only handover copy: `README.md`, `beast-rigs-proposal.json`, `build_quadruped.py`, `build_serpent.py`, `build_winged.py`, `quadruped_clips.json`, `serpent_clips.json`, the `out_*` reports) and `Documents\HANDOFF-trellis-laptop-2026-11-01.md` ("OpenTrident Dev ... owns library, beast rig"). Nothing in `C:\HANDOVER-Macbook` may be moved, unzipped or edited. Ask OpenTrident Dev before building on any of it.

## The rig standard (`beast-rigs-proposal.json`)
Rules in the file: deform bones only exported to glTF; 4-influence limit; Unity Generic; names are data, no literals in code; the `role` column is the retarget key. Each bone row has `bone`, `parent`, `role`, `tier` (core or optional), `name_source`.

| Rig | Bones | Core / optional | Roles |
|---|---|---|---|
| `quadruped` | 44 | 36 / 8 | root, pelvis, spine (2), chest, neck (3), head, jaw, ear (4), eye (2), front_clavicle, front_upper, front_lower, front_foot, front_toe (each 2), hind_hip, hind_upper, hind_lower, hind_foot, hind_toe (each 2), twist (4), tail (4). Bone names follow Rigify (`DEF-spine.004` pelvis, `DEF-thigh.L`, `DEF-front_thigh.L`, ...) |
| `winged_quadruped` | 58 (quadruped plus 14) | 50 / 8 | adds wing_root, wing_upper, wing_lower, wing_hand (each 2) and wing_spar (6) |
| `serpent` | 29 | 27 / 2 | root, spine (18), neck (3), head, jaw, tail (5) |
| `winged_humanoid_socket` | 14 extra on the 106 rig | 14 / 0 | wing_socket (2), wing_chain (12), for a winged humanoid, on top of the humanoid rig |

## Steps (as the OpenTrident scripts do them)
1. **Parts first.** Build the creature as parts (body, head, each limb, attachments), never one mesh (`trellis-part`, one image per part, upscaled to 2048 px or more). `flux/flux_dragon.py` is the repo's dragon image test.
2. **Pick the rig** from the table by anatomy: four legs, wings (a two-legged wyvern uses the hind legs only, as `build_winged.py` does), or limbless (serpent).
3. **Role map.** Map each source bone (or each body part) to a target bone by role. In `build_quadruped.py` this is the `ROLE_MAP` dict at the top (for example `Hips` to `DEF-spine.004`; `Neck` split one third each across `DEF-spine.008` to `.010`; `FrontUpLeg.L` to `DEF-front_thigh.L`); feet and toes keep their rest orientation so they stay planted. `build_winged.py` has its own `ROLE_MAP` from the Dragon bones to the same rows plus the wing rows.
4. **Build and skin** in headless Blender 5.2 (the repo's `blender_exe`): `blender -b -P build_serpent.py -- ROWS_JSON OUT_DIR CLIPS_JSON`, `blender -b -P build_quadruped.py -- ROWS_JSON BUILD_DIR OUT_DIR`, `blender -b -P build_winged.py -- ROWS_JSON BUILD_DIR OUT_DIR`. Bone positions come from the source mesh's own armature; limit to 4 influences per vertex.
5. **Clips.** Quadruped clips Idle, Walk, Run, Death are retargeted from the CC0 Quaternius Cow; Attack and Hit are authored rows in `quadruped_clips.json` (each channel rotates the bones of a role group about a world axis, with a ground-contact fix that lifts the hips when a foot would go below the rest ground line). Serpent clips (Idle, Move, Attack, Hit, Death) are authored rows in `serpent_clips.json` (a travelling wave along the spine plus neck/head pitch). Winged clips (Attack, Attack2, Hit, Death, Flying) come from the Dragon's own skeleton by role.
6. **Check** the report JSON, then LOOK at the stills the scripts render (`out_*/still_*.png`: rest, idle, walk, attack, death) before anything is called done.

## What the handover reports say (OpenTrident outputs, not factory receipts)
- Quadruped (`out_quad/quadruped_report.json`): 38 bones, 37 deform, zero-weight vertices 0, max influences 4, hips height ratio 0.94, GLB 311,120 bytes. Its ground fix could not repair the Death clip (worst foot height -0.3611 before and after).
- Winged (`out_winged/winged_report.json`): 37 bones, 36 deform, zero-weight vertices 0, max influences 4, GLB 304,772 bytes.
- Serpent (`out_serpent/serpent_report.json`): 29 bones, 28 deform, zero-weight vertices 0, influences 7 before the limit and 4 after, GLB 10,674,780 bytes.
These are the only beast numbers that exist. No stretch or compression gate has been measured for a beast.

## Gates
Only what the reports show: zero-weight vertices 0, at most 4 influences, deform-only export, clips play without a foot below the ground line. A stretch/compression gate for beasts has to be agreed with Dom first (the humanoid gate in `bind-humanoid` is for the psyche-106 rig).

## Traps (from `README.md`)
- Never `hide_viewport` a source armature in headless Blender: its animation then never plays. Use `hide_render` only.
- The CC0 Quaternius packs are NOT in the repo: fetch them yourself (`fetch_itch.py`) and unzip the FBX into `BUILD_DIR/src` and `src_dragon`. Game rips are reference only.
- Binary outputs are not committed (`tools/beast/MODELS.md` records path and sha256 instead). Handing a dragon GLB to OpenTrident Dev also needs the orientation fix (`Documents\HANDOFF-trellis-laptop-2026-11-01.md`, item 2).

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
- Do check the `role` of every bone against the table before skinning; the role column is the retarget key.
- Do keep bone names, parents and roles in the rows file, not in code.
- Do ask OpenTrident Dev (owner of the beast rig) before changing the rows or the clips.

Don't
- Don't build characters or creatures as ONE mesh: build them in parts (body + gear parts; for creatures the body, head and each limb or attachment as parts). A fused mesh hides geometry (a cloaked coat hides the legs), cannot be rigged and fails the bind gate (the fused vampire bound with stretch 29.8; the T-posed body part bound with 1.31). Applies to humanoids, beasts and anything animated.
- Don't run two GPU jobs at once (two hard freezes on 2026-10-31); the hook `gpu_lock_guard.py` and the MCP tools refuse a second one.
- Don't trust 'done' without a receipt file, and don't trust a receipt without looking at the output.
- Don't put literals (prompts, sizes, seeds, budgets, paths) in a run: add or edit a kit row, a preset or config (the hook `no_literals.py` blocks inline numbers on `generate.py` and `flux_kit.py`).
- Don't call a proof a final, and don't show the 2048/500k benchmark profile as one.
- Don't invent a gate, a bone count or a clip list that the rows and reports do not show.
- Don't use `bind/bind_best.py` (humanoid donor transfer onto the 106 rig) for a beast.
- Don't ship a game-rip model or a model with an unclear licence; the sources here are CC0 (Quaternius) or in-house.
