# Build order: a chain of gates (plugin 0.1.24)

The vampire that failed skipped stages: a fused mesh from one cell, no sheet, no cut-outs. The order is now enforced by `plugins/factory/lib/cf_chain.py`, not only described.
Each stage refuses to run without the previous stage's PASS evidence on disk. The refusal names the gate that is open.

| # | Gate | Evidence on disk | Enforced where |
|---|---|---|---|
| 1 | Sheet complete | `manifests/<kit>.json`: every cell PASS, or WEAK with a note (a `mirror_of` cell counts through its source); none REJECT or unreviewed; a consistency pass recorded (`manifest.py --consistency review.json`) | `run_3d.py`, `run_character.py`, hooks (`run_3d`, `trellis_run`) |
| 2 | Dom's yes on the contact sheet | `receipts/<character_id>/approval-*.json`, written by the Factory with `tools/record_approval.py <kit> --by Dom` after he said yes in chat; refused while gate 1 is open | same |
| 3 | Cut-outs | per part `receipts/<part asset>/cutout-*.json`, status ok, output png passes the input gate (subject fill >= `gates.min_subject_frac`, heads >= `gates.min_head_px`, portrait or square) | `run_3d.py --cutout-png` |
| 4 | Part meshes | `run_3d.py` / `trellis_run` start only with 1 and 2 (and 3 with `--cutout-png`) | same |
| 4b | Body mesh after head mesh | `trellis-*.json` ok for the head part before the body part (`cf_chain.order_reasons`); the rest of the fixed order is in `docs/vampire-v2-rules.md` | same |
| 5 | Cleanup with the face-loss cap | pending: the cleanup stage tool (asm_stage_a port) does not exist yet | not gated yet |
| 6 | Capped assembly | pending | not gated yet |
| 7 | Bind with the landmark rig check against psyche-106-v3 | pending (the bind gate checks stretch, compression, weights and influences today) | not gated yet |
| 8 | QC, then lit stills, head crop, three clips | `qc_gate.py` (stills wait for a passing qc receipt), read-back gate | hooks |
| 9 | v1 contact sheet to Dom, iterate on his picks | pending | not gated yet |

`run_character.py --dry-run` prints which gates are already satisfied and which are pending. An asset belongs to a character when `characters/*.json` has a `character_id` (or
`asset_prefix`) that is the asset id or a prefix of it; the spec names its sheet kit in `sheet_kit`. A spec without `sheet_kit` is reported as NOT EVALUATED and refused. An asset that matches
no character spec (a benchmark, an ad-hoc test) is outside the chain. `FACTORY_RAW=<reason>` lets one run through and is logged; the MCP tools have no such door.

Vocabulary: the verdict words are the review manifests' own, PASS / WEAK / REJECT (`manifest.py --verdict`, `--import-review`).
