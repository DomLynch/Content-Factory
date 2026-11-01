# Planned skills (NOT written)

Rule: a skill is written only from a stage that has run end to end at least once with a receipt. These have not, so they have no SKILL.md. (This file sits in `skills/` but is not a skill: it has no frontmatter and no folder.)

| Planned skill | Why not yet |
|---|---|
| bind-beast | No quadruped or beast bind has run; only the humanoid hybrid binder has receipts. |
| assemble-character | `tools/assemble_character.py` exists and `config.json -> assemble` is set, but no assembled-character receipt exists. |
| stills-and-turntable | Only `stills` receipts for single bound bodies (`vampire-dracula-body-tpose`, `vampire-dracula-front`); no turntable, no multi-clip render proven. |
| publish-to-library | `tools/publish.py` and `publish/vampire-dracula-106.json` exist; no publish receipt has run end to end. |
| environment | `kits/hades_gate_kit.json` and `manifests/hades_gate.json` have Flux receipts, but no environment goes through 3D to a placed set. |
| hero-rebake | `run_3d.py --from-latents` and the `hero_rebake` preset are coded, never run. |

Other unwritten sub-stages: gear binding (`bind_gear.py`), texture repair (`tools/run_textures.py`, one receipt, on a non-passing bind), mesh clean (`run_clean.py`).
