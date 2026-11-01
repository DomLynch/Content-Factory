"""A/B harness, Pixal3D vs TRELLIS on ONE cut-out png and ONE seed (plugin 0.1.14; decision rule in config/config.json "ab" and skills/ab-shape).
Usage:
  run_ab.py <asset_id> <png> [--seed N] [--engines both|trellis|pixal3d]          run the GPU half(s), TRELLIS first (both = halves, then collect)
  run_ab.py <asset_id> <png> [--seed N] [--engines ...] --collect                 gather finished halves from receipts, bind both, QC, turntables, contact sheet, scorecard
  run_ab.py --decide <scorecard.json>                                              apply the decision rule to the filled blind scores: WIN or NO-WIN
Input: the Factory's ALREADY cut-out RGBA png (cutout stage run once), the same file for both engines: Pixal3D gets it as-is (alpha present = no rembg), the TRELLIS half gets it through
run_3d.py --cutout-png (no second cut-out). A png without alpha is refused.
Options: --naf on|off (Pixal3D half only, default on; off = run_pixal3d.py --no-naf, labelled ROUGH / NAF-off, never decisive), --cutout-receipt PATH, --bind auto|yes|no (auto = bind when the mesh looks like a body-only T-pose, else skip and say why; yes = refuse if it does not), --no-qc, --no-render, --force (redo bind/QC/render),
         --trellis-receipt PATH / --pixal3d-receipt PATH (a half run in another checkout).
Engines run through the existing stage scripts (run_3d.py preset tokens98k_fresh, run_pixal3d.py preset standard) under the plugin's GPU guard (the MCP tool ab_shape starts each half under
gpu_guard.ps1, which takes the GPU lock); each half writes an `ab` receipt (mode half) and its engine asset id is <asset_id>-trellis / <asset_id>-pixal3d, so the pipeline label of a
mesh is never mixed. A GPU half REFUSES (exit 3) while a stage process, a live GPU lock or low free RAM says the GPU is not ours. Order of the collect: bind gate, QC, turntables, scorecard.
All numbers and paths come from config/config.json ("ab") and config.local.json."""
import argparse, json, os, subprocess, sys, time

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "ab"))
import cf_common
import ab_core
import cf_proc

REPO = cf_common.REPO


def run_logged(cmd, log, timeout, env=None, cwd=None):
    """Run a child, output to log. Returns exit code or 'timeout' (the whole tree is killed on timeout)."""
    with open(log, "w", encoding="utf-8") as lf:
        p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, env=dict(os.environ, **(env or {})), cwd=cwd)
        try:
            return p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            cf_proc.kill_tree(p.pid)
            return "timeout"


def fresh_receipt(asset, stage, t0):
    hit = ab_core.newest(REPO, asset, stage, ok_only=False)
    if hit and os.path.getmtime(hit[0]) >= t0 - 2:
        return hit
    return None


# ---- a GPU half ---------------------------------------------------------------------------------------------------------------------------------
def run_half(cfg, engine, asset_id, png, png_sha, seed, naf=True, cutout_receipt=None):
    loc, ab = cfg["local"], cfg["ab"]
    e = ab["engines"][engine]
    eid = ab_core.engine_asset(asset_id, engine, ab)
    why = ab_core.alive_reason(own=ab_core.own_pids)
    if why:
        print(why)
        return 3
    cmd = [loc["python"], os.path.join(TOOLS, e["script"]), eid, png, "--preset", e["preset"], "--seed", str(seed)]
    cutout_receipt = cutout_receipt or ab_core.find_cutout_receipt(REPO, png_sha)
    if engine == "trellis":   # the same cut-out for both engines: no second background removal on this side
        cmd += ["--cutout-png", png] + (["--cutout-receipt", cutout_receipt] if cutout_receipt else [])
    if engine == "pixal3d" and not naf:
        cmd += ["--no-naf"]
    log = os.path.join(loc["logs_dir"], f"ab_{asset_id}_{engine}.txt")
    t0 = time.time()
    rc = run_logged(cmd, log, ab["wall_hard_cap_s"] + 900)
    wall = round(time.time() - t0, 1)
    hit = fresh_receipt(eid, e["stage"], t0)
    rec = hit[1] if hit else {}
    glb = next((o["path"] for o in rec.get("outputs", []) if o["path"].lower().endswith(".glb") and os.path.exists(o["path"])), None)
    ok = rc == 0 and rec.get("status") == "ok" and bool(glb)
    over = wall > ab["wall_budget_s"]
    tail = [] if ok else ab_core.log_tail(log)   # a crash (e.g. sparse conv on ROCm) is recorded with its traceback tail; the harness never retries or patches
    err = "" if ok else (f"{engine} half failed: exit {rc}, engine receipt status {rec.get('status', 'none')}; see {log}" + (f" ({rec.get('error')})" if rec.get("error") else "")
                         + (f" | last log line: {tail[-1]}" if tail else ""))
    cf_common.write_receipt(cfg, asset_id, "ab", tool="run_ab.py", model={"id": f"{engine} half of the Pixal3D vs TRELLIS A/B", "pipeline": e["pipeline"]}, inputs=[png],
                            params={"mode": "half", "engine": engine, "pipeline": e["pipeline"], "preset": e["preset"], "seed": seed, "png_sha256": png_sha, "engine_asset": eid,
                                    "engine_receipt": hit[0] if hit else None, "wall_budget_s": ab["wall_budget_s"], "over_budget": over, "command": cmd, "log": log, "log_tail": tail,
                                    "cutout_receipt": cutout_receipt if engine == "trellis" else None, "cutout_png": png,
                                    **({"naf": naf, "quality_label": "" if naf else "ROUGH / NAF-off"} if engine == "pixal3d" else {})},
                            timing={"wall_s": wall}, memory=rec.get("memory") or {}, outputs=[glb] if glb else [], status="ok" if ok else "fail", error=err)
    print(f"ab half {engine}: {'ok' if ok else 'FAIL'} wall {wall} s{' (OVER the ' + str(ab['wall_budget_s']) + ' s budget)' if over else ''} -> {glb}")
    return 0 if ok else 1


# ---- collect: bind gate, QC, turntables, scorecard ----------------------------------------------------------------------------------------------
def do_bind(cfg, eid, glb, donor, mode, force):
    """Bind numbers for one mesh. Returns (numbers dict, refusal text). Reuses a bind receipt of the same GLB bytes and donor unless force."""
    ab, loc, bd = cfg["ab"], cfg["local"], cfg["bind"]
    sha = ab_core.sha256_file(glb)
    if mode == "no":
        return {"measured": False, "skipped": "--bind no"}, ""
    why = ab_core.tpose_reason(glb, ab["bind"]["min_span_over_height"])
    if why:
        return {"measured": False, "skipped": why}, (why if mode == "yes" else "")
    if not force:
        for f, r in [h for h in (ab_core.newest(REPO, eid, "bind", ok_only=False),) if h]:
            ins = [i.get("sha256") for i in r.get("inputs", [])]
            if sha in ins and ((r.get("params") or {}).get("metrics") or {}).get("donor") == donor:
                return ab_core.bind_numbers(r, f), ""
    t0 = time.time()
    cmd = [loc["python"], os.path.join(TOOLS, "run_bind.py"), eid, glb]
    rc = run_logged(cmd, os.path.join(loc["logs_dir"], f"ab_bind_{eid}.txt"), cfg["bind"]["timeout_s"] + cfg["bind"]["export_timeout_s"] + 60, env={"BIND_FORCE_DONOR": donor})
    hit = fresh_receipt(eid, "bind", t0)
    if not hit:
        return {"measured": False, "skipped": f"bind produced no receipt (exit {rc})"}, ""
    return ab_core.bind_numbers(hit[1], hit[0]), ""


def do_qc(cfg, eid, glb, png, force):
    ab, loc = cfg["ab"], cfg["local"]
    if not ab["qc"]["enabled"]:
        return {"available": False, "reason": "disabled in config ab.qc"}
    sha = ab_core.sha256_file(glb)
    if not force:
        hit = ab_core.newest(REPO, eid, "qc", ok_only=True)
        if hit and sha in [i.get("sha256") for i in hit[1].get("inputs", [])]:
            return qc_numbers(hit)
    t0 = time.time()
    run_logged([loc["python"], os.path.join(TOOLS, "run_qc.py"), eid, glb, "--concept", png], os.path.join(loc["logs_dir"], f"ab_qc_{eid}.txt"), ab["qc"]["timeout_s"])
    hit = fresh_receipt(eid, "qc", t0)
    return qc_numbers(hit) if hit else {"available": False, "reason": "run_qc.py wrote no receipt"}


def qc_numbers(hit):
    f, r = hit
    p = r.get("params") or {}
    m = p.get("metrics") or {}
    return {"available": True, "receipt": f, "overall": p.get("overall"), "failed": p.get("failed", []), "colour_flag": p.get("colour_flag"), "texture": m.get("texture"), "geometry": m.get("geometry")}


def do_render(cfg, label, glb, out_dir, seed, force):
    """Matched turntable of one mesh in <out_dir>/<label>/ (the folder name is the blind label). Skipped, with the reason, when disabled or the script is absent."""
    ab, loc = cfg["ab"], cfg["local"]
    rc_ = ab["render"]
    script = os.path.join(REPO, rc_["script"])
    if not rc_.get("enabled", True):
        return {"status": "skipped", "reason": "disabled in config ab.render"}
    if not os.path.isfile(script):
        return {"status": "skipped", "reason": f"turntable script not present: {script}"}
    if not os.path.exists(loc["blender_exe"]):
        return {"status": "skipped", "reason": f"blender not found: {loc['blender_exe']}"}
    d = os.path.join(out_dir, label)
    os.makedirs(d, exist_ok=True)
    matched = ab_core.matched_render_config(ab)
    rcfg = {**matched, "glb": glb, "out_dir": d, "label": label, "seed": seed, "config_hash": ab_core.config_hash(matched), "glb_sha256": ab_core.sha256_file(glb)}
    manifest = os.path.join(d, "turntable.json")
    if not force and os.path.exists(manifest):
        try:
            m = ab_core.read_json(manifest)
            if m.get("glb_sha256") == rcfg["glb_sha256"] and m.get("config_hash") == rcfg["config_hash"] and m.get("status") == "ok":
                return m
        except Exception:
            pass
    cfg_path = os.path.join(d, "render_config.json")
    with open(cfg_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rcfg, f, indent=2)
    rc = run_logged([loc["blender_exe"], "--background", "--python", script, "--", "--config", cfg_path], os.path.join(loc["logs_dir"], f"ab_render_{label}.txt"), rc_["timeout_s"])
    if rc != 0 or not os.path.exists(manifest):
        return {"status": "fail", "reason": f"blender exit {rc}, no manifest"}
    return ab_core.read_json(manifest)


def collect(cfg, a, asset_id, png, png_sha, seed, engines):
    loc, ab = cfg["local"], cfg["ab"]
    why = ab_core.alive_reason(own=ab_core.own_pids, check_stage=False, check_lock=False)   # CPU Blender and numpy only: the RAM gate, not the GPU rules
    if why:
        print(why)
        return 3
    out_dir = os.path.join(loc["outputs_dir"], ab["out_subdir"], asset_id)
    os.makedirs(out_dir, exist_ok=True)
    sc_path = os.path.join(out_dir, "scorecard.json")
    old = ab_core.read_json(sc_path) if os.path.exists(sc_path) else None
    results, missing = {}, []
    for e in engines:
        override = getattr(a, f"{e}_receipt")
        if not override and ab_core.latest_failed_half(REPO, asset_id, e):
            missing.append(f"the newest {e} half failed")   # an older ok result is stale: the failed run is the one that counts
            continue
        r, why = ab_core.find_engine_result(REPO, asset_id, e, ab, override)
        if r:
            results[e] = r
        else:
            missing.append(why)
    if missing:
        failures = {}
        for e in engines:
            if e not in results:
                hit = ab_core.latest_failed_half(REPO, asset_id, e)
                if hit:
                    failures[e] = {"receipt": hit[0], "error": hit[1].get("error", ""), "log_tail": (hit[1].get("params") or {}).get("log_tail", [])}
        if failures:   # a half crashed: record it as status fail in the scorecard, with the traceback tail
            fsc = ab_core.failure_scorecard(asset_id, png, png_sha, seed, failures)
            write_scorecard(fsc, sc_path, os.path.join(out_dir, "scorecard.md"), failed=True)
            cf_common.write_receipt(cfg, asset_id, "ab", tool="run_ab.py", model={"id": "Pixal3D vs TRELLIS A/B collect"}, inputs=[png],
                                    params={"mode": "collect", "engines": engines, "seed": seed, "png_sha256": png_sha, "scorecard": sc_path, "failures": failures},
                                    outputs=[sc_path], status="fail", error="half failed: " + ", ".join(sorted(failures)))
            print("COLLECT: half failed: " + "; ".join(f"{e}: {f['error']}" for e, f in failures.items()) + f" (scorecard {sc_path})")
            return 6
        print("COLLECT REFUSED: " + "; ".join(missing))
        return 4
    info, problems = ab_core.consistency(results, png_sha, seed, {})
    if problems:
        print("COLLECT REFUSED: the halves are not comparable: " + "; ".join(problems))
        return 4
    key = (old or {}).get("blind_key") if old and old.get("png", {}).get("sha256") == png_sha and old.get("seed") == seed else None
    key = key or ab_core.new_blind_key(tuple(ab["order"]))
    lm = ab_core.label_map(key)
    donor = ab["donor"]
    engines_out, refusal = {}, ""
    # order: bind gate on both, QC, then turntables
    for e in engines:
        r = results[e]
        rec = r["rec"]
        glb = r["glb"]
        bind, why = do_bind(cfg, r["engine_asset"], glb, donor, a.bind, a.force)
        if why:
            refusal = why
        engines_out[e] = {"label": lm.get(e), "pipeline": ab["engines"][e]["pipeline"], "preset": ab["engines"][e]["preset"], "engine_asset": r["engine_asset"], "receipt": r["receipt"],
                          "half_receipt": r["half_receipt"], "glb": glb, "glb_sha256": ab_core.sha256_file(glb), "wall_s": (rec.get("timing") or {}).get("wall_s"),
                          "wall_budget_s": ab["wall_budget_s"], "over_budget": ((rec.get("timing") or {}).get("wall_s") or 0) > ab["wall_budget_s"],
                          "lowest_free_ram_gb": (rec.get("memory") or {}).get("lowest_free_ram_gb"), "memory_note": (rec.get("memory") or {}).get("peak_stage_note"),
                          "stage_tokens": (rec.get("params") or {}).get("stage_tokens"), "bind": bind}
    if refusal:
        print("COLLECT REFUSED: " + refusal)
        return 5
    donors = {e: (engines_out[e]["bind"] or {}).get("donor") for e in engines}
    info["same_donor"] = len({d for d in donors.values() if d}) <= 1 if any(donors.values()) else None
    for e in engines:
        engines_out[e]["qc"] = {"available": False, "reason": "--no-qc"} if a.no_qc else do_qc(cfg, engines_out[e]["engine_asset"], engines_out[e]["glb"], png, a.force)
    for e in engines:
        if a.no_render:
            engines_out[e]["render"] = {"status": "skipped", "reason": "--no-render"}
        else:
            engines_out[e]["render"] = do_render(cfg, lm[e], engines_out[e]["glb"], out_dir, seed, a.force)
        rr = engines_out[e]["render"]
        engines_out[e]["images"] = {"frames": rr.get("frames", []), "face_crop": rr.get("face_crop"), "hair_crop": rr.get("hair_crop")}
    sheet = None
    try:
        import contact_sheet_ab   # tools/ab/contact_sheet_ab.py (rough, second commit); absent = no sheet
        sheet = contact_sheet_ab.make(engines_out, lm, out_dir)
    except ImportError:
        pass
    status = "complete" if len(engines) == 2 else "partial"
    pxrec = (results.get("pixal3d") or {}).get("rec") or {}
    pxhalf = ((results.get("pixal3d") or {}).get("half") or {}).get("params") or {}
    naf = pxhalf.get("naf", (pxrec.get("params") or {}).get("naf", True))
    sc = ab_core.merge_existing(ab_core.new_scorecard(asset_id=asset_id, png=png, png_sha=png_sha, seed=seed, key=key, engines=engines_out, scoring=ab["scoring"],
                                                      consistency_info=info, status=status), old)
    sc["contact_sheet"] = sheet
    sc.update(naf=bool(naf), rough=not naf, quality_label="" if naf else "ROUGH / NAF-off")
    if status == "complete":
        ab_core.apply_to_scorecard(sc, ab["bind"]["tolerance"])   # decision stays NO-WIN "scores incomplete" until a human fills blind_scores and runs --decide
    md_path = os.path.join(out_dir, "scorecard.md")
    write_scorecard(sc, sc_path, md_path)
    cf_common.write_receipt(cfg, asset_id, "ab", tool="run_ab.py", model={"id": "Pixal3D vs TRELLIS A/B collect"}, inputs=[png],
                            params={"mode": "collect", "engines": engines, "seed": seed, "png_sha256": png_sha, "scorecard": sc_path, "contact_sheet": sheet, "status": status,
                                    "consistency": info, "pipelines": {e: engines_out[e]["pipeline"] for e in engines}},
                            timing={}, outputs=[sc_path, md_path] + ([sheet] if sheet else []), status="ok", error="")
    print(f"ab collect {status}: {sc_path}")
    return 0


def write_scorecard(sc, sc_path, md_path, failed=False):
    with open(sc_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(sc, f, indent=2)
    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(ab_core.failure_markdown(sc) if failed else ab_core.render_markdown(sc))


def cmd_decide(path, tol=None):
    sc = ab_core.read_json(path)
    d = ab_core.apply_to_scorecard(sc, tol if tol is not None else 0.0)
    write_scorecard(sc, path, os.path.join(os.path.dirname(path), "scorecard.md"))
    print(ab_core.decision_line(d))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("asset_id", nargs="?")
    ap.add_argument("image", nargs="?")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--engines", choices=("both", "trellis", "pixal3d"), default="both")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--decide", default=None)
    ap.add_argument("--bind", choices=("auto", "yes", "no"), default="auto")
    ap.add_argument("--no-qc", action="store_true")
    ap.add_argument("--naf", choices=("on", "off"), default="on")
    ap.add_argument("--cutout-receipt", dest="cutout_receipt", default=None)
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--trellis-receipt", dest="trellis_receipt", default=None)
    ap.add_argument("--pixal3d-receipt", dest="pixal3d_receipt", default=None)
    a = ap.parse_args(argv)
    cfg = cf_common.load_config()
    if a.decide:
        return cmd_decide(a.decide, cfg["ab"]["bind"]["tolerance"])
    if not a.asset_id or not a.image:
        ap.error("asset_id and image are required")
    png = os.path.abspath(a.image)
    if not os.path.isfile(png):
        sys.exit(f"image not found: {png}")
    why = ab_core.rgba_reason(png)
    if why:
        print(why)
        return 2
    seed = a.seed if a.seed is not None else cfg["defaults"]["seed"]
    png_sha = ab_core.sha256_file(png)
    engines = list(cfg["ab"]["order"]) if a.engines == "both" else [a.engines]
    if a.collect:
        return collect(cfg, a, a.asset_id, png, png_sha, seed, engines)
    for e in engines:   # TRELLIS first
        rc = run_half(cfg, e, a.asset_id, png, png_sha, seed, naf=(a.naf == "on"), cutout_receipt=a.cutout_receipt)
        if rc:
            return rc
    return collect(cfg, a, a.asset_id, png, png_sha, seed, engines) if a.engines == "both" else 0


if __name__ == "__main__":
    sys.exit(main())
