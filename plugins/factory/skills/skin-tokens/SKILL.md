---
name: skin-tokens
description: "DRAFT. Skin a T-posed humanoid body GLB onto the psyche-106 rig with SkinTokens (VAST-AI, MIT) instead of the Blender donor transfer, then gate it like the donor bind plus the relative-to-control clause. GPU job. Use when the donor bind fails the gate or to compare weights."
---

> **STATUS: DRAFT, not yet run end to end.** Plumbing and gate are tested offline; no SkinTokens bind has run on the laptop GPU yet.
>
> Unproven:
> - No receipt exists for `run_skin_tokens.py`. The vampire T-pose body has not been skinned by SkinTokens; stretch, compression and the relative clause against the `tall` control are unmeasured.
> - The ROCm port (flash-attn optional, SDPA fallback) was only smoke tested on CPU. Whether `demo.py --use_skeleton` accepts a mesh plus an unskinned armature, whether `--use_transfer` keeps the rig's metre scale, and whether bone names survive are all unverified (the export step records `bones_missing`; the gate fails on it).
> - Weights are clamped to 4 influences and renormalised in Blender before measuring (`config.json -> skin_tokens.clamp_influences`); the clamp is recorded in the receipt.

# skin-tokens

Default binder: with `config.json -> bind.binder` set to `skin_tokens` (flip: `python tools/set_default_binder.py skin_tokens`) the MCP tool `bind` and `tools/run_character.py` run SkinTokens first and use the donor bind only as the labelled `FALLBACK donor (MakeHuman weights)` when `bind.fallback_to_donor` is true and SkinTokens fails the gate or its prerequisites are missing. The repo ships with `bind.binder` = `donor` until the proving run below passes; after a fallback the bound body is whichever GLB `bound_source.json` in the asset's work folder names (`assemble_character.py` reads it).

Tool: MCP `skin_tokens_bind(glb, skeleton, asset_id)`; script `tools/run_skin_tokens.py <asset_id> <body.glb> [<rig.glb>]`; Blender helpers `tools/skin_tokens/prep_input.py`, `to_rig_body.py`; gate `tools/skin_tokens/gate.py`. Settings: `config.json -> skin_tokens` and `bind.gate`; paths in `config.local.json` (`skin_tokens_repo_dir`, `skin_tokens_python`, `rig_glb`, `blender_exe`). The code is MIT (github.com/VAST-AI-Research/SkinTokens), weights VAST-AI/SkinTokens (MIT on the model card). Patch for ROCm: `tools/skin_tokens/patches/rocm_sdpa.patch` (adds `src/model/attn_compat.py`).

Launcher: `config.json -> skin_tokens.launcher` (empty by default) may name an external launcher with the Windows/ROCm workarounds (a local file, not in this repo). When set, the skintokens step runs `[python, launcher, in.glb, out.glb]` with `NUM_BEAMS` from `skin_tokens.num_beams` instead of `demo.py`; the file must exist or the run stops before any step, and the receipt records `params.launcher` and `params.launcher_sha256`.

## Steps
1. Input: a body-only, T-posed humanoid GLB (same rule as `bind-humanoid`).
2. Check `factory_status`: SkinTokens is a GPU job, so it needs the GPU lock free and 6 GB of free RAM. One GPU job at a time.
3. Call `skin_tokens_bind` (the hook `plugin_guard.py` refuses the raw script). It builds body + skeleton input in Blender, runs `demo.py --use_skeleton`, names the result `Rig`/`Body`, clamps influences, then measures with `bind/metrics2.py --control`.
4. Gate (same numbers as `run_bind.py`, from `config.json -> bind.gate`): p99 stretch <= 2.0, p99 compression ratio >= 0.5, zero-weight vertices 0, at most 4 influences, weight sums within 1e-3. Plus the relative clause that `run_bind.py` skips: stretch <= control x 1.10 and compression >= control x 0.90 (`control_tall.metrics.json`). Plus every rig bone kept.
5. Output `bound_skintokens.glb` next to the donor `bound.glb`; compare both receipts (stage `bind`) before choosing one.

## Proving run (not yet done)
Vampire T-pose body vs the donor bind (`receipts/vampire-dracula-body-tpose/bind-20261101T092209Z.json`: stretch 1.31, compression 0.612, relative clause failed). Command (GPU window agreed with the Factory first, `factory_status` shows GPU free): MCP `skin_tokens_bind(glb=<outputs_dir>/3d/vampire-dracula-body-tpose/mesh.glb, asset_id="vampire-dracula-body-tpose-skintokens")` (skeleton defaults to `rig_glb`). Expect `receipts/vampire-dracula-body-tpose-skintokens/bind-<utc>.json` with `tool: run_skin_tokens.py`, outputs `bound_skintokens.glb`, `.blend`, metrics, and `params.metrics.checks` listing all eight checks. Done means a `bind` receipt with `status: ok`, `checks.relative_to_control: true`, and the numbers beside the donor ones.

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
- Do keep the receipt of every bind, failed ones too.

- Do compare against the donor bind receipt of the same body before choosing SkinTokens.

Don't
- Don't build characters or creatures as ONE mesh: build them in parts (body + gear parts; for creatures the body, head and each limb or attachment as parts). A fused mesh hides geometry (a cloaked coat hides the legs), cannot be rigged and fails the bind gate (the fused vampire bound with stretch 29.8; the T-posed body part bound with 1.31). Applies to humanoids, beasts and anything animated.
- Don't run two GPU jobs at once (two hard freezes on 2026-10-31); the hook `gpu_lock_guard.py` and the MCP tools refuse a second one.
- Don't trust 'done' without a receipt file, and don't trust a receipt without looking at the output.
- Don't put literals (prompts, sizes, seeds, budgets, paths) in a run: add or edit a kit row, a preset or config (the hook `no_literals.py` blocks inline numbers on `generate.py` and `flux_kit.py`).
- Don't call a proof a final, and don't show the 2048/500k benchmark profile as one.
- Don't run it while another GPU job holds the lock.
