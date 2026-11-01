---
name: bind-humanoid
description: Bind a TRELLIS humanoid body mesh to the psyche-106 rig with the hybrid donor binder in headless Blender and gate the result (stretch, compression, weights, influences, relative to the donor control). Use when a body GLB needs a skeleton.
---

# bind-humanoid (DRAFT, proven once at the absolute gate only)

Wrapper: `tools/run_bind.py <asset_id> <mesh.glb>`. Binder: `bind/bind_best.py`. Metrics: `bind/metrics2.py`. CPU only (Blender `--background`, no render). Paths: `config.json -> bind` and `config.local.json` (`blender_exe`, `rig_glb`, `donors_dir`).

## Evidence
- FAIL, dressed single mesh: `receipts/vampire-dracula-front/bind-20261101T061945Z.json`: p99 stretch 29.82, compression 0.275.
- FAIL, A-posed body: `receipts/vampire-dracula-body/bind-20261101T084011Z.json`: p99 stretch 18.79, compression 0.187, donor `dwarf` auto-picked.
- PASS at the absolute gate, T-posed body: `receipts/vampire-dracula-body-tpose/bind-20261101T092209Z.json`: stretch 1.31, compression 0.612, zero-weight 0, max influences 4, wall 546 s, donor `tall` forced, receipt status `ok`.
- CAVEAT found while writing this skill: that same run's `bind_best.py` summary says `GATE_FAIL`. `metrics2.py` also requires p99 stretch <= control x 1.10 and p99 compression >= control x 0.90. The `tall` control (`bind/controls/control_tall.metrics.json`) is stretch 1.355 and compression 0.721, so the compression floor is 0.649 and 0.612 misses it. `run_bind.py` does not check the relative clause, so its receipt reads `ok`. The T-pose body passes the absolute gate and NOT the relative-to-control gate. Unresolved; do not call it a clean pass.

## Steps
1. Input: a body-only, base-layer, **T-posed** humanoid mesh from `trellis-part` (no coat, cloak or mantle). Gear is bound separately; that is not covered here.
2. Force the donor (auto pick uses chest width and misreads A-posed arms): `$env:BIND_FORCE_DONOR = "tall"`. Donors live in `donors_dir`; controls for `tall` and `dwarf` exist in `bind/controls`.
3. Run: `python tools\run_bind.py <asset_id> <outputs_dir>\3d\<asset_id>\mesh.glb`. It checks Blender, rig and donors exist, runs `bind_best.py` (hybrid binder, then donor binder; each Blender call killed at 290 s), measures each with `metrics2.py --control`, exports `bound.glb` (objects Rig and Body) and writes `receipts/<asset_id>/bind-<utc>.json`.
4. Gate, area-weighted over the Idle, Walk and Attack clips, only edges with rest length of 5 mm or more:
   - p99 stretch <= 2.0
   - p99 compression ratio >= 0.5
   - zero-weight vertices 0, at most 4 influences, weight sums within 1e-3 (Toe_End may be unweighted)
   - relative to the donor control: stretch <= control x 1.10 and compression >= control x 0.90 (checked by `bind_best.py`, NOT by `run_bind.py`)
5. Read `bind_work/*.metrics.json` and `violating_edges_by_region` (hands, feet, neck, shoulders, crotch) to see where it fails.

## The parts rule
Body bound strict (the gate above). Gear parts (coat, cloak, boots) are bound looser through `config.json -> gear` (p99 stretch <= 3.0, `bind/bind_gear.py`, `tools/run_gear.py`; unproven, no skill yet). Never bind a fused clothed mesh.

## Time
Bind wall 305 s on a failing run, 546 s on the T-pose run; lowest free RAM 2.6-2.8 GB. The donor control is built once per donor and cached.

## Do / Don't
Do
- Do LOOK at every image and output yourself before calling a stage done: open the picture or render, check it is not black, cropped, mis-framed, speckled or mismatched to the reference, and check the numbers. A receipt only proves the tool ran. (Learned 2026-11-01: black images sent as done; stills with the head cut off; a speckled texture.)
- Do hold a quality bar of 9/10 or higher, comparable to the best of Unreal Engine 5.8 in 2026, for every FINAL asset, with NO compromise on quality, visuals or effects. Score it honestly against the reference sheet. Anything below 9 is a proof, not a final: label it as a proof, say what is wrong, and do not ship it as finished.
- Do bind a T-posed body (T-pose body: stretch 1.31).
- Do force the donor with `BIND_FORCE_DONOR`.
- Do read the `bind_best.py` gate (including the relative clause), not only the receipt status.
- Do bind gear as separate parts with the looser gate.
- Do keep the receipt of every bind, failed ones too.

Don't
- Don't bind an A-posed body: the binder unbends the arms (A-pose body failed, stretch 18.8).
- Don't build characters or creatures as ONE mesh: build them in parts (body + gear parts; for creatures the body, head and each limb or attachment as parts). A fused mesh hides geometry (a cloaked coat hides the legs), cannot be rigged and fails the bind gate (the fused vampire bound with stretch 29.8; the T-posed body part bound with 1.31). Applies to humanoids, beasts and anything animated.
- Don't rely on the auto donor pick for an A-pose.
- Don't run another GPU job meanwhile (one at a time; two hard freezes on 2026-10-31).
- Don't assume paths are in config only: `bind/*.py` still hold their own Blender, donor and rig paths (known debt); check them when moving machines.
- Don't trust "done" without a receipt, and don't call a bind passed on `run_bind.py` status alone.
