# LESSONS

RULE: append-only, dated. Any lane or Bonsai adds one entry per lesson, with the receipt path or the failure it came from. Do not edit or delete old entries; add a correcting entry instead.
Each plugin release (`plugins/factory`) folds the new lessons into the Do / Don't section of the relevant skill and bumps the version in `plugins/factory/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`. Bumps are cheap: refine often.

Entry format: `- DATE | lesson | source (receipt path or failure) | skill`

- 2026-11-01 | Do not build characters or creatures as ONE mesh: build them in parts (body plus gear parts; creatures: body, head, limbs and attachments). A fused mesh hides geometry (a coat hides the legs), cannot be rigged and fails the bind gate. Universal rule, not only for cloaks. | `receipts/vampire-dracula-front/bind-20261101T061945Z.json` (p99 stretch 29.82, compression 0.275) | bind-humanoid, trellis-part
- 2026-11-01 | Do not render Flux above 1.5 MP on this box: 2048x1536 thrashed RAM to 0.1 GB free and took hours. At 1.5 MP free RAM still touches 0.4 to 2 GB (guard kills at 2 GB). | session failure 2026-11-01 (no receipt for the 2048x1536 attempt); `receipts/vampire-head_profile-615c32/flux-20261101T050402Z.json` (0.0 GB free at 1.5 MP) | character-sheet
- 2026-11-01 | Do not run two GPU jobs at once. | two hard freezes on 2026-10-31 (no receipt; see `Documents\crash_monitor.csv` and the handoff note) | all, hook `gpu_lock_guard.py`
- 2026-11-01 | Do not skip kit pieces: the character sheet is the standard. | owner rule, `config/character_sheet_template.json` | character-sheet
- 2026-11-01 | Bind a T-posed body, not an A-posed one: the binder unbends the arms. A-pose body stretch 18.8; T-pose body stretch 1.31. | `receipts/vampire-dracula-body/bind-20261101T084011Z.json` vs `receipts/vampire-dracula-body-tpose/bind-20261101T092209Z.json` | bind-humanoid
- 2026-11-01 | The T-pose bind passes the absolute gate but fails the relative-to-control clause (compression 0.612 < 0.649 = tall control 0.721 x 0.9); `run_bind.py` does not check that clause so its receipt says ok. Read `bind_best.py` GATE_FAIL, or add the clause to `run_bind.py`. | `receipts/vampire-dracula-body-tpose/bind-20261101T092209Z.json`, `bind/controls/control_tall.metrics.json`, `bind/metrics2.py` line 156-163 | bind-humanoid
- 2026-11-01 | Do not trust a "done" without a receipt. | owner rule; `tools/cf_common.write_receipt` | all
- 2026-11-01 | Do not put literals in a run: use a kit row, a preset or config. | owner rule; `config/config.json` | all
- 2026-11-01 | Flux outputs a 256x1024 fully black decode block on some 1024x1536 renders: run `tools/fix_black_block.py` and look again. | `tools/fix_black_block.py` docstring; renders listed in `receipts/vampire-*/flux-*.json` | character-sheet
- 2026-11-01 | A second Flux image in the same process comes out black: one kit row per process. | `flux/flux_kit.py` docstring; `bench/run_kit.ps1` | character-sheet
- 2026-11-01 | Over the attention budget TRELLIS is 6 to 10 times slower (tiled attention); default budget is 28 GB; token cliff near 24k tokens. Receipts: 28 GB body 377 s and 518 s, 24 GB dressed figure 1749 s, no budget dressed figure 3183 s (different inputs, indicative only). | `receipts/vampire-dracula-body*/trellis-*.json`, `receipts/vampire-dracula-attn24/trellis-20261101T070559Z.json`, `receipts/vampire-dracula-front/trellis-20261101T061436Z.json` | trellis-part
- 2026-11-01 | The cut-out step must not receive `TRELLIS3_REMBG=none` (it reads it as a model id). Cut-out also needs the py3.11 venv (einops). | `tools/run_3d.py` comment; failed `receipts/vampire-dracula-front/cutout-20261101T051814Z.json` and `...T051900Z.json` | trellis-part

- 2026-11-01 | Always inspect and view the actual images and outputs before confirming a stage done; a receipt is not proof of quality. | black gate and hall environment images sent as done (env_hades_gate/hall), stills with head cut off (kits_out/3d/vampire-dracula-body-tpose/stills), speckled textures (receipts/vampire-dracula-front/textures-*)
- 2026-11-01 | Quality bar for finals: 9/10 or higher, comparable to the best of Unreal Engine 5.8 in 2026; below 9 is a labelled proof, not a final. | Dom's standing brief; the first dressed vampire (faceted fur, speckled texture) was a proof, not a final
