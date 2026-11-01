# Content-Factory

Everything that makes assets for Frankendom: image prompts and scripts (Flux), TRELLIS run scripts, cut-out helper, rig binding, benchmark queues and the GPU/RAM guard.

- `docs/` asset standard and Windows/ROCm notes
- `tools/` cut-out helper, GPU memory probe, `gpu_guard.ps1` (kills a job when free RAM drops below a limit)
- `flux/` Flux klein image scripts (dragon, tiling test, environment shots)
- `bind/` rig binding to the 106-bone rig (donor transfer, metrics, diagnostics)
- `bench/` benchmark queues (attention budget)

Engine patches live in DomLynch/Trellis-3. Finished characters and rigs live in the library repo. No binaries in git: models and outputs stay on the laptop and the storage box.
