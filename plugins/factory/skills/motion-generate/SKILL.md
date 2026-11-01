---
name: motion-generate
description: "DRAFT for text-to-motion. Generate a motion clip from a sentence with NVIDIA ARDY (local weights, local Llama 3 text encoder, no network, no token) and retarget it onto the psyche-106 rig into a Mixamo-format clip GLB that the stills stage finds by name. Use when a bound character needs an Idle, Walk or Attack that Mixamo does not have."
---

> **STATUS: DRAFT, not yet run end to end.** Written and unit-tested (fake processes, no GPU) in plugin 0.1.8; the retarget script was exercised in headless Blender on CPU with a synthetic motion, never with a real ARDY generation.
>
> Unproven:
> - No real ARDY generation has been run on this machine (the GPU belongs to the character sheet while this was built). The proving run below needs a GPU window.
> - The text encoder (LLM2Vec on the local Llama 3 mirror) has not been loaded end to end: the mirror was still downloading, and the CPU load needs about 24 GB of free RAM.
> - The retarget has not been looked at on the bound vampire: foot sliding, arm roll and finger pose are unchecked. Fingers other than the thumb root follow the hand rigidly.
> - Kimodo (SOMA 77 joints) is cloned and its weights downloaded, but its path is NOT run: it pins `transformers==5.1.0` against ARDY's `5.8.1`, so it needs its own venv. A name table for it exists in `config/config.json` (`motion.retarget.skeletons.somaskel77`) and is untested.
> - The C++ motion-correction extension (ARDY's foot-skate clean-up; needs CMake and a C++17 compiler, its build downloads pybind11 and Eigen) is NOT built: CMake is not installed here. `ardy_generate.py` detects the missing extension and runs ARDY with `--no-postprocess`, so foot sliding is not cleaned up (the motion.json records `postprocess`). ARDY's own installer can skip the build with `ARDY_SKIP_MOTION_CORRECTION`.
>
> Treat every step below as a plan to verify, and write the first end-to-end receipt before removing this banner.

# motion-generate

Wrapper: `tools/run_motion.py <asset_id> "<text>"` (MCP tool `motion_generate(text, ...)`). Settings: `config/config.json -> motion`, paths in `config.local.json` (`motion_python`, `ardy_repo_dir`, `ardy_checkpoints_dir`, `text_encoders_dir`, `llama_mirror_dir`, `blender_exe`, `rig_glb`, `mixamo_clips_dir`).

## What it does
1. `tools/motion/prepare_text_encoder.py` logic points the two LLM2Vec adapters (McGill-NLP, MIT) at the LOCAL Llama 3 8B Instruct mirror by rewriting `base_model_name_or_path` in their `adapter_config.json` (the original is kept as `.orig`). No gated download, no token.
2. The Motion-312 venv (`Documents\Motion-312`, torch ROCm copied from the Trellis venv, never installed into it) runs `tools/motion/ardy_generate.py`, which runs ARDY's own `scripts/generate.py` unmodified with `TEXT_ENCODER_MODE=local`, `HF_HUB_OFFLINE=1` and `TEXT_ENCODERS_DIR`. Model: `ARDY-Core-RP-20FPS-Horizon40` (27-joint Core skeleton, 20 fps). `--encoder-device cpu` (env `TEXT_ENCODER_DEVICE=cpu`) keeps the encoder off the GPU; it needs about 24 GB free RAM.
3. Headless Blender (`tools/motion/retarget_blender.py`) retargets by name and rest direction onto psyche-106 and exports `<clip_id>.glb`: `mixamorig:*` nodes, no skin, one animation named `clip`, the format of `mixamo_clips_dir`. The Core spine has four bones and the rig three: Spine, Spine2 and Spine3 of ARDY drive Spine, Spine1 and Spine2.
4. Output `<outputs_dir>/motion/<asset_id>/<clip_id>/`: the clip, `motion.json`, `ardy_motion.npz`, `retarget_result.json`; receipt `receipts/<asset_id>/motion-*.json` (inputs hashed, licences noted).

## Using the clip in stills
`run_stills.py` finds clips by id as `<mixamo_clips_dir>/<id>.glb`. Call the tool with `install=true` (copies the clip there, never overwriting) and set `stills.clips.idle|walk|attack` in config to the clip id. Nothing in `run_stills.py` changes.

## Steps
1. `factory_status` first: GPU free, RAM at least 6 GB (the tool refuses otherwise; a text encoder on the GPU shares the 32 GB pool).
2. `motion_generate(text="A person walks forward.", asset_id="vampire-dracula-106", duration=4, clip_id="ardy-walk-v1", install=false)`.
3. Open the receipt (`status: ok`), then run `stills` with the clip configured and LOOK at the frames.
4. One sentence per clip, in the voice of the training prompts ("A person walks forward."), 1 to 10 seconds.

## Finishing the setup (needs RAM headroom, so not while the Flux sheet runs)
1. In `Documents\Motion-312` (torch ROCm already copied from the Trellis venv): `Scripts\python.exe -m pip install` ARDY's pins from its `pyproject.toml` (numpy below 2, scipy, hydra-core, omegaconf, pydantic, transformers==5.8.1, peft, einops, vector-quantize-pytorch 1.25.x, safetensors, gradio_client, tqdm, packaging, pyyaml), then add a `.pth` file with the ardy repo path (or `ARDY_SKIP_MOTION_CORRECTION=1 pip install -e ..\ardy --no-deps`).
2. Optional C++ clean-up: install CMake, load the VS Build Tools environment, `pip install -e ..\ardy --no-deps` (builds `motion_correction` on the CPU).
3. `python tools/motion/prepare_text_encoder.py <text_encoders_dir> <llama_mirror_dir> --check` (the tool applies it itself) once the mirror's four shards and index are complete.

## Proving run (needs a GPU window: the sheet must be finished and `.gpu_lock` free)
Three calls, one at a time, `factory_status` first (GPU free, RAM at least 6 GB):
- `motion_generate(text="A person stands idle, breathing.", asset_id="vampire-dracula-106", duration=4, seed=0, clip_id="ardy-idle-v1", in_place=true)`
- `motion_generate(text="A person walks forward.", asset_id="vampire-dracula-106", duration=4, seed=0, clip_id="ardy-walk-v1")`
- `motion_generate(text="A person attacks with a heavy overhead strike.", asset_id="vampire-dracula-106", duration=4, seed=0, clip_id="ardy-attack-v1", in_place=true)`
Expect per call: `status ok`, a `motion-*.json` receipt with `status ok`, `params.frames` 80, `fps` 20, `retarget.mapped_bones` 24 names, a clip in `<outputs_dir>/motion/vampire-dracula-106/<clip_id>/`, wall time dominated by loading the text encoder (14 GB bf16 on the GPU; with `encoder_device="cpu"` about 24 GB RAM). Then `install=true` (or copy), set `stills.clips` idle/walk/attack to the three ids, run `stills` on the bound vampire and look at the frames: arms not bent backwards, feet near the ground, head up.

## Evidence
- Offline tests: `plugins/factory/tests/test_motion.py`.
- Weights and licences: see `THIRD_PARTY_NOTICES.md`.

## Do / Don't

Do
- Do LOOK at every image and output yourself before calling a stage done: render the clip in stills, check the arms do not fold backwards, the feet do not float or slide, the head is up. A receipt only proves the tool ran.
- Do hold a quality bar of 9/10 or higher for every FINAL asset; a generated clip that has not been looked at on the bound character is a proof, not a final.
- Do treat the 2048 texture / 500k face profile as a BENCHMARK profile: never shown as a final (finals come from the hero re-bake).
- Do upscale every picked sheet cell to 2048 px or more before it goes into TRELLIS.
- Do present work as lit PBR renders under a three-point light rig; the stills stage is proof lighting.
- Do keep anything below 8/10 by Dom's eye out of the library and label a proof a proof.
- Do version every character v1, v2, v3, each with a receipt and a contact sheet.
- Do keep the Llama 3 notice: "Built with Meta Llama 3", and keep the NVIDIA notice in `THIRD_PARTY_NOTICES.md`.
- Do run one GPU job at a time; the tool and hooks refuse a second.

Don't
- Don't build characters or creatures as ONE mesh: build them in parts (this clip only moves the bound rig).
- Don't read, paste or put a Hugging Face token anywhere; the encoder runs from the local mirror with `HF_HUB_OFFLINE=1`.
- Don't call the Llama mirror "meta-llama": the code enforces the model id `meta-llama/Meta-Llama-3-8B-Instruct` after load only so the chat template applies; the weights come from the local mirror folder.
- Don't install into `Trellis-3-312` or touch the Factory checkout; the venv for this stage is `Motion-312`.
- Don't put literals in a run: text, duration and seed are tool arguments, everything else is config.
- Don't trust 'done' without a receipt file, and don't trust a receipt without looking at the output.
- Don't call a proof a final, and don't judge a clip from a single frame.
