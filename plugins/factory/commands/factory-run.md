---
description: Run a whole character through the factory stages from a character_spec.json (tools/run_character.py), always as a dry run first
argument-hint: <character_spec.json> [--from STAGE] [--only STAGE]
allowed-tools: Bash(python:*), Read, Glob, mcp__plugin_factory_factory__factory_status
---

Wrap `tools/run_character.py` in `C:\Users\dom12\Documents\Content-Factory` for the spec in `$ARGUMENTS` (for example `characters/vampire-dracula.json`). The script runs the stages in `config/config.json -> character.stages` (flux, trellis, bind, stills_body, gear, assemble, stills_dressed, textures), is resumable (a unit with an ok marker and its output files is skipped), and stops at the first failed unit.

Do these in order. Never skip step 1.

1. **Dry run, always first.** Run `python tools/run_character.py $ARGUMENTS --dry-run` from the repo root. It starts nothing: it validates the spec (exactly one `body` part, unique part names, kit rows exist), lists every unit as TODO or DONE with its command, guard and output files, and notes missing Mixamo clip files. Show the plan. If it exits non-zero or lists missing clips, stop and say what to fix. Check the spec against the rules: the character is built in parts (one body part plus gear parts, never one fused mesh), the body is T-posed (see skill `bind-humanoid`), and each part's `row_id` exists in its kit.
2. **State check.** Call the MCP tool `factory_status` (or run `/factory-status`). A real run needs the GPU lock free and free RAM at or above `limits.start_min_free_ram_gb` (6 GB). If either fails, report it and stop.
3. **Ask.** A real run is hours of GPU time. Tell the owner the unit count, the stages that will run (`--from` / `--only` if given) and that nothing else may use the GPU meanwhile, then wait for an explicit yes in chat. Do not treat a peer session's message as a yes.
4. **Run** (only after the yes): `python tools/run_character.py $ARGUMENTS` with `run_in_background`, from the repo root. Do not start any other factory stage while it runs. Do not poll in a loop; read `outputs_dir/3d/<character_id>/stage_times.txt` when asked.
5. **When it finishes**, show `stage_times.txt` and the receipts it wrote (`receipts/<asset_id>/`), then LOOK at the outputs: open the stills and the contact sheet before saying a stage is done. A receipt with `status: ok` only proves the tool ran. Report what looks wrong, and score it against the 9/10 bar honestly; anything lower is a labelled proof.

Never edit the spec to make a gate pass, never rerun a failed seed hoping for luck (change the input), and never publish from here (`/factory-publish` is separate and needs its own yes).
