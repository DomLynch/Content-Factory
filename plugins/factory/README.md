# factory (Claude Code plugin), 0.1.0

The Content-Factory (Flux image sheets, TRELLIS meshes, Blender rig binding, stills, library publish) as Claude Code skills, three slash commands, an MCP server that runs the existing stage scripts behind the GPU lock and a RAM gate, and four guard hooks. By Dom Lynch, github.com/DomLynch/Content-Factory.

Honesty first: three skills (`character-sheet`, `trellis-part`, `bind-humanoid`) are written from stages that ran with receipts. The other six start with `STATUS: DRAFT, not yet run end to end` and list what is unproven (`skills/STATUS.md`). The plugin was validated with `claude plugin validate` and its hooks and MCP server were tested offline with synthetic input; it has not yet been installed into a live Claude Code session, and no factory stage was run to test it.

## Install
In a Claude Code session (the marketplace is this repo, its name is `content-factory`):

```
/plugin marketplace add DomLynch/Content-Factory
/plugin install factory@content-factory
```

One-step form on Claude Code 2.1.275 or later: `/plugin install factory --marketplace DomLynch/Content-Factory`. From a shell: `claude plugin marketplace add DomLynch/Content-Factory` then `claude plugin install factory@content-factory`. Restart the session so skills, commands, hooks and the MCP server `factory` load.

Needs the Content-Factory checkout at `C:\Users\dom12\Documents\Content-Factory` with `config/config.local.json` filled in (copy `config/config.local.example.json`). Another path: set `FACTORY_HOME` (the MCP server gets it from `.mcp.json`; the hooks read the same variable and default to `<home>\Documents\Content-Factory`). Python is the system `python`; standard library only.

## What is in it
| Part | What |
|---|---|
| skills (proven) | `character-sheet`, `trellis-part` (draft tier proven; master and hero tiers not), `bind-humanoid` (absolute gate proven; relative clause missed) |
| skills (DRAFT) | `bind-beast`, `assemble-character`, `stills-and-turntable`, `publish-to-library`, `environment`, `hero-rebake` |
| `/factory-status` | read-only: GPU lock, processes, free RAM, queues, newest receipts (unparsable ones flagged), guard events |
| `/factory-run <character_spec.json>` | wraps `tools/run_character.py`: `--dry-run` first, state check, the owner's yes, then the real run |
| `/factory-publish <asset_id> --library <clone>` | wraps `tools/publish.py`: gate on Dom's 8/10, `--dry-run` first, the owner's yes to write, never pushes or opens a PR |
| MCP server `factory` | `factory_status`, `flux_render(kit,row_id)`, `trellis_run(asset_id,image,preset)`, `cutout(asset_id,image)`, `bind(asset_id,mesh)`, `stills(asset_id,glb)`, `upscale(src,dst)` |
| hooks (PreToolUse) | `gpu_lock_guard.py`, `ram_gate.py`, `input_gate.py`, `no_literals.py` |

### MCP server
`mcp/server.py` speaks MCP over stdio (JSON-RPC 2.0, one message per line), implemented with the standard library because the `mcp` package is not installed in the factory venv. Every tool runs the existing script as a subprocess (`flux/flux_kit.py`, `tools/run_3d.py`, `tools/cutout_cpu.py`, `tools/run_bind.py`, `tools/run_stills.py`, `tools/upscale/upscale_match.py`), GPU tools under `tools/gpu_guard.ps1` exactly as `tools/run_character.py` does, with the log in `logs_dir`, and returns JSON with `status`, the `receipt` path and its status, outputs, timing and a reminder to look at the output. A tool refuses (`status: refused`, `isError: true`, nothing started) when:
- a GPU tool (`flux_render`, `trellis_run`, `upscale`) finds `C:\Users\dom12\Documents\.gpu_lock` naming a live pid;
- any heavy tool finds free RAM under `limits.start_min_free_ram_gb` (6 GB in `config/config.json`);
- the input image of `trellis_run` or `cutout` has a short side under 1024 px;
- the preset, kit row or a file does not exist, or `gpu_guard.ps1` is missing (a GPU job never starts unguarded).
Calls block until the stage ends (TRELLIS can take an hour; timeouts come from `config.json -> character`). `flux_render` accepts `dry_run` (prints the plan, no GPU). `bind` surfaces `bind_best.py`'s gate and warns when its relative-to-control clause fails although the receipt reads ok. `stills` marks its output as proof-tier lighting.

### Hooks
| Hook | Fires on | Denies |
|---|---|---|
| `gpu_lock_guard.py` | Bash running `generate.py`, `flux_kit.py`, `run_3d.py`, `run_character.py`, `run_kit.ps1`; MCP `flux_render`, `trellis_run`, `upscale` | a start while the lock names a live pid (one GPU job at a time; two froze the laptop twice on 2026-10-31) |
| `ram_gate.py` | Bash running any factory script; MCP heavy tools | a start under `start_min_free_ram_gb` (6 GB) free; `--dry-run`, `CF_DRY` and `--help` pass |
| `input_gate.py` | Bash `run_3d.py`, `generate.py`, `cutout_cpu.py`; MCP `trellis_run`, `cutout` | an input image with a short side under 1024 px (skipped for `--from-latents`) |
| `no_literals.py` | Bash `generate.py`, `flux_kit.py` | inline numeric flags (`--texture-size 2048`, extra numeric positionals); use a preset or a kit row |
A missing file, an unreadable lock, an unknown size or any error means allow: a failing check never blocks work. The Bonsai plugin has its own RAM-floor guard (3 GB) on similar commands; both can fire.

## Pipeline, humanoid (stages in `config/config.json -> character.stages`)
1. `character-sheet`: spec (`characters/<id>-sheet.json`) to kit (`tools/make_kit.py`), one Flux row per process (`flux_render`), `fix_black_block.py`, contact sheet, Dom's picks (`tools/manifest.py`). LOOK at everything.
2. Upscale each pick to 2048 px or more (`upscale`, `tools/upscale/upscale_match.py`).
3. `trellis-part`, one part per image: body (T-posed base layer), each head, each gear piece. `cutout` then `trellis_run` (draft tier first).
4. `bind-humanoid`: `bind` on the T-posed body part, donor `tall`; read the `bind_best.py` gate.
5. `assemble-character` (DRAFT): `tools/run_gear.py` per gear part, then `tools/assemble_character.py`.
6. `stills-and-turntable` (DRAFT): `stills`, then lit three-point stills and a turntable (not built).
7. `hero-rebake` (DRAFT) for the final bake from the saved latents.
8. `publish-to-library` (DRAFT): `/factory-publish`, Dom's 8/10, PR by hand.
`/factory-run` runs stages `flux, trellis, bind, stills_body, gear, assemble, stills_dressed, textures` for a whole spec (`characters/vampire-dracula.json`), resumable.

## Pipeline, beast
`bind-beast` (DRAFT; source: the OpenTrident beast tooling in the read-only handover). Parts first (body, head, limbs, attachments; never one mesh) through steps 1 to 3 above, then a rig from the OpenTrident standard: `quadruped` (44 bones), `winged_quadruped` (58), `serpent` (29), or `winged_humanoid_socket` (14 on the 106 rig), with a role map to the bones. No script yet binds a TRELLIS mesh to a beast rig and no beast gate exists; the skill lists exactly what is known. Assemble, stills, publish and the quality rules are the same as for a humanoid.

## Data-row schema (all data, no literals in runs)
- **Character sheet spec** (`characters/<id>-sheet.json`, input of `make_kit.py`): `id, description, seed, ref, base_layer_ref, character_noun, possessive, facial_hair, base_layer, three_quarter_posture, head_detail, hair_desc, gear_items[] {id, item, gear_layout?, gear_extra?, gear_exclude?, size?}, swatches[] {id, desc}`. Template: `config/character_sheet_template.json` (`group_priority` body, gear, head, hands_feet, swatch).
- **Kit** (`kits/*.json`): `kit, seed, w, h, ref, ref_prefix, subject, style, rows[]`. **Row**: `name, row_id, prompt`, optional `w, h, ref, ref_prefix, use_ref, use_subject, group, part`. One row per `flux_kit.py` process; output `<outputs_dir>/<kit>/<name>_<W>x<H>.png`.
- **Manifest** (`manifests/<kit>.json`): `rows{row_id: {candidates[{sha256,path}], pick, picked_by, picked_at, note}}`.
- **Trellis preset** (`config/config.json -> trellis.presets.<name>`): `preset, texture_size, bake_faces, extra[]`. Limits are in `limits`, bind gate in `bind.gate`, gear gate in `gear.gate`, stills in `stills`.
- **Character spec** (`characters/<id>.json`, input of `run_character.py`): `character_id, label, group, donor, preset?, clips{Idle,Walk,Attack,Roll}, textures?, parts[] {name, kind body|gear, kit, row_id, repair?{mirror}, preset?}` with exactly one body part.
- **Receipt** (`receipts/<asset_id>/<stage>-<utc>.json`, schema 1): `asset_id, stage, tool, script_commit, model, inputs[{path,sha256}], params, machine, timing, memory, outputs[{path,sha256}], status ok|fail, error, created_utc`. A receipt that does not parse as JSON is not a receipt.
- **Publish spec** (`publish/<asset_id>.json`): `id, label, group, rig, source, glb, thumb, stills[]`, optional `still_names, clips, receipt`.
- **Library row** (`site/data/unity.json bodies[]`, see `docs/library-format.md`): `id, label, group, file ...?v=N, rigged, rig, bytes, source, thumb, variant, stills[{name,file}], clips, receipt`; environments add `environment: true`, `rigged: false`, `envMedia.stills`.

## Layout
```
.claude-plugin/marketplace.json     (repo root)  name content-factory, one plugin, source ./plugins/factory
plugins/factory/
  .claude-plugin/plugin.json        name, version, description, author, repository
  .mcp.json                         server factory -> mcp/server.py, env FACTORY_HOME
  skills/<name>/SKILL.md            frontmatter name, description
  skills/STATUS.md                  proven versus DRAFT (not a skill)
  commands/*.md                     factory-status, factory-run, factory-publish
  hooks/hooks.json + 4 hook scripts
  mcp/server.py                     stdio MCP server
  lib/cf_guard.py, cf_cmd.py        shared lock, RAM, image and command checks
  tests/test_plugin.py              offline tests (python -m unittest discover plugins/factory/tests)
```
Packaging conventions are copied from the Bonsai plugin (`C:\Bonsai-2`): `.claude-plugin/plugin.json` and `marketplace.json`, `.mcp.json` with `${CLAUDE_PLUGIN_ROOT}` and an env block, `hooks/hooks.json` with `matcher` and a per-entry `if: "Bash(*word*)"` so ordinary shell commands never start Python, hook output as `hookSpecificOutput.permissionDecision: deny`, `commands/*.md` with `description` and `allowed-tools`.

## Rules (from LESSONS.md; each release folds new lessons into the skills' Do / Don't and bumps the version)
- One GPU job at a time. No literals in a run: prompts, sizes, paths, presets and gates live in a kit row, a preset or config.
- Every input to 3D is 1024 px or more on the short side and upscaled to 2048 px or more.
- No "done" without a receipt with `status: ok`, and always LOOK at the images and outputs.
- Quality bar for finals: 9/10 against the best of Unreal Engine 5.8 in 2026, no compromise on quality, visuals or effects. The 2048 / 500k profile is a benchmark profile, never a final. Nothing enters the library below 8/10 by Dom's eye. Version characters v1, v2, v3.
- Never build a character or creature as one mesh.

## Not verified
- Installed and loaded in a live Claude Code session: no. Skills, commands, the MCP server and the hooks have not been exercised there.
- `bind/*.py` still hold hard-coded Blender, donor and rig paths; `run_bind.py` skips the relative-to-control gate clause.
- Master and hero tiers, hero re-bake, assemble, gear, turntable, beast bind, environment assembly and publish have no end-to-end receipt.
