# Vampire v2 head pass (data layer ready, nothing rendered yet)

Why: the v1 sheet face reads 35 to 40 with smooth, warm skin. The concept (`flux_env\dracula-gpt-ref.png`) is a man of 50 to 60: gaunt, hollow cheeks, sharp cheekbones, deep-set eyes in dark shadowed sockets, pale grey-white skin with a cold undertone, fine lines at the eyes and brow.

Data: `characters/vampire-dracula-gpt-sheet-v2.json` (kit `vampire_gpt_sheet_v2`, generated to `kits/vampire_gpt_sheet_v2_kit.json`, outputs in `kits_out\vampire_gpt_sheet_v2`). It is the v1 spec plus the optional fields `head_age_desc`, `head_skin_desc`, `body_age_desc`, `body_skin_desc`, a changed `hand_skin_desc`, and the identity lock fields `head_front_ref` and `head_lock_ref`. The v1 spec and the live v1 kit are untouched (regeneration of v1 gives 0 differences).

## The 5 cells, in this order (row seed 45 on each)
1. `head_front` : reference is the concept face crop `kits_out\vampire_ref_crops\concept_face_crop.png` (240 x 240 px native, from the standing figure; to be upscaled first, see below). This is the new identity lock.
2. `head_three_quarter`, 3. `head_profile`, 4. `head_back`, 5. `head_top` : reference is the v2 `head_front_1024x1024.png`. Do not start 2 to 5 until head_front has passed review.

`head_profile_right` is a mirror of `head_profile` (row key `mirror_of`, made by the mirror tool, never rendered). `head_eyes_closeup` is not part of this pass.

## GPU time
About 30 minutes for the five cells (one Flux cell per process, about 5 to 6 minutes each, one GPU job at a time, guard armed), plus the 20 to 30 s upscale below.

## Reference upscale step (before head_front)
The crop is only 240 px, too small to condition a 1024 cell. Enlarge it with Real-ESRGAN, colour-matched: `tools\upscale\upscale_match.py` on `concept_face_crop.png`, GPU, 20 to 30 s, inside the GPU window (not parallel to a Flux cell). Write the result over the path in `head_front_ref` only after looking at it (or change that spec field to the upscaled file name and regenerate the kit).

## Review rule
Every head is compared (a) against the concept face crop for age, gauntness and skin (a face that reads younger or warmer than the crop is a REJECT, not a WEAK), and (b) against its siblings for hair, braids, moustache, chin, collar. Score honestly; a proof is labelled a proof.

## Not in this pass
Body cells, hands and feet carry the new skin and build text in the v2 kit, but are only re-rendered if Dom asks after the head pass.
