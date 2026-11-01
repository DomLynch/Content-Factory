# Vampire v2 head pass (data layer ready, nothing rendered yet)

Why: the v1 sheet face reads 35 to 40 with smooth, warm skin. The concept (`flux_env\dracula-gpt-ref.png`) is a man of 50 to 60: gaunt, hollow cheeks, sharp cheekbones, deep-set eyes in dark shadowed sockets, pale grey-white skin with a cold undertone, fine lines at the eyes and brow.

Data: `characters/vampire-dracula-gpt-sheet-v2.json` (kit `vampire_gpt_sheet_v2`, generated to `kits/vampire_gpt_sheet_v2_kit.json`, outputs in `kits_out\vampire_gpt_sheet_v2`). It is the v1 spec plus the optional fields `head_age_desc`, `head_skin_desc`, `body_age_desc`, `body_skin_desc`, a changed `hand_skin_desc`, and the identity lock fields `head_front_ref` and `head_lock_ref`. The v1 spec and the live v1 kit are untouched (regeneration of v1 gives 0 differences).

## The 5 cells, in this order (row seed 45 on each)
1. `head_front` : reference is `identity_ref`, the Flux klein Dracula (`flux_test\dracula_flux_klein_seed42.png`, 1024 x 1360). This is the new identity lock. (The earlier 240 px crop of the GPT art is no longer used.)
2. `head_three_quarter`, 3. `head_profile`, 4. `head_back`, 5. `head_top` : reference is the v2 `head_front_1024x1024.png`. Do not start 2 to 5 until head_front has passed review.

`head_profile_right` is a mirror of `head_profile` (row key `mirror_of`, made by the mirror tool, never rendered). `head_eyes_closeup` is not part of this pass.

## GPU time
About 30 minutes for the five cells (one Flux cell per process, about 5 to 6 minutes each, one GPU job at a time, guard armed), plus the 20 to 30 s upscale below.

## Reference upscale step (not needed now)
identity_ref is a full 1024 x 1360 image, so no upscale is needed. If the whole standing figure turns out to be too small a face for the lock, crop the head from it and upscale with `tools\upscale\upscale_match.py` (GPU, 20 to 30 s, inside the GPU window), then point `identity_ref` at that file.

## Review rule
Every head is compared (a) against the concept face crop for age, gauntness and skin (a face that reads younger or warmer than the crop is a REJECT, not a WEAK), and (b) against its siblings for hair, braids, moustache, chin, collar. Score honestly; a proof is labelled a proof.

## Not in this pass
Body cells, hands and feet carry the new skin and build text in the v2 kit, but are only re-rendered if Dom asks after the head pass.

## Update 2026-11-01 (Strategy ruling): identity vs parts
- `identity_ref` = the Flux klein Dracula: the IDENTITY and PALETTE reference (older gaunt man about 55, lined, pale cold grey skin, long loose wavy black hair swept back, NO braids, thin upswept moustache; muted oxblood, matte wool, dark brown-black fur, dull antique gold, photographic colour, no gloss). Head_front and body_underlayer_front condition on it; the other head rows lock to the v2 head_front, the other body rows to the v2 body front.
- `parts_ref` = the GPT art: parts inventory only (which costume pieces exist and how they are cut), never face or colour. In v2 the gear flats carry it as the row reference with the prefix "use this image only for the cut and shape of the garment; take the colour and material from the text" (spec flag `gear_use_parts_ref`, one field to turn off). Warning: earlier lessons say a reference leaks the whole costume into gear flats and gives the 1536 x 1024 black-block corner; if a flat leaks, set `gear_use_parts_ref` to false and regenerate.
- Palette is data: `coat_palette`, `cloak_palette`, `fur_palette`, `gold_palette`, `leather_palette`, `colour_rule`, `gear_colour_rule` (appended to every gear prompt).
- Re-roll order after v1 is bound: the 5 head cells first (head_front, head_three_quarter, head_profile, head_back, head_top; profile_right is a mirror), then the gear flats whose colour reads saturated: gear_coat, gear_coat_back, gear_tabard, gear_belt_buckle_closeup and probably gear_cuffs and gear_collar_closeup; body, hands and feet stay as rendered in v1.
