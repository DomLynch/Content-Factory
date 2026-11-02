#!/usr/bin/env python3
"""Factory MCP server (stdio, standard library only): the Content-Factory stages as tools for any Claude Code session.

  factory_status()                     GPU lock, free RAM, limits, newest receipts. Read-only.
  flux_render(kit, row_id)             one Flux row            -> flux/flux_kit.py        (GPU)
  trellis_run(asset_id, image, preset) cut-out then TRELLIS    -> tools/run_3d.py         (GPU)
  pixal3d_run(asset_id, image, preset) Pixal3D single view     -> tools/run_pixal3d.py   (GPU)
  ab_shape(png, seed, asset_id, engines, collect)  Pixal3D vs TRELLIS A/B harness -> tools/run_ab.py (GPU halves under the lock, TRELLIS first; collect = bind gate, QC, turntables, scorecard on the CPU)
  cutout(asset_id, image)              BiRefNet cut-out (CPU)  -> tools/cutout_cpu.py
  bind(asset_id, mesh, binder)         config bind.binder: skin_tokens -> tools/run_skin_tokens.py (GPU) with the donor bind as the labelled FALLBACK, or donor -> tools/run_bind.py (CPU Blender)
  skin_tokens_bind(glb, skeleton)      SkinTokens skin weights -> tools/run_skin_tokens.py (GPU, gated like bind + relative-to-control)
  stills(asset_id, glb)                head/idle/walk/attack   -> tools/run_stills.py     (CPU Blender)
  upscale(src, dst)                    Real-ESRGAN x4 + match  -> tools/upscale/upscale_match.py (GPU, Vulkan)
  motion_generate(text, ...)           ARDY (default) or Kimodo (backend, optional constraints) text-to-motion + Blender retarget onto psyche-106 -> tools/run_motion.py (GPU, then CPU Blender), a Mixamo-format clip GLB
  cloth(asset_id, part, body_glb, mesh) simulated cloth drape of a gear part (cloak first) on the T-posed bound body -> tools/run_cloth.py (CPU Blender, no GPU, Blender lock)
  materials(asset_id, glb, part, swatch | character_spec)  scanned CC0 PBR material on a gear part vs its baked texture, same three-point rig -> tools/run_materials.py (CPU Blender, no GPU lock, 10 GB RAM, blender_job.lock)
  qc(asset_id, glb, concept)           geometry/texture/silhouette checks -> tools/run_qc.py (CPU, numpy, light RAM gate; hooks/qc_gate.py makes `stills` wait for its pass)

Every tool runs the EXISTING script as a subprocess in the Content-Factory checkout (FACTORY_HOME, default <home>\\Documents\\Content-Factory), never reimplements it,
and returns the path of the receipt the script wrote. GPU tools start under tools/gpu_guard.ps1 exactly like tools/run_character.py does.
Refusals (nothing is started, the result says why): a GPU tool while Documents\\.gpu_lock names a live pid; any heavy tool while free RAM is under
config/config.json limits.start_min_free_ram_gb; an input image with a short side under 1024 px; an unknown preset, kit row or missing file.
Every stage subprocess carries a short-lived nonce (FACTORY_PLUGIN_TOKEN, file under config plugin_guard.nonce_dir): the MCP server spawns children itself, so this path never passes through
the Bash/PowerShell hook (hooks/plugin_guard.py), which refuses raw stage-script calls that lack a nonce or the logged FACTORY_RAW override.
The MCP handshake is implemented here by hand (JSON-RPC 2.0, one message per line) because the `mcp` package is not installed in the factory venv.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cf_guard  # noqa: E402
import cf_proc  # noqa: E402

HOME = cf_guard.HOME
SERVER_VERSION = json.loads((Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))["version"]
PROTOCOL_DEFAULT = "2024-11-05"


# ---- config -----------------------------------------------------------------------------------------------------------------------------------------
def cfg() -> dict:
    c = cf_guard.read_json(HOME / "config" / "config.json")
    local = HOME / "config" / "config.local.json"
    if not local.exists():
        raise RuntimeError(f"{local} is missing: copy config.local.example.json and fill in this machine's paths")
    c["local"] = cf_guard.read_json(local)
    return c


def newest_receipts(limit: int = 10) -> list[dict]:
    files = glob.glob(str(HOME / "receipts" / "*" / "*.json"))
    files.sort(key=os.path.getmtime, reverse=True)
    out = []
    for f in files[:limit]:
        row = {"path": f}
        try:
            r = cf_guard.read_json(f)
            row.update(asset=r.get("asset_id"), stage=r.get("stage"), status=r.get("status"), wall_s=(r.get("timing") or {}).get("wall_s"),
                       lowest_free_ram_gb=(r.get("memory") or {}).get("lowest_free_ram_gb"), error=r.get("error", ""))
        except Exception as e:
            row.update(status="UNREADABLE", error=f"{type(e).__name__}: not valid JSON (a receipt must parse)")
        out.append(row)
    return out


# ---- gates ------------------------------------------------------------------------------------------------------------------------------------------
def gate(gpu: bool, heavy: bool, image: str | None = None) -> str:
    """'' to go ahead, else why the tool refuses to start. Pure checks, nothing is started."""
    if gpu:
        reason = cf_guard.lock_reason(cf_guard.read_lock_text())
        if reason:
            return reason
    if heavy:
        reason = cf_guard.ram_reason(cf_guard.free_ram_gb())
        if reason:
            return reason
    if image:
        reason = cf_guard.small_image_reason(image)
        if reason:
            return reason
    return ""


# ---- running a stage --------------------------------------------------------------------------------------------------------------------------------
def run_stage(tool: str, cmd: list[str], *, cwd=None, env=None, timeout: int, guard: str | None = None) -> tuple[object, str, float]:
    """Run one existing script as a subprocess; stdout+stderr go to a log in logs_dir. Returns (exit code or 'timeout', log path, t0)."""
    c = cfg()
    loc, lim = c["local"], c["limits"]
    log = os.path.join(loc["logs_dir"], f"mcp_{tool}_{time.strftime('%Y%m%dT%H%M%S')}.txt")
    gproc = None
    if guard:
        if not os.path.exists(loc["guard_script"]):
            raise RuntimeError(f"guard script not found: {loc['guard_script']} (a GPU job never starts unguarded)")
        gproc = subprocess.Popen(["powershell", "-NoProfile", "-File", loc["guard_script"], "-Match", guard,
                                  "-MinFreeRamGB", str(lim["guard_min_free_ram_gb"]), "-MaxGpuGB", str(lim["guard_max_gpu_gb"])],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(c["character"]["guard_wait_s"])
    t0 = time.time()
    nonce = cf_guard.issue_nonce(tool)   # the plugin's own token: the child carries it, the Bash/PowerShell hook (plugin_guard.py) honours it
    proc = job = None
    try:
        with open(log, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(cmd, cwd=cwd or str(HOME), env=dict(os.environ, **(env or {}), **({cf_guard.TOKEN_ENV: nonce} if nonce else {})),
                                    stdout=lf, stderr=subprocess.STDOUT, start_new_session=(os.name != "nt"))
            job = cf_proc.bind_to_job(proc.pid)   # 0.1.5: if this server is killed (TaskStop, crash) the kernel kills the whole stage tree
            try:
                rc: object = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                rc = "timeout"
    finally:
        # 0.1.5: any way out (timeout, cancel, error) kills the WHOLE tree of the stage, and only then are the guard and the lock released
        if proc is not None:
            cf_proc.kill_tree(proc.pid)
        cf_proc.close_job(job)
        cf_guard.revoke_nonce(nonce)
        if gproc and gproc.poll() is None:
            gproc.kill()
    return rc, log, t0


def finish(tool: str, rc, log: str, t0: float, stages: list[str], asset_id: str | None = None, extra: dict | None = None) -> dict:
    """Find the receipt(s) the script wrote after t0 and build the result. status is 'ok' only if the exit code is 0 AND the main receipt says ok."""
    found = {}
    for st in stages:
        pat = str(HOME / "receipts" / (asset_id or "*") / f"{st}-*.json")
        fresh = [f for f in glob.glob(pat) if os.path.getmtime(f) >= t0 - 2]
        if fresh:
            found[st] = max(fresh, key=os.path.getmtime)
    main_stage = stages[-1]
    res = {"tool": tool, "exit": rc, "log": log, "receipt": found.get(main_stage), "receipts": found, "status": "fail"}
    if found.get(main_stage):
        try:
            r = cf_guard.read_json(found[main_stage])
            res.update(receipt_status=r.get("status"), error=r.get("error", ""), outputs=[o["path"] for o in r.get("outputs", [])],
                       wall_s=(r.get("timing") or {}).get("wall_s"), lowest_free_ram_gb=(r.get("memory") or {}).get("lowest_free_ram_gb"))
            res["status"] = "ok" if rc == 0 and r.get("status") == "ok" else "fail"
        except Exception as e:
            res["error"] = f"receipt unreadable: {type(e).__name__}"
    else:
        res["error"] = "no receipt was written: the stage did not finish. Read the log."
    res["next"] = "Open the outputs and LOOK at them before calling this done; a receipt only proves the tool ran."
    res.update(extra or {})
    return res


def need_file(path: str, what: str) -> str:
    p = Path(path)
    return "" if p.is_file() else f"{what} not found: {path}"


# ---- tools ------------------------------------------------------------------------------------------------------------------------------------------
def t_factory_status() -> dict:
    lock_text = cf_guard.read_lock_text()
    lock = None
    if lock_text:
        try:
            lock = json.loads(lock_text.lstrip("﻿"))
            lock["pid_alive"] = cf_guard.pid_alive(int(lock.get("pid", 0)))
        except (ValueError, TypeError):
            lock = {"unreadable": True}
    free = cf_guard.free_ram_gb()
    busy = bool(cf_guard.lock_reason(lock_text))
    return {"gpu": "busy (a new GPU job must wait)" if busy else "free", "lock": lock, "free_ram_gb": free, "start_min_free_ram_gb": cf_guard.start_min_free_gb(),
            "ram_ok_to_start": not cf_guard.ram_reason(free), "factory_home": str(HOME), "newest_receipts": newest_receipts()}


def t_flux_render(kit: str, row_id: str, dry_run: bool = False) -> dict:
    kit_path = Path(kit) if os.path.isabs(kit) else HOME / kit
    miss = need_file(str(kit_path), "kit")
    if miss:
        return {"status": "refused", "error": miss}
    rows = cf_guard.read_json(kit_path).get("rows", [])
    if not any(r.get("row_id", r.get("name")) == row_id for r in rows):
        return {"status": "refused", "error": f"row_id {row_id} is not in {kit_path.name}"}
    c = cfg()
    cmd = [c["local"]["python"], "-u", str(HOME / "flux" / "flux_kit.py"), str(kit_path), row_id]
    if dry_run:   # flux_kit.py prints asset id, output path and size under CF_DRY and exits; no GPU
        nonce = cf_guard.issue_nonce("flux_render_dry")
        try:
            r = subprocess.run(cmd, cwd=str(HOME), env=dict(os.environ, CF_DRY="1", **({cf_guard.TOKEN_ENV: nonce} if nonce else {})), capture_output=True, text=True, timeout=120)
        finally:
            cf_guard.revoke_nonce(nonce)
        return {"status": "dry-run", "exit": r.returncode, "stdout": r.stdout[-2000:], "stderr": r.stderr[-500:]}
    why = gate(gpu=True, heavy=True)
    if why:
        return {"status": "refused", "error": why}
    rc, log, t0 = run_stage("flux_render", cmd, timeout=c["character"]["flux_timeout_s"], guard=c["character"]["guard_match"]["flux"])
    return finish("flux_render", rc, log, t0, ["flux"],
                  extra={"after": "Run tools/fix_black_block.py on a 1024x1536 output and look again. One row per process."})


def t_trellis_run(asset_id: str, image: str, preset: str = "", speed_reason: str = "") -> dict:
    c = cfg()
    preset = preset or c["character"]["default_preset"]
    presets = c["trellis"]["presets"]
    if preset not in presets:
        return {"status": "refused", "error": f"unknown preset {preset}; choose from {sorted(presets)} (config/config.json -> trellis.presets)"}
    miss = need_file(image, "image")
    if miss:
        return {"status": "refused", "error": miss}
    why = gate(gpu=True, heavy=True, image=image)
    if why:
        return {"status": "refused", "error": why}
    cmd = [c["local"]["python"], str(HOME / "tools" / "run_3d.py"), asset_id, image, "--preset", preset] + (["--speed-reason", speed_reason] if speed_reason else [])
    rc, log, t0 = run_stage("trellis_run", cmd, timeout=c["character"]["trellis_timeout_s"], guard=c["character"]["guard_match"]["trellis"])
    extra = {"preset": preset}
    if presets[preset].get("texture_size", 0) < 4096:
        extra["tier_note"] = "2048 texture / 500k faces is a benchmark profile: a proof, never shown as a final. Finals come from the hero rebake (hero_rebake preset, --from-latents)."
    return finish("trellis_run", rc, log, t0, ["cutout", "trellis"], asset_id, extra)


def t_pixal3d_run(asset_id: str, image: str = "", preset: str = "", views=None, mirror_profile: bool = False, three_quarter: bool = True, speed_reason: str = "") -> dict:
    c = cfg()
    px = c["pixal3d"]
    preset = preset or px["default_preset"]
    if preset not in px["presets"]:
        return {"status": "refused", "error": f"unknown preset {preset}; choose from {sorted(px['presets'])} (config/config.json -> pixal3d.presets)"}
    if views:   # 0.1.20: multi-view, the capability this tool exists for (tools/pixal3d_mv.py)
        return _pixal3d_multiview(c, px, asset_id, preset, views, mirror_profile, three_quarter)
    if not image:
        return {"status": "refused", "error": "give image (single view, A/B only) or views (multi-view)"}
    miss = need_file(image, "image")
    if miss:
        return {"status": "refused", "error": miss}
    why = gate(gpu=True, heavy=True, image=image)
    if why:
        return {"status": "refused", "error": why}
    cmd = [c["local"]["python"], str(HOME / "tools" / "run_pixal3d.py"), asset_id, image, "--preset", preset] + (["--speed-reason", speed_reason] if speed_reason else [])
    rc, log, t0 = run_stage("pixal3d_run", cmd, timeout=px["timeout_s"], guard=px["guard_match"])
    return finish("pixal3d_run", rc, log, t0, ["pixal3d"], asset_id,
                  {"preset": preset, "pipeline": "PIXAL3D", "note": "A different pipeline from TRELLIS: label it PIXAL3D, and compare it with TRELLIS only through the A/B on the same input and seed."})


def _pixal3d_multiview(c: dict, px: dict, asset_id: str, preset: str, views, mirror_profile: bool, three_quarter: bool) -> dict:
    """Multi-view refusals first (nothing is started): unreadable or unknown views, no alpha, size mismatch, the 1024 px and subject rules on EACH view, missing _mv weights
    (the exact file list), then the GPU lock and RAM gate."""
    sys.path.insert(0, str(HOME / "tools"))
    import pixal3d_mv
    model_dir = pixal3d_mv.mv_model_dir(c)
    why = pixal3d_mv.weights_reason(model_dir)
    if why:
        return {"status": "refused", "error": why, "missing": pixal3d_mv.weights_missing(model_dir)}
    try:
        v = pixal3d_mv.validate(views, mirror_profile or None, three_quarter)
    except pixal3d_mv.MVError as e:
        return {"status": "refused", "error": str(e)}
    why = gate(gpu=True, heavy=True)
    if why:
        return {"status": "refused", "error": why}
    spec = views if isinstance(views, str) else json.dumps({"views": {r["cell"]: r["source"] for r in v["rows"]}})
    cmd = [c["local"]["python"], str(HOME / "tools" / "run_pixal3d.py"), asset_id, "--views", spec, "--preset", preset]
    if mirror_profile:
        cmd.append("--mirror-profile")
    if not three_quarter:
        cmd.append("--no-three-quarter")
    rc, log, t0 = run_stage("pixal3d_run", cmd, timeout=px["timeout_s"], guard=px["guard_match"])
    return finish("pixal3d_run", rc, log, t0, ["pixal3d"], asset_id,
                  {"preset": preset, "pipeline": "PIXAL3D", "mode": "multiview", "views": [(r["cell"], r["angle_deg"]) for r in v["rows"]], "assumptions": v["assumptions"],
                   "note": "Multi-view: the sheet's views joined into one shape (cameras given). Assumption flags are in the receipt; LOOK at the turntable and compare with the single-view result."})


def t_ab_shape(png: str, asset_id: str, seed: int | None = None, engines: str = "both", collect: bool = False, bind: str = "auto", naf: str = "on") -> dict:
    """0.1.14: the Pixal3D vs TRELLIS A/B on ONE png and ONE seed. Each GPU half is its own guarded stage (TRELLIS first), run_ab.py --collect gathers the receipts
    (bind gate on both meshes with the same donor, QC, matched turntables, contact sheet, blind scorecard). engines: both | trellis | pixal3d. collect=true runs no GPU half."""
    c = cfg()
    ab = c["ab"]
    if engines not in ("both", "trellis", "pixal3d"):
        return {"status": "refused", "error": f"engines must be both, trellis or pixal3d, not {engines!r}"}
    if bind not in ("auto", "yes", "no"):
        return {"status": "refused", "error": f"bind must be auto, yes or no, not {bind!r}"}
    if naf not in ("on", "off"):
        return {"status": "refused", "error": f"naf must be on or off, not {naf!r}"}
    miss = need_file(png, "png")
    if miss:
        return {"status": "refused", "error": miss}
    seed = c["defaults"]["seed"] if seed is None else int(seed)
    sys.path.insert(0, str(HOME / "tools" / "ab"))
    import ab_core   # pure checks only
    why = ab_core.rgba_reason(png)   # the input is the Factory's already cut-out RGBA png, the same file for both engines
    if why:
        return {"status": "refused", "error": why}
    why = gate(gpu=not collect, heavy=True, image=None if collect else png)
    if why:
        return {"status": "refused", "error": why}
    if not collect:   # the sheet queue is alive = refuse: a stage process, a live lock or low RAM (nothing is started)
        why = ab_core.alive_reason(own=ab_core.own_pids)
        if why:
            return {"status": "refused", "error": why}
    halves = list(ab["order"]) if engines == "both" else [engines]
    py, script = c["local"]["python"], str(HOME / "tools" / "run_ab.py")
    results, ok = {}, True
    if not collect:
        for e in halves:   # TRELLIS first, each half under its own gpu_guard.ps1 (which writes the GPU lock) and with the whole tree killed on any exit
            eng = ab["engines"][e]
            cmd = [py, script, asset_id, png, "--seed", str(seed), "--engines", e] + (["--naf", naf] if e == "pixal3d" else [])
            rc, log, t0 = run_stage(f"ab_shape_{e}", cmd, timeout=ab["wall_hard_cap_s"] + 1200, guard=eng["guard_match"])
            results[e] = finish(f"ab_shape_{e}", rc, log, t0, ["ab"], asset_id, {"engine": e, "pipeline": eng["pipeline"]})
            if results[e]["status"] != "ok":
                ok = False
                break
    out = {"tool": "ab_shape", "asset_id": asset_id, "seed": seed, "engines": halves, "halves": results}
    if (collect or (engines == "both" and ok)):
        b = c["bind"]
        cmd = [py, script, asset_id, png, "--seed", str(seed), "--engines", engines, "--collect", "--bind", bind]
        rc, log, t0 = run_stage("ab_shape_collect", cmd, timeout=2 * (b["timeout_s"] + b["export_timeout_s"] + 60) + ab["qc"]["timeout_s"] * 2 + ab["render"]["timeout_s"] + 600)
        out["collect"] = finish("ab_shape_collect", rc, log, t0, ["ab"], asset_id)
        ok = ok and out["collect"]["status"] == "ok"
        if out["collect"].get("outputs"):
            out["scorecard"] = next((o for o in out["collect"]["outputs"] if o.endswith("scorecard.md")), None)
    out["status"] = "ok" if ok else "fail"
    out["next"] = ("Score blind (A/B, 0-10: face, hair, colour match, bake faults) in scorecard.json, then `python tools/run_ab.py --decide <scorecard.json>`. "
                   "The two meshes are labelled TRELLIS and PIXAL3D and are compared only through this A/B. LOOK at the turntables yourself.")
    return out


def t_cutout(asset_id: str, image: str) -> dict:
    c = cfg()
    loc, tr = c["local"], c["trellis"]
    miss = need_file(image, "image")
    if miss:
        return {"status": "refused", "error": miss}
    why = gate(gpu=False, heavy=True, image=image)
    if why:
        return {"status": "refused", "error": why}
    sys.path.insert(0, str(HOME / "tools"))
    import cf_common   # the repo's own receipt writer (standard library only)
    work = os.path.join(loc["outputs_dir"], "3d", asset_id)
    os.makedirs(work, exist_ok=True)
    out = os.path.join(work, "cutout.png")
    env = {k: v for k, v in tr["env"].items() if k != "TRELLIS3_REMBG"}   # cutout_cpu.py reads TRELLIS3_REMBG as its model id (LESSONS 2026-11-01)
    ram = cf_common.FreeRamWatch()
    ram.start()
    cmd = [loc["cutout_python"], os.path.join(loc["cutout_repo_dir"], "tools", "cutout_cpu.py"), image, out]
    rc, log, t0 = run_stage("cutout", cmd, cwd=loc["cutout_repo_dir"], env=env, timeout=c["character"]["cpu_timeout_s"])
    ok = rc == 0 and os.path.exists(out)
    cf_common.write_receipt(c, asset_id, "cutout", tool="cutout_cpu.py", model={"id": tr["cutout_model"]}, inputs=[image], params={"device": "cpu", "log": log},
                            timing={"wall_s": round(time.time() - t0, 1)}, memory={"lowest_free_ram_gb": ram.stop()}, outputs=[out],
                            status="ok" if ok else "fail", error="" if ok else f"exit {rc}, see log")
    return finish("cutout", rc, log, t0, ["cutout"], asset_id)


def _route():
    """tools/binder_route.py of the factory checkout (pure functions: which binder, prerequisites, labels)."""
    tools = str(HOME / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import binder_route
    return binder_route


def _donor_bind(c: dict, asset_id: str, mesh: str, donor: str, fallback_reason: str = "") -> dict:
    """The donor bind (tools/run_bind.py, CPU Blender), unchanged. With a fallback_reason the receipt and the result carry the FALLBACK label."""
    route = _route()
    donor = donor or c["character"]["default_donor"]
    b = c["bind"]
    cmd = [c["local"]["python"], str(HOME / "tools" / "run_bind.py"), asset_id, mesh]
    env = {"BIND_FORCE_DONOR": donor}
    if fallback_reason:
        env[route.REASON_ENV] = fallback_reason
    rc, log, t0 = run_stage("bind", cmd, env=env, timeout=b["timeout_s"] + b["export_timeout_s"] + 60)
    extra = {"donor": donor, "binder_used": "donor", "label": route.FALLBACK_LABEL if fallback_reason else route.DONOR_LABEL}
    if fallback_reason:
        extra["fallback_reason"] = fallback_reason
    res = finish("bind", rc, log, t0, ["bind"], asset_id, extra)
    if res.get("receipt"):
        try:
            m = cf_guard.read_json(res["receipt"]).get("params", {}).get("metrics", {})
            res["metrics"] = {k: m.get(k) for k in ("p99_stretch", "p99_compression_ratio", "checks", "binder", "donor", "bind_best_gate")}
            if "FAIL" in json.dumps(m.get("bind_best_gate", "")).upper():
                res["relative_clause_warning"] = ("bind_best.py reports GATE_FAIL (relative-to-control clause) although the receipt status may read ok: "
                                                  "run_bind.py does not check that clause. Do not call this a clean pass (LESSONS 2026-11-01).")
        except Exception:
            pass
    return res


def t_bind(asset_id: str, mesh: str, donor: str = "", binder: str = "") -> dict:
    """Bind a body to the psyche-106 rig with the configured binder (config bind.binder, or the `binder` argument): skin_tokens (GPU) or donor (CPU, the labelled fallback).
    SkinTokens that fails its gate, or whose prerequisites are missing, falls back to the donor bind when bind.fallback_to_donor is true; the result and the receipt say FALLBACK."""
    c = cfg()
    miss = need_file(mesh, "mesh")
    if miss:
        return {"status": "refused", "error": miss}
    why = gate(gpu=False, heavy=True)
    if why:
        return {"status": "refused", "error": why}
    try:
        route = _route()
        which = binder or route.binder(c)
        if which not in route.BINDERS:
            return {"status": "refused", "error": f"unknown binder {which!r}; choose from {list(route.BINDERS)}"}
    except (ValueError, ImportError) as e:
        return {"status": "refused", "error": f"binder config: {e}"}
    if which == "donor":
        return _donor_bind(c, asset_id, mesh, donor)
    allow = route.fallback_allowed(c)
    missing = route.skin_tokens_missing(c, str(HOME))
    if missing:
        reason = "SkinTokens prerequisites missing: " + "; ".join(missing)
        if not allow:
            return {"status": "refused", "error": reason + " (bind.fallback_to_donor is false)", "binder_used": None}
        return _donor_bind(c, asset_id, mesh, donor, reason)
    res = t_skin_tokens_bind(mesh, asset_id=asset_id)
    res["binder_used"] = "skin_tokens"
    if res.get("status") in ("ok", "refused"):   # refused = nothing ran (live GPU lock, low RAM): not a gate failure, so no silent switch to another binder
        return res
    if not allow:
        return res
    reason = f"SkinTokens bind failed ({res.get('error') or 'see its receipt'}; receipt {res.get('receipt')})"
    fb = _donor_bind(c, asset_id, mesh, donor, reason)
    fb["skin_tokens_attempt"] = {"status": res.get("status"), "receipt": res.get("receipt"), "error": res.get("error"), "log": res.get("log")}
    return fb


def t_skin_tokens_bind(glb: str, skeleton: str = "", asset_id: str = "") -> dict:
    """SkinTokens (VAST-AI, MIT) skins `glb` onto the skeleton (default: the psyche-106 rig, config.local rig_glb). GPU. Same gate as bind plus the relative-to-control clause."""
    c = cfg()
    loc, st = c["local"], c["skin_tokens"]
    skeleton = skeleton or loc.get("rig_glb", "")
    for path, what in ((glb, "glb"), (skeleton, "skeleton")):
        miss = need_file(path, what)
        if miss:
            return {"status": "refused", "error": miss}
    why = gate(gpu=True, heavy=True)
    if why:
        return {"status": "refused", "error": why}
    control = HOME / st["control_json"].format(donor=st["control_donor"])
    for path, what in ((loc.get("skin_tokens_python", ""), "skin_tokens_python"), (os.path.join(loc.get("skin_tokens_repo_dir", ""), st["ckpt"]), "SkinTokens checkpoint"),
                       (str(control), "donor control metrics (the relative-to-control clause needs it; bind_best.py builds it once per donor)"),
                       *(((st["launcher"], "skin_tokens.launcher"),) if st.get("launcher") else ())):
        if not path or not os.path.isfile(path):
            return {"status": "refused", "error": f"{what} not found: {path}"}
    p = Path(glb)
    asset_id = asset_id or (p.parent.name if p.stem == "mesh" else p.stem)
    cmd = [loc["python"], str(HOME / "tools" / "run_skin_tokens.py"), asset_id, glb, skeleton]
    b = c["bind"]
    rc, log, t0 = run_stage("skin_tokens_bind", cmd, timeout=st["prep_timeout_s"] + st["run_timeout_s"] + st["export_timeout_s"] + st["metrics_timeout_s"] + 60,
                            guard="run_skin_tokens")
    res = finish("skin_tokens_bind", rc, log, t0, ["bind"], asset_id, {"binder": "skin_tokens", "skeleton": skeleton, "control": str(control)})
    if res.get("receipt"):
        try:
            m = cf_guard.read_json(res["receipt"]).get("params", {}).get("metrics", {})
            res["metrics"] = {k: m.get(k) for k in ("p99_stretch", "p99_compression_ratio", "checks", "binder", "violating_edges_by_region")}
            if m.get("checks", {}).get("relative_to_control") is False:
                res["relative_clause_warning"] = "the relative-to-control clause failed (stretch <= control x 1.10, compression >= control x 0.90): not a clean pass."
        except Exception:
            pass
    res["gate_note"] = f"gate = bind.gate (stretch <= {b['gate']['p99_stretch_max']}, compression >= {b['gate']['p99_compression_ratio_min']}) + relative-to-control; compare against the donor bind before choosing."
    return res


def t_stills(asset_id: str, glb: str) -> dict:
    c = cfg()
    miss = need_file(glb, "glb")
    if miss:
        return {"status": "refused", "error": miss}
    why = gate(gpu=False, heavy=True)
    if why:
        return {"status": "refused", "error": why}
    cmd = [c["local"]["python"], str(HOME / "tools" / "run_stills.py"), asset_id, glb]
    rc, log, t0 = run_stage("stills", cmd, timeout=c["stills"]["timeout_s"] + 60)
    return finish("stills", rc, log, t0, ["stills"], asset_id,
                  {"tier_note": "run_stills.py lights with one sun and a flat world colour (config stills.sun_energy / world_color): proof tier, not a three-point-lit PBR presentation."})


def t_cloth(asset_id: str, part: str, body_glb: str, mesh: str = "", preset: str = "", mode: str = "rest", max_frames: int = 0) -> dict:
    """0.1.18: Blender cloth simulation of ONE gear part (cloak first) on the T-posed bound body (tools/run_cloth.py). CPU Blender only: RAM gate, no GPU lock. mode=clip is a stub."""
    c = cfg()
    cl = c["cloth"]
    if mode not in cl["modes"]:
        return {"status": "refused", "error": f"mode must be one of {cl['modes']}, not {mode!r}"}
    if mode == "clip":
        return {"status": "refused", "error": "NotImplemented: per-clip cloth simulation (idle/walk baked as shape keys) is not built; only mode=rest. Use rest mode, then bind the settled mesh with run_gear.py."}
    names = [k for k in cl["presets"] if not k.startswith("_")]
    if (preset or cl["default_preset"]) not in names:
        return {"status": "refused", "error": f"unknown cloth preset {preset!r}; presets: {', '.join(names)}"}
    if max_frames < 0:
        return {"status": "refused", "error": "max_frames must be 0 (config) or positive"}
    mesh = mesh or str(Path(c["local"]["outputs_dir"]) / "3d" / asset_id / f"{part}.glb")
    for path, what in ((mesh, "part mesh"), (body_glb, "bound body glb")):
        miss = need_file(path, what)
        if miss:
            return {"status": "refused", "error": miss}
    why = gate(gpu=False, heavy=True)
    if why:
        return {"status": "refused", "error": why}
    cmd = [c["local"]["python"], str(HOME / "tools" / "run_cloth.py"), asset_id, part, mesh, body_glb, "--mode", mode]
    if preset:
        cmd += ["--preset", preset]
    if max_frames:
        cmd += ["--max-frames", str(max_frames)]
    g = cl["guard"]
    rc, log, t0 = run_stage("cloth", cmd, timeout=cl["timeout_s"] + g["lock_wait_s"] + g["flux_wait_s"] + 120)
    res = finish("cloth", rc, log, t0, ["cloth"], asset_id,
                 {"next_step": f"The settled rest mesh is cloth_{part}.glb beside the bound body: bind it with run_gear.py (gear stage) so it follows the skeleton. LOOK at it in a lit render first."})
    if res.get("receipt"):
        try:
            m = cf_guard.read_json(res["receipt"]).get("params", {}).get("metrics", {})
            res["metrics"] = {"sim": m.get("sim"), "penetration": m.get("penetration"), "pins": m.get("pins"), "counts": m.get("counts"), "checks": m.get("checks")}
            if m.get("sim") and not m["sim"].get("settled"):
                res["settle_warning"] = "the sim hit max_frames without settling: the mesh is the last frame, not a rest state. Look at it, raise max_frames or damp the preset."
        except Exception:
            pass
    return res


def _materials_plan(c: dict, spec_path: Path, root: str) -> dict:
    """The swatch -> scanned set plan of a character spec (parts coat, cloak, boots), read from config/materials.json and the sets on disk. Nothing is started."""
    mapping = cf_guard.read_json(HOME / c["materials"]["mapping"])
    spec = cf_guard.read_json(spec_path)
    rows = []
    for part in spec.get("parts", []):
        name = part.get("name")
        if name not in mapping["parts"]:
            continue
        sw = mapping["parts"][name]["swatch"]
        row = mapping["swatches"].get(sw, {})
        mat = row.get("material")
        have = bool(mat) and (Path(root) / mat / "manifest.json").is_file()
        rows.append({"part": name, "swatch": sw, "material": mat, "fit": row.get("fit"), "on_disk": have, "licence": mapping["materials"][mat]["licence"] if mat else None})
    gaps = sorted(k for k, v in mapping["swatches"].items() if not v.get("material"))
    return {"status": "plan", "character": spec.get("character_id"), "parts": rows, "swatches_without_a_scan": gaps,
            "next": "Run materials(asset_id, glb, part) per part once on_disk is true; fetch_materials.py downloads only with the owner's yes (it is not a plugin tool)."}


def t_materials(asset_id: str = "", glb: str = "", character_spec: str = "", part: str = "coat", swatch: str = "") -> dict:
    """0.1.17: scanned CC0 PBR material on ONE gear-part GLB under the A/B three-point rig, side by side with the baked texture (tools/run_materials.py, CPU Blender, no GPU).
    With character_spec and no glb: the swatch -> scanned set plan only."""
    c = cfg()
    m = c["materials"]
    loc = c["local"]
    root = loc.get("materials_dir") or os.path.join(loc.get("work_dir", ""), "materials")
    if character_spec and not glb:
        p = Path(character_spec) if os.path.isabs(character_spec) else HOME / character_spec
        miss = need_file(str(p), "character_spec")
        return {"status": "refused", "error": miss} if miss else _materials_plan(c, p, root)
    if not asset_id or not glb:
        return {"status": "refused", "error": "asset_id and glb are required (or only character_spec for the plan)"}
    if part not in ("coat", "cloak", "boots"):
        return {"status": "refused", "error": f"part must be coat, cloak or boots, not {part!r}"}
    miss = need_file(glb, "glb")
    if miss:
        return {"status": "refused", "error": miss}
    mapping = cf_guard.read_json(HOME / m["mapping"])
    sw = swatch or mapping["parts"][part]["swatch"]
    row = mapping["swatches"].get(sw)
    if row is None:
        return {"status": "refused", "error": f"unknown swatch {sw!r}; choose from {sorted(mapping['swatches'])}"}
    if not row.get("material"):
        return {"status": "refused", "error": f"swatch {sw} has no scanned set: {row['fit']}"}
    if not (Path(root) / row["material"] / "manifest.json").is_file():
        return {"status": "refused", "error": f"scanned set {row['material']} is not on disk under {root}: tools/materials/fetch_materials.py downloads it, only with the owner's yes"}
    why = gate(gpu=False, heavy=False) or cf_guard.ram_reason(cf_guard.free_ram_gb(), m["min_free_ram_gb"])   # CPU Blender: no GPU lock; 10 GB free RAM for a Blender job
    if why:
        return {"status": "refused", "error": why}
    cmd = [loc["python"], str(HOME / "tools" / "run_materials.py"), asset_id, glb, "--part", part] + (["--swatch", swatch] if swatch else [])
    # run_materials.py waits up to ram_wait_s for RAM/Flux and lock_wait_s for blender_job.lock, then runs two renders and a compose
    rc, log, t0 = run_stage("materials", cmd, timeout=m["ram_wait_s"] + m["lock_wait_s"] + 3 * m["timeout_s"] + 120)
    res = finish("materials", rc, log, t0, ["materials"], asset_id, {"part": part, "swatch": sw, "material": row["material"], "licence": "CC0-1.0", "fit": row["fit"],
                 "tier_note": "Draft: first scanned-material path, box-projected UVs in metres (part height assumption in config/materials.json). LOOK at the side-by-side before using it."})
    return res


def t_qc(asset_id: str, glb: str, concept: str = "", budget_tris: int = 0, render: str = "") -> dict:
    c = cfg()
    q = c["qc"]
    miss = need_file(glb, "glb") or (need_file(concept, "concept") if concept else "") or (need_file(render, "render") if render else "")
    if miss:
        return {"status": "refused", "error": miss}
    why = cf_guard.ram_reason(cf_guard.free_ram_gb(), q.get("min_free_ram_gb", 2.5))   # light gate: numpy only, no model
    if why:
        return {"status": "refused", "error": why}
    cmd = [c["local"]["python"], str(HOME / "tools" / "run_qc.py"), asset_id, glb]
    if concept:
        cmd += ["--concept", concept]
    if budget_tris:
        cmd += ["--budget-tris", str(int(budget_tris))]
    moge = q.get("moge") or {}
    if render and moge.get("enabled"):   # 0.1.12, optional and off by default: run_qc.py scores the render against the concept with MoGe-2
        cmd += ["--render", render]
    rc, log, t0 = run_stage("qc", cmd, timeout=q.get("timeout_s", 600) + (moge.get("timeout_s", 600) if moge.get("enabled") else 0))
    res = finish("qc", rc, log, t0, ["qc"], asset_id)
    if res.get("receipt"):
        try:
            p = cf_guard.read_json(res["receipt"]).get("params", {})
            res.update(qc_overall=p.get("overall"), failed_metrics=p.get("failed", []), skipped_metrics=p.get("skipped", []), colour_flag=p.get("colour_flag"))
            res["checks"] = [f"{r['metric']} {r['value']} {r['op']} {r['limit']}: {'pass' if r['pass'] else 'FAIL'}" for r in p.get("checks", [])]
            if not concept:
                res["warning"] = "no concept image: the texture-vs-concept and silhouette checks were skipped."
            if p.get("moge"):
                res["moge"] = p["moge"]
                res["notes"] = p.get("notes", [])
            res["next"] = ("status ok means the QC ran. qc_overall says whether the mesh passed; the `stills` tool refuses (hooks/qc_gate.py) unless the latest QC receipt for "
                           "this asset passes. Open the mesh and LOOK at it before calling it done.")
        except Exception:
            pass
    return res


def t_upscale(src: str, dst: str, passes: int = 1, asset_id: str = "") -> dict:
    c = cfg()
    miss = need_file(src, "src")
    if miss:
        return {"status": "refused", "error": miss}
    why = gate(gpu=True, heavy=True)
    if why:
        return {"status": "refused", "error": why}
    asset_id = asset_id or Path(dst).stem   # upscale_match.py writes a receipt only when it is given an asset id
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    cmd = [c["local"]["python"], str(HOME / "tools" / "upscale" / "upscale_match.py"), src, dst, str(int(passes)), asset_id]
    rc, log, t0 = run_stage("upscale", cmd, timeout=c["character"]["cpu_timeout_s"], guard="upscale_match")
    res = finish("upscale", rc, log, t0, ["upscale"], asset_id)
    size = cf_guard.image_size(dst) if os.path.exists(dst) else None
    if size:
        res["output_size"] = list(size)
        if min(size) < 2048:
            res["warning"] = "output short side is under 2048 px: inputs to TRELLIS should be 2048 px or more; run another pass."
    return res


def t_motion_generate(text: str, asset_id: str = "motion-clips", duration: float | None = None, seed: int | None = None, clip_id: str = "", in_place: bool = False,
                      install: bool = False, device: str = "gpu", encoder_device: str = "", backend: str = "ardy", constraints=None) -> dict:
    """0.1.8: text -> ARDY motion -> retarget onto the 106 rig -> clip GLB the stills stage finds by name (tools/run_motion.py). 0.1.12: backend="kimodo" (own venv) and optional sparse constraints
    (a path to a Kimodo constraints.json or the list itself)."""
    import argparse
    c = cfg()
    if device not in ("gpu", "cpu"):
        return {"status": "refused", "error": f"device must be gpu or cpu, not {device!r}"}
    sys.path.insert(0, str(HOME / "tools"))
    sys.path.insert(0, str(HOME / "tools" / "motion"))
    import run_motion   # pure checks only (no start): its main() is behind __main__
    ns = argparse.Namespace(asset_id=asset_id, text=text, duration=duration, seed=seed, clip_id=clip_id, in_place=in_place, install=install, device=device,
                            encoder_device=encoder_device, backend=backend, constraints=constraints)
    why = gate(gpu=(device == "gpu"), heavy=True)   # a CPU-only run does not need the GPU lock, but still needs RAM
    if why:
        return {"status": "refused", "error": why}
    probs = run_motion.problems(c, ns, cf_guard.free_ram_gb())
    if probs:
        return {"status": "refused", "error": "; ".join(probs)}
    m = c["motion"]
    con_arg = None
    if isinstance(constraints, list):   # inline constraints: validated above, handed to the script as a file (command lines stay short)
        con_arg = os.path.join(c["local"]["logs_dir"], f"mcp_motion_constraints_{time.strftime('%Y%m%dT%H%M%S')}.json")
        with open(con_arg, "w", encoding="utf-8") as f:
            json.dump(constraints, f)
    elif constraints:
        con_arg = str(constraints)
    cmd = [c["local"]["python"], str(HOME / "tools" / "run_motion.py"), asset_id, text, "--device", device, "--backend", backend]
    cmd += ["--constraints", con_arg] if con_arg else []
    for flag, val in (("--duration", duration), ("--seed", seed), ("--clip-id", clip_id), ("--encoder-device", encoder_device)):
        if val not in (None, ""):
            cmd += [flag, str(val)]
    cmd += (["--in-place"] if in_place else []) + (["--install"] if install else [])
    rc, log, t0 = run_stage("motion_generate", cmd, timeout=m["timeout_s"] + m["retarget_timeout_s"] + 120, guard=(m["kimodo_guard_match"] if backend == "kimodo" else m["guard_match"]) if device == "gpu" else None)
    kim = backend == "kimodo"
    res = finish("motion_generate", rc, log, t0, ["motion"], asset_id,
                 {"licences": ("Kimodo code Apache-2.0; Kimodo weights NVIDIA Open Model License; " if kim else "ARDY code Apache-2.0; ARDY weights NVIDIA Open Model Agreement; ")
                              + "Llama 3 Community License (Built with Meta Llama 3): see THIRD_PARTY_NOTICES.md",
                  "tier_note": ("Proof tier (never run on a GPU in 0.1.12): Kimodo SOMA 77-joint skeleton retargeted by name and rest-direction onto the 106 rig; fingers 1-3 are mapped. " if kim else
                                "Proof tier: ARDY Core 27-joint skeleton retargeted by name and rest-direction onto the 106 rig. Fingers other than the thumb root follow the hand rigidly. ")
                              + "LOOK at the clip in stills before using it."})
    if res.get("receipt"):
        try:
            p = cf_guard.read_json(res["receipt"]).get("params", {})
            res.update(clip_id=p.get("clip_id"), stills_use=p.get("stills_use"), installed_clip=p.get("installed_clip"), frames=p.get("frames"), fps=p.get("fps"))
        except Exception:
            pass
    return res


_STR = {"type": "string"}
TOOLS = {
    "factory_status": (t_factory_status, "Read-only: GPU lock (and whether its pid is alive), free RAM against the start limit, newest receipts (unparsable ones are flagged).",
                       {}, [], False, False),
    "flux_render": (t_flux_render, "Render ONE Flux row of a kit (flux/flux_kit.py <kit> <row_id>) under the GPU guard. Refuses while another GPU job holds the lock or free RAM is "
                    "under the start limit. dry_run=true only prints the plan (CF_DRY). Returns the receipt path.",
                    {"kit": {**_STR, "description": "kit json, e.g. kits/vampire_sheet_kit.json"}, "row_id": _STR, "dry_run": {"type": "boolean"}}, ["kit", "row_id"], True, True),
    "trellis_run": (t_trellis_run, "Cut-out then TRELLIS for one picked image (tools/run_3d.py) with a preset from config/config.json trellis.presets (default: the hero settings; a speed preset such as master_reduced is a benchmark "
                    "profile and needs speed_reason). Refuses on a live GPU lock, low RAM, or an image whose short side is under 1024 px. Returns the cutout and trellis receipt paths.",
                    {"asset_id": _STR, "image": _STR, "preset": _STR, "speed_reason": _STR}, ["asset_id", "image"], True, True),
    "pixal3d_run": (t_pixal3d_run, "One cut-out image to a textured GLB with Pixal3D (tools/run_pixal3d.py) under the GPU guard. Presets from config/config.json pixal3d.presets. Refuses on a live GPU lock, "
                    "low RAM, or an image whose short side is under 1024 px. Writes a pixal3d receipt (stage pixal3d); the asset pipeline label is PIXAL3D. "
                    "MULTI-VIEW (0.1.20, the capability this tool exists for): pass `views` instead of `image` to join the sheet's views into ONE shape (four-view input and pixel back-projection, cameras given). "
                    "Refuses on missing _mv weights (exact file list), a view without alpha, differing view sizes, a view under the 1024 px / 60% subject gates. Receipt params.mode = multiview with angles, hashes and assumption flags.",
                    {"asset_id": _STR, "image": {**_STR, "description": "single view (kept for the A/B against TRELLIS); omit when views is given"}, "preset": _STR,
                     "views": {"description": "multi-view: a dict cell -> cut-out png path (head_front, head_profile, head_three_quarter, head_back; or body_underlayer_front/left/right/back), or a folder / mapping json path (tools/pixal3d_mv.py)", "anyOf": [{"type": "object"}, {"type": "string"}]},
                     "mirror_profile": {"type": "boolean", "description": "multi-view: mirror the one profile to stand in for the missing opposite one (flagged assumption, default false)"},
                     "three_quarter": {"type": "boolean", "description": "multi-view: include the three-quarter cell at its nominal 45 degrees (default true)"}}, ["asset_id"], True, True),
    "ab_shape": (t_ab_shape, "Pixal3D vs TRELLIS A/B on ONE cut-out png and ONE seed (tools/run_ab.py). engines=both|trellis|pixal3d runs the GPU half(s), TRELLIS first (preset tokens98k_fresh, then Pixal3D preset standard), "
                 "each under the GPU lock and RAM gate; a half REFUSES while a stage process (the sheet queue), a live GPU lock or low RAM says the GPU is not free. collect=true runs no GPU half: it gathers the finished halves "
                 "from receipts, binds both meshes with the same donor (bind gate numbers), runs QC, renders matched lit turntables, writes a contact sheet and a blind A/B scorecard (JSON + Markdown). "
                 "Decision: Pixal3D wins only if it beats TRELLIS by 1 point or more on two criteria AND its bind gate is no worse (python tools/run_ab.py --decide).",
                 {"png": {**_STR, "description": "the Factory's already cut-out RGBA png (alpha required, short side >= 1024 px), the same file for both engines"}, "asset_id": _STR, "seed": {"type": "integer", "description": "default config defaults.seed (42); the same seed goes to both engines"},
                  "engines": {"type": "string", "enum": ["both", "trellis", "pixal3d"], "description": "which half(s) to run, or with collect which to gather (default both)"},
                  "collect": {"type": "boolean", "description": "true = no GPU half: gather results from receipts, bind, QC, turntables, scorecard"},
                  "bind": {"type": "string", "enum": ["auto", "yes", "no"], "description": "auto (default) binds when the mesh looks like a body-only T-pose and says why when it skips; yes refuses if it is not; no skips"},
                  "naf": {"type": "string", "enum": ["on", "off"], "description": "Pixal3D half only (default on). off = run_pixal3d.py --no-naf: labelled ROUGH / NAF-off, the scorecard is never decisive"}}, ["png", "asset_id"], True, True),
    "cutout": (t_cutout, "BiRefNet cut-out on the CPU (tools/cutout_cpu.py) with TRELLIS3_REMBG removed from its environment. Refuses on low RAM or a short side under 1024 px. Returns the receipt path.",
               {"asset_id": _STR, "image": _STR}, ["asset_id", "image"], False, True),
    "bind": (t_bind, "Bind a body mesh to the psyche-106 rig with the binder in config bind.binder: skin_tokens (SkinTokens, GPU job, tools/run_skin_tokens.py) or donor (tools/run_bind.py, CPU Blender, "
             "MakeHuman donor weights, the labelled fallback). When skin_tokens fails its gate or its prerequisites are missing and bind.fallback_to_donor is true, the donor bind runs and the receipt and result say "
             "'FALLBACK donor (MakeHuman weights)' (params.binder_used). A busy GPU lock or low RAM refuses and never falls back. Returns the receipt path, the gate metrics, binder_used and a "
             "warning when bind_best.py reports GATE_FAIL on the relative-to-control clause that run_bind.py does not check.",
             {"asset_id": _STR, "mesh": _STR, "donor": {**_STR, "description": "optional, donor binder only, default config character.default_donor"},
              "binder": {"type": "string", "enum": ["skin_tokens", "donor"], "description": "optional, overrides config bind.binder for this call"}}, ["asset_id", "mesh"], False, True),
    "skin_tokens_bind": (t_skin_tokens_bind, "Skin a body GLB onto a skeleton with SkinTokens (VAST-AI, MIT; tools/run_skin_tokens.py, demo.py --use_skeleton), default skeleton the psyche-106 rig. "
                         "GPU job under the lock, RAM gate and gpu_guard. The result is gated like bind (config bind.gate) AND against the donor control (relative clause); the receipt is stage "
                         "bind. Output bound_skintokens.glb never overwrites the donor bind. Refuses on a live GPU lock, low RAM, a missing file, SkinTokens install or donor control.",
                         {"glb": {**_STR, "description": "body mesh GLB (T-posed, body only)"}, "skeleton": {**_STR, "description": "optional rig GLB, default config.local rig_glb"},
                          "asset_id": {**_STR, "description": "optional, default the folder of mesh.glb or the file stem"}}, ["glb"], True, True),
    "stills": (t_stills, "Render head, idle, walk, attack stills and a thumbnail of a bound GLB (tools/run_stills.py, Cycles on CPU). Proof-tier lighting. Refused (hooks/qc_gate.py) until the latest `qc` receipt for the asset passes. Returns the receipt path.",
               {"asset_id": _STR, "glb": _STR}, ["asset_id", "glb"], False, True),
    "cloth": (t_cloth, "Simulated cloth drape (tools/run_cloth.py, CPU Blender, NO GPU lock): a gear part (cloak first) becomes a Cloth object, the T-posed bound body a Collision object, the vertices near the neck and shoulder bones are pinned, "
              "it is simulated until its speed settles, and the settled mesh is exported as the part's new REST pose (cloth_<part>.glb, UVs kept, penetration into the full-resolution body reported). Presets silk, cotton, wool. "
              "mode=clip (per-clip sim as shape keys) is a stub and refuses with NotImplemented. Refuses on low RAM, a missing file, an unknown preset; the runner also waits for the exclusive Blender lock and for a Flux model load to pass. Returns the receipt path and sim, penetration, pin numbers.",
              {"asset_id": _STR, "part": {**_STR, "description": "gear part name, e.g. cloak"}, "body_glb": {**_STR, "description": "the bound T-pose body GLB (bound.glb)"},
               "mesh": {**_STR, "description": "the part mesh GLB, unskinned (default <outputs_dir>/3d/<asset_id>/<part>.glb)"}, "preset": {**_STR, "description": "silk | cotton | wool (default config cloth.default_preset)"},
               "mode": {"type": "string", "enum": ["rest", "clip"], "description": "rest (default) or clip (NotImplemented)"}, "max_frames": {"type": "integer", "description": "0 = config cloth.sim.max_frames"}}, ["asset_id", "part", "body_glb"], False, True),
    "materials": (t_materials, "Scanned CC0 PBR material (Poly Haven / ambientCG, config/materials.json) on ONE gear-part GLB (coat, cloak or boots) rendered with the SAME camera and three-point rig as the A/B turntable, "
                  "side by side with the baked-texture render (tools/run_materials.py, CPU Blender, NO GPU lock). Give asset_id + glb (+ part, + swatch), or only character_spec for the swatch -> scanned set plan. "
                  "Refuses on free RAM under materials.min_free_ram_gb (10), a missing glb or scanned set, an unknown part or swatch, and a swatch with no scan (pale_skin, dark_hair, lace). Waits for a Flux model load and for "
                  "blender_job.lock. Writes a materials receipt. Never downloads: tools/materials/fetch_materials.py does that, only with the owner's yes.",
                  {"asset_id": _STR, "glb": {**_STR, "description": "the gear part GLB, e.g. kits_out/3d/vampire-dracula-coat/mesh.glb"},
                   "character_spec": {**_STR, "description": "characters/<id>.json: with no glb, returns the swatch -> scanned set plan"},
                   "part": {"type": "string", "enum": ["coat", "cloak", "boots"], "description": "default coat"},
                   "swatch": {**_STR, "description": "optional, default config/materials.json parts[part].swatch, e.g. velvet_lining, black_leather"}}, [], False, True),
    "qc": (t_qc, "Deterministic QC of a textured mesh GLB (tools/run_qc.py, CPU, numpy only): non-manifold and boundary edges, loose shells, triangles vs budget, faceting; near-black "
           "texels, UV coverage and stretch, luminance/saturation against the concept image (flags linear-not-encoded); front silhouette coverage vs the concept matte (thin-structure loss). "
           "Thresholds: config/config.json qc. Writes receipts/<asset>/qc-*.json with pass/fail per metric and overall; `stills` is refused until the latest one passes. Optional concept (image path) and budget_tris.",
           {"asset_id": _STR, "glb": _STR, "concept": {**_STR, "description": "concept/cutout image (RGBA matte preferred); without it the concept checks are skipped"},
            "budget_tris": {"type": "integer", "description": "triangle budget; omitted = config qc.geometry.budget_tris (null = not checked)"},
            "render": {**_STR, "description": "optional image of the mesh (a still); only used when config qc.moge.enabled (MoGe-2 faceting and normal/depth agreement against the concept; off by default)"}}, ["asset_id", "glb"], False, False),
    "upscale": (t_upscale, "Real-ESRGAN x4 with Lab colour match (tools/upscale/upscale_match.py) under the GPU guard. asset_id defaults to the output file stem so a receipt is written. "
                "Refuses on a live GPU lock or low RAM. Returns the receipt path.",
                {"src": _STR, "dst": _STR, "passes": {"type": "integer"}, "asset_id": _STR}, ["src", "dst"], True, True),
    "motion_generate": (t_motion_generate, "Generate a motion clip from text with NVIDIA ARDY (default) or Kimodo (backend=kimodo, own Kimodo-312 venv, optional sparse constraints) (tools/run_motion.py) and retarget it onto the psyche-106 rig with headless Blender into a Mixamo-format clip GLB "
                        "that the stills stage finds by name (install=true copies it to mixamo_clips_dir, never overwriting). The text encoder is LLM2Vec on a LOCAL Llama 3 mirror: no network, no token. "
                        "Refuses on a live GPU lock (device=gpu), low RAM, a missing or wrong-size weight/Llama shard/adapter/venv/rig file, an over-long text or duration, invalid constraints. Returns the receipt path, clip id and the stills hint.",
                        {"text": {**_STR, "description": "what the character does, one sentence, e.g. 'A person walks forward.'"}, "asset_id": {**_STR, "description": "receipt folder, default motion-clips"},
                         "duration": {"type": "number", "description": "seconds, 1 to config motion.max_duration_s"}, "seed": {"type": "integer"}, "clip_id": _STR,
                         "in_place": {"type": "boolean"}, "install": {"type": "boolean"}, "device": {**_STR, "description": "gpu (default) or cpu"},
                         "encoder_device": {**_STR, "description": "cuda or cpu for the text encoder (cpu needs about 24 GB free RAM)"},
                         "backend": {"type": "string", "enum": ["ardy", "kimodo"], "description": "ardy (default, text only) or kimodo (SOMA 77 joints, 30 fps, own venv)"},
                         "constraints": {"description": "kimodo only: a path to a Kimodo constraints.json, or the list itself: [{type: root2d|fullbody|left-hand|right-hand|left-foot|right-foot|end-effector, frame_indices, smooth_root_2d | root_positions + local_joints_rot, joint_names}] (one-sentence text)",
                                         "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "object"}}]}}, ["text"], True, True),
}


# ---- MCP over stdio ---------------------------------------------------------------------------------------------------------------------------------
def call_tool(name: str, args: dict) -> tuple[str, bool]:
    fn = TOOLS[name][0]
    try:
        result = fn(**args)
    except TypeError as e:
        return f"bad arguments for {name}: {e}", True
    except Exception as e:   # the model sees the failure; the server stays up
        return f"{type(e).__name__}: {e}", True
    bad = result.get("status") in ("refused", "fail")
    return json.dumps(result, indent=2, default=str), bad


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None   # notifications (initialized, cancelled) need no reply
    if method == "initialize":
        result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", PROTOCOL_DEFAULT), "capabilities": {"tools": {}},
                  "serverInfo": {"name": "factory", "version": SERVER_VERSION}}
    elif method == "tools/list":
        result = {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r},
                             **({"annotations": {"readOnlyHint": True}} if n == "factory_status" else {})} for n, (_, d, p, r, _g, _h) in TOOLS.items()]}
    elif method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {params.get('name')}"}}
        text, err = call_tool(params["name"], params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": err}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _on_term(signum, frame):
    raise SystemExit(0)   # unwinds through run_stage's finally, which kills the running stage's tree


def main() -> None:
    import signal
    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), _on_term)
            except (ValueError, OSError):
                pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line))
        except json.JSONDecodeError as e:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse error: {e}"}}
        except Exception as e:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
