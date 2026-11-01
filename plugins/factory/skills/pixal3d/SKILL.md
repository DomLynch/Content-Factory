---
name: pixal3d
description: "Image to textured GLB with Pixal3D (TencentARC, MIT) through the pixal3d_run tool: a second shape pipeline next to TRELLIS, compared with it only by the A/B on the same input and seed. Use when a cut-out picked image should be run through Pixal3D."
---

> **STATUS: DRAFT, not yet run end to end.** Weights are downloaded and checksummed and the tool, hooks and tests exist, but the model has not been run in the factory and no receipt exists yet. The A/B against TRELLIS has not run.
>
> Unproven: the natten shim has never been compared with real natten (no CUDA here), only with a plain-Python reference written from NATTEN's documented semantics; that Pixal3D runs on this ROCm stack at all, its wall time and peak memory, and whether it beats TRELLIS on the vampire body cell.

# Pixal3D (pipeline label PIXAL3D)

## What it is
TencentARC Pixal3D, MIT (code, weights and NOTICE re-read 2026-11-01). Single image in, PBR textured GLB out. It builds on the TRELLIS.2 stack the factory already runs, so it uses the same native ROCm wheels in its own venv `Pixal3D-312` next to `Trellis-3-312`.

## How to run
1. The input is the factory's cut-out PNG (short side at least 1024 px, subject at least 60% of the frame height; the input gate applies).
2. Call the MCP tool `pixal3d_run(asset_id, image, preset)`. Presets are in `config/config.json` under `pixal3d.presets`: draft and standard (1024, low VRAM), master (1536, low VRAM).
3. It refuses while another GPU job holds the lock, while free RAM is under the start limit, or on a small input. It writes a `pixal3d` receipt; the asset's pipeline label is PIXAL3D.
4. Look at the GLB and a lit turntable before saying it is done.

## What the entry script changes (in memory only)
- DINOv3 and MoGe-2 are read from local folders (no network, no token).
- The background remover in the weights' `pipeline.json` is rewritten once from briaai/RMBG-2.0 (licence bria-rmbg-2.0, non-commercial) to ZhengPeng7/BiRefNet (MIT); the original is kept as `pipeline.json.orig`.
- Attention is sdpa (flash-attn is only used when asked).
- NAF and natten (plugin 0.1.15): the image-conditioning models (shape_512, shape_1024, tex_1024) load the NAF feature upsampler (valeoai/NAF, Apache-2.0) with `torch.hub.load`, and its hubconf declares the CUDA-only `natten` as a dependency, which the ROCm venv cannot install. The entry puts `tools/pixal3d_shims/natten` (pure PyTorch 2-D neighborhood attention, same window rule and scaling, no stride, no causal, no position bias, slower than the CUDA kernels) FIRST on `sys.path`. The venv and the Pixal3D clone are not touched. Other launchers: `PYTHONPATH=<repo>	ools\pixal3d_shims`.
- NAF cache: the entry sets `TORCH_HOME` to config.local `pixal3d_torch_home` (default `work_dir/models/torch-hub`). torch.hub fetches the GitHub zip of valeoai/NAF into `hub/valeoai_NAF_main` and `naf_release.pth` into `hub/checkpoints` on first use, so pre-fetch both once with the network up (commands below) and the run stays offline. The entry prints whether each is present.
- NAF-off fallback: `--no-naf` on `run_pixal3d.py` (or config `pixal3d.naf` false) replaces NAF with a bicubic upsample of the DINOv3 map. `use_naf_upsample=False` is NOT used: the shipped flow models take 2 x 1024 projection channels (lr ++ hr) and would crash on 1024. The receipt then has `params.naf=false` and `params.quality_label="ROUGH / NAF-off"`; with NAF on, `params.naf=true`. A NAF-off mesh is a rough proof, never a final, and is not an A/B result against TRELLIS.

## NAF pre-fetch (once, network up, no GPU needed)
Set `T` to the folder in `pixal3d_torch_home`, then with the Pixal3D venv python (the shim satisfies hubconf's `natten` check; it loads on CPU and downloads, nothing else):
`PowerShell: $env:TORCH_HOME=$T; & <Pixal3D-312 python> -c "import sys; sys.path.insert(0, r'<repo>	ools\pixal3d_shims'); import torch; torch.hub.load('valeoai/NAF','naf',pretrained=True,device='cpu',trust_repo=True)"`
Or by hand: the zip `https://github.com/valeoai/NAF/archive/refs/heads/main.zip` extracted so that `$T\hubaleoai_NAF_main\hubconf.py` exists (the zip's top folder is NAF-main: rename it), and `https://github.com/valeoai/NAF/releases/download/model/naf_release.pth` saved as `$T\hub\checkpoints
af_release.pth`. Do not run this while a GPU job runs on a tight-RAM box without need; it uses a few hundred MB.

## The A/B (decision rule from OpenTrident Dev)
Run it with the `ab_shape` tool (skill ab-shape). Same cut-out PNG, same seed, about 10 minutes each, one GPU job at a time under the lock, TRELLIS first. Score blind on lit turntables, four criteria 0 to 10: face, hair, colour match to the sheet, bake faults. Run the bind gate on both. Pixal3D wins only if it beats TRELLIS by 1 point or more on two criteria and the bind gate is no worse.

## Multi-view (plugin 0.1.18, DRAFT: nothing here has run on a GPU)
Capability we lacked: four-view input and pixel back-projection (joining the sheet's views into one shape). Single-view mode exists only for the A/B against Trellis-3; Pixal3D stays only if the A/B or the multi-view proof earns it.

How it works. Upstream `inference_mv.py` (weights `ckpts/*_mv`, config `pipeline_mv.json`) takes V views with GIVEN cameras (`transforms.json`, no MoGe), projects every view's DINOv3 features into the same 3D grid and averages them, so a profile and a back view constrain the shape directly. `tools/pixal3d_mv.py` (CPU only) turns the sheet's cut-out PNGs into that input directory; `pixal3d_run(asset_id, views=...)` or `run_pixal3d.py <asset> --views <folder|mapping.json|inline json>` assembles and runs it. The receipt is stage `pixal3d`, label PIXAL3D, `params.mode = "multiview"`.

| sheet cell | role | azimuth | note |
|---|---|---|---|
| head_front / body_underlayer_front | front, frame 0 (main view) | 0 | must be the canonical front view |
| head_profile / body_underlayer_left | profile, subject faces image-left | 90 | facing set by eye 2026-11-01, override with `facing` in a mapping file |
| body_underlayer_right | profile, subject faces image-right | 270 | reads as a three-quarter, not a true profile: nominal angle, flagged |
| head_three_quarter | three-quarter, faces image-left | 45 (nominal) | NOT on the 90 degree orbit; upstream accepts any camera matrix and averages features, so it is included; leave out with `--no-three-quarter` / `three_quarter=false` |
| head_back / body_underlayer_back | back | 180 | |

Rig: the upstream example (20 degree fov, distance 3.1192, elevation 0, mesh_scale 1). Each cut-out is re-framed onto a transparent 1024 square, subject height 85% of the frame, box centred, so every view has the same scale (upstream never crops or rescales). Upstream takes any number of views, so a missing opposite profile is NOT invented: `--mirror-profile` / `mirror_profile=true` mirrors the one profile to make it (opt-in, flagged `mirrored_profile`; asymmetric detail is copied to the other side).

Refuses, with numbers, before any GPU work: a view without alpha or with a fully opaque alpha, views of different sizes, a view under the 1024 px short side or 60% subject rule (per view, also in the input gate hook), a pose too wide for the frame, a missing or short `_mv` weight file (the exact list, e.g. `ckpts/ss_flow_img_dit_1_3B_64_bf16_mv.safetensors`, shape 512 / 1024 and tex 1024 `_mv`, their json, `pipeline_mv.json`; the tool never downloads), a mix of head and body cells, no front cell.

Model folder and GLB export. Multi-view loads `config pixal3d.mv_model_dir` (default `work_dir/Pixal3D-run`: the patched offline `pipeline_mv.json`, which already names the local BiRefNet and DINOv3, plus a `ckpts` link to `models/Pixal3D/ckpts` holding the four `_mv` weights). `inference_mv.py` exports the GLB with the SAME cumesh call as single view, which crashes on this ROCm build (io.hip:134). Hook point: `config pixal3d.export.module` (default empty = upstream as is) is imported and its `install()` called BEFORE the upstream call; the CPU exporter `pixal3d_glb_cpu` from the export-fix PR (branch `factory/pixal-export-0.1.16`) is meant for it (set `export.module` to `pixal3d_glb_cpu`, `export.path` to its folder; label such a run PX-CPUGLB). This PR does not depend on that one: until it is merged and configured, a multi-view run on this box dies at the export. The receipt records `params.export_module`.

Known risks (sheet review of 2026-11-01): the cells disagree (head_front has loose hair, head_profile a scalp plait, head_back hanging braids; the sheet's right body cell is turned). Averaged features from inconsistent views blur; look at the result and at the receipt flags before judging the method. `pipeline_mv.json` names RMBG-2.0 (non-commercial) like `pipeline.json`: the entry rewrites it once to the local BiRefNet (original kept as `pipeline_mv.json.orig`). Every view carries alpha, so no matting runs.

Head proof (when the `_mv` weights are on disk and one GPU window exists): `pixal3d_run(asset_id="vampire-head-mv", views={"head_front": <cutout>, "head_profile": <cutout>, "head_three_quarter": <cutout>, "head_back": <cutout>}, preset="standard")`, then again with `three_quarter=false` and with `mirror_profile=true`, and compare each against the single-view TRELLIS head of 2026-11-01 (`kits_out/3d/vampire-dracula-head-v2`) on lit turntables. The A/B harness (`ab_shape`) stays single-view: a multi-view engine was not added because the A/B is one png in, one seed, two engines, and the multi-view comparison needs different inputs; compare by hand as above.

## Do / Don't
- Do LOOK at every image and output yourself before calling a stage done: open the GLB, the turntable and the stills, check for black, cropped or speckled results; a receipt only proves the tool ran.
- Do treat a 9/10 or higher result by Dom's eye as the bar for a final; anything below is a proof and is labelled a proof.
- Do treat the draft and standard presets (1024) as a BENCHMARK profile: never a final, never shown as one.
- Do give the tool an input of 2048 px or more (upscaled, cut out, subject filling the frame); the gate refuses under 1024 px.
- Do present work as lit PBR renders under a three-point light rig (key, fill, rim), never flat workbench renders.
- Do keep anything below 8/10 by Dom's eye out of the library: nothing enters the library below 8/10.
- Do version every character v1, v2, v3, each with a receipt, a contact sheet and Dom's pick per part.
- Do label the asset PIXAL3D; a Pixal3D mesh is compared with a TRELLIS output only through the A/B above.
- Don't build characters or creatures as ONE mesh: build them in parts (body, head, hands, gear) and run each through the pipeline.
- Don't use RMBG-2.0 (non-commercial) or run two GPU jobs at once.
- Don't call a run done without the receipt and a look at the output.
- Do read the receipt's `params.assumptions` flags on a multi-view run (facing by eye, nominal three-quarter angle, mirrored profile) before judging the method.
- Don't mirror a profile or guess a facing silently: both are flagged assumptions in the receipt.
