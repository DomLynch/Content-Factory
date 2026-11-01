---
name: cloth
description: "Simulated cloth drape of a gear part (cloak first): the part becomes a Blender Cloth object, the T-posed bound body a Collision object, the neck and shoulder vertices are pinned, it is simulated until it settles and the settled mesh becomes the part's new rest pose (tool cloth). Use when a cloak, coat tail or cape should hang and fold on the body instead of being a baked one-piece shape."
---

> **STATUS: DRAFT, not yet run end to end.** The stage, hooks and offline tests exist. Two real runs on a PROCEDURAL proof cloak (a clean UV grid sized to the vampire's T-posed body; the `cloak_alone` row was never built into a mesh, so no real cloak part exists) were aborted by the sim's own RAM floor while Flux loaded: no settled mesh, penetration number or lit render exists yet. Also unproven: binding the settled mesh with `run_gear.py`. Per-clip simulation (`mode=clip`) is a stub that refuses with NotImplemented.

Capability we lacked: simulated cloth drape (cloth that hangs and settles on the body instead of a baked one-piece shape).

# Cloth: drape a gear part on the body

## What it does (CPU Blender, no GPU, no GPU lock)
`cloth(asset_id, part, body_glb, mesh, preset, mode, max_frames)` runs `tools/run_cloth.py` -> `tools/cloth/cloth_blender.py` in headless Blender 5.2:
1. The bound body GLB goes to REST (T) pose; its mesh is the **Collision** object (decimated proxy, `cloth.collision.proxy_faces`; the full-resolution body is kept only to measure penetration). Collision: cloth standoff `cloth_distance_min_m` 12 mm, 4 collision steps, body outer thickness 8 mm, friction 8, damping 0.6; self collision on (6 mm, friction 5).
2. The part is a **Cloth** object with a preset (`silk` and `cotton` are Blender's own preset values; `wool` is ours, 0.45 kg/m2: mass, tension/compression/shear stiffness, bending, damping, quality 5 steps). A part over 60k faces is decimated to about 40k first (reported), vertices are merged across glTF seam splits (a cloth must be connected; UVs are per loop and survive).
3. **Pin rule**: a vertex is pinned (vertex group weight 1, pin stiffness 1) when it is within 0.10 m below the highest point of the pin bones (`shoulder` and `neck` in the lower-case bone name, minus `twist`, `helper`, `cape`) AND within 0.22 m of a pin bone segment. Count and rule go into the receipt.
4. Frame by frame at 24 fps (disk cache in a temp folder under `cloth_work`, deleted after the run). **Settled** = the p99 speed of the free vertices stays under 0.02 m/s for 8 frames in a row after at least 40 frames; otherwise it stops at `sim.max_frames` (240) and says `settled: false`.
5. The settled coordinates are written into the original mesh (same as applying the cloth modifier at that frame): UVs and material kept, normals recomputed, GLB `<outputs_dir>/3d/<asset_id>/cloth_<part>.glb`, re-imported and counted.
6. **Penetration** of free cloth vertices and face centres into the FULL-resolution body, reported as `max_free_m`, p99 and counts. The body is not watertight, so a sample is inside only when at least 4 of 6 axis rays cross it an odd number of times; the nearest-normal sign test is reported next to it and is NOT reliable on this body. Gate: `max_free_m` <= 0.02 m, no NaN, counts and UVs unchanged, enough pins, not exploded.

Flow: `bind` the body -> build the part (TRELLIS) -> `cloth` (rest pose) -> `run_gear.py` on `cloth_<part>.glb` (skin it, helper `cape` bones blend in) -> `assemble_character`. Cloth is a REST-POSE tool: the skinned cloak still follows the skeleton by weights, it does not re-simulate per clip.

## Guards before Blender starts (CPU, but the box is tight)
Free RAM >= 10 GB (`cloth.guard.min_free_ram_gb`), the exclusive lock file (`config.local blender_lock`, default `<work_dir>/blender_job.lock`; waits 10 minutes while its pid is alive, then gives up), and no Flux START or model-load line in the newest queue log of `logs_dir` in the last 60 s (a Flux cell needs about 14 GB at load). The sim aborts at `limits.guard_min_free_ram_gb` (3 GB) and says so. The hook `ram_gate.py` covers the tool and `plugin_guard.py` covers a raw `run_cloth.py`.

## Do / Don't
- Do LOOK at every image and output yourself before calling a stage done: render the settled cloth on the body from the front, side and back; a receipt and a penetration number only prove the tool ran.
- Do treat a 9/10 or higher result by Dom's eye as the bar for a final; a drape that clips, floats or hangs like a plank is a proof, not a final.
- Do treat the default settings (wool preset, 25k-face collision proxy, 240 frames) as a BENCHMARK profile: never a final, never shown as one.
- Do give the part and the body the same scale and T-pose placement, and build the cloak from a source image of 2048 px or more (upscaled, cut out, subject filling the frame).
- Do present the result as a lit PBR render under a three-point light rig (key, fill, rim), never a flat workbench render.
- Do keep anything below 8/10 by Dom's eye out of the library: nothing enters the library below 8/10.
- Do version every character v1, v2, v3, each with a receipt, a contact sheet and Dom's pick per part.
- Do read `sim.settled` and `penetration.max_free_m` in the receipt; if it did not settle the mesh is the last frame, not a rest state.
- Don't build characters or creatures as ONE mesh: build the cloak as its own part (body, head, hands, gear), then drape it; a fused one-piece figure cannot be simulated.
- Don't run it while another Blender job or a Flux model load is going, don't lower the 10 GB floor, and don't use the GPU: the stage has no GPU lock because it needs none.
- Don't trust the nearest-normal penetration number on this body, don't call a run done without the receipt and a look at the render, and don't claim per-clip cloth: `mode=clip` is NotImplemented.
