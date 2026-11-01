# Skill status (this file sits in `skills/` but is not a skill: no frontmatter, no folder)

Rule: a skill for a stage that has not run end to end with a receipt starts with the banner `STATUS: DRAFT, not yet run end to end` and lists what is unproven. Only the three proven skills carry no banner. When a draft stage gets its first end-to-end receipt, remove its banner, add the receipt path to its Evidence, and bump the plugin version (`.claude-plugin/plugin.json` and the repo's `.claude-plugin/marketplace.json`).

| Skill | State | What is unproven (see the banner in the skill) |
|---|---|---|
| character-sheet | proven (single cells) | the full 23-cell sheet run and `contact_sheet.py` have no receipt (noted in the skill's Evidence) |
| trellis-part | proven (draft tier) | master and hero tiers have no receipt |
| bind-humanoid | proven (absolute gate) | the relative-to-control clause is missed by the T-pose body and not checked by `run_bind.py` (noted in the skill) |
| bind-beast | DRAFT | no beast bind in the factory; OpenTrident beast rigs are a proposal; no beast gate |
| assemble-character | DRAFT | no `gear` or `assemble` receipt |
| stills-and-turntable | DRAFT | single-sun proof lighting only; no turntable; no dressed character |
| publish-to-library | DRAFT | the only publish receipt is NUL bytes; no PR made |
| environment | DRAFT | Flux, upscale and one prop only; no Infinigen, no Unity assembly |
| hero-rebake | DRAFT | two attempts killed by the RAM guard; no finished hero bake |
| motion-generate | DRAFT | no real ARDY generation yet (needs a GPU window); text encoder never loaded end to end; retarget not looked at on the bound character; Kimodo path not run |

Other unwritten sub-stages (mentioned inside skills): gear binding details (`bind_gear.py`), mesh clean (`run_clean.py`, no receipt).
