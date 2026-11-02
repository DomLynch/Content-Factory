# Vampire v2 rules (plugin 0.1.26)

What the vampire v2 run taught, in one page. **E** = enforced by a gate or tool, **R** = rule in the skills (the Factory follows it, nothing blocks it yet), **P** = pending (listed in `cf_chain.PENDING`).

| # | Rule | Where |
|---|---|---|
| 1 | Angle cells use the WHOLE two-view image as the reference, never a single frontal face. Kit row key `ref_mode: "sheet"` marks them. **R** | `character-sheet` |
| 2 | **E** `ref_max_px`: the identity reference is capped at the render long edge (default for body/head/hands; row or kit key `ref_max_px`, env `CF_REF_MAX_PX`). Below the edge is refused. 4K refs cost 14 min per cell and corrupted non-square cells. `flux_kit.py` resizes, the receipt records `ref_max_px`. | `cf_flux`, `flux_kit.py`, config `flux.ref_long_by_group: "render"` |
| 3 | **E** A row that reads another row's output declares `needs: [row_id]`. Until those cells have PASS or WEAK the render exits 3 = HOLD (the queue waits, nothing fails). | `cf_flux.is_hold` |
| 4 | **E** A head row with `yaw_deg` is complete only when `manifest.py <kit> --yaw ROW DEG` recorded a yaw within `gates.yaw_tol_deg` (15). | `cf_chain.sheet_reasons` |
| 5 | **E** `supplied_by: "Dom"` + `file` rows (Codex/GPT flats under the row names) are cells, never Flux renders. Flux renders only identity cells and the body. | `cf_flux` |
| 6 | Feet cells: two views, heel at the bottom. Hands: ringless for modelling, ringed as the placement reference. **R** | `character-sheet` |
| 7 | Trellis: single image on this box; crop heads to head+neck; above ~24k tokens attention is tiled (6-10x slower); `--attn-auto-gb 28` is the ceiling. Multi-image conditioning is a Trellis-3 request (LESSONS). **R** | `trellis-part` |
| 8 | A projection texture pass after Trellis fixes backs and sides. **P** | `cf_chain.PENDING` |
| 9 | **E** Keep-awake on every GPU run: `cf_common.keep_awake()` in `flux_kit.py` and `run_3d.py` (SetThreadExecutionState while the process lives; no settings changed). | `tools/cf_common.py` |
| 10 | Dom's 95% rule is the QC verdict policy: below it a cell is WEAK or REJECT, not PASS. The measured definition is owed by Dom/Strategy; today the verdict is the reviewer's. **R** | `character-sheet` |
| 11 | **E (first arrow)** Fixed stage order, the Factory may not reorder it: head mesh -> body cells -> body mesh -> head v2 -> gear -> assembly -> exports. The body mesh is refused until the head mesh has an ok trellis receipt. The rest: **P**. | `cf_chain.order_reasons`, `STAGE_ORDER` |
| 12 | Two exports per character, GLB + FBX, from ONE Blender master. FBX is **P**: implementation path is the Demon pipeline's `work\export_fbx_v7.py` (listed under files not in git in `handover\make-human.md`); a publish prints the pending line until the spec names an `fbx`. | `publish` |
| 13 | **E** DONE gate: the OpenTrident data-layer row. A publish spec needs `datalayer` (a file that names the asset id). Reference copy: `reference\opentrident-datalayer\` in the live Content-Factory (`gear-pieces.json`, `gear-picker-rules.json`, `FACTORY.md`). | `cf_gates.publish_reasons` |

Escape hatch for every **E** gate is the same as before: `FACTORY_RAW=<reason>` (logged); the MCP tools have none.
