r"""Flux quality policy (plugin 0.1.6, part A; standard library only).

A render that the policy refuses never starts (flux_kit.py exits 2, the PreToolUse hook denies), and the refusal carries the numbers. Rules:

  every row type   steps >= flux.min_steps (16, the distilled default); resolution not below the kit row's w x h
  body/head/hands  the reference runs full size: no downscale at all
  gear/swatch      the reference may be downscaled, but to >= flux.gear_ref_min_long_side (640) on the long side
  receipt          records steps, ref size, ref sha256, attention backend, process mode and cached-ref-conds on/off (receipt_reason checks it)

Row type: the row's `row_type`, else its `group`, else its `part`, else the kit's `row_type`, else the row name prefix (head_, hands_, body_, mat_/swatch_ -> swatch). Unknown = "other"
(steps and resolution still apply; no reference rule). The run's real settings come from the kit/row JSON and these environment switches (inline `VAR=1 python ...` also counts):
  FACTORY_FLUX_STEPS, FACTORY_FLUX_W, FACTORY_FLUX_H, FACTORY_FLUX_REF_MAX_SIDE (a reference downscaled to this long side).
FACTORY_RAW=<reason> in the environment (or the command) lets a run through and is logged (cf_guard.raw_override). Any error means allow: a failing check never blocks work by itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import cf_guard

DEFAULTS = {"min_steps": 16, "gear_ref_min_long_side": 640, "attention_backend": "auto", "single_process_queue": True, "cache_ref_conds": True, "vae_tiling": None}
STRICT = ("body", "head", "hands")
RELAXED = ("gear", "swatch")
ENV_KEYS = ("FACTORY_FLUX_STEPS", "FACTORY_FLUX_W", "FACTORY_FLUX_H", "FACTORY_FLUX_REF_MAX_SIDE")
RECEIPT_FIELDS = ("steps", "ref_size", "ref_sha256", "attention_backend", "process_mode", "cached_ref_conds")


def flux_cfg() -> dict:
    c = dict(DEFAULTS)
    try:
        c.update(cf_guard.config().get("flux") or {})
    except Exception:
        pass
    return c


def row_type(kit: dict, row: dict) -> str:
    for v in (row.get("row_type"), row.get("group"), row.get("part"), kit.get("row_type")):
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
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
        "steps": _int(env.get("FACTORY_FLUX_STEPS"), _int(row.get("steps", kit.get("steps")), c["min_steps"])),
        "w": _int(env.get("FACTORY_FLUX_W"), row_w), "h": _int(env.get("FACTORY_FLUX_H"), row_h), "row_w": row_w, "row_h": row_h,
        "ref": ref, "ref_size": list(size) if size else None,
        "ref_max_side": _int(env.get("FACTORY_FLUX_REF_MAX_SIDE"), _int(row.get("ref_max_side"), None)),
    }


def policy_reasons(spec: dict) -> list[str]:
    c = flux_cfg()
    bad = []
    n = spec["row"]
    if spec["steps"] < c["min_steps"]:
        bad.append(f"FLUX POLICY: row {n} runs {spec['steps']} steps; the floor is {c['min_steps']} (the distilled default). Fewer steps is a proof, not a quality render.")
    if spec["w"] and spec["h"] and spec["row_w"] and spec["row_h"] and (spec["w"] < spec["row_w"] or spec["h"] < spec["row_h"]):
        bad.append(f"FLUX POLICY: row {n} renders {spec['w']}x{spec['h']}, below the kit row's {spec['row_w']}x{spec['row_h']}. Render at the row size or larger.")
    long_side = max(spec["ref_size"]) if spec["ref_size"] else None
    cap = spec["ref_max_side"]
    if cap and long_side and cap < long_side:
        if spec["row_type"] in STRICT:
            bad.append(f"FLUX POLICY: {spec['row_type']} row {n} downscales the reference from {long_side} to {cap} px on the long side; "
                       f"body, head and hands run with the full-size reference ({spec['ref_size'][0]}x{spec['ref_size'][1]}).")
        elif spec["row_type"] in RELAXED and cap < c["gear_ref_min_long_side"]:
            bad.append(f"FLUX POLICY: {spec['row_type']} row {n} downscales the reference to {cap} px on the long side; the floor for gear and swatches is {c['gear_ref_min_long_side']}.")
    return bad


def check_row(kit: dict, row: dict, env: dict | None = None) -> list[str]:
    return policy_reasons(run_spec(kit, row, env))


def receipt_fields(spec: dict, ref_path, backend: str, process_mode: str, cached: bool) -> dict:
    """The policy fields flux_kit.py adds to the receipt params."""
    sha = None
    if ref_path and os.path.isfile(ref_path):
        h = hashlib.sha256()
        with open(ref_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
    return {"row_type": spec["row_type"], "steps": spec["steps"], "ref_size": spec["ref_size"], "ref_max_side": spec["ref_max_side"], "ref_sha256": sha,
            "attention_backend": backend, "process_mode": process_mode, "cached_ref_conds": bool(cached)}


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
