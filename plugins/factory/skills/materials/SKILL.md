---
name: materials
description: "Scanned CC0 PBR materials (Poly Haven, ambientCG) on a gear part, lit under the A/B three-point rig and shown side by side with its baked texture (tool materials): real measured surfaces instead of baked texture. Use when a coat, cloak or boots part needs velvet, fur or leather that reads as the real thing."
---

> **STATUS: DRAFT, not yet run end to end.** The mapping, downloader, guards, Blender script, tool, hooks and offline tests exist; the sets are downloaded and verified, and the first proof (vampire coat, scanned damask vs baked) is described in the PR. Nothing here has produced a 9/10 final.

Capability we lacked: scanned PBR materials (real measured surfaces instead of baked texture).

# Materials: scanned CC0 PBR on a gear part

## What it does
`materials(asset_id, glb, part, swatch)` runs `tools/run_materials.py`: ONE gear-part GLB is rendered twice in headless Blender (Cycles on CPU), once with its baked texture and once with the scanned set that the swatch resolves to (`config/materials.json`), then the frames are joined side by side (baked LEFT, scanned RIGHT) and a `materials` receipt is written. The camera, the three-point rig (key, fill, rim), the neutral world, the 1.8 m normalisation, resolution and samples are the A/B turntable's (`config/config.json` ab.render, `tools/ab/turntable_blender.py`): there is no second light rig. No GPU, so no GPU lock; a RAM gate and a Blender lock apply.

## Swatch to scanned set (`config/materials.json`)
crimson_damask_velvet = quatrefoil_jacquard_fabric (Poly Haven), velvet_lining = velour_velvet (Poly Haven), black_fur = curly_teddy_natural (Poly Haven, dark tint), black_leather = Leather032 (ambientCG), gold_embroidery = Metal034 and gold_metal = Metal042B (ambientCG), steel = Metal009 (ambientCG, dark tint). pale_skin, dark_hair and lace have NO CC0 scan: the tool refuses them and says so; keep the baked texture for those. All sets are CC0, 2K, OpenGL normals. Sets live in `<materials_dir>/<asset_id>/` with a `manifest.json` (source, licence, URL, sha256 per file, map conventions).

## How to run
1. `materials(character_spec="characters/vampire-dracula.json")` prints the plan: part, swatch, set, fit, and whether the set is on disk.
2. If a set is missing, ask the owner: `tools/materials/fetch_materials.py` downloads (IPv4, one file at a time, resume, checksum verified) and needs `--approved`; it is not a plugin tool and never runs unasked.
3. `materials(asset_id, glb, part)` for coat, cloak or boots. It waits (bounded) for 10 GB of free RAM, for any Flux model load (60 s window) and for `blender_job.lock`, then renders.
4. Open `side_by_side_turn_*.png` and look at the weave, the pile and the specular at grazing light before calling it better or worse.

## Tiling assumption
TRELLIS atlas UVs have no common scale, so the part gets a second UV layer by box projection in metres: one texture repeat covers the scan's real sample size (`tile_m`). Metres come from `parts.<part>.part_height_m` (coat 1.05, cloak 1.45, boots 0.55 in a 1.8 m character). If a weave looks too coarse or too fine, fix the part height or `tile_m` in the mapping, never the UVs by hand.

## Do / Don't
- Do LOOK at every image and output yourself before calling a stage done: open the side-by-side frames; a receipt only proves the tool ran.
- Do treat a 9/10 or higher result by Dom's eye as the bar for a final; this stage makes a better proof, not a final by itself.
- Do treat 2K scans on a 2048/500k mesh as a BENCHMARK profile: never a final, never shown as one; finals need the hero bake and 4K sets.
- Do feed parts of 2048 px or more source quality and keep the subject filling the frame, as every other stage requires.
- Do present work as lit PBR renders under a three-point light rig (key, fill, rim); this stage reuses the A/B rig and must never get its own.
- Do keep anything below 8/10 by Dom's eye out of the library: nothing enters the library below 8/10.
- Do version every character v1, v2, v3, each with a receipt, a contact sheet and Dom's pick per part.
- Do record licence, source URL and sha256 for every scan; only CC0 sets enter `config/materials.json`.
- Don't build characters or creatures as ONE mesh: build them in parts (body, head, hands, gear) and give each part its own material.
- Don't download anything without the owner's yes, don't run a Blender job under 10 GB of free RAM, during a Flux model load or without `blender_job.lock`, and don't touch the GPU.
- Don't invent a scan for pale_skin, dark_hair or lace, and don't call a run done without the receipt and a look at the output.
