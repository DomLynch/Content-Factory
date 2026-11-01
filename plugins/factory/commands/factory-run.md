---
description: STUB, not implemented. Will run one factory stage for one asset behind the GPU lock. Does nothing yet.
---

STUB. Do not run any stage from this command.

Tell the user that `/factory-run` is a placeholder in plugin `factory` 0.1.0-draft. Planned behaviour, not built: take a stage name and an asset id, check `/factory-status` (GPU lock, free RAM), arm `tools/gpu_guard.ps1`, run exactly one stage wrapper (`flux/flux_kit.py`, `tools/run_3d.py`, `tools/run_bind.py`), then show the new receipt.

Until it is written and proven, point the user to the skills `character-sheet`, `trellis-part` and `bind-humanoid` for the exact manual commands.
