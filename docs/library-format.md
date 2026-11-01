# Library (opentrident-steam) Unity-tab format - from OpenTrident Dev, 2026-11-01

- Row file: `site/data/unity.json`, object `{about, bodies:[...]}`. Template row: `demon2-106`.
- Per body: `id`, `label`, `group`, `file` (`models/unity/<id>.glb?v=N`), `rigged: true`, `rig` (e.g. "69 joints (65 Mixamo + head 2)"), `bytes`, `source`, `thumb` (`models/unity/thumbs/<id>.png?v=N`), `variant: "standard"`.
- Environments: `environment: true`, `rigged: false`, `envMedia.stills`.
- Rigged GLBs must be on the 106-joint Mixamo skeleton. Stills go under `models/unity/` (`thumbs/` or `<id>/stills`). Clips come from the shared Mixamo library by id, not embedded.
- Cache is 86400 s: bump `?v=N` on every change. Publish is by PR only (tools/publish.py, last step).
- Clips: all 7500 Mixamo clips on the 106 rig at `models/mixamo/clips/<id>.glb` (VPS site; index `models/mixamo/clips/index.json`, tags `site/data/clip-tags.json`).
  - Idle `113410901-idle`, Walk `124540901-walk`, site Attack pin `101550901-kicking`.
  - Roll: `120430902-sprinting-forward-roll`, `114490901-dive-roll`, `118140901-stand-to-roll`, `gl015-dodge-roll`.
  - Attack (vampire fit): `9034009-zombie-neck-bite`, `9026004-zombie-attack`, `121360901-zombie-attack`.
