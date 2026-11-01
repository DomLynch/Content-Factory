# factory (Claude Code plugin), 0.1.0-draft

Skills and commands for the Content-Factory in this repo. DRAFT: written from the repo's scripts and receipts. The plugin has never been installed or loaded in Claude Code, no command has run, and the hook was only self-checked with synthetic payloads. Expect breakage.

## Layout (copied from the installed Bonsai plugin)
```
.claude-plugin/marketplace.json     (repo root)  one plugin entry, source ./plugins/factory
plugins/factory/
  .claude-plugin/plugin.json        name, version, description, author, repository
  skills/<name>/SKILL.md            frontmatter: name, description
  skills/PLANNED.md                 skills not yet written (not a skill)
  commands/*.md                     frontmatter: description (+ allowed-tools)
  hooks/hooks.json, gpu_lock_guard.py
```
Conventions come from `C:\Users\dom12\.claude\plugins\cache\bonsai-2\bonsai\0.1.3` (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `hooks/hooks.json` with `matcher` and per-entry `if: "Bash(*word*)"`, `commands/bonsai-status.md`, `skills/lane-status/SKILL.md`, `.mcp.json`) and the marketplace copy `C:\Users\dom12\.claude\plugins\marketplaces\bonsai-2`. The Unity plugin was not found under `~/.claude/plugins` (only Bonsai is installed there), so nothing was copied from it. No `.mcp.json` is shipped: the factory has no MCP server.

## Pipeline for a humanoid (proven stages only)
1. `character-sheet`: spec -> `tools/make_kit.py` -> kit -> `flux/flux_kit.py` one row per process -> `fix_black_block.py` -> `contact_sheet.py`. Partly proven (single cells yes, full 23-cell sheet not yet).
2. `trellis-part`: picked 1024 px+ T-posed base-layer body image -> `tools/run_3d.py` (cut-out, then TRELLIS) -> `mesh.glb`.
3. `bind-humanoid`: `BIND_FORCE_DONOR=tall`, `tools/run_bind.py` -> `bound.glb` + gate. Passes the absolute gate; the relative-to-control clause is currently missed (see the skill).
Not yet skills: gear (coats, cloaks) per part, assemble, stills/turntable, publish, environments, hero rebake, beasts. See `skills/PLANNED.md`.

## Data layout (repo root)
- `config/`: `config.json` (limits, presets, gates; no machine paths), `config.local.json` (gitignored, this machine's paths), `character_sheet_template.json`.
- `kits/`: one JSON per kit, rows rendered by `flux_kit.py`.
- `receipts/<asset>/<stage>-<utc>.json`: one receipt per stage run (status, timing, memory, inputs and outputs with sha256).
- `manifests/`: per-environment/asset manifests (for example `hades_gate.json`).
- `index/assets.json`: built by `tools/index.py` from the latest receipt per stage.
- Also: `characters/` (specs), `publish/` (publish specs), `bind/`, `flux/`, `tools/`, `bench/`, `docs/`, `LESSONS.md`.
Outputs, models and binaries are not in git (`outputs_dir` on the laptop).

## Rules
- One GPU job at a time (two at once froze the laptop twice on 2026-10-31). The hook below enforces it for the entry-point scripts.
- No literals in a run: prompts, sizes, paths, presets and gates live in a kit row, a preset or config.
- 1024 px input gate: every view fed to 3D is at least 1024 px on the short side, measured with PIL.
- No "done" without a receipt with `status: ok`.
- Free RAM guard at 2 GB (`tools/gpu_guard.ps1`); max 1.6 MP per Flux render.
- A skill is written only from a stage that has run end to end with a receipt.
- New lessons go to `LESSONS.md`; each release folds them into the skills' Do / Don't and bumps the version.

## Commands
- `/factory-status`: real, read-only instructions (not yet run).
- `/factory-run`, `/factory-publish`: STUBS, they do nothing.

## Hook
`hooks/hooks.json` -> `hooks/gpu_lock_guard.py` (PreToolUse, Bash). Denies a command that starts `generate.py`, `flux_kit.py`, `run_3d.py`, `run_character.py` or `run_kit.ps1` when `Documents\.gpu_lock` names a live pid. Allows on a missing, stale or unreadable lock, on any error, and for `CF_DRY` dry runs. Limits: it only sees Bash commands whose text names those scripts (a job started another way, or by a queue script already running, is not seen), and `gpu_guard.ps1` writes the lock only once the job process exists. The Bonsai plugin has its own RAM-floor guard on the same commands; the two coexist. Self-checked offline on 11 synthetic cases; not exercised in a live session.

## TODO / untested
- Install via the marketplace and confirm skills, commands and the hook load (`/plugin`, restart). Not done on purpose (no plugin installs from this task).
- `bind/*.py` still hold hard-coded Blender, donor and rig paths; `run_bind.py` skips the relative-to-control gate clause.
- `run_3d.py --from-latents` and the hero preset are unproven.
- Add a lock-and-RAM check to `/factory-run` once it exists.

## Quality bar
Final assets must score 9/10 or higher against the best of Unreal Engine 5.8 in 2026, with no compromise on quality, visuals or effects. Always open and look at every image and output before saying a stage is done. Anything lower is a labelled proof.
