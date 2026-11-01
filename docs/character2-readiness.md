# Character 2 readiness: dry run of the knight (2026-11-01)

Method: CPU only, nothing rendered, no model loaded, the live Dracula queue untouched. Files created: `characters/knight-dryrun.json`, `kits/knight_dryrun_kit.json`, this note. Line numbers are of this checkout (local `main`, commit c02993a plus the uncommitted changes) unless marked `origin/main`.

## 0. Verdict

Spec plus one reference image gets a complete, valid 29-row kit in one command with zero code or template edits. The pipeline around the kit is NOT hands-free yet, for three reasons:

1. The checkout the queue runs from is 18 commits ahead and 56 behind `origin/main` (`git rev-list --left-right --count main...origin/main` = `18 56`). The sheet-rule gate (reference-first, mirror refusal, re-roll cap), `tools/mirror_cell.py` and `manifest.py --verdict` exist only on `origin/main`. Here they do not exist. The queue still contains the code that reacts to them (`bench/flux_queue.ps1:34`).
2. `bench/flux_queue.ps1` (untracked, in no commit) ignores the template's identity-first order, hard-codes Dracula row names, and does not skip `swatch_of` rows.
3. The "spec" is 61 hand-written fields (`characters/knight-dryrun.json`), not a reference image. Nothing in the repo turns an image into those fields.

Time estimate for the knight sheet: 26 renders (29 rows minus 3 mirrors) x 4.5 min mean (Dracula queue log, 39 cells of 2.4 min or more: mean 4.48, median 3.75) = about 1.95 h. With a 20 percent re-roll allowance (5 more cells) about 2.3 h. That fits 2.5 h only if the first pass is not rejected at the Dracula rate. First-pass reject rate cannot be measured without rendering. Dracula evidence: 44 files in `kits_out\vampire_gpt_sheet\rejected` for a 43-cell sheet, and `LESSONS` says 31 rejects before the rules, 0 in 9 cells after (`plugins/factory/skills/character-sheet/SKILL.md` on `origin/main`, "Sheet rules"). The under-20-percent target is therefore unproven.

## 1. Dry run results

### Step 1: spec to kit
- Copy made by changing only `id`, `kit_name`, `ref` (stand-in `C:\Users\dom12\Documents\flux_env\dracula-gpt-ref.png`, exists, 1,480,504 bytes) and `base_layer_ref` (`...\kits_out\knight_dryrun\body_underlayer_front_1024x1536.png`). A key-by-key compare shows exactly those 4 keys differ from `characters/_example_second_character.json`.
- `python tools\make_kit.py characters\knight-dryrun.json` printed `kits\knight_dryrun_kit.json 29 rows`, exit 0. No error, no missing field, no unfilled placeholder, no "order" warning (`tools/make_kit.py:73-75`).
- Things noticed, none blocking:
  - `base_layer_ref` must be typed by hand and must equal `<outputs_dir>\<kit_name>\body_underlayer_front_<W>x<H>.png`. It is a function of `kit_name` and the machine-local `outputs_dir`, but `make_kit.py:10-11` lists it as required input and does not derive it. A typo is found only when `body_underlayer_left` renders and `flux_kit.py:56` fails to open the file.
  - Neither `make_kit.py` nor `CF_DRY` checks that `ref` or `base_layer_ref` exist (`flux/flux_kit.py:21-30` returns before touching any file). The example spec's `C:\placeholder\...` paths would pass the dry run.
  - `hand_ring_rule`, `hand_accessory` and 40 more prose fields are required even for a character with no rings (`make_kit.py:10,23` plus the template `required_fields`).
  - Kit seed is 7 in the example, 42 in Dracula. Nothing in a gate depends on it, but each character needs a number picked by hand.

### Step 2: compare and per-row dry run
- `tools\compare_kits.py kits\vampire_gpt_sheet_kit.json kits\knight_dryrun_kit.json --ignore-order`: exit 1 (it exits 1 on any difference), 43 vs 29 rows, 59 differences.
  - 21 rows are common and in the same relative order; all 21 have identical group, w, h, use_ref and mirror_of. Only prompt (21, expected: character words), ref_prefix (18, carries `{facial_hair}`), ref (4, the `base_layer_ref` path of the other kit), seed and seed_note (6 each, Dracula `row_seeds`) differ.
  - 22 Dracula-only rows (gear_coat, gear_cloak, ... swatch_*) and 8 knight-only rows (gear_breastplate, gear_surcoat, gear_gauntlets, gear_boots, 4 swatches). These are the `gear_items` and `swatches` repeat cells (`make_kit.py:38`), so they are expected.
- `CF_DRY=1 flux\flux_kit.py kits\knight_dryrun_kit.json <row>` for all 29 rows: all exit 0, each printed asset id, path under `kits_out\knight_dryrun\`, size. No output directory was created (`flux_kit.py:28-31`). The local `flux_kit.py` dry run does not evaluate the sheet rules, so the dry run cannot tell you that a row would be refused.

### Step 3: what `bench\flux_queue.ps1 kits\knight_dryrun_kit.json` would do (read, not run)
Order (lines 12-14: `$first` list, then everything else in kit order, then `$last` list):

| # | row | queue action |
|---|-----|--------------|
| 1-2 | body_underlayer_back, body_underlayer_three_quarter | render, no reference |
| 3-6 | head_profile, head_back, head_three_quarter, head_top | render, reference |
| 7-9 | hand_back_views, foot_left, foot_right | render, no reference |
| 10-13 | head_front, body_underlayer_front, body_underlayer_left, body_underlayer_right | render (identity cells are 10 and 11, after 3-9) |
| 14,16 | hand_open_left, hand_fist_left | render |
| 15,17 | hand_open_right, hand_fist_right | SKIP, `mirror_of` (line 20) |
| 18-21 | gear_breastplate, gear_surcoat, gear_gauntlets, gear_boots | render |
| 22-25 | swatch_polished_steel, swatch_blue_wool, swatch_brown_leather, swatch_skin | render (knight swatches have no `swatch_of`) |
| 26-28 | head_eyes_closeup, gear_belt_buckle_closeup, gear_collar_closeup | render, last |
| 29 | head_profile_right | SKIP, `mirror_of` |

- Skips: hold file rows (line 18-19), `mirror_of` (line 20), any row whose `<kit>\<name>_*.png` exists (line 21). `swatch_of` is NOT skipped (see 2.3).
- Hold file: `C:\Users\dom12\Documents\flux_env\queue_hold.txt`, literal path on line 18, read once per row, holds by bare row id (not kit-qualified). It exists now (2 bytes, empty). Any id listed there holds that row in every kit.
- Re-roll cap (lines 22-23): counts `END <row> ok=True` lines in `<work_dir>\flux_queue_<kit>.log`; at 2 or more and no row `seed`, it logs `REROLL_CAP` and skips. Having any row seed disables the cap for that row, whether or not the prompt changed.
- Settle (line 25): up to 13 polls x 2 s (26 s cap) until no `flux_kit` python is left and free RAM is at least `-MinStartGB` (default 12, line 3, not `limits.start_min_free_ram_gb` = 6). Then wait up to 30 min for RAM (line 26); under it, `SKIP ... low RAM` (line 27).
- Per row: guard started hidden with config limits (line 28), `flux_kit.py kit rowId` (line 31), guard killed (line 32). `rc 2` plus `SHEET RULE` in the row log gives `HOLD` and `continue` (line 34). `END <row> ok=<png exists>` and, on failure, `FAIL_TAIL` (lines 36-38). `Retries` = 3 passes over the whole list (line 15), then `DONE` (line 41).
- Gates in the path: whatever `flux_kit.py` enforces itself. On this checkout that is only: row seed (line 18), RAM start wait (line 37, `cf_common.wait_for_free_ram`), black image under mean 3 (lines 64-67, rc 2 with `BLACK_IMAGE`, which the queue logs as FAIL_TAIL). The Claude hooks (`gpu_lock_guard.py`, `ram_gate.py`, `no_literals.py`) do not run for a detached PowerShell queue.
- Fresh-character consequence on this checkout: rows 1-9 render before the identity cells (rows 10-11), because `$first` outranks kit order. No gate stops them. Rows 12-13 (`body_underlayer_left/right`) need `base_layer_ref`; they come after row 11, so they work.
- Fresh-character consequence on `origin/main` code: rows 1-9 and 12-13 would exit 2 `SHEET RULE (reference first)` and be HELD (about 9 process starts of up to 26 s settle each, pass after pass). After identity cells 10-11 render, nothing can pass: the verdict is a human act (`manifest.py --verdict`), and the queue makes only 3 passes in about 1 h, then prints DONE with the held rows still missing. A human must PASS both identity cells and restart the queue.

### Step 4: `tools\run_character.py --dry-run`
- Takes a different spec: `characters/vampire-dracula.json` style (`character_id`, `clips`, `parts[]` of kit + row_id). It is not the sheet spec, and not generated by anything (`tools/run_character.py:30-48`). I wrote a throwaway knight run spec in `%TEMP%` (deleted) pointing at knight kit rows. `--dry-run` printed `DRY RUN knight-dryrun: 19 units` and the full command list (flux, fix_black_block, run_3d, run_bind, run_stills, run_gear, assemble, stills). It loaded no model and wrote nothing.
- What it covers: unit planning, output paths, resume markers, missing Mixamo clips. What it does not: kit validity, gate verdicts, whether the part row is the right pose.
- Problems it exposes:
  - The Dracula run spec uses a separate T-pose parts kit (`kits/vampire_parts_kit.json`, rows `body_underlayer`, `coat_alone`, ...). The sheet template has no T-pose body row and no `parts` template, so a knight run spec on sheet rows would feed the A-pose `body_underlayer_front` to the binder, which `LESSONS.md` (T-pose line) says fails the bind gate (stretch 18.8 vs 1.31).
  - Gear part names `coat`, `cloak`, `boots` are tied to `config/config.json` `gear.fit.parts` and `gear.helpers.parts` (lines 237, 254). A knight "surcoat/breastplate" has to be renamed to those or the config extended.
  - `run_character.py:96-98` runs `fix_black_block.py` after every Flux unit, which contradicts `LESSONS.md:32` ("reject, never repair").
  - `run_character.py:95` runs `flux_kit.py` directly, so it does not hold on the identity verdict either; there is no way to pass a verdict through it.

## 2. (a) Hand edits to files other than the spec and the reference image

Needed in the dry run: none. The following are what a real run of a second character would still need, or would silently get wrong.

### 2.1 Required to avoid wrong behaviour
| File:line | Edit | Why |
|---|---|---|
| `bench/flux_queue.ps1:12` | `$first` list names Dracula-era row ids (`body_underlayer_back`, `head_profile`, `hand_back_views`, `foot_left` ...). They are template ids, so they exist for the knight, but the list inverts the template's own identity-first `order` (`config/character_sheet_template_gpt.json:304-321`). | Siblings render, or are gate-held, before the identity cell. Delete both lists and use kit order (already priority-sorted by `make_kit.py:66-72`). |
| `bench/flux_queue.ps1:13` | `$last` names `gear_belt_buckle_closeup`, `gear_collar_closeup`, which are Dracula `gear_items` ids. The same two ids are written in the template `order` (`config/character_sheet_template_gpt.json:320-321`). | A character whose close-up items have other ids gets no "closeups last" (`make_kit.py:73-75` only prints a WARNING). The knight example matches only because its author copied the ids. |
| `bench/flux_queue.ps1:20` | Add `if($r.swatch_of){ continue }`. | Template note (`config/character_sheet_template_gpt.json:3`) says the queue skips `swatch_of` rows; the code does not. Dracula's 8 `swatch_of` swatches are not in `kits_out\vampire_gpt_sheet` yet, so the live queue will Flux-render them (text-only, about 4 min each) and `swatch_crop.py:54` then refuses to overwrite without `--force`. Check this before the live queue reaches the swatch rows. |
| `bench/flux_queue.ps1:18` | Literal `C:\Users\dom12\Documents\flux_env\queue_hold.txt`. Move to `config.local.json` and key holds by `<kit>:<row>`. | Machine path in a repo script; a hold for `head_profile` also holds the knight's `head_profile`. |
| `bench/flux_queue.ps1:3` | `-MinStartGB 12` default. | `config/config.json` `limits.start_min_free_ram_gb` is 6; two numbers for one rule. |
| `tools/swatch_crop.py:31` | Default kit is `kits\vampire_gpt_sheet_kit.json`. | Running it without an argument crops Dracula's sheet. |
| `tools/run_character.py:96-98` | Remove the `fix_black_block` unit. | `LESSONS.md:32`. |
| `plugins/factory/skills/character-sheet/SKILL.md` step 6 (line 27, local copy) | Says repair the black block; also lacks the whole "Sheet rules" section that `origin/main` has. | Same lesson; stale skill text sent to the next session. |
| `git` state | Merge `origin/main` into local `main` (56 behind), push the 18 local commits, and commit `bench/flux_queue.ps1` (it shows as `??`). | Gate, `mirror_cell.py`, `manifest.py --verdict`, `--review` exist only on `origin/main`; `swatch_crop.py` and `swatch_of` exist only locally (`origin/main` has no `tools/swatch_crop.py`). |

### 2.2 Per-character hand-written content (inside the spec, but it is the real cost)
- 61 keys in `characters/knight-dryrun.json`, about 45 of them free prose that must agree across views (hair, collar, chin, ring, wrist, pose). No tool derives them from the reference image. Treat this as the biggest remaining manual step.
- `base_layer_ref` path (above), `ref` path (absolute, machine-local), `row_seeds` (after rejects), `swatch_of` boxes (below).

### 2.3 Swatch crops
- `tools/swatch_crop.py:59-61` prints native size but has no minimum. Dracula's boxes in `characters/vampire-dracula-gpt-sheet.json` come out at 84x84, 130x130, 130x130, 100x100, 160x160, 200x200, 240x240, 260x260 native (computed from the box fractions and source sizes), all resized to 1024 with LANCZOS. All 7 made so far sit in `kits_out\vampire_gpt_sheet\rejected\` as `*_crop_too_soft.png`. The kit still carries the same boxes, so a re-run reproduces them.
- The knight example spec has no `swatch_of`; its 4 swatches would be Flux text-only renders. That path works but takes 4 cells of GPU, and a crop path with a 512 px floor does not exist yet.

## 3. (b) Rules learned tonight: where each one lives

"Gate" means `origin/main` only unless stated; the local checkout has no gate.

| Rule | Carried by | Cite | Status |
|---|---|---|---|
| Reference first (identity cell PASS before siblings) | Gate (`cf_flux.sheet_reasons`, exit 2 `SHEET RULE`) | `origin/main:plugins/factory/lib/cf_flux.py` (b85e8cc, lines about 215-225); queue reaction `bench/flux_queue.ps1:34` | Not in this checkout. Queue order contradicts it (`:12-14`). Verdict entry needs a human (`manifest.py --verdict`, `origin/main:tools/manifest.py:18`). |
| Identity-first order | Template `order` (`config/character_sheet_template_gpt.json:304-306`) and `make_kit.py:66-72` | emitted correctly in the knight kit (head_front, body_underlayer_front first) | Overridden by the queue (`flux_queue.ps1:12-14`). NOT carried end to end. |
| `mirror_of` | Template (`:95,156,177`), `make_kit.py:63-64`, queue skip (`flux_queue.ps1:20`), tool `tools/mirror_cell.py` | tool is `origin/main` only | Carried in data and queue. The flip itself and the PASS check are not available here. `foot_right` is a normal render (`template:200`) although the skill text says right feet are flips. |
| `swatch_of` (crop of an approved cell) | `make_kit.py:61-62`, `tools/swatch_crop.py` | local only, unpushed | Partly. Queue does not skip it (`flux_queue.ps1:20`). |
| 512 px native crop minimum | Nowhere | `tools/swatch_crop.py:59-61` prints, never refuses; `config/config.json:135` `crop_px: 512` belongs to texture compare, not swatches | NOT carried. Dracula boxes are 84-260 px. |
| Re-roll cap | Queue (log-based, `flux_queue.ps1:22-23`) and gate (receipt-based, `cf_flux` `REROLL_CAP = 2`) | | Queue copy is keyed on row id, off if any row seed exists, so a prompt fix without a seed change is still capped, and a seed with no change in prompt disables the cap. Gate copy hashes prompt, seed and reference but `row_prompt_seed` reads `kit["seed"]` only (`origin/main` `cf_flux.py:163`, versus `flux_kit.py:18` here which uses the row seed), so for rows with `row_seeds` the match looks wrong. Verify on a rendered row. Not in this checkout either way. |
| Row seeds with a note | `make_kit.py:58-60`, `flux_kit.py:18` | | Carried. Applying them is a manual spec edit after a reject. |
| `FAIL_TAIL` logging | Queue | `flux_queue.ps1:36-38` | Carried (queue only). No FAIL_TAIL lines in the Dracula log so far. |
| Settle poll | Queue | `flux_queue.ps1:24-25` | Carried (queue only, 26 s cap). |
| Outfit clause on body cells, ring clause on hands | Template prompts via required spec fields `outfit_desc`, `hand_ring_rule` (`make_kit.py:23`) | knight prompts contain the outfit text in all 5 body rows, ring text in hand rows (`hand_fist_left` has the ring but not `hand_ring_rule`, as intended) | Carried. |
| Closeups last | Template `order` by literal row ids (`:320-321`) plus queue `$last` (`:13`) | | Carried only for gear ids that happen to match. |
| No reference for gear, hands, feet, swatches | Template `group_defaults` (`:283`); `use_ref` copied by `make_kit.py:53-55`; `body_underlayer_back/three_quarter` carry `use_ref: false` in the template | knight kit: 18 rows `use_ref` false | Carried. |
| Black-block rule: reject, never repair | `LESSONS.md:32` and the no-reference rule above remove the cause | detection: `flux_kit.py:64-67` catches only a fully black image (mean under 3). A 256x1024 corner is not detected by any script; `fix_black_block.py` still exists and is still invoked by `run_character.py:98` and SKILL step 6 | Partly. Detection is the reviewer's eyes. |
| Per-view fixed description (hair, chin, collar, handedness) | Template with spec fields (`head_*`, `hand_handedness_right`, ...) | the 21 shared rows rebuilt from the knight spec | Carried by the template, but the values are the 61 hand-written fields. |
| Consistency QC by sibling comparison | A human or Claude pass that writes review JSON (`manifests/vampire_gpt_sheet_review/*.json`); import is `manifest.py --review` on `origin/main` only | no script compares siblings | NOT carried as a tool. |
| Queue hold file | Queue | `flux_queue.ps1:18-19` | Carried, machine-local, unkeyed by kit, stale entries persist. |
| RAM start gate | `cf_common.wait_for_free_ram` (6 GB, 30 min) in `flux_kit.py:37`; queue wait (12 GB, `:3,:26`); hook `ram_gate.py` for Claude-launched runs | | Carried twice with different numbers. |
| One GPU job at a time | Hook `gpu_lock_guard.py` (Claude Bash only), `tools/gpu_guard.ps1` | | Not enforced between two detached queues. |
| Sheet is the standard / look before done | Skill text, `LESSONS.md` | | Human discipline, not a gate. |

## 4. (c) What a human still does per cell and per sheet
1. Write the 61-field spec from the reference image and pick `ref`, `base_layer_ref`, seed (no tool).
2. Run `make_kit.py`, `CF_DRY` check (does not check file existence).
3. Start the queue by hand with the kit path. After a kit swap or kit edit, restart it by hand: it reads the kit once at start (`flux_queue.ps1:7`).
4. View each png (no automatic quality score). Write verdicts: PASS on `head_front` and `body_underlayer_front` before siblings can render on `origin/main`, then restart the queue because it ends after 3 passes.
5. Move rejected pngs to `rejected\` by hand with a reason in the file name (the Dracula folder shows `_qc1`, `_leaked`, `_crop_too_soft` suffix habits). The queue then re-renders that row automatically on its next start with the same seed and prompt unless the human also edits the spec (new row seed plus note, or a prompt fix), re-runs `make_kit.py`, and restarts the queue.
6. Mirror flips: `mirror_cell.py` (origin only). Crop swatches: `swatch_crop.py` with hand-chosen boxes.
7. Black corner check by eye on every 1536x1024 and 1024x1536 cell.
8. Consistency QC across siblings (review JSON), contact sheet, `manifest.py --pick`.
9. Edit `queue_hold.txt` when a row waits on a fix.
10. For 3D: write the run spec and the T-pose parts kit by hand; check clip files.

## 5. Ranked PRs to make character 2 hands-free

1. **Sync and commit state.** Merge `origin/main` into local `main`, push the 18 local commits (template order, `swatch_of`, `row_seeds`), commit `bench/flux_queue.ps1`. Without this none of the gate rules exist where the queue runs. Cheap, blocks everything else.
2. **Queue reads the kit, not lists.** Drop `$first/$last`; skip `swatch_of`; do not exit while rows are only HELD, instead poll `manifests/<kit>.json` for the identity verdict (and exit with a clear "waiting for verdict" state); hold file in config and keyed `<kit>:<row>`; MinStartGB from config; re-roll cap from receipts (prompt, seed, reference hash), not row id; write a one-line status file per pass.
3. **Swatch quality floor.** `swatch_crop.py` refuses native crops under 512 px (config key), reports which rows fall back to a Flux text-only render, and either picks a box automatically or falls back without a human. Re-cut Dracula's 7 boxes.
4. **Spec from one image.** `tools/spec_from_ref.py`: fills the 61 fields from the reference (Claude vision with the template's `required_fields` as the schema), derives `base_layer_ref` and `kit_name` from `id`, and checks cross-field consistency (hair style appears in all `head_*` fields). Extend the dry run to fail when `ref` or `base_layer_ref` files are missing (except `base_layer_ref` before the front cell exists, which it should report as pending).
5. **Automatic first-look QC.** A CPU tool run by the queue after each cell: black corner detector (reject to `rejected\` with reason and bump nothing), identity and sibling comparison against the identity cell, writes `verdict` proposals. Human confirms rather than discovers. Remove `fix_black_block` from `run_character.py:96-98` and SKILL step 6.
6. **Mirror automation.** Mark `foot_right` as `mirror_of foot_left` in the template (`:200`) and make the queue call `mirror_cell.py` after the source verdict PASS (saves 3 to 4 renders per character).
7. **Template-level closeup and gear naming.** Replace the literal ids in template `order` (`:320-321`) with a `form: closeup` group entry, so closeups are last for any character.
8. **Parts kit and run spec generation.** Template for the T-pose body and gear-alone parts, and `run_character` spec derived from the sheet kit, with part names mapped to `gear.fit.parts`.
9. **Portability.** One `config.local.json` key for the hold file; `swatch_crop.py` default kit removed (kit argument required); CF_DRY prints which sheet rules would refuse the row.
10. **Measure.** After items 1 to 5, render the knight with a real reference and record first-pass reject count and GPU hours in this file. Until then "under 20 percent" and "under 2.5 h" are estimates (about 1.95 h plain, about 2.3 h with 20 percent re-rolls).
