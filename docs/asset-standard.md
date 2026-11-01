# MAX GRAPHICS ASSET STANDARD (DRAFT for Dom, not committed)

Goal: the best-looking characters and props we can ship from Unity (URP) using Flux, TRELLIS-3 and Blender on the 64 GB laptop.
Where we win is asset quality and art direction, not engine tech (Nanite/Lumen are Unreal's lead).

## 1. Inputs (hard gates, from Dom)
- Concept art from Flux 3 klein (internal licence terms accepted by Dom): front, side and back as **separate files**, one seed per character, each view **>= 1024 px** on the short side, measured with PIL before any run.
- No contact sheets, no crops, no seed reruns after a failed run, hard characters built in parts (body, each head, then assemble).
- Background: cut out first with `tools/cutout_cpu.py` (BiRefNet, MIT), then generate with `TRELLIS3_REMBG=none`.

## 2. Master asset (one per character)
- `generate.py <cut-out.png> --preset hero --device rocm --single-process --profile-memory` on the native path (venv B).
- Hero = 1536 shape, 8K textures, 2M faces, normal map. Only run after the same input passes at the reduced master (2048 tex, 500k faces) and Dom has seen the stills.
- Keep: `.glb`, `.latents.pt` (so decode/bake can be redone with `--from-latents`), the receipt json and the profile table.

## 3. Two tiers from the same master
| Tier | Use | Faces | Textures | Notes |
|---|---|---|---|---|
| MASTER | stills, marketing, close-ups | up to 2M | 8K | never shipped in the build |
| GAME LOD0 | hero camera distance | 1,000,000 | 4K | `--bake-faces 1000000 --texture-size 4096` |
| GAME LOD1 | mid distance | 250,000 | 2K | |
| GAME LOD2 | far / crowd | 50,000 | 1K | |
- Mips on, compressed textures (KTX2/BasisU or platform default) via gltf-transform; each export writes a receipt.
- The tier table lives in a data row (factory.json), not in scripts. The GAME tier targets a Steam minimum spec set later.

## 4. Materials
Full PBR set: base colour, normal, roughness/metallic (packed), AO, emissive where it applies. Gamma auto stays on; check mean colour against the photo (the bake must not drift grey/dark).
Known blocker: the shoe master was dark and grainy and the robot bake came out near-neutral grey against a copper original. The clean-sample test decides input vs bake; until that is closed no asset is "done".

## 5. Rig and animation
- Bind to psyche-106 only (never the 65-bone set) with SkinTokens (the default binder once `config.json -> bind.binder` is `skin_tokens`; the labelled fallback is a donor weight transfer from nine CC0 donor bodies, receipt label `FALLBACK donor (MakeHuman weights)`); acceptance gate (same for both binders): max edge stretch <= 2.0x, min compression >= 0.5x over Idle/Walk/Attack, zero-weight vertices 0 (except toe-end bones), <= 4 influences, weights sum to 1 within 1e-3.
- Fingers: fixed pre-splayed straightening pose before binding; mitten hands only for crowd and boss bodies, not heroes.

## 6. Definition of done
Three clean engine stills (head crop, Idle, Walk, Attack at near camera) reviewed by Dom, plus the receipt and the metric table. Nothing else counts.

## 7. Engine
Unity URP for now. One A/B (URP everything-on vs HDRP in a copy of the project) happens only on Dom's go; nobody changes the live project's render pipeline.

## Open items
- Hero-preset time per asset: pending the ATTN_AUTO_GB and token-budget benchmarks on the native path.
- Texture colour fidelity (grey/dark bake) unresolved.
- Steam minimum spec not set.
