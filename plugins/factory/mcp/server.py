#!/usr/bin/env python3
"""Factory MCP server (stdio, standard library only): the Content-Factory stages as tools for any Claude Code session.

  factory_status()                     GPU lock, free RAM, limits, newest receipts. Read-only.
  flux_render(kit, row_id)             one Flux row            -> flux/flux_kit.py        (GPU)
  trellis_run(asset_id, image, preset) cut-out then TRELLIS    -> tools/run_3d.py         (GPU)
  cutout(asset_id, image)              BiRefNet cut-out (CPU)  -> tools/cutout_cpu.py
  bind(asset_id, mesh)                 hybrid donor bind       -> tools/run_bind.py       (CPU Blender)
  skin_tokens_bind(glb, skeleton)      SkinTokens skin weights -> tools/run_skin_tokens.py (GPU, gated like bind + relative-to-control)
  stills(asset_id, glb)                head/idle/walk/attack   -> tools/run_stills.py     (CPU Blender)
  upscale(src, dst)                    Real-ESRGAN x4 + match  -> tools/upscale/upscale_match.py (GPU, Vulkan)

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
SERVER_VERSION = "0.1.7"
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


def t_trellis_run(asset_id: str, image: str, preset: str = "") -> dict:
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
    cmd = [c["local"]["python"], str(HOME / "tools" / "run_3d.py"), asset_id, image, "--preset", preset]
    rc, log, t0 = run_stage("trellis_run", cmd, timeout=c["character"]["trellis_timeout_s"], guard=c["character"]["guard_match"]["trellis"])
    extra = {"preset": preset}
    if presets[preset].get("texture_size", 0) < 4096:
        extra["tier_note"] = "2048 texture / 500k faces is a benchmark profile: a proof, never shown as a final. Finals come from the hero rebake (hero_rebake preset, --from-latents)."
    return finish("trellis_run", rc, log, t0, ["cutout", "trellis"], asset_id, extra)


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


def t_bind(asset_id: str, mesh: str, donor: str = "") -> dict:
    c = cfg()
    miss = need_file(mesh, "mesh")
    if miss:
        return {"status": "refused", "error": miss}
    why = gate(gpu=False, heavy=True)
    if why:
        return {"status": "refused", "error": why}
    donor = donor or c["character"]["default_donor"]
    b = c["bind"]
    cmd = [c["local"]["python"], str(HOME / "tools" / "run_bind.py"), asset_id, mesh]
    rc, log, t0 = run_stage("bind", cmd, env={"BIND_FORCE_DONOR": donor}, timeout=b["timeout_s"] + b["export_timeout_s"] + 60)
    extra = {"donor": donor}
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
                       (str(control), "donor control metrics (the relative-to-control clause needs it; bind_best.py builds it once per donor)")):
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


_STR = {"type": "string"}
TOOLS = {
    "factory_status": (t_factory_status, "Read-only: GPU lock (and whether its pid is alive), free RAM against the start limit, newest receipts (unparsable ones are flagged).",
                       {}, [], False, False),
    "flux_render": (t_flux_render, "Render ONE Flux row of a kit (flux/flux_kit.py <kit> <row_id>) under the GPU guard. Refuses while another GPU job holds the lock or free RAM is "
                    "under the start limit. dry_run=true only prints the plan (CF_DRY). Returns the receipt path.",
                    {"kit": {**_STR, "description": "kit json, e.g. kits/vampire_sheet_kit.json"}, "row_id": _STR, "dry_run": {"type": "boolean"}}, ["kit", "row_id"], True, True),
    "trellis_run": (t_trellis_run, "Cut-out then TRELLIS for one picked image (tools/run_3d.py) with a preset from config/config.json trellis.presets (default master_reduced, a benchmark "
                    "profile, not a final). Refuses on a live GPU lock, low RAM, or an image whose short side is under 1024 px. Returns the cutout and trellis receipt paths.",
                    {"asset_id": _STR, "image": _STR, "preset": _STR}, ["asset_id", "image"], True, True),
    "cutout": (t_cutout, "BiRefNet cut-out on the CPU (tools/cutout_cpu.py) with TRELLIS3_REMBG removed from its environment. Refuses on low RAM or a short side under 1024 px. Returns the receipt path.",
               {"asset_id": _STR, "image": _STR}, ["asset_id", "image"], False, True),
    "bind": (t_bind, "Bind a body mesh to the psyche-106 rig (tools/run_bind.py, CPU Blender) with the donor forced (default from config). Returns the receipt path, the gate metrics and a "
             "warning when bind_best.py reports GATE_FAIL on the relative-to-control clause that run_bind.py does not check.",
             {"asset_id": _STR, "mesh": _STR, "donor": {**_STR, "description": "optional, default config character.default_donor"}}, ["asset_id", "mesh"], False, True),
    "skin_tokens_bind": (t_skin_tokens_bind, "Skin a body GLB onto a skeleton with SkinTokens (VAST-AI, MIT; tools/run_skin_tokens.py, demo.py --use_skeleton), default skeleton the psyche-106 rig. "
                         "GPU job under the lock, RAM gate and gpu_guard. The result is gated like bind (config bind.gate) AND against the donor control (relative clause); the receipt is stage "
                         "bind. Output bound_skintokens.glb never overwrites the donor bind. Refuses on a live GPU lock, low RAM, a missing file, SkinTokens install or donor control.",
                         {"glb": {**_STR, "description": "body mesh GLB (T-posed, body only)"}, "skeleton": {**_STR, "description": "optional rig GLB, default config.local rig_glb"},
                          "asset_id": {**_STR, "description": "optional, default the folder of mesh.glb or the file stem"}}, ["glb"], True, True),
    "stills": (t_stills, "Render head, idle, walk, attack stills and a thumbnail of a bound GLB (tools/run_stills.py, Cycles on CPU). Proof-tier lighting. Returns the receipt path.",
               {"asset_id": _STR, "glb": _STR}, ["asset_id", "glb"], False, True),
    "upscale": (t_upscale, "Real-ESRGAN x4 with Lab colour match (tools/upscale/upscale_match.py) under the GPU guard. asset_id defaults to the output file stem so a receipt is written. "
                "Refuses on a live GPU lock or low RAM. Returns the receipt path.",
                {"src": _STR, "dst": _STR, "passes": {"type": "integer"}, "asset_id": _STR}, ["src", "dst"], True, True),
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
