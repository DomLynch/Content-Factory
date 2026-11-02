# Content-Factory

**Rule: plug gaps, no duplicates.** Before a tool, script or skill is added, name the capability we lacked in one line and check that nothing here already covers it; extend the existing tool instead of writing a second one. The build order is enforced by gates, not documented only: see `docs/build-order.md`.

Everything that makes assets for Frankendom: image prompts and scripts (Flux), TRELLIS run scripts, cut-out helper, rig binding, benchmark queues and the GPU/RAM guard.

- `docs/` asset standard and Windows/ROCm notes
- `tools/` cut-out helper, GPU memory probe, `gpu_guard.ps1` (kills a job when free RAM drops below a limit)
- `flux/` Flux klein image scripts (dragon, tiling test, environment shots)
- `bind/` rig binding to the 106-bone rig: metrics and diagnostics, plus the labelled fallback donor transfer (the default binder is SkinTokens, `tools/run_skin_tokens.py`; choose with `config.json -> bind.binder`, flip with `tools/set_default_binder.py`)
- `bench/` benchmark queues (attention budget)

Engine patches live in DomLynch/Trellis-3. Finished characters and rigs live in the library repo. No binaries in git: models and outputs stay on the laptop and the storage box.
