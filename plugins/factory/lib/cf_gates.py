r"""The six quality gates of plugin 0.1.4 (standard library only; PIL is used for the picture check when it is importable).

Each check returns '' when the work may go ahead, else one refusal line that carries the number that failed. Nothing here starts a job.
Every check returns '' when it cannot tell (missing file, no PIL, unreadable json): a failing check must never block work by itself.
The exception is a refusal for MISSING EVIDENCE that a gate exists to demand (no token count, no producing receipt, no lighting tier).

  1. input     subject fills >= min_subject_frac of the frame height, portrait or square; head parts >= min_head_px tall
  2. token     hero/master bakes sample at the tier's token floor; a re-bake from latents below the floor is refused
  3. parts     nothing is published unless every part in the character spec's parts list (plus the body) was assembled with a receipt
  4. light     stills that leave the factory are three-point lit; proof lighting stays inside
  5. readback  stills and publish print the upstream numbers and refuse when the file does not match the receipt that made it
  6. pipeline  every published asset names the pipeline that made it (TRELLIS, SCULPT, ...), and the name must agree with the receipts

Numbers live in config/config.json -> gates (defaults below), never in the scripts.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os

import cf_guard

DEFAULTS = {
    "min_subject_frac": 0.60,
    "min_head_px": 512,
    # A floor is a MINIMUM, never a cap: any run may sample more. 98304 is the only completion measured on this GPU (robot, 28 GB attention budget, 704 s whole run).
    # CEILING TEST (run when the GPU is free; it belongs to the Flux sheet until the owner says yes): robot_1076 at --max-tokens 131072 and 196608 with --attn-auto-gb 28;
    # record completes / peak GPU / wall. The hero floor moves to the highest count that completes in under 2x the 98304 wall time.
    "token_floor": {"master": 98304, "hero": 98304},
    "preset_tier": {"hero_rebake": "hero", "tokens98k_fresh": "master"},
    "lighting_tier_required": "three_point",
    "pipelines": ["TRELLIS", "SCULPT"],
}


def gates_cfg() -> dict:
    g = json.loads(json.dumps(DEFAULTS))
    try:
        for k, v in (cf_guard.config().get("gates") or {}).items():
            if isinstance(v, dict) and isinstance(g.get(k), dict):
                g[k].update(v)
            else:
                g[k] = v
    except Exception:
        pass
    return g


def _read(path) -> dict | None:
    try:
        d = cf_guard.read_json(path)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _abs(p: str) -> str:
    return p if os.path.isabs(p) else str(cf_guard.HOME / p)


def receipts(pattern: str = "*/*.json") -> list[tuple[str, dict]]:
    """[(path, receipt)] for every readable receipt under the factory home, newest file first."""
    out = []
    for f in sorted(glob.glob(str(cf_guard.HOME / "receipts" / pattern)), key=os.path.getmtime, reverse=True):
        r = _read(f)
        if r:
            out.append((f, r))
    return out


# ---- 1. input gate ------------------------------------------------------------------------------------------------------------------------------
def subject_box(path) -> tuple[int, int, int] | None:
    """(frame width, frame height, subject height) in pixels of the original image, or None when it cannot be measured.

    Subject = the alpha bounding box for a cut-out, else everything that differs from the corner background colour.
    """
    try:
        from PIL import Image, ImageChops
        with Image.open(path) as im:
            w, h = im.size
            scale = min(1.0, 1024 / max(w, h))
            small = im.convert("RGBA").resize((max(1, round(w * scale)), max(1, round(h * scale))))
        alpha = small.split()[3]
        if alpha.getextrema()[0] < 255:
            mask = alpha.point(lambda v: 255 if v > 16 else 0)
        else:
            rgb = small.convert("RGB")
            sw, sh = rgb.size
            corners = [rgb.getpixel(c) for c in ((0, 0), (sw - 1, 0), (0, sh - 1), (sw - 1, sh - 1))]
            bg = max(set(corners), key=corners.count)
            mask = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).convert("L").point(lambda v: 255 if v > 32 else 0)
        box = mask.getbbox()
        if not box:
            return w, h, 0
        return w, h, round((box[3] - box[1]) / scale)
    except Exception:
        return None


def input_reason(path) -> str:
    g = gates_cfg()
    m = subject_box(path)
    if m is None:
        return ""
    w, h, sub = m
    if sub == 0:
        return ""   # nothing to measure (blank image): cannot tell, so do not block
    name = os.path.basename(str(path))
    if h < w:
        return (f"INPUT GATE: {name} is {w}x{h}, landscape. Inputs must be portrait or square (height >= width). "
                "Re-render or crop it, look at it, then retry.")
    frac = sub / h if h else 0
    if frac < g["min_subject_frac"]:
        return (f"INPUT GATE: the subject in {name} is {sub} px of a {h} px frame = {frac:.0%} of the height; the gate is {g['min_subject_frac']:.0%}. "
                "Crop in on the subject (a small subject is what produced the 80 px face), upscale, look at it, then retry.")
    if "head" in name.lower() and sub < g["min_head_px"]:
        return (f"INPUT GATE: head input {name} has a subject {sub} px tall; head parts need at least {g['min_head_px']} px. "
                "Render or crop the head larger, upscale, look at it, then retry.")
    return ""


# ---- 2. token gate ------------------------------------------------------------------------------------------------------------------------------
def source_tokens(asset_id: str) -> tuple[int, str] | None:
    """(shape_hr_tokens, receipt path) of the newest trellis receipt of this asset that recorded a token count."""
    for f in sorted(glob.glob(str(cf_guard.HOME / "receipts" / asset_id / "trellis-*.json")), key=os.path.getmtime, reverse=True):
        tok = (((_read(f) or {}).get("params") or {}).get("stage_tokens") or {}).get("shape_hr_tokens")
        if isinstance(tok, int):
            return tok, f
    return None


def token_reason(asset_id: str, preset: str, from_latents: bool) -> str:
    g = gates_cfg()
    tier = g["preset_tier"].get(preset)
    floor = g["token_floor"].get(tier or "")
    if not tier or not floor:
        return ""
    if from_latents:
        src = source_tokens(asset_id)
        if src is None:
            return (f"TOKEN GATE: re-bake of '{asset_id}' ({tier} tier, floor {floor} tokens) from latents with no recorded token count. "
                    "No trellis receipt of that asset has params.stage_tokens.shape_hr_tokens, so the shape cannot be shown to meet the floor. "
                    f"Sample again at >= {floor} tokens (--max-tokens), then re-bake.")
        tok, f = src
        if tok < floor:
            return (f"TOKEN GATE: re-bake of '{asset_id}' from latents sampled at {tok} tokens ({os.path.basename(f)}); the {tier} tier floor is {floor}. "
                    f"A bake cannot add detail the shape never had. Sample again with --max-tokens >= {floor}, then re-bake.")
        return ""
    extra = list((cf_guard.config().get("trellis", {}).get("presets", {}).get(preset) or {}).get("extra") or [])
    for i, a in enumerate(extra[:-1]):
        if a == "--max-tokens":
            try:
                n = int(extra[i + 1])
            except ValueError:
                return ""
            if n < floor:
                return f"TOKEN GATE: preset '{preset}' ({tier} tier) samples at --max-tokens {n}; the floor is {floor}. Raise it in config trellis.presets, then retry."
    return ""


# ---- producing receipt, read-back, pipeline ----------------------------------------------------------------------------------------------------
def producer_of(file_path) -> tuple[str, dict] | None:
    """The receipt that lists this file (same path and same sha256) among its outputs, newest first."""
    try:
        sha = _sha256(file_path)
    except Exception:
        return None
    key = _norm(file_path)
    for f, r in receipts():
        for o in r.get("outputs") or []:
            if o.get("sha256") == sha and _norm(o.get("path", "")) == key:
                return f, r
    return None


def asset_pipeline(asset_id: str) -> str:
    """'TRELLIS' when the asset has a trellis receipt, else ''."""
    return "TRELLIS" if asset_id and glob.glob(str(cf_guard.HOME / "receipts" / asset_id / "trellis-*.json")) else ""


def readback_line(label: str, glb: str, prod: tuple[str, dict]) -> str:
    f, r = prod
    aid = r.get("asset_id", "")
    src = source_tokens(aid) if aid else None
    return (f"READ-BACK {label}: {os.path.basename(glb)} sha {_sha256(glb)[:12]} = output of {r.get('stage')} receipt {os.path.basename(f)} "
            f"(asset {aid}, status {r.get('status')}, hr tokens {src[0] if src else 'n/a'}, pipeline {asset_pipeline(aid) or 'unknown'}).")


def stills_readback(glb: str) -> tuple[str, str]:
    """(refusal, read-back line) before a stills render: the GLB must be the recorded output of an ok receipt."""
    if not os.path.isfile(glb):
        return "", ""
    prod = producer_of(glb)
    if prod is None:
        return (f"READ-BACK GATE: {os.path.basename(glb)} is not the recorded output of any receipt (no receipt lists this path with this sha256). "
                "Stills may only be rendered from a file a stage receipt vouches for; re-run the stage that makes it, or fix the receipt."), ""
    f, r = prod
    if r.get("status") != "ok":
        return f"READ-BACK GATE: the receipt that made {os.path.basename(glb)} ({os.path.basename(f)}) says status {r.get('status')}, not ok.", ""
    return "", readback_line("stills input", glb, prod)


# ---- 3. parts gate ------------------------------------------------------------------------------------------------------------------------------
def character_spec(char_id: str) -> dict | None:
    for f in glob.glob(str(cf_guard.HOME / "characters" / "*.json")):
        r = _read(f)
        if r and r.get("character_id") == char_id:
            return r
    return None


def assembled_parts(char_id: str) -> tuple[set[str], str]:
    """(part names present in the newest ok assembly receipt, its file name)."""
    for f in sorted(glob.glob(str(cf_guard.HOME / "receipts" / char_id / "bind-*.json")), key=os.path.getmtime, reverse=True):
        r = _read(f)
        p = (r or {}).get("params") or {}
        if r and p.get("assembly") and r.get("status") == "ok":
            names = {"body"}
            for g in p.get("parts") or []:
                names.add(os.path.splitext(os.path.basename(g))[0].removeprefix("gear_"))
            names.update(str(n) for n in p.get("assembled_parts") or [])
            return names, os.path.basename(f)
    return set(), ""


def parts_reason(char_id: str) -> str:
    spec = character_spec(char_id)
    if spec is None:
        return (f"PARTS GATE: no characters/*.json has character_id '{char_id}', so the kit cannot be checked. "
                "A published asset must come from a character spec listing every part it is made of.")
    need = {"body"} | {p.get("name") for p in spec.get("parts", []) if p.get("name")}   # the kit's own parts list: a creature kit without hands is complete without hands
    have, rec = assembled_parts(char_id)
    missing = sorted(need - have)
    if missing:
        return (f"PARTS GATE: '{char_id}' is not complete: missing {', '.join(missing)} ({len(need) - len(missing)}/{len(need)} parts assembled; "
                f"newest assembly receipt {rec or 'none'} has: {', '.join(sorted(have)) or 'nothing'}). Body-only is a proof and is never sent. "
                "Build and bind the missing parts, assemble with receipts, then publish.")
    return ""


# ---- 4/5/6. publish ----------------------------------------------------------------------------------------------------------------------------
def publish_reasons(spec: dict) -> tuple[list[str], list[str]]:
    """([refusals], [read-back lines]) for a publish spec (publish/<asset_id>.json). Empty refusals = may publish."""
    g = gates_cfg()
    bad, info = [], []
    cid = spec.get("id", "")

    why = parts_reason(cid)
    if why:
        bad.append(why)

    pipe = str(spec.get("pipeline", "")).upper()
    if not pipe:
        bad.append(f"PIPELINE GATE: publish spec for '{cid}' has no \"pipeline\" key; every published asset says which pipeline made it ({', '.join(g['pipelines'])}).")
    elif pipe not in g["pipelines"]:
        bad.append(f"PIPELINE GATE: pipeline '{pipe}' is not one of {', '.join(g['pipelines'])}.")

    glb = _abs(spec.get("glb", ""))
    if os.path.isfile(glb):
        prod = producer_of(glb)
        if prod is None:
            bad.append(f"READ-BACK GATE: {os.path.basename(glb)} is not the recorded output of any receipt (sha256 {_sha256(glb)[:12]}); the file differs from what the stage wrote.")
        else:
            info.append(readback_line("publish glb", glb, prod))
            aid = prod[1].get("asset_id", "")
            real = asset_pipeline(aid)
            if pipe and real and pipe != real:
                bad.append(f"PIPELINE GATE: spec says {pipe} but the receipts of '{aid}' show {real} (a trellis receipt exists). A sculpt is never labelled TRELLIS, or the reverse.")

    stills = [_abs(s) for s in spec.get("stills") or []]
    srec = None
    if stills:
        key = _norm(stills[0])
        for f, r in receipts("*/stills-*.json"):
            if any(_norm(o.get("path", "")) == key for o in r.get("outputs") or []):
                srec = (f, r)
                break
    if srec is None:
        bad.append(f"LIGHTING GATE: no stills receipt lists {os.path.basename(stills[0]) if stills else 'any still'}; the lighting tier of these stills is unknown, so they count as proof.")
    else:
        f, r = srec
        tier = (r.get("params") or {}).get("lighting_tier", "proof")
        if tier != g["lighting_tier_required"]:
            bad.append(f"LIGHTING GATE: stills receipt {os.path.basename(f)} has lighting_tier '{tier}', the library needs '{g['lighting_tier_required']}'. "
                       "Proof lighting (one sun, flat world) never leaves the factory.")
        ins = [i.get("sha256") for i in r.get("inputs") or []]
        if os.path.isfile(glb) and ins and _sha256(glb) not in ins:
            bad.append(f"READ-BACK GATE: the stills in {os.path.basename(f)} were rendered from a different GLB than the one being published (sha256 {_sha256(glb)[:12]} not among its inputs).")
        else:
            info.append(f"READ-BACK stills: {os.path.basename(f)} lighting_tier {tier}, {len(r.get('outputs') or [])} outputs, status {r.get('status')}.")
    return bad, info
