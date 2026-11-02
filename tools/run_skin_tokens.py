"""Skin a body onto the psyche-106 rig with SkinTokens (VAST-AI, MIT; demo.py --use_skeleton), gate it, one receipt (stage "bind").
Usage: run_skin_tokens.py <asset_id> <body.glb> [<skeleton.glb>]      (skeleton default: config.local rig_glb)
GPU job (the MCP tool skin_tokens_bind starts it under tools/gpu_guard.ps1). Settings: config/config.json ("skin_tokens", "bind.gate"), paths: config.local.json.
Steps: Blender CPU builds body+skeleton input -> SkinTokens demo.py -> Blender CPU names Rig/Body, clamps influences, saves .blend + GLB -> Blender CPU runs
bind/metrics2.py with the donor control -> gate (bind.gate numbers + relative-to-control clause) -> receipt. Output GLB is bound_skintokens.glb; the donor bind's bound.glb is never touched."""
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "skin_tokens"))
import cf_common
import gate as sgate


def main(argv):
    cf_common.pop_allow_stale()
    argv = sys.argv
    if len(argv) not in (3, 4):
        sys.exit(__doc__)
    cf_common.plugin_gate("models", "bind", "skin_tokens")   # latest-models gate (plugin 0.1.27)
    asset_id, body = argv[1], os.path.abspath(argv[2])
    cfg = cf_common.load_config()
    loc, bd, st = cfg["local"], cfg["bind"], cfg["skin_tokens"]
    skeleton = os.path.abspath(argv[3]) if len(argv) == 4 else loc["rig_glb"]
    work = os.path.join(loc["outputs_dir"], "3d", asset_id)
    wk = os.path.join(work, st["work_subdir"])
    os.makedirs(wk, exist_ok=True)
    log = os.path.join(loc["logs_dir"], f"skintokens_{asset_id}.txt")
    bound_glb = os.path.join(work, st["bound_glb_name"])
    blend = os.path.splitext(bound_glb)[0] + ".blend"
    control_path = os.path.join(cf_common.REPO, st["control_json"].format(donor=st["control_donor"]))
    repo_dir, py = loc["skin_tokens_repo_dir"], loc["skin_tokens_python"]
    params = {"gate": bd["gate"], "relative": st["relative"], "ckpt": st["ckpt"], "skeleton": skeleton, "control": control_path, "log": log,
              "use_transfer": st["use_transfer"], "clamp_influences": bd["gate"]["max_influences"] if st["clamp_influences"] else 0,
              "blender": loc["blender_exe"], "skin_tokens_repo": repo_dir, "binder_used": "skin_tokens",
              "launcher": st.get("launcher") or None}

    def receipt(status, error="", outs=(), timing=None, mem=None, extra=None):
        return cf_common.write_receipt(cfg, asset_id, "bind", tool="run_skin_tokens.py", model={"id": "SkinTokens TokenRig (VAST-AI, MIT)", "ckpt": st["ckpt"]},
                                       inputs=[body, skeleton], params={**params, **(extra or {})}, timing=timing or {}, memory=mem or {},
                                       outputs=list(outs), status=status, error=error)

    missing = [f"{k}: {p}" for k, p in (("blender_exe", loc["blender_exe"]), ("skeleton", skeleton), ("skin_tokens_repo_dir", repo_dir), ("skin_tokens_python", py),
                                         ("ckpt", os.path.join(repo_dir, st["ckpt"])), ("vae_ckpt", os.path.join(repo_dir, st["vae_ckpt"])), ("control", control_path),
                                         ("body", body)) + ((("launcher", st["launcher"]),) if st.get("launcher") else ()) if not os.path.exists(p)]
    if missing:
        receipt("fail", "missing: " + "; ".join(missing))
        sys.exit("missing: " + "; ".join(missing))
    control = sgate.load_control(control_path)
    launcher = st.get("launcher") or ""   # optional external launcher (Models Dev's local file, never copied into the repo): runs instead of demo.py
    if launcher:
        params["launcher_sha256"] = cf_common.sha256(launcher)

    ram = cf_common.FreeRamWatch(); ram.start()
    t0 = time.time()
    inp, prep_json = os.path.join(wk, "input_skeleton.glb"), os.path.join(wk, "prep.json")
    raw_out, export_json = os.path.join(wk, "skintokens_raw.glb"), os.path.join(wk, "export.json")
    best_metrics = os.path.join(wk, "skin_tokens.metrics.json")
    sdir = os.path.join(cf_common.REPO, st["script_dir"])

    def step(name, cmd, timeout, cwd=None, env=None):
        with open(log, "a", encoding="utf-8") as lf:
            lf.write(f"\n==== {name}: {' '.join(cmd)}\n"); lf.flush()
            try:
                return subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout, cwd=cwd, env=env).returncode
            except subprocess.TimeoutExpired:
                return "timeout"

    open(log, "w").close()
    blender = [loc["blender_exe"], "--background", "--python"]
    steps = [
        ("prep", blender + [os.path.join(sdir, st["prep_script"]), "--", body, skeleton, inp, prep_json], st["prep_timeout_s"], None, None, inp),
        ("skintokens", [py, launcher, inp, raw_out] if launcher else [py, "demo.py", "--input", inp, "--output", raw_out, "--use_skeleton", "--model_ckpt", st["ckpt"], "--num_beams", str(st["num_beams"]),
                        "--top_k", str(st["top_k"]), "--top_p", str(st["top_p"]), "--temperature", str(st["temperature"]),
                        "--repetition_penalty", str(st["repetition_penalty"])] + (["--use_transfer"] if st["use_transfer"] else []),
         st["run_timeout_s"], repo_dir, dict(os.environ, **st["attn_env"], **({"NUM_BEAMS": str(st["num_beams"])} if launcher else {}), PYTHONPATH=repo_dir, **({"SKINTOKENS_BPY_PYTHON": loc["skin_tokens_bpy_python"]} if loc.get("skin_tokens_bpy_python") else {})), raw_out),
        ("export", blender + [os.path.join(sdir, st["export_script"]), "--", raw_out, blend, bound_glb, export_json, prep_json, bd["rig_name"], bd["body_name"],
                              str(params["clamp_influences"])], st["export_timeout_s"], None, None, blend),
        ("metrics", blender + [os.path.join(cf_common.REPO, st["metrics_script"]), "--", blend, best_metrics, "--control", control_path],
         st["metrics_timeout_s"], None, None, best_metrics),
    ]
    for name, cmd, tmo, cwd, env, product in steps:
        rc = step(name, cmd, tmo, cwd, env)
        if rc != 0 or not os.path.exists(product):
            receipt("fail", f"{name} step failed (exit {rc}), see log", timing={"wall_s": round(time.time() - t0, 1)}, mem={"lowest_free_ram_gb": ram.stop()},
                    outs=[p for p in (inp, raw_out) if os.path.exists(p)])
            sys.exit(f"{name} failed: {rc}")
    m = json.load(open(best_metrics, encoding="utf-8"))
    exp = json.load(open(export_json, encoding="utf-8"))
    checks = sgate.checks(m, bd["gate"], control, st["relative"], exp)
    ok = all(checks.values()) and os.path.exists(bound_glb)
    err = "" if ok else "gate failed: " + ",".join(sgate.failed(checks)) + ("" if os.path.exists(bound_glb) else "; glb export missing")
    metrics = {k: m.get(k) for k in ("edges_measured_ge_5mm", "p99_stretch", "p99_compression_ratio", "diag_max_stretch", "diag_min_ratio", "violating_edges_by_region", "score", "gate_pass")}
    metrics.update(weights=m.get("weights"), checks=checks, gate_passed=ok, binder="skin_tokens", control=control, export=exp, metrics2_gate=m.get("gate"))
    receipt("ok" if ok else "fail", err, outs=[p for p in (bound_glb, blend, best_metrics, raw_out) if os.path.exists(p)],
            timing={"wall_s": round(time.time() - t0, 1)}, mem={"lowest_free_ram_gb": ram.stop()}, extra={"metrics": metrics})
    print("skin_tokens", "ok" if ok else "FAIL", "p99_stretch", m.get("p99_stretch"), "p99_compression", m.get("p99_compression_ratio"), "->", bound_glb)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv)
