---
name: motion-generate
description: "DRAFT for text-to-motion. Generate a motion clip from a sentence with NVIDIA ARDY (default) or NVIDIA Kimodo (backend kimodo, optional sparse constraints, own venv), both with local weights and the local Llama 3 text encoder (no network, no token), and retarget it onto the psyche-106 rig into a Mixamo-format clip GLB that the stills stage finds by name. Use when a bound character needs an Idle, Walk or Attack that Mixamo does not have."
---

> **STATUS: DRAFT, not yet run end to end.** Written and unit-tested (fake processes, no GPU) in plugin 0.1.8 (ARDY) and 0.1.12 (Kimodo backend, local text-encoder checks); the retarget script was exercised in headless Blender on CPU with a synthetic motion, never with a real ARDY or Kimodo generation. Nothing in this skill has run on a GPU.
>
> Unproven:
> - No real ARDY generation has been run on this machine (the GPU belongs to the character sheet while this was built). The proving run below needs a GPU window.
> - The text encoder (LLM2Vec on the local Llama 3 mirror) has not been loaded end to end: the mirror was still downloading, and the CPU load needs about 24 GB of free RAM.
> - The retarget has not been looked at on the bound vampire: foot sliding, arm roll and finger pose are unchecked. Fingers other than the thumb root follow the hand rigidly.
> - Kimodo (backend `kimodo`, SOMA 77 joints, 30 fps) is wired in 0.1.12 but has never run: its venv `Kimodo-312` does not exist yet (Kimodo pins `transformers==5.1.0`, ARDY needs `5.8.1`, so it cannot share `Motion-312`), the weights are downloaded but their hash is not part of this plugin, and the SOMA -> psyche-106 name table (`SKELETON_TABLES` in `retarget_blender.py`, same content in `config/config.json`) is UNRUN. The retarget assumes the source's rest pose has identity global rotations (true for the ARDY export by design); for SOMA that is not checked: look for twisted limbs on the first run.
> - Kimodo constraints are validated for shape and range only (`tools/run_motion.py`); whether the model follows them, and whether the units and canonical origin below match the retargeted clip, is unproven.
> - The LLM2Vec encoder path is confirmed by reading ARDY's and Kimodo's code (below), never by loading it: Llama shards 2 and 3 were still downloading when 0.1.12 was written.
> - The C++ motion-correction extension (ARDY's foot-skate clean-up; needs CMake and a C++17 compiler, its build downloads pybind11 and Eigen) is NOT built: CMake is not installed here. `ardy_generate.py` detects the missing extension and runs ARDY with `--no-postprocess`, so foot sliding is not cleaned up (the motion.json records `postprocess`). ARDY's own installer can skip the build with `ARDY_SKIP_MOTION_CORRECTION`.
>
> Treat every step below as a plan to verify, and write the first end-to-end receipt before removing this banner.

# motion-generate

Wrapper: `tools/run_motion.py <asset_id> "<text>" [--backend ardy|kimodo] [--constraints file.json]` (MCP tool `motion_generate(text, backend, constraints, ...)`). Settings: `config/config.json -> motion`, paths in `config.local.json` (`motion_python`, `ardy_repo_dir`, `ardy_checkpoints_dir`, `kimodo_python`, `kimodo_repo_dir`, `kimodo_checkpoints_dir`, `text_encoders_dir`, `llama_mirror_dir`, `blender_exe`, `rig_glb`, `mixamo_clips_dir`). The three `kimodo_*` keys default under `work_dir` (venv `<work_dir>\Kimodo-312`, repo `<work_dir>\kimodo`, weights `<work_dir>\models\kimodo`) when absent.

## Backends
| | `ardy` (default) | `kimodo` |
|---|---|---|
| Model | `ARDY-Core-RP-20FPS-Horizon40`, Core 27 joints, 20 fps | `Kimodo-SOMA-RP-v1.2`, SOMA 77 joints (the model works on 30 and converts to 77 on output), 30 fps |
| Venv | `Motion-312` (transformers 5.8.1) | `Kimodo-312` (transformers 5.1.0), own venv, never created by the plugin |
| Script | `tools/motion/ardy_generate.py` (runs ARDY `scripts/generate.py`) | `tools/motion/kimodo_generate.py` (runs `kimodo.scripts.generate`, sets `CHECKPOINT_DIR` so nothing is downloaded) |
| Input | text | text plus optional constraints |
| Name table | `cskel27` | `somaskel77` (UNRUN): thigh `Leg`, shin `Shin`, spine `Spine1`/`Spine2`/`Chest` onto the rig's three spine bones, `Neck1`, fingers 1 to 3 |
| Clip id prefix | `ardy-` | `kimodo-` |

Both write the same `motion.json` (schema 1) that the one retarget script reads, the same clip format and the same receipt (`params.backend`, `model.id`, licences, inputs hashed). Gates are identical: GPU lock (`device=gpu`), RAM gate, `plugin_guard` (raw `kimodo_generate.py` calls are refused like `ardy_generate.py`; hooks cover Bash and PowerShell).

### Kimodo text and constraints
- Kimodo splits the text at periods and makes one segment per sentence, each `duration` seconds (30 fps). The tool refuses when sentences times duration exceed `max_duration_s` (10 s). Use one sentence per clip.
- `constraints` is a path to a Kimodo `constraints.json` or the list itself (kimodo `docs/source/user_guide/constraints.md`). Types: `root2d` (`smooth_root_2d` [T,2] metres, optional `global_root_heading` [T,2] cos/sin: a path or waypoints), `fullbody` (keyframes: `root_positions` [T,3] with y the absolute hip height, `local_joints_rot` [T,77 or 30,3] axis-angle), `left-hand`, `right-hand`, `left-foot`, `right-foot`, `end-effector` (same fields, plus `joint_names`). `frame_indices` are 0-based, unique, below `duration x 30`. Y-up, metres, relative to the canonical start (root at x,z = 0,0 at frame 0, facing +Z). Limits: 8 sets, 16 frames per set, 400000 bytes (config `motion.constraints`). Constraints are refused for ARDY and for multi-sentence text.
- Example: `constraints=[{"type": "root2d", "frame_indices": [0, 60, 119], "smooth_root_2d": [[0, 0], [0.8, 0], [1.6, 0.2]]}]` with `text="A person walks forward."`, `duration=4`.
- Kimodo's own foot-skate post-process needs the C++ `motion_correction` extension; without it `kimodo_generate.py` passes `--no-postprocess` and `motion.json` records `postprocess: false`.

### Refusals (nothing is started; the line names the path or the numbers)
- `Kimodo venv python not found: <work_dir>\Kimodo-312\Scripts\python.exe (the Kimodo-312 venv needs kimodo's own pins, transformers==5.1.0 against ARDY's 5.8.1; this plugin never creates it)`
- `Kimodo weights <path>\model.safetensors are N bytes, expected 1133177724 (download unfinished or wrong file)` and `Kimodo weights file missing: <path>`
- `mirror shard model-00002-of-00004.safetensors is N bytes, the repo's is 4999802720 (download unfinished or wrong file)` (also `mirror shard missing`, `adapter file missing: McGill-NLP/<adapter>/adapter_model.safetensors`, `adapter file is empty`)
- `the text encoder on the CPU needs about 24 GB free RAM (16 GB of bf16 weights) and N GB is free` (`encoder_device=cpu`, also `TEXT_ENCODER_DEVICE=cpu` for Kimodo)
- `constraints ...`: type, frame range, shape and size problems are listed one by one.

## What it does
1. `tools/motion/prepare_text_encoder.py` logic points the two LLM2Vec adapters (McGill-NLP, MIT) at the LOCAL Llama 3 8B Instruct mirror by rewriting `base_model_name_or_path` in their `adapter_config.json` (the original is kept as `.orig`). No gated download, no token. 0.1.12 read both generators' code to confirm this is exactly what they expect: ARDY (`ardy/model/llm2vec`) and Kimodo (`kimodo/model/llm2vec`) vendor the same loader. With `TEXT_ENCODERS_DIR` set, the base is `<dir>/McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp` (a LoRA adapter folder with its own `config.json` and tokenizer; `LLM2Vec.from_pretrained` loads the model class from it, then, because it carries a `peft_config`, applies and merges that adapter on the Llama named by its `adapter_config.json`) and the peft model is `.../LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised`, applied on top. So Llama 3 8B Instruct plus the `mntp` adapter plus the `mntp-supervised` adapter, all read from local folders. Config keys: `text_encoders_dir` and `llama_mirror_dir` (config.local), `motion.text_encoder_name` (`meta-llama/Meta-Llama-3-8B-Instruct`) and `motion.llama_shard_bytes` (config).
   - Chat-template name: LLM2Vec only applies the Llama 3 chat template when `config._name_or_path` is exactly `meta-llama/Meta-Llama-3-8B-Instruct`. `tools/motion/encoder_patch.py` (shared by both generators, passed `--llama-id` from `motion.text_encoder_name`) forces and asserts that name after loading, and keeps a `cpu` encoder on the CPU. The weights still come from the mirror folder.
   - Size gate: before anything starts the four Llama shards must exist and match the repo's sizes (4976698672, 4999802720, 4915916176, 1168138808 bytes) and both adapters must exist and be non-empty; otherwise the tool refuses with the numbers.
2. The Motion-312 venv (`Documents\Motion-312`, torch ROCm copied from the Trellis venv, never installed into it) runs `tools/motion/ardy_generate.py`, which runs ARDY's own `scripts/generate.py` unmodified with `TEXT_ENCODER_MODE=local`, `HF_HUB_OFFLINE=1` and `TEXT_ENCODERS_DIR`. Model: `ARDY-Core-RP-20FPS-Horizon40` (27-joint Core skeleton, 20 fps). `--encoder-device cpu` (env `TEXT_ENCODER_DEVICE=cpu`) keeps the encoder off the GPU; it needs about 24 GB free RAM.
3. Headless Blender (`tools/motion/retarget_blender.py`) retargets by name and rest direction onto psyche-106 and exports `<clip_id>.glb`: `mixamorig:*` nodes, no skin, one animation named `clip`, the format of `mixamo_clips_dir`. The Core spine has four bones and the rig three: Spine, Spine2 and Spine3 of ARDY drive Spine, Spine1 and Spine2.
4. Output `<outputs_dir>/motion/<asset_id>/<clip_id>/`: the clip, `motion.json`, `ardy_motion.npz` (`kimodo_motion.npz` and `constraints.json` for Kimodo), `retarget_result.json`; receipt `receipts/<asset_id>/motion-*.json` (inputs hashed, licences noted).

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

## Finishing the Kimodo setup (not done; needs RAM headroom and a decision on where torch comes from)
1. Create `<work_dir>\Kimodo-312` (py3.12) with torch the same way `Motion-312` got it (copied from the Trellis venv, never installed into it), then kimodo's pins from its `pyproject.toml` (`transformers==5.1.0`, `peft>=0.18`, ...) and `pip install -e ..\kimodo --no-deps` or a `.pth` file with the repo path. Do NOT install into `Motion-312` or `Trellis-3-312`.
2. Optional: the C++ `motion_correction` extension (kimodo `MotionCorrection`) for the foot-skate post-process.
3. Set `kimodo_python` in `config.local.json` only if the venv is not at the default path.

## Proving run (needs a GPU window: the sheet must be finished and `.gpu_lock` free)
Three calls, one at a time, `factory_status` first (GPU free, RAM at least 6 GB):
- `motion_generate(text="A person stands idle, breathing.", asset_id="vampire-dracula-106", duration=4, seed=0, clip_id="ardy-idle-v1", in_place=true)`
- `motion_generate(text="A person walks forward.", asset_id="vampire-dracula-106", duration=4, seed=0, clip_id="ardy-walk-v1")`
- `motion_generate(text="A person attacks with a heavy overhead strike.", asset_id="vampire-dracula-106", duration=4, seed=0, clip_id="ardy-attack-v1", in_place=true)`
Expect per call: `status ok`, a `motion-*.json` receipt with `status ok`, `params.frames` 80, `fps` 20, `retarget.mapped_bones` 24 names, a clip in `<outputs_dir>/motion/vampire-dracula-106/<clip_id>/`, wall time dominated by loading the text encoder (14 GB bf16 on the GPU; with `encoder_device="cpu"` about 24 GB RAM). Then `install=true` (or copy), set `stills.clips` idle/walk/attack to the three ids, run `stills` on the bound vampire and look at the frames: arms not bent backwards, feet near the ground, head up.

Kimodo proving run (only after the `Kimodo-312` venv exists and the Llama mirror is complete): `motion_generate(text="A person walks forward.", asset_id="vampire-dracula-106", duration=4, seed=0, clip_id="kimodo-walk-v1", backend="kimodo")` expects `params.backend` `kimodo`, `frames` 120, `fps` 30, `skeleton` `somaskel77`. Then once with a `root2d` constraint and look at whether the clip follows the path.

## Evidence
- Offline tests: `plugins/factory/tests/test_motion.py` (ARDY) and `test_motion_kimodo.py` (Kimodo backend, constraints, size and encoder gates, hooks).
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
- Don't install into `Trellis-3-312` or touch the Factory checkout; the venv for ARDY is `Motion-312` and for Kimodo `Kimodo-312` (their transformers pins differ, so never one venv for both).
- Don't put literals in a run: text, duration and seed are tool arguments, everything else is config.
- Don't trust 'done' without a receipt file, and don't trust a receipt without looking at the output.
- Don't call a proof a final, and don't judge a clip from a single frame.
