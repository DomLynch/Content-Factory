r"""Flux quality policy (plugin 0.1.6, part A; standard library only).

A render that the policy refuses never starts (flux_kit.py exits 2, the PreToolUse hook denies), and the refusal carries the numbers. Rules:

  every row type   steps >= flux.steps (16, the distilled default); resolution not below the kit row's w x h
  body/head/hands  flux.ref_long_by_group = 0: the reference runs full size, no downscale at all (group hands_feet counts as hands)
  gear/swatch      flux.ref_long_by_group = 640: the reference may be downscaled, but to >= 640 on the long side
  receipt          records steps, ref size, ref sha256, attention backend, process mode and cached-ref-conds on/off (receipt_reason checks it)

Row type: the row's `row_type`, else its `group` (the Factory gates on group, not on the name), else its `part`, else the kit's `row_type`, else the row name prefix (head_, hands_, body_, mat_/swatch_ -> swatch). Unknown = "other"
(steps and resolution still apply; no reference rule). The run's real settings come from the kit/row JSON and these environment switches (inline `VAR=1 python ...` also counts):
  CF_STEPS, CF_REF_LONG (reference long side; unset or 0 = full size) - the names flux_kit_fast.py uses - and FACTORY_FLUX_W / FACTORY_FLUX_H.
FACTORY_RAW=<reason> in the environment (or the command) lets a run through and is logged (cf_guard.raw_override). Any error means allow: a failing check never blocks work by itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import cf_guard

DEFAULTS = {"steps": 16, "ref_long_by_group": {"body": 0, "head": 0, "hands_feet": 0, "gear": 640, "swatch": 640}, "attention_backend": "native", "single_process_queue": True,
            "cache_ref_conds": True, "vae_tiling": None, "vae_slicing": None}
ALIASES = {"hands_feet": "hands", "hand": "hands", "feet": "hands"}
STRICT = ("body", "head", "hands")
RELAXED = ("gear", "swatch")
ENV_KEYS = ("CF_STEPS", "CF_REF_LONG", "FACTORY_FLUX_W", "FACTORY_FLUX_H")
RECEIPT_FIELDS = ("steps", "ref_size", "ref_sha256", "attention_backend", "process_mode", "cached_ref_conds", "w", "h", "vae_tiling", "vae_slicing")


def flux_cfg() -> dict:
    c = dict(DEFAULTS)
    try:
        user = dict(cf_guard.config().get("flux") or {})
        c["ref_long_by_group"] = {**DEFAULTS["ref_long_by_group"], **(user.pop("ref_long_by_group", None) or {})}
        c.update(user)
    except Exception:
        pass
    return c


def row_type(kit: dict, row: dict) -> str:
    for v in (row.get("row_type"), row.get("group"), row.get("part"), kit.get("row_type")):
        if isinstance(v, str) and v.strip():
            t = v.strip().lower()
            return ALIASES.get(t, t)
    name = str(row.get("name") or row.get("row_id") or "").lower()
    for prefix, t in (("head", "head"), ("hands", "hands"), ("hand_", "hands"), ("body", "body"), ("mat_", "swatch"), ("swatch", "swatch")):
        if name.startswith(prefix):
            return t
    return "other"


def inline_env(command: str) -> dict:
    """VAR=value pairs of the policy switches written in front of (or around) a command."""
    out = {}
    for k in ENV_KEYS:
        m = re.search(rf"\b{k}=['\"]?(\d+)", command or "")
        if m:
            out[k] = m.group(1)
    return out


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def run_spec(kit: dict, row: dict, env: dict | None = None) -> dict:
    """What the run is going to do, from the kit row and the environment switches."""
    env = env if env is not None else os.environ
    c = flux_cfg()
    row_w, row_h = row.get("w", kit.get("w")), row.get("h", kit.get("h"))
    ref = row.get("ref", kit.get("ref")) if row.get("use_ref", True) else None
    size = cf_guard.image_size(ref) if ref and os.path.isfile(ref) else None
    return {
        "row": row.get("row_id", row.get("name")), "row_type": row_type(kit, row),
        "steps": _int(env.get("CF_STEPS"), _int(row.get("steps", kit.get("steps")), c["steps"])),
        "w": _int(env.get("FACTORY_FLUX_W"), row_w), "h": _int(env.get("FACTORY_FLUX_H"), row_h), "row_w": row_w, "row_h": row_h,
        "ref": ref, "ref_size": list(size) if size else None,
        "ref_long": _int(env.get("CF_REF_LONG"), _int(row.get("ref_long"), None)) or None,   # 0 or unset = full size
    }


def policy_reasons(spec: dict) -> list[str]:
    c = flux_cfg()
    bad = []
    n = spec["row"]
    if spec["steps"] < c["steps"]:
        bad.append(f"FLUX POLICY: row {n} runs {spec['steps']} steps; the floor is {c['steps']} (the distilled default). Fewer steps is a proof, not a quality render.")
    if spec["w"] and spec["h"] and spec["row_w"] and spec["row_h"] and (spec["w"] < spec["row_w"] or spec["h"] < spec["row_h"]):
        bad.append(f"FLUX POLICY: row {n} renders {spec['w']}x{spec['h']}, below the kit row's {spec['row_w']}x{spec['row_h']}. Render at the row size or larger.")
    long_side = max(spec["ref_size"]) if spec["ref_size"] else None
    cap = spec["ref_long"]
    if cap and long_side and cap < long_side:
        t = spec["row_type"]
        floor = c["ref_long_by_group"].get("hands_feet" if t == "hands" else t)
        if floor == 0:
            bad.append(f"FLUX POLICY: {t} row {n} downscales the reference from {long_side} to {cap} px on the long side; "
                       f"body, head and hands run with the full-size reference ({spec['ref_size'][0]}x{spec['ref_size'][1]}).")
        elif floor and cap < floor:
            bad.append(f"FLUX POLICY: {t} row {n} downscales the reference to {cap} px on the long side; the floor for {t} rows is {floor}.")
    return bad


def check_row(kit: dict, row: dict, env: dict | None = None, kit_path: str | None = None) -> list[str]:
    return policy_reasons(run_spec(kit, row, env)) + (sheet_reasons(kit, row, kit_path) if kit_path else [])


def receipt_fields(spec: dict, ref_path, backend: str, process_mode: str, cached: bool, vae_tiling=None, vae_slicing=None) -> dict:
    """The policy fields flux_kit.py adds to the receipt params."""
    sha = None
    if ref_path and os.path.isfile(ref_path):
        h = hashlib.sha256()
        with open(ref_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
    return {"row_type": spec["row_type"], "steps": spec["steps"], "ref_size": spec["ref_size"], "ref_long": spec["ref_long"], "ref_sha256": sha,
            "attention_backend": backend, "process_mode": process_mode, "cached_ref_conds": bool(cached), "w": spec["w"], "h": spec["h"],
            "vae_tiling": vae_tiling, "vae_slicing": vae_slicing}


def receipt_reason(rec: dict) -> str:
    """'' when a flux receipt carries every policy field (a reference-free row needs no ref fields), else what is missing."""
    p = rec.get("params") or {}
    need = [f for f in RECEIPT_FIELDS if f not in p]
    if p.get("reference") is None and "ref_size" in need and "ref_sha256" in need:
        need = [f for f in need if f not in ("ref_size", "ref_sha256")]
    if p.get("reference") and p.get("ref_sha256") is None and "ref_sha256" not in need:
        need.append("ref_sha256")
    return f"FLUX POLICY: the flux receipt is missing {', '.join(need)}." if need else ""


def kit_row(kit_path: str, row_id: str) -> tuple[dict, dict] | None:
    try:
        kit = json.loads(Path(kit_path).read_text(encoding="utf-8-sig"))
        row = next(r for r in kit["rows"] if r.get("row_id", r.get("name")) == row_id)
        return kit, row
    except Exception:
        return None


# ---- sheet rules (plugin 0.1.20): reference-first, mirrors, re-roll cap -------------------------------------------------------------------------
# Evidence (the vampire sheet): 31 rejects before the rules, 0 in 9 cells after. Rules:
#   1 reference first  a non-identity row of a group renders only after the group's identity cell has a QC verdict PASS AND Dom's pick (manifest pick, picked_by Dom, current candidate; waived only by kit key skip_identity_review holding Dom's words) in manifests/<kit>.json (kit key `identity`
#                      {group: row_id}, else config flux.identity_cells). Siblings are judged against that cell.
#   2 mirrors          a row with `mirror_of` in the kit (data layer, source of truth) is a flip of its source, never a render:
#                      `python tools/mirror_cell.py <kit> [row_id]`.
#   3 re-roll cap      the same prompt + seed + reference may be rolled twice; a third identical roll is refused. A change (QC prompt_fix in the
#                      prompt, a new seed, a new identity reference) makes it a different roll.
REROLL_CAP = 2
IDENTITY_DEFAULT = {"head": "head_front", "body": "body_underlayer_front"}


def row_prompt_seed(kit: dict, row: dict, default_seed=42) -> tuple[str, object]:
    """The prompt and seed exactly as flux_kit.py builds them."""
    seed = kit.get("seed", default_seed)
    prompt = " ".join([row["prompt"], kit["subject"] if row.get("use_subject", True) else "", kit["style"]]).strip()
    ref = row.get("ref", kit.get("ref"))
    if ref and row.get("use_ref", True):
        prompt = row.get("ref_prefix", kit["ref_prefix"]) + " " + prompt
    return prompt, seed


def _flux_receipts(kit_name: str, row_id: str) -> list[dict]:
    out = []
    for p in sorted((cf_guard.HOME / "receipts").glob(f"{kit_name}-{row_id}-*/flux-*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8-sig")))
        except Exception:
            continue
    return out


def _sha(path):
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sheet_reasons(kit: dict, row: dict, kit_path: str | None = None) -> list[str]:
    """Refusals of the three sheet rules; [] when the row may render. Never raises (a failing check allows)."""
    try:
        return _sheet_reasons(kit, row, kit_path)
    except Exception:
        return []


def verdict(kit_name: str, row_id: str) -> str:
    """The QC verdict recorded for a row in manifests/<kit>.json (rows[row_id].verdict: PASS, WEAK or REJECT), '' when none."""
    try:
        m = json.loads((cf_guard.HOME / "manifests" / f"{kit_name}.json").read_text(encoding="utf-8-sig"))
        return str(m["rows"][row_id].get("verdict") or "").upper()
    except Exception:
        return ""


def dom_pick(kit_name: str, row_id: str) -> str:
    """'' when Dom has picked the CURRENT candidate of this row (manifests/<kit>.json rows[row].pick == newest candidate sha, picked_by Dom), else why not."""
    try:
        e = json.loads((cf_guard.HOME / "manifests" / f"{kit_name}.json").read_text(encoding="utf-8-sig"))["rows"][row_id]
    except Exception:
        return "no manifest entry"
    if not e.get("pick"):
        return "no pick recorded"
    if not str(e.get("picked_by") or "").strip().lower().startswith("dom"):
        return f"picked by '{e.get('picked_by') or ''}', not Dom"
    cands = e.get("candidates") or []
    if cands and cands[-1].get("sha256") != e["pick"]:
        return "the cell was re-rendered after Dom picked it (the pick is stale)"
    return ""


def _sheet_reasons(kit: dict, row: dict, kit_path: str | None) -> list[str]:
    bad = []
    rid = row.get("row_id", row.get("name"))
    kname = kit.get("kit", "")
    if row.get("mirror_of"):
        bad.append(f"SHEET RULE (mirror): row {rid} is a mirror of {row['mirror_of']}, not a render. Run tools/mirror_cell.py {kit_path or '<kit.json>'} {rid} on the passed {row['mirror_of']} cell.")
    t = row_type(kit, row)
    ident = {**IDENTITY_DEFAULT, **(flux_cfg().get("identity_cells") or {}), **(kit.get("identity") or {})}
    idrow = ident.get(t)
    if idrow and idrow != rid:
        if not any(r.get("row_id", r.get("name")) == idrow for r in kit.get("rows", [])):
            bad.append(f"SHEET RULE (reference first): the kit has no identity cell {idrow} for the {t} group, so {t} row {rid} cannot be judged against one. "
                       f"Add the row, or name another with the kit key identity {{\"{t}\": \"<row_id>\"}}.")
        elif verdict(kname, idrow) != "PASS":
            bad.append(f"SHEET RULE (reference first): {t} row {rid} waits for the identity cell {idrow}: it needs a QC verdict PASS in manifests/{kname}.json "
                       f"(now '{verdict(kname, idrow) or 'none'}'; python tools/manifest.py <kit> --verdict {idrow} PASS). Judge {rid} against it.")
        else:
            why = dom_pick(kname, idrow)
            waived = "skip identity review" in str(kit.get("skip_identity_review") or "").lower()
            if why and not waived:
                bad.append(f"SHEET RULE (Dom's pick): {t} row {rid} waits for Dom to see the identity cell {idrow}: {why}. Show him that one cell, record his word with "
                           f"python tools/manifest.py <kit> --pick {idrow} --by Dom --note \"...\". Only Dom's explicit words 'skip identity review', written into the kit key skip_identity_review, waive it.")
    if t == "head" and kit.get("humanoid") is not False:   # a humanoid head group cannot leave age and complexion unsaid (the vampire came out 15-20 years younger and warmer than its art)
        empty = [k for k in ("age", "complexion") if not str(kit.get(k) or "").strip()]
        if empty:
            bad.append(f"SHEET RULE (spec): head row {rid}: the kit has no {' and '.join(empty)}. Add {' and '.join(empty)} to the character spec (make_kit copies them to the kit); "
                       "a humanoid head group must state them. Creature kits set `humanoid: false`.")
    prompt, seed = row_prompt_seed(kit, row)
    ref = row.get("ref", kit.get("ref")) if row.get("use_ref", True) else None
    sha = _sha(ref)
    same = [r for r in _flux_receipts(kname, rid)
            if (r.get("params") or {}).get("prompt") == prompt and (r.get("params") or {}).get("seed") == seed and (r.get("params") or {}).get("ref_sha256") == sha]
    if len(same) >= REROLL_CAP:
        bad.append(f"SHEET RULE (re-roll cap): row {rid} was already rolled {len(same)} times with this exact prompt, seed and reference. "
                   f"A third roll needs a documented change: edit the prompt (QC prompt_fix), change the seed, or change the identity reference.")
    return bad
