"""Bind a TRELLIS mesh to the psyche-106 rig with the hybrid binder (bind/bind_best.py), export bound.glb, gate it, one receipt.
Usage: run_bind.py <asset_id> <mesh.glb>
Settings come from config/config.json ("bind") and config.local.json; nothing here is a literal path or number.
CPU only: Blender runs --background and never renders (bind_best is called without --stills).
Note: bind/*.py still carry their own Blender, donor and rig paths; this wrapper checks the configured ones exist and records them."""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

if len(sys.argv) != 3:
    sys.exit(__doc__)
asset_id, mesh = sys.argv[1], os.path.abspath(sys.argv[2])

cfg = cf_common.load_config()
loc, bd = cfg["local"], cfg["bind"]
gate = bd["gate"]
work = os.path.join(loc["outputs_dir"], "3d", asset_id)
bwork = os.path.join(work, bd["work_subdir"])
os.makedirs(bwork, exist_ok=True)
bound_blend = os.path.join(work, os.path.splitext(bd["bound_glb_name"])[0] + ".blend")
bound_glb = os.path.join(work, bd["bound_glb_name"])
best_json = os.path.splitext(bound_blend)[0] + ".best.json"
log = os.path.join(loc["logs_dir"], f"bind_{asset_id}.txt")
params = {"gate": gate, "blender": loc["blender_exe"], "rig": loc["rig_glb"], "donors_dir": loc["donors_dir"], "log": log}


def receipt(status, error="", outs=(), timing=None, mem=None, extra=None):
    cf_common.write_receipt(cfg, asset_id, "bind", tool=bd["best_script"], model={"id": "psyche-106 hybrid donor transfer"}, inputs=[mesh, loc["rig_glb"]],
                            params={**params, **(extra or {})}, timing=timing or {}, memory=mem or {}, outputs=list(outs), status=status, error=error)


# preflight: fail before spending any time
missing = [f"{k}: {loc[k]}" for k in ("blender_exe", "rig_glb", "donors_dir") if not os.path.exists(loc[k])]
if not os.path.exists(mesh):
    missing.append(f"mesh: {mesh}")
if missing:
    receipt("fail", "missing: " + "; ".join(missing))
    sys.exit("missing: " + "; ".join(missing))

ram = cf_common.FreeRamWatch(); ram.start()
t0 = time.time()
best = os.path.join(cf_common.REPO, bd["script_dir"], bd["best_script"])
cmd = [loc["python"], "-I", best, mesh, bound_blend, "--work", bwork]
try:
    with open(log, "w", encoding="utf-8") as lf:
        r = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=bd["timeout_s"]).returncode
except subprocess.TimeoutExpired:
    r = "timeout"
if r != 0 or not os.path.exists(bound_blend) or not os.path.exists(best_json):
    receipt("fail", f"bind_best exit {r}, see log", timing={"wall_s": round(time.time() - t0, 1)}, mem={"lowest_free_ram_gb": ram.stop()})
    sys.exit(f"bind_best failed: {r}")
summ = json.load(open(best_json, encoding="utf-8"))
mpath = os.path.join(bwork, summ["best"] + ".metrics.json")
m = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else {}
w = m.get("weights", {})

# export the winning .blend (Rig + Body, skinned) to GLB, CPU background Blender, no render
rig_n, body_n = bd["rig_name"], bd["body_name"]
expr = ("import bpy; bpy.ops.wm.open_mainfile(filepath=%r); bpy.ops.object.select_all(action='DESELECT'); "
        "[bpy.data.objects[n].select_set(True) for n in (%r, %r)]; bpy.context.view_layer.objects.active = bpy.data.objects[%r]; "
        "bpy.ops.export_scene.gltf(filepath=%r, export_format='GLB', use_selection=True, export_apply=False)"
        % (bound_blend, rig_n, body_n, rig_n, bound_glb))
if os.path.exists(bound_glb):
    os.remove(bound_glb)
with open(log, "a", encoding="utf-8") as lf:
    try:
        re_ = subprocess.run([loc["blender_exe"], "--background", "--python-expr", expr], stdout=lf, stderr=subprocess.STDOUT, timeout=bd["export_timeout_s"]).returncode
    except subprocess.TimeoutExpired:
        re_ = "timeout"

checks = {
    "edges_measured_ge_min": m.get("edges_measured_ge_5mm", 0) > 0,
    "p99_stretch": m.get("p99_stretch", 9e9) <= gate["p99_stretch_max"],
    "p99_compression_ratio": m.get("p99_compression_ratio", -1) >= gate["p99_compression_ratio_min"],
    "zero_weight_vertices": w.get("zero_weight_vertices", 1) <= gate["zero_weight_vertices_max"],
    "max_influences": w.get("max_influences", 99) <= gate["max_influences"],
    "weight_sum_dev": w.get("weight_sum_max_dev") is not None and w["weight_sum_max_dev"] <= gate["weight_sum_tol"],
}
passed = all(checks.values())
exported = os.path.exists(bound_glb)
metrics = {k: m.get(k) for k in ("edges_measured_ge_5mm", "p99_stretch", "p99_compression_ratio", "diag_max_stretch", "diag_min_ratio", "violating_edges_by_region", "score", "gate_pass")}
metrics.update(weights=w, checks=checks, gate_passed=passed, binder=summ["best"], donor=summ["donor"], bind_best_gate=summ["gate"], binders=summ["binders"])
ok = passed and exported
errs = ([] if passed else ["gate failed: " + ",".join(k for k, v in checks.items() if not v)]) + ([] if exported else [f"glb export failed ({re_})"])
err = "; ".join(errs)
receipt("ok" if ok else "fail", err, outs=[p for p in (bound_glb, bound_blend, best_json, mpath) if os.path.exists(p)],
        timing={"wall_s": round(time.time() - t0, 1)}, mem={"lowest_free_ram_gb": ram.stop()}, extra={"metrics": metrics})
print("bind", "ok" if ok else "FAIL", "binder", summ["best"], "p99_stretch", m.get("p99_stretch"), "p99_compression", m.get("p99_compression_ratio"), "->", bound_glb)
sys.exit(0 if ok else 1)
