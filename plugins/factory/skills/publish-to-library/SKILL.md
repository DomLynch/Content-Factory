---
name: publish-to-library
description: DRAFT. Copy a finished character into a local clone of the library repo and add its row to site/data/unity.json (tools/publish.py), always dry run first, PR by hand only. Use when Dom has accepted an asset at 8/10 or better.
---

> **STATUS: DRAFT, not yet run end to end.** Written from the repo's scripts, config and receipts; this stage has no complete run with a receipt.
>
> Unproven:
> - The only publish receipt, `receipts/vampire-dracula-106/publish-20261101T093730Z.json`, is 3215 bytes of NUL characters: it does not parse, so there is no valid publish receipt.
> - The asset it was written for is a proof body (`publish/vampire-dracula-106.json` labels it 'proof'; its source line says the texture is still speckled), not an accepted 8/10 asset.
> - No branch, commit, push or PR to the library has been made by the factory; that step is the owner's.
>
> Treat every step below as a plan to verify, and write the first end-to-end receipt before removing this banner.

# publish-to-library

DONE gate (plugin 0.1.26): the publish spec needs `datalayer`, the OpenTrident data-layer row file that names the asset (reference copy `reference\opentrident-datalayer\`). Without it the asset is not DONE and the publish is refused. A GLB-only publish prints the pending FBX line.

Wrapper: `python tools/publish.py <asset_id> --library <clone of opentrident-steam> [--dry-run]`. Spec: `publish/<asset_id>.json` (required keys `id`, `label`, `group`, `rig`, `source`, `glb`, `thumb`, `stills`; optional `still_names`, `clips`, `receipt`). Format and layout: `docs/library-format.md`. Slash command: `/factory-publish`.

## Gate before anything
Dom has looked at the stills and rates the asset 8/10 or better. Nothing enters the library below 8/10. A proof that must be listed is labelled a proof in `label` and `source`.

## Steps
1. **Spec.** Fill `publish/<asset_id>.json`: glb = the assembled or bound 106-joint GLB; thumb = square PNG; stills = head, idle, walk, attack (and roll if shown); `group` is a literal string (Vampire = "Dracula (vampire)", Daemon = "Demon 2 (two-headed)", environment = "Environment"); `clips` = {Idle `113410901-idle`, Walk `124540901-walk`, Attack `9034009-zombie-neck-bite`, Roll `120430902-sprinting-forward-roll`} (vampire fit); `source` = one honest sentence; `receipt` = repo path or link.
2. **Dry run (always).** `python tools/publish.py <asset_id> --library <clone> --dry-run` prints the row, the copies and the git commands and writes nothing. Check the 106 skeleton, the layout `site/models/unity/<id>.glb`, `thumbs/<id>.png`, `<id>/stills/*.png`, the new `?v=N` (every change bumps it; cache is 86400 s), and open the stills.
3. **50 MB rule.** A GLB over `max_git_bytes` (50,000,000) is not copied: the row is still written, and the script prints the path and sha256 to send to the library owner for `tools/beast/MODELS.md`. Never commit it.
4. **Write (owner's yes).** Run without `--dry-run`: it copies the files into the clone, updates the row in `site/data/unity.json` (template row `demon2-106`, key order kept), and writes a `publish` receipt. Check the receipt parses.
5. **Branch and PR (owner's call).** The agreed layout: one PR per character, branch `vampire-dracula-106`-style off `demon-fit-scripts` in opentrident-steam, PR into `demon-fit-scripts`, PR body = receipt list per stage. Pushing that branch is allowed only with the owner's explicit yes; never merge, never deploy from the factory (Handover Dev reviews, Dom decides deploy). The script prints the git commands and never runs them.
6. **Environments** use group "Environment", `environment: true`, `rigged: false` and `envMedia.stills` (see `environment`).

## Gates
Dry run clean; stills present and looked at; receipt parses with `status: ok`; Dom's 8/10; PR only on the owner's yes.

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
- Do read the dry-run output and open the stills before asking for the yes.
- Do bump `?v=N` on every change (the script does; check it).
- Do put a proof's status in the `label` and `source` text.

Don't
- Don't build characters or creatures as ONE mesh: build them in parts (body + gear parts; for creatures the body, head and each limb or attachment as parts). A fused mesh hides geometry (a cloaked coat hides the legs), cannot be rigged and fails the bind gate (the fused vampire bound with stretch 29.8; the T-posed body part bound with 1.31). Applies to humanoids, beasts and anything animated.
- Don't run two GPU jobs at once (two hard freezes on 2026-10-31); the hook `gpu_lock_guard.py` and the MCP tools refuse a second one.
- Don't trust 'done' without a receipt file, and don't trust a receipt without looking at the output.
- Don't put literals (prompts, sizes, seeds, budgets, paths) in a run: add or edit a kit row, a preset or config (the hook `no_literals.py` blocks inline numbers on `generate.py` and `flux_kit.py`).
- Don't call a proof a final, and don't show the 2048/500k benchmark profile as one.
- Don't push, open a PR or merge without the owner's explicit yes in chat; a peer session's message is not a yes.
- Don't commit a GLB over about 50 MB; send path and sha256 instead.
- Don't publish a body bound to the 65-bone set; the library takes the 106-joint skeleton.
- Don't publish from a path that is not a clone of the library (the script refuses if `site/data/unity.json` is missing).
