"""Fallback: clean a TRELLIS mesh before rig binding (bind/clean_mesh.py), one receipt (stage "bind", params.clean=true).
Usage: run_clean.py <asset_id> <mesh.glb>
Output: <outputs_dir>/3d/<asset_id>/<clean.out_name>; feed that to run_bind.py. Settings from config/config.json ("clean") and config.local.json.
CPU only: Blender --background; the texture re-bake (voxel mode only) uses Cycles CPU at 1 sample."""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

if len(sys.argv) != 3:
    sys.exit(__doc__)
asset_id, mesh = sys.argv[1], os.path.abspath(sys.argv[2])

cfg = cf_common.load_config()
loc, cl = cfg["local"], cfg["clean"]
work = os.path.join(loc["outputs_dir"], "3d", asset_id)
os.makedirs(work, exist_ok=True)
out = os.path.join(work, cl["out_name"])
rep_json = os.path.splitext(out)[0] + ".report.json"
log = os.path.join(loc["logs_dir"], f"clean_{asset_id}.txt")
script = os.path.join(cf_common.REPO, cl["script_dir"], cl["script"])
keys = ("merge_dist_m", "min_shell_verts", "count_weld_m", "voxel_m", "tex_size", "bake_margin_px", "bake_extrude_m")
params = {"clean": True, "settings": {k: cl[k] for k in keys}, "blender": loc["blender_exe"], "log": log}


def receipt(status, error="", outs=(), timing=None, mem=None, extra=None):
    cf_common.write_receipt(cfg, asset_id, "bind", tool=cl["script"], model={"id": "mesh clean (blender)"}, inputs=[mesh], params={**params, **(extra or {})},
                            timing=timing or {}, memory=mem or {}, outputs=list(outs), status=status, error=error)


missing = [f"{k}: {loc[k]}" for k in ("blender_exe",) if not os.path.exists(loc[k])]
if not os.path.exists(mesh):
    missing.append(f"mesh: {mesh}")
if not os.path.exists(script):
    missing.append(f"script: {script}")
if missing:
    receipt("fail", "missing: " + "; ".join(missing))
    sys.exit("missing: " + "; ".join(missing))

for p in (out, rep_json):
    if os.path.exists(p):
        os.remove(p)
ram = cf_common.FreeRamWatch(); ram.start()
t0 = time.time()
cmd = [loc["blender_exe"], "--background", "--python", script, "--", mesh, out,
       "--merge-dist", str(cl["merge_dist_m"]), "--min-shell-verts", str(cl["min_shell_verts"]), "--count-weld", str(cl["count_weld_m"]),
       "--voxel", str(cl["voxel_m"]), "--tex-size", str(cl["tex_size"]), "--bake-margin", str(cl["bake_margin_px"]),
       "--bake-extrude", str(cl["bake_extrude_m"]), "--report", rep_json]
try:
    with open(log, "w", encoding="utf-8") as lf:
        r = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=cl["timeout_s"]).returncode
except subprocess.TimeoutExpired:
    r = "timeout"
timing, mem = {"wall_s": round(time.time() - t0, 1)}, {"lowest_free_ram_gb": ram.stop()}
if r != 0 or not os.path.exists(out) or not os.path.exists(rep_json):
    receipt("fail", f"clean_mesh exit {r}, see log", timing=timing, mem=mem)
    sys.exit(f"clean failed: {r}")
rep = json.load(open(rep_json, encoding="utf-8"))
receipt("ok", outs=[p for p in (out, rep_json, rep.get("baked_texture", "")) if p and os.path.exists(p)], timing=timing, mem=mem,
        extra={"before": rep["before"], "after": rep["after"], "deleted_shell_verts": rep["deleted_shell_verts"]})
print("clean ok before", rep["before"], "after", rep["after"], "->", out)
