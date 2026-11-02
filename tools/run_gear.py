"""Bind one GEAR part (coat, cloak, mantle, boots) to the bound body's skeleton by nearest-surface-point weight transfer, gate it, one receipt.
Usage: run_gear.py <asset_id> <part_name> <gear_mesh.glb> <bound_body.glb>
Settings come from config/config.json ("gear", plus "bind" and "stills" for rig names and clips) and config.local.json; nothing here is a literal path or number.
CPU only: Blender runs --background (bind/bind_gear.py), no render, no GPU.
Gear hangs rather than deforms, so the gate is looser than the body gate: zero-weight vertices 0, <= max influences, weights sum to 1, p99 stretch <= gear.gate.p99_stretch_max.
Output: <outputs_dir>/3d/<asset_id>/<glb_prefix><part>.glb (Rig + gear mesh, skinned)."""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

if len(sys.argv) != 5:
    sys.exit(__doc__)
asset_id, part, gear_mesh, body_glb = sys.argv[1], sys.argv[2], os.path.abspath(sys.argv[3]), os.path.abspath(sys.argv[4])

cfg = cf_common.load_config()
cf_common.plugin_gate("ram_share")   # RAM-sharing rule (plugin 0.1.27)
loc, gg, st = cfg["local"], cfg["gear"], cfg["stills"]
gate = gg["gate"]
work = os.path.join(loc["outputs_dir"], "3d", asset_id)
gwork = os.path.join(work, gg["work_subdir"])
os.makedirs(gwork, exist_ok=True)
out_glb = os.path.join(work, f"{gg['glb_prefix']}{part}.glb")
script = os.path.join(cf_common.REPO, cfg["bind"]["script_dir"], gg["script"])
argf = os.path.join(gwork, f"{part}_args.json")
resf = os.path.join(gwork, f"{part}_result.json")
log = os.path.join(loc["logs_dir"], f"gear_{asset_id}_{part}.txt")
clips = {k: os.path.join(loc["mixamo_clips_dir"], st["clips"][k] + st["clip_ext"]) for k in gg["stretch_clips"]}
params = {"part": part, "gate": gate, "blender": loc["blender_exe"], "log": log, "clips": gg["stretch_clips"],
          "smooth_passes": gg["smooth_passes"], "max_influences": gg["max_influences"], "fit": gg["fit"]["mode"]}


def receipt(status, error="", outs=(), timing=None, mem=None, extra=None):
    cf_common.write_receipt(cfg, asset_id, "bind", tool=gg["script"], model={"id": "nearest-surface-point weight transfer from bound body"},
                            inputs=[gear_mesh, body_glb], params={**params, **(extra or {})}, timing=timing or {}, memory=mem or {}, outputs=list(outs), status=status, error=error)


problems = [f"{k} not found: {loc[k]}" for k in ("blender_exe", "mixamo_clips_dir") if not os.path.exists(loc[k])]
problems += [f"{k} clip not found: {p}" for k, p in clips.items() if not os.path.exists(p)]
problems += [f"{n} not found: {p}" for n, p in (("gear mesh", gear_mesh), ("bound body", body_glb), ("script", script)) if not os.path.exists(p)]
if problems:
    receipt("fail", "; ".join(problems))
    sys.exit("cannot bind gear:\n  " + "\n  ".join(problems))

with open(argf, "w", encoding="utf-8") as f:
    json.dump({"gear": gg, "stills": st, "bind": cfg["bind"], "part": part, "body": body_glb, "gear_glb": gear_mesh, "out": out_glb, "result": resf, "clips": clips}, f)
for p in (resf, out_glb):
    if os.path.exists(p):
        os.remove(p)

ram = cf_common.FreeRamWatch(); ram.start()
t0 = time.time()
try:
    with open(log, "w", encoding="utf-8") as lf:
        r = subprocess.run([loc["blender_exe"], "--background", "--python", script, "--", argf], stdout=lf, stderr=subprocess.STDOUT, timeout=gg["timeout_s"]).returncode
except subprocess.TimeoutExpired:
    r = "timeout"
res = json.load(open(resf, encoding="utf-8")) if os.path.exists(resf) else {}
timing, mem = {"wall_s": round(time.time() - t0, 1)}, {"lowest_free_ram_gb": ram.stop()}
if r != 0 or not os.path.exists(out_glb):
    receipt("fail", f"blender exit {r}{': ' + res['error'] if res.get('error') else ''}, see log", timing=timing, mem=mem, extra={"result": res})
    sys.exit(f"bind_gear failed: {r}")

w, s = res.get("weights", {}), res.get("stretch", {})
checks = {
    "edges_measured": s.get("edges_measured", 0) > 0 and s.get("p99_stretch") is not None,
    "p99_stretch": s.get("p99_stretch", 9e9) <= gate["p99_stretch_max"],
    "zero_weight_vertices": w.get("zero_weight_vertices", 1) <= gate["zero_weight_vertices_max"],
    "max_influences": w.get("max_influences", 99) <= gate["max_influences"],
    "weight_sum_dev": w.get("weight_sum_max_dev") is not None and w["weight_sum_max_dev"] <= gate["weight_sum_tol"],
}
passed = all(checks.values())
err = "" if passed else "gate failed: " + ",".join(k for k, v in checks.items() if not v)
receipt("ok" if passed else "fail", err, outs=[out_glb], timing=timing, mem=mem, extra={"metrics": {**res, "checks": checks, "gate_passed": passed}})
print("gear", part, "ok" if passed else "FAIL", "p99_stretch", s.get("p99_stretch"), "->", out_glb)
sys.exit(0 if passed else 1)
