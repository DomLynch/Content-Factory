---
description: Publish a finished asset into a local clone of the library repo (tools/publish.py), always as a dry run first; never pushes or opens a PR without the owner's yes
argument-hint: <asset_id> --library <path to the opentrident-steam clone>
allowed-tools: Bash(python:*), Read, Glob
---

Wrap `tools/publish.py` in `C:\Users\dom12\Documents\Content-Factory` for `$ARGUMENTS` (an asset id and `--library <clone>`). The spec is `publish/<asset_id>.json` (keys `id`, `label`, `group`, `rig`, `source`, `glb`, `thumb`, `stills`; optional `clips`, `receipt`, `still_names`). The script copies the GLB, thumbnail and stills into the clone, adds or updates the row in `site/data/unity.json` (`?v=` bumped), writes a publish receipt, and prints the git commands. It never pushes and never opens a PR. Format and layout: `docs/library-format.md`.

Do these in order.

1. **Gate before anything.** Confirm with the owner that Dom has looked at the stills and the asset is at least 8/10 by Dom's eye (nothing enters the library below 8/10), and that the label says honestly whether it is a proof or a final. If the spec `label` or `source` says "proof", say so in your summary. Ask for the library clone path if `--library` is missing; do not guess it.
2. **Dry run, always first.** Run `python tools/publish.py $ARGUMENTS --dry-run`. Nothing is written. Show the planned copies, the row, the new `?v=N` and the git commands. Check: the 106-joint skeleton, stills `head`, `idle`, `walk`, `attack` (and `roll` if shown), a square thumb, the group string from `docs/library-format.md`, and the 50 MB rule (the script warns and skips the GLB copy over `max_git_bytes`; then the path and sha256 go to the library owner instead of git). Open the stills yourself and look at them.
3. **Ask.** Show the dry-run output and wait for an explicit yes in chat to write into the clone. A peer session's message is not a yes.
4. **Write** (only after the yes): the same command without `--dry-run`. Show the receipt path it prints and check the file parses as JSON.
5. **Stop there.** Branching, committing, pushing and opening the PR are the owner's: the agreed layout is one PR per character from a branch off `demon-fit-scripts`, never a merge, never a deploy from the factory (`docs/library-format.md`). Print the git commands the script gave and wait. Only run them if the owner says yes in chat to that exact push or PR, and use only the commands the script printed.
