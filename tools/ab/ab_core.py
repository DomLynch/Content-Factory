"""Pure logic of the Pixal3D vs TRELLIS A/B harness (plugin 0.1.14). Standard library only; nothing here starts a job, imports a model or touches a GPU.

  alive_reason()        refusal for a GPU half: a live stage process (not our own), a live GPU lock, or free RAM under the start limit
  glb_extents/tpose_reason   stdlib GLB bounding box and the body-only T-pose check that the bind gate needs
  new_blind_key / label_map   random but RECORDED engine -> A/B mapping so scoring is blind
  find_engine_result()  the finished result of one half, from receipts
  bind_numbers / bind_no_worse   bind-gate numbers from a bind receipt and the "no worse" comparison
  matched_render_config()   the ONE turntable config both meshes are rendered with (camera path, three-point rig, world, resolution, samples)
  decide()              the decision rule on filled scores: WIN / NO-WIN
  new_scorecard / merge_existing / render_markdown   scorecard JSON + Markdown with empty blind-score fields

Decision rule (OpenTrident Dev): Pixal3D wins only if it beats TRELLIS by win_margin (1 point) or more on win_criteria_needed (2) criteria AND its bind gate is no worse.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import secrets
import struct
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "plugins" / "factory" / "lib"))
os.environ.setdefault("FACTORY_HOME", str(REPO))
import cf_guard  # noqa: E402
import cf_proc  # noqa: E402


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


# ---- refusal ------------------------------------------------------------------------------------------------------------------------------------
def alive_reason(*, procs=None, lock_text="__read__", free="__read__", own=None, need=None, check_stage=True, check_lock=True) -> str:
    """'' to go ahead, else the one refusal line. A GPU half is refused while the sheet queue (or anything else on the GPU) is alive:
    a live stage process from cf_proc.stage_procs() other than our own (pids in `own`, the harness and its children, and the gpu_guard.ps1 watcher whose
    command line merely names a stage), or a live GPU lock whose pid is not ours, or free RAM under limits.start_min_free_ram_gb.
    Fail-open ONLY for lookup errors (the lookup raised or returned nothing); a live process that was seen always refuses."""
    own_arg = own
    own = {os.getpid(), os.getppid()}

    def full_own():   # `own` may be a callable (own_pids): the process-tree lookup is only paid for when something live was seen
        return own | set(own_arg() if callable(own_arg) else (own_arg or ()))

    if check_stage:
        try:
            rows = cf_proc.stage_procs() if procs is None else procs
        except Exception:
            rows = []
        rows = [(p, c) for p, c in rows if p not in own and "gpu_guard" not in c]
        if rows:
            mine = full_own()
            rows = [(p, c) for p, c in rows if p not in mine]
        if rows:
            pid, cmd = rows[0]
            more = f" (+{len(rows) - 1} more)" if len(rows) > 1 else ""
            return (f"REFUSED: the A/B does not start a GPU half while a stage process is alive (the sheet queue or another job): pid {pid}{more}: {cmd[:140]}. "
                    "Wait for it to finish, then retry. Nothing was started.")
    if check_lock:
        text = cf_guard.read_lock_text() if lock_text == "__read__" else lock_text
        why = cf_guard.lock_reason(text)
        if why:
            try:
                lpid = int(json.loads((text or "").lstrip("﻿")).get("pid", 0))
            except Exception:
                lpid = 0
            if lpid not in full_own():
                return "REFUSED: " + why + " Nothing was started."
    f = cf_guard.free_ram_gb() if free == "__read__" else free
    why = cf_guard.ram_reason(f, need)
    if why:
        return "REFUSED: " + why + " Nothing was started."
    return ""


def own_pids() -> set[int]:
    me = os.getpid()
    return {me, os.getppid()} | cf_proc.descendants(me)


# ---- the input must be the already cut-out RGBA png ---------------------------------------------------------------------------------------------------
def rgba_reason(path) -> str:
    """'' when the png carries an alpha channel (colour type 4 or 6, or a tRNS chunk): the Factory's one cut-out, so no engine runs its own background removal on one side only.
    Else the refusal. An unreadable file is allowed through ('' = cannot tell)."""
    try:
        with open(path, "rb") as f:
            data = f.read(1 << 16)
    except OSError:
        return ""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 26:
        return (f"REFUSED: {os.path.basename(str(path))} is not a png. The A/B input is the Factory's already cut-out RGBA png (cutout stage), the same file for both engines, "
                "so background removal cannot confound the result. Run the cutout tool first.")
    if data[25] in (4, 6) or b"tRNS" in data:
        return ""
    return (f"REFUSED: {os.path.basename(str(path))} has no alpha channel (png colour type {data[25]}). The A/B input is the Factory's already cut-out RGBA png (cutout stage), the same file "
            "for both engines, so background removal cannot confound the result. Run the cutout tool first.")


def find_cutout_receipt(repo, png_sha: str) -> str | None:
    """The Factory's cutout receipt whose output is exactly this png (same sha256), newest first; None when there is none."""
    for f in sorted(glob.glob(str(Path(repo) / "receipts" / "*" / "cutout-*.json")), key=os.path.getmtime, reverse=True):
        try:
            if any(o.get("sha256") == png_sha for o in read_json(f).get("outputs", [])):
                return f
        except Exception:
            continue
    return None


# ---- T-pose check (bind needs a body-only T-pose mesh) ---------------------------------------------------------------------------------------------
def glb_extents(path) -> tuple[float, float, float] | None:
    """Sorted (largest, middle, smallest) bounding-box extents from the POSITION accessor min/max of a GLB, or None when unreadable. Node transforms are ignored."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if head[:4] != b"glTF":
                return None
            (total,) = struct.unpack("<I", head[8:12])
            doc = None
            while f.tell() < total:
                ch = f.read(8)
                if len(ch) < 8:
                    break
                ln, kind = struct.unpack("<I4s", ch)
                data = f.read(ln)
                if kind == b"JSON":
                    doc = json.loads(data.decode("utf-8"))
                    break
        if not doc:
            return None
        lo, hi = [9e30] * 3, [-9e30] * 3
        for m in doc.get("meshes", []):
            for prim in m.get("primitives", []):
                acc = doc["accessors"][prim["attributes"]["POSITION"]]
                for i in range(3):
                    lo[i], hi[i] = min(lo[i], acc["min"][i]), max(hi[i], acc["max"][i])
        if lo[0] > hi[0]:
            return None
        return tuple(sorted((hi[i] - lo[i] for i in range(3)), reverse=True))   # type: ignore[return-value]
    except Exception:
        return None


def tpose_reason(path, min_ratio: float) -> str:
    """'' when the mesh looks like an upright T-pose body (arm span about as wide as it is tall: middle extent / largest extent >= min_ratio), else why bind is refused.
    A heuristic from the bounding box only: it cannot see a dressed body, and an unreadable GLB is allowed through ('' = cannot tell)."""
    e = glb_extents(path)
    if e is None or e[0] <= 0:
        return ""
    ratio = e[1] / e[0]
    if ratio >= min_ratio:
        return ""
    return (f"bind refused: {os.path.basename(str(path))} does not look like a body-only T-pose (bounding box {e[0]:.2f} x {e[1]:.2f} x {e[2]:.2f}, span/height {ratio:.2f} < {min_ratio}). "
            "run_bind.py needs a T-posed body-only mesh (an A-pose bind stretches 18.8 against 1.31, LESSONS 2026-11-01); feed the body cell's mesh, or use --bind no.")


# ---- blind labels -------------------------------------------------------------------------------------------------------------------------------
def new_blind_key(engines=("trellis", "pixal3d"), choice=None) -> dict:
    """{"A": engine, "B": engine}: a random but recorded assignment. `choice` is injectable for tests (default secrets.randbelow)."""
    order = list(engines)
    flip = (choice or (lambda: secrets.randbelow(2)))()
    if flip:
        order.reverse()
    return {"A": order[0], "B": order[1]}


def label_map(key: dict) -> dict:
    return {engine: label for label, engine in key.items()}


# ---- finding a half's result --------------------------------------------------------------------------------------------------------------------
def engine_asset(asset_id: str, engine: str, ab: dict) -> str:
    return asset_id + ab["engines"][engine]["asset_suffix"]


def newest(repo, asset, stage, ok_only=True):
    files = sorted(glob.glob(str(Path(repo) / "receipts" / asset / f"{stage}-*.json")), key=os.path.getmtime, reverse=True)
    for f in files:
        try:
            r = read_json(f)
        except Exception:
            continue
        if not ok_only or r.get("status") == "ok":
            return f, r
    return None


def find_engine_result(repo, asset_id: str, engine: str, ab: dict, override: str | None = None) -> tuple[dict | None, str]:
    """(result, '') or (None, why). result = {engine_asset, receipt, rec, glb, half_receipt, half}. Reads receipts only.
    Order: --<engine>-receipt override, else the newest ok `ab` half receipt of this asset for this engine, else the newest ok engine receipt of <asset><suffix>."""
    e = ab["engines"][engine]
    eid = engine_asset(asset_id, engine, ab)
    half = None
    for f in sorted(glob.glob(str(Path(repo) / "receipts" / asset_id / "ab-*.json")), key=os.path.getmtime, reverse=True):
        try:
            r = read_json(f)
        except Exception:
            continue
        p = r.get("params") or {}
        if r.get("status") == "ok" and p.get("mode") == "half" and p.get("engine") == engine:
            half = (f, r)
            break
    if override:
        try:
            path, rec = override, read_json(override)
        except Exception as ex:
            return None, f"{engine} receipt {override} is not readable: {type(ex).__name__}"
        if rec.get("status") != "ok":
            return None, f"{engine} receipt {override} says status {rec.get('status')}, not ok"
    else:
        hit = None
        if half and (half[1].get("params") or {}).get("engine_receipt") and os.path.isfile(half[1]["params"]["engine_receipt"]):
            try:
                hit = (half[1]["params"]["engine_receipt"], read_json(half[1]["params"]["engine_receipt"]))
            except Exception:
                hit = None
        hit = hit or newest(repo, eid, e["stage"])
        if not hit:
            return None, (f"no ok {e['stage']} receipt for {eid}: the {engine} half has not finished. Run ab_shape(engines='{engine}') with the same png and seed, then collect")
        path, rec = hit
    glbs = [o["path"] for o in rec.get("outputs", []) if str(o.get("path", "")).lower().endswith(".glb")]
    glb = next((g for g in glbs if os.path.isfile(g)), None)
    if not glb:
        return None, f"{engine} receipt {path} lists no GLB that exists on disk ({glbs or 'no outputs'})"
    want = next((o.get("sha256") for o in rec["outputs"] if o["path"] == glb), None)
    if want and sha256_file(glb) != want:
        return None, f"{engine} GLB {glb} no longer matches the sha256 in its receipt: the file changed after the run"
    return {"engine_asset": rec.get("asset_id", eid), "receipt": path, "rec": rec, "glb": glb, "half_receipt": half[0] if half else None, "half": half[1] if half else None}, ""


def consistency(results: dict, png_sha: str, seed: int, donors: dict) -> tuple[dict, list[str]]:
    """Check that the halves used the same png and the same seed (and the same donor for bind). (info, problems): problems refuse the collect."""
    problems, notes = [], []
    shas, seeds = {}, {}
    for e, r in results.items():
        hp = (r.get("half") or {}).get("params") or {}
        shas[e] = hp.get("png_sha256")
        seeds[e] = hp.get("seed", (r["rec"].get("params") or {}).get("seed"))
        if shas[e] is None:
            notes.append(f"{e}: no `ab` half receipt, so the input png could not be checked")
        elif shas[e] != png_sha:
            problems.append(f"{e} half was run on a different png (sha {str(shas[e])[:12]} vs {png_sha[:12]})")
        if seeds[e] is None:
            notes.append(f"{e}: seed not recorded in its receipts")
        elif int(seeds[e]) != int(seed):
            problems.append(f"{e} half was run with seed {seeds[e]}, not {seed}")
    known = {d for d in donors.values() if d}
    return {"same_png": all(v == png_sha for v in shas.values() if v) if any(shas.values()) else None,
            "same_seed": all(int(v) == int(seed) for v in seeds.values() if v is not None) if any(v is not None for v in seeds.values()) else None,
            "same_donor": (len(known) <= 1) if known else None, "notes": notes}, problems


# ---- bind numbers ---------------------------------------------------------------------------------------------------------------------------------
def bind_numbers(rec: dict | None, receipt_path: str = "") -> dict:
    """The bind-gate numbers of one mesh from its bind receipt (an ok OR a failed receipt: a gate fail still carries the numbers)."""
    if not rec:
        return {"measured": False}
    m = (rec.get("params") or {}).get("metrics") or {}
    if not m:
        return {"measured": False, "receipt": receipt_path, "error": rec.get("error", "")}
    return {"measured": True, "receipt": receipt_path, "status": rec.get("status"), "gate_passed": bool(m.get("gate_passed")), "p99_stretch": m.get("p99_stretch"),
            "p99_compression_ratio": m.get("p99_compression_ratio"), "bind_best_gate": m.get("bind_best_gate"), "checks": m.get("checks"), "binder": m.get("binder"),
            "donor": m.get("donor"), "violating_edges_by_region": m.get("violating_edges_by_region"), "weights": m.get("weights"), "wall_s": (rec.get("timing") or {}).get("wall_s")}


def bind_no_worse(px: dict, tr: dict, tol: float = 0.0) -> tuple[bool | None, list[str]]:
    """Is the Pixal3D bind gate no worse than TRELLIS's? True / False, or None when either was not measured (the rule then cannot be met)."""
    if not (px.get("measured") and tr.get("measured")):
        return None, ["bind gate not measured on both meshes"]
    why = []
    if tr.get("gate_passed") and not px.get("gate_passed"):
        why.append("Pixal3D fails the absolute gate where TRELLIS passes")
    if "FAIL" in str(px.get("bind_best_gate", "")).upper() and "FAIL" not in str(tr.get("bind_best_gate", "")).upper():
        why.append("Pixal3D fails the relative-to-control clause where TRELLIS does not")
    ps, ts = px.get("p99_stretch"), tr.get("p99_stretch")
    if ps is not None and ts is not None and ps > ts * (1 + tol):
        why.append(f"p99 stretch {ps} > TRELLIS {ts}")
    pc, tc = px.get("p99_compression_ratio"), tr.get("p99_compression_ratio")
    if pc is not None and tc is not None and pc < tc * (1 - tol):
        why.append(f"p99 compression {pc} < TRELLIS {tc}")
    return (not why), why


# ---- decision rule --------------------------------------------------------------------------------------------------------------------------------
def decide(scores: dict, key: dict, bind_ok, scoring: dict, rough: bool = False) -> dict:
    """Apply the rule to blind scores {"A": {criterion: 0-10}, "B": {...}} and the recorded key {"A": engine, "B": engine}.
    WIN only when Pixal3D beats TRELLIS by >= win_margin on >= win_criteria_needed criteria AND bind_ok is True. Anything else (incl. missing/invalid scores or an unmeasured bind gate) is NO-WIN."""
    crit, lo, hi = scoring["criteria"], scoring["min"], scoring["max"]
    margin, need = float(scoring["win_margin"]), int(scoring["win_criteria_needed"])
    lm = label_map(key)
    out = {"verdict": "NO-WIN", "reason": "", "diffs": {}, "criteria_won": [], "bind_no_worse": bind_ok}
    if rough:   # a NAF-off Pixal3D run is a rough fallback: never decisive, no WIN and no NO-WIN is printed
        out.update(verdict="ROUGH", reason="ROUGH: not decisive, rerun with NAF on")
        return out
    try:
        px, tr = scores[lm["pixal3d"]], scores[lm["trellis"]]
    except KeyError:
        out["reason"] = "scores missing for A or B"
        return out
    for who, row in (("Pixal3D", px), ("TRELLIS", tr)):
        for c in crit:
            v = row.get(c)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                out["reason"] = f"scores incomplete: {who} {c} is not filled"
                return out
            if not (lo <= v <= hi):
                out["reason"] = f"{who} {c} = {v} is outside {lo}-{hi}"
                return out
    out["diffs"] = {c: round(px[c] - tr[c], 3) for c in crit}
    out["criteria_won"] = [c for c in crit if px[c] - tr[c] >= margin - 1e-9]
    if len(out["criteria_won"]) < need:
        out["reason"] = f"Pixal3D beats TRELLIS by >= {margin} on {len(out['criteria_won'])} criteria, needs {need}"
    elif bind_ok is None:
        out["reason"] = "bind gate not measured on both meshes: the rule cannot be met"
    elif not bind_ok:
        out["reason"] = "Pixal3D bind gate is worse than TRELLIS"
    else:
        out["verdict"] = "WIN"
        out["reason"] = f"Pixal3D beats TRELLIS by >= {margin} on {', '.join(out['criteria_won'])} and its bind gate is no worse"
    return out


def decision_line(d: dict) -> str:
    if d["verdict"] == "ROUGH":
        return d["reason"]
    return f"{d['verdict']}: {d['reason']} (diffs Pixal3D - TRELLIS: {d['diffs']})"


# ---- matched turntable config ---------------------------------------------------------------------------------------------------------------------
def matched_render_config(ab: dict) -> dict:
    """The one render config both meshes get (same camera path and angles, same three-point rig, same world, resolution, samples; Cycles on CPU). Only glb and out_dir differ per mesh."""
    r = json.loads(json.dumps(ab["render"]))
    for k in ("script", "timeout_s", "enabled"):
        r.pop(k, None)
    return r


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


# ---- a half that failed: the traceback tail goes into the receipt and the scorecard, never a retry -----------------------------------------------------
def log_tail(path, n: int = 40) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [l.rstrip() for l in f.read().splitlines()[-n:]]
    except OSError:
        return []


def latest_failed_half(repo, asset_id: str, engine: str):
    """(path, receipt) of the newest `ab` half receipt of this engine when it says fail and no ok half of that engine is newer; else None."""
    files = sorted(glob.glob(str(Path(repo) / "receipts" / asset_id / "ab-*.json")), key=os.path.getmtime, reverse=True)
    for f in files:
        try:
            r = read_json(f)
        except Exception:
            continue
        p = r.get("params") or {}
        if p.get("mode") == "half" and p.get("engine") == engine:
            return (f, r) if r.get("status") == "fail" else None
    return None


def failure_scorecard(asset_id, png, png_sha, seed, failures: dict) -> dict:
    """The scorecard of a run where a half failed (status half_failed): the error and the traceback tail, and a NO-WIN decision that says why."""
    names = ", ".join(sorted(failures))
    return {"schema": 1, "asset_id": asset_id, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": "half_failed", "png": {"path": png, "sha256": png_sha},
            "seed": seed, "failures": failures, "decision": {"verdict": "NO-WIN", "reason": f"half failed: {names}. No retry with patches inside the window; the traceback tail is in this scorecard.", "diffs": {}}}


def failure_markdown(sc: dict) -> str:
    lines = [f"# A/B scorecard: {sc['asset_id']}: HALF FAILED", ""]
    for e, f in sc["failures"].items():
        lines += [f"## {e}: status fail", "", f"Receipt `{f.get('receipt')}`. {f.get('error', '')}", "", "```", *f.get("log_tail", []), "```", ""]
    return "\n".join(lines)


# ---- scorecard ------------------------------------------------------------------------------------------------------------------------------------
def empty_scores(criteria) -> dict:
    return {label: {c: None for c in criteria} for label in ("A", "B")}


def new_scorecard(*, asset_id, png, png_sha, seed, key, engines: dict, scoring: dict, consistency_info: dict, status: str) -> dict:
    return {"schema": 1, "asset_id": asset_id, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": status,
            "png": {"path": png, "sha256": png_sha}, "seed": seed, "blind_key": key, "labels": label_map(key), "engines": engines, "consistency": consistency_info,
            "blind_scores": empty_scores(scoring["criteria"]), "scored_by": None, "naf": True, "rough": False, "quality_label": "", "notes": "", "decision_rule": scoring, "decision": None}


def merge_existing(new: dict, old: dict | None) -> dict:
    """Re-collecting must never wipe a human's scores: keep blind_scores, scored_by, notes and the key of an existing scorecard for the same asset, png and seed."""
    if not old or old.get("png", {}).get("sha256") != new["png"]["sha256"] or old.get("seed") != new["seed"]:
        return new
    for k in ("blind_scores", "scored_by", "notes"):
        new[k] = old.get(k, new[k])
    return new


def bind_verdict(sc: dict, tol: float = 0.0):
    e = sc["engines"]
    if "pixal3d" not in e or "trellis" not in e:
        return None, ["only one half collected"]
    return bind_no_worse(e["pixal3d"]["bind"], e["trellis"]["bind"], tol)


def apply_to_scorecard(sc: dict, tol: float = 0.0) -> dict:
    ok, why = bind_verdict(sc, tol)
    d = decide(sc["blind_scores"], sc["blind_key"], ok, sc["decision_rule"], rough=bool(sc.get("rough")))
    d["bind_notes"] = why
    sc["decision"] = d
    return d


def _fmt(v):
    return "n/a" if v is None else str(v)


def render_markdown(sc: dict) -> str:
    """Blind view: engines appear only as A and B; the key is in scorecard.json (do not open it until the scores are filled)."""
    lm = sc["labels"]
    crit = sc["decision_rule"]["criteria"]
    lines = ([f"# {sc['quality_label']}", "", f"**{sc['quality_label']}: the Pixal3D half ran with NAF off. This scorecard is NOT decisive; rerun with NAF on.**", ""] if sc.get("rough") else [])
    lines += [f"# A/B scorecard: {sc['asset_id']}", "",
             f"Input png `{sc['png']['path']}` sha {sc['png']['sha256'][:12]}, seed {sc['seed']}. Status: {sc['status']}. Engines are labelled A and B; the mapping is recorded in `scorecard.json` (blind_key): do not read it until you have scored.", ""]
    labels = [l for l in ("A", "B") if sc["blind_key"][l] in sc["engines"]]
    lines += ["## Automatic columns", "", "| | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]

    def row(name, fn):
        lines.append(f"| {name} | " + " | ".join(_fmt(fn(sc["engines"][sc["blind_key"][l]])) for l in labels) + " |")

    row("wall s", lambda e: e.get("wall_s"))
    row("over wall budget", lambda e: e.get("over_budget"))
    row("lowest free RAM GB", lambda e: e.get("lowest_free_ram_gb"))
    row("bind measured", lambda e: e["bind"].get("measured"))
    row("bind gate passed", lambda e: e["bind"].get("gate_passed"))
    row("bind p99 stretch", lambda e: e["bind"].get("p99_stretch"))
    row("bind p99 compression", lambda e: e["bind"].get("p99_compression_ratio"))
    row("bind relative clause", lambda e: e["bind"].get("bind_best_gate"))
    row("qc overall", lambda e: e["qc"].get("overall"))
    row("qc near-black %", lambda e: (e["qc"].get("texture") or {}).get("near_black_pct"))
    row("face crop", lambda e: (e.get("images") or {}).get("face_crop"))
    row("hair crop", lambda e: (e.get("images") or {}).get("hair_crop"))
    lines += ["", "## Blind scores (0 to 10, fill in `blind_scores` in scorecard.json)", "", "| criterion | " + " | ".join(["A", "B"]) + " |", "|---|---|---|"]
    for c in crit:
        lines.append(f"| {c} | " + " | ".join(_fmt(sc["blind_scores"][l].get(c)) for l in ("A", "B")) + " |")
    lines += ["", "Rule: Pixal3D wins only if it beats TRELLIS by 1 point or more on two criteria AND its bind gate is no worse. Apply it with `python tools/run_ab.py --decide <scorecard.json>`.", ""]
    if sc.get("decision"):
        lines += ["## Decision", "", decision_line(sc["decision"]), ""]
    return "\n".join(lines)
