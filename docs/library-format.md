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

## Agreed layout for a factory character (OpenTrident Dev, 2026-11-01) - supersedes the paths above where they differ
- Branch off `demon-fit-scripts` in opentrident-steam, one PR per character, PR body = receipt list per stage; Handover Dev reviews, Dom decides deploy. No deploy from the factory.
- Files: `site/models/unity/<id>.glb` (106 skeleton, meshopt ok, no embedded clips), `site/models/unity/thumbs/<id>.png` (square), `site/models/unity/stills/<id>/{head,idle,walk,attack}.png` (+ `roll.png` if shown). Binaries over about 50 MB stay out of git: add path + sha256 to `tools/beast/MODELS.md` instead.
- Row appended to `site/data/unity.json` `bodies[]`: id, label, group (e.g. "Vampire (Dracula)"), file `models/unity/<id>.glb?v=1`, thumb, rigged true, rig, bytes, variant "standard", source (one honest sentence), plus new optional `clips` {"Idle","Walk","Attack","Roll"} (clip ids) and `receipt` (repo path or link).
- Environments: group "Environment", `environment: true`, `rigged: false`, `envMedia.stills[]` like necropolis.

## Final answers for the first character (vampire-dracula-106), 2026-11-01
- Stills path: `models/unity/vampire-dracula-106/stills/{head,idle,walk,attack}.png` (supersedes `stills/<id>/`). Bump `?v=` on glb and thumb.
- group is a literal string: Daemon = "Demon 2 (two-headed)", Necropolis = "Environment", Vampire = "Dracula (vampire)" (own heading).
- Row also lists stills like envMedia.stills: `"stills": [{"name":"Head crop","file":"models/unity/vampire-dracula-106/stills/head.png"}, ...]`, plus `"receipt"` and `"clips"` {"Idle":"113410901-idle","Walk":"124540901-walk","Attack":"9034009-zombie-neck-bite","Roll":"120430902-sprinting-forward-roll"}.
- Branch: clone opentrident-steam, branch `vampire-dracula-106` off `demon-fit-scripts`, PR into `demon-fit-scripts`. Pushing that branch is fine; never merge. Reviewer merges/deploys only on Dom's word. GLB over about 50 MB: path + sha256 to Handover Dev instead of git.
