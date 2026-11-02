"""Run a whole character through the factory stages, unattended and resumable.
Usage: run_character.py <character_spec.json> [--from STAGE] [--only STAGE] [--dry-run]
Spec (characters/*.json): character_id, label, group, donor, optional preset, clips {Idle,Walk,Attack,Roll}, optional textures (bool),
parts [{name, kind body|gear, kit (kit json), row_id (row of its Flux sheet), optional repair {"mirror": CX}, optional preset}]. Exactly one body part.
Stages (config "character.stages"): flux, trellis, bind, stills_body, gear, assemble, stills_dressed, textures.
Each unit calls the existing tool as a subprocess. GPU units (flux, trellis) run under the gpu_guard.ps1 convention of bench/run_kit.ps1
(guard started hidden, killed when the job ends) and are followed by the configured pause. A unit is skipped when its marker (status ok)
and its output files both exist. After every unit outputs_dir/3d/<character_id>/stage_times.json is rewritten, plus a human table.
A failed unit stops the run, exit code 1. All paths and numbers come from config/config.json ("character") and config.local.json.
Note: run_stills.py takes its clip ids from config ("stills.clips"); the spec clips go to the library row and are checked to exist."""
import argparse, datetime, json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common
import binder_route

BOUND = "{BOUND}"   # placeholder in a unit command, replaced by the bound body GLB the bind stage actually produced (binder_route.resolve_bound)
TOOLS = os.path.dirname(os.path.abspath(__file__))


def read_json(p):
    with open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2)


def validate_spec(spec, cfg):
    errs = []
    for k in ("character_id", "parts", "clips"):
        if k not in spec:
            errs.append(f"spec missing '{k}'")
    parts = spec.get("parts", [])
    if sum(1 for p in parts if p.get("kind") == "body") != 1:
        errs.append("spec needs exactly one part with kind 'body'")
    names = [p.get("name") for p in parts]
    if len(set(names)) != len(names):
        errs.append("part names must be unique")
    for p in parts:
        for k in ("name", "kind", "kit", "row_id"):
            if k not in p:
                errs.append(f"part {p.get('name', '?')} missing '{k}'")
        if p.get("kind") not in ("body", "gear"):
            errs.append(f"part {p.get('name')}: kind must be body or gear")
    return errs


class Plan:
    """Builds the ordered list of units from the spec; reads kit files only."""

    def __init__(self, spec, cfg):
        self.spec, self.cfg = spec, cfg
        self.loc, self.ch = cfg["local"], cfg["character"]
        self.cid = spec["character_id"]
        self.out = self.loc["outputs_dir"]
        self.work = os.path.join(self.out, "3d", self.cid)
        self.py = self.loc["python"]
        self.parts = spec["parts"]
        self.body = next(p for p in self.parts if p["kind"] == "body")
        self.gear = [p for p in self.parts if p["kind"] == "gear"]
        self.binder = binder_route.binder(cfg)
        self.assembled = os.path.join(self.work, cfg["assemble"]["output_name"].format(asset_id=self.cid))
        self.body_stills_id = self.cid + self.ch["stills_body_suffix"]
        self.dressed_id = self.cid + self.ch["stills_dressed_suffix"]

    @property
    def bound(self):
        return binder_route.resolve_bound(self.cfg, self.work)

    def part_id(self, p):
        return f"{self.cid}-{p['name']}"

    def mesh(self, p):
        return os.path.join(self.out, "3d", self.part_id(p), "mesh.glb")

    def sheet(self, p):
        kit = read_json(os.path.join(cf_common.REPO, p["kit"]))
        row = next((r for r in kit["rows"] if r.get("row_id", r["name"]) == p["row_id"]), None)
        if row is None:
            raise SystemExit(f"part {p['name']}: row_id {p['row_id']} not in {p['kit']}")
        w, h = row.get("w", kit.get("w", self.cfg["defaults"]["w"])), row.get("h", kit.get("h", self.cfg["defaults"]["h"]))
        return os.path.join(self.out, kit["kit"], f"{row['name']}_{w}x{h}.png")

    def stills_pngs(self, asset_id):
        st = self.cfg["stills"]
        d = os.path.join(self.out, "3d", asset_id, st["subdir"])
        return [os.path.join(d, n + ".png") for n in ["head"] + list(st["clips"]) + ["thumb"]]

    def units(self):
        s, ch, py, cfg = self.spec, self.ch, self.py, self.cfg
        U = []

        def unit(stage, part, cmd, outputs, timeout, gpu=None, env=None, fallback=None):
            U.append(dict(stage=stage, part=part, cmd=cmd, outputs=outputs, timeout=timeout, gpu=gpu, env=env or {}, fallback=fallback))
        for p in self.parts:
            img = self.sheet(p)
            unit("flux", p["name"], [py, "-u", os.path.join(cf_common.REPO, "flux", "flux_kit.py"), os.path.join(cf_common.REPO, p["kit"]), p["row_id"]],
                 [img], ch["flux_timeout_s"], gpu="flux")
            mirror = p.get("repair", {}).get("mirror")
            fix = [py, os.path.join(TOOLS, "fix_black_block.py"), img] + (["--mirror", str(mirror)] if mirror is not None else [])
            unit("flux", p["name"] + "-fixblack", fix, [img], ch["cpu_timeout_s"])
        for p in self.parts:
            preset = p.get("preset", s.get("preset", ch["default_preset"]))
            unit("trellis", p["name"], [py, os.path.join(TOOLS, "run_3d.py"), self.part_id(p), self.sheet(p), "--preset", preset],
                 [self.mesh(p)], ch["trellis_timeout_s"], gpu="trellis")
        donor_unit = dict(cmd=[py, os.path.join(TOOLS, "run_bind.py"), self.cid, self.mesh(self.body)], outputs=[os.path.join(self.work, binder_route.bound_name(cfg, "donor"))],
                          timeout=cfg["bind"]["timeout_s"] + cfg["bind"]["export_timeout_s"] + 60, env={"BIND_FORCE_DONOR": s.get("donor", ch["default_donor"])}, binder="donor")
        if self.binder == "donor":
            unit("bind", self.body["name"], donor_unit["cmd"], donor_unit["outputs"], donor_unit["timeout"], env=donor_unit["env"])
        else:   # SkinTokens first (a GPU job); the donor bind only as the labelled FALLBACK, when bind.fallback_to_donor allows it
            st = cfg["skin_tokens"]
            unit("bind", self.body["name"], [py, os.path.join(TOOLS, "run_skin_tokens.py"), self.cid, self.mesh(self.body)],
                 [os.path.join(self.work, binder_route.bound_name(cfg, "skin_tokens"))],
                 st["prep_timeout_s"] + st["run_timeout_s"] + st["export_timeout_s"] + st["metrics_timeout_s"] + 60, gpu="skin_tokens",
                 fallback=donor_unit if binder_route.fallback_allowed(cfg) else None)
        unit("stills_body", self.body["name"], [py, os.path.join(TOOLS, "run_stills.py"), self.body_stills_id, BOUND],
             self.stills_pngs(self.body_stills_id), cfg["stills"]["timeout_s"] + 60)
        for p in self.gear:
            unit("gear", p["name"], [py, os.path.join(TOOLS, "run_gear.py"), self.cid, p["name"], self.mesh(p), BOUND],
                 [os.path.join(self.work, f"{cfg['gear']['glb_prefix']}{p['name']}.glb")], cfg["gear"]["timeout_s"] + 60)
        unit("assemble", "all", [py, os.path.join(TOOLS, "assemble_character.py"), self.cid], [self.assembled], cfg["assemble"]["timeout_s"] + 60)
        unit("stills_dressed", "all", [py, os.path.join(TOOLS, "run_stills.py"), self.dressed_id, self.assembled],
             self.stills_pngs(self.dressed_id), cfg["stills"]["timeout_s"] + 60)
        if s.get("textures"):
            for p in self.parts:
                unit("textures", p["name"], [py, os.path.join(TOOLS, "run_textures.py"), self.part_id(p), self.mesh(p)], [self.mesh(p)], cfg["textures"]["timeout_s"] + 60)
        return U


class Runner:
    def __init__(self, plan):
        self.plan, self.cfg = plan, plan.cfg
        self.ch, self.loc = plan.ch, plan.loc
        self.times_path = os.path.join(plan.work, self.ch["stage_times_name"])
        self.mdir = os.path.join(plan.work, self.ch["markers_subdir"])
        self.times = read_json(self.times_path) if os.path.exists(self.times_path) else []

    def marker(self, u):
        return os.path.join(self.mdir, f"{u['stage']}-{u['part']}.ok.json")

    def done(self, u):
        m = self.marker(u)
        if not os.path.exists(m):
            return False
        row = read_json(m)
        if u["stage"] == "bind":   # a bind done with another binder than the configured one is not done; a fallback leaves the donor GLB, so check what the marker says it wrote
            if row.get("binder_config", "donor") != self.plan.binder:
                return False
            return row.get("status") == "ok" and all(os.path.exists(o) for o in row.get("outputs") or u["outputs"])
        return row.get("status") == "ok" and all(os.path.exists(o) for o in u["outputs"])

    def prior_wall(self, u):
        return read_json(self.marker(u)).get("wall_s", 0)

    def record(self, u, wall_s, status):
        keep = wall_s
        self.times = [t for t in self.times if not (t["stage"] == u["stage"] and t["part"] == u["part"])]
        self.times.append({"stage": u["stage"], "part": u["part"], "wall_s": keep, "status": status})
        order = {s: i for i, s in enumerate(self.ch["stages"])}
        self.times.sort(key=lambda t: order.get(t["stage"], 99))
        write_json(self.times_path, self.times)
        with open(os.path.join(self.plan.work, self.ch["summary_name"]), "w", encoding="utf-8") as f:
            f.write(self.table())

    def table(self):
        rows = [f"{'stage':<16}{'part':<18}{'wall_s':>9}  status"]
        rows += [f"{t['stage']:<16}{t['part']:<18}{t['wall_s']:>9}  {t['status']}" for t in self.times]
        total = round(sum(t["wall_s"] for t in self.times if t["status"] in ("ok", "skipped", "fail")), 1)
        rows.append(f"{'TOTAL':<16}{'':<18}{total:>9}")
        return "\n".join(rows) + "\n"

    def run_unit(self, u):
        """Run a unit; a bind unit that has a donor fallback runs it when SkinTokens' prerequisites are missing or the SkinTokens bind failed. Returns (ok, rc, wall, log);
        self.used = {'binder_used', 'reason', 'outputs'} for the bind unit."""
        self.used = {"binder_used": None, "reason": "", "outputs": u["outputs"]}
        fb = u.get("fallback")
        if fb:
            missing = binder_route.skin_tokens_missing(self.cfg, cf_common.REPO)
            if missing:
                reason = "SkinTokens prerequisites missing: " + "; ".join(missing)
                print(f"[fall] bind: {reason}", flush=True)
                return self._fallback(u, fb, reason, 0.0)
        ok, rc, wall, log = self._run(u)
        if u["stage"] == "bind":
            self.used["binder_used"] = self.plan.binder   # the primary run is the configured binder
        if not ok and fb:
            reason = f"SkinTokens bind failed (exit {rc}, log {log})"
            print(f"[fall] bind: {reason}", flush=True)
            return self._fallback(u, fb, reason, wall)
        return ok, rc, wall, log

    def _fallback(self, u, fb, reason, spent):
        fu = dict(u, cmd=fb["cmd"], outputs=fb["outputs"], timeout=fb["timeout"], gpu=None, env=dict(fb["env"], **{binder_route.REASON_ENV: reason}), fallback=None)
        ok, rc, wall, log = self._run(fu)
        self.used = {"binder_used": "donor", "reason": reason, "outputs": fb["outputs"]}
        return ok, rc, round(wall + spent, 1), log

    def _run(self, u):
        lim = self.cfg["limits"]
        guard = None
        env = dict(os.environ, **u["env"])
        cmd = [c.replace(BOUND, self.plan.bound) for c in u["cmd"]]
        log = os.path.join(self.loc["logs_dir"], f"{self.ch['log_prefix']}{self.plan.cid}_{u['stage']}_{u['part']}.txt")
        if u["gpu"]:
            guard = subprocess.Popen(["powershell", "-NoProfile", "-File", self.loc["guard_script"], "-Match", self.ch["guard_match"][u["gpu"]],
                                      "-MinFreeRamGB", str(lim["guard_min_free_ram_gb"]), "-MaxGpuGB", str(lim["guard_max_gpu_gb"])],
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            time.sleep(self.ch["guard_wait_s"])
        t0 = time.time()
        try:
            with open(log, "w", encoding="utf-8") as lf:
                rc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env, timeout=u["timeout"]).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        except OSError as e:
            rc = f"cannot start: {e}"
        wall = round(time.time() - t0, 1)
        if guard and guard.poll() is None:
            guard.kill()
        ok = rc == 0 and all(os.path.exists(o) for o in u["outputs"])
        if u["gpu"]:
            time.sleep(self.ch["pause_s"])
        return ok, rc, wall, log

    def go(self, units):
        for u in units:
            tag = f"{u['stage']}/{u['part']}"
            if self.done(u):
                print(f"[skip] {tag} (marker ok, outputs present)")
                self.record(u, self.prior_wall(u), "skipped")
                continue
            print(f"[run ] {tag}", flush=True)
            ok, rc, wall, log = self.run_unit(u)
            if not ok:
                self.record(u, wall, "fail")
                print(self.table())
                print(f"FAILED at {tag}: exit {rc}, missing outputs: {[o for o in u['outputs'] if not os.path.exists(o)]}\nlog: {log}\n"
                      "Stopped. Fix it and rerun the same command; finished units are skipped.")
                return 1
            row = {"status": "ok", "stage": u["stage"], "part": u["part"], "wall_s": wall,
                   "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "outputs": u["outputs"]}
            if u["stage"] == "bind":   # which binder made the body, recorded for the later stages and for the label
                row.update(outputs=self.used["outputs"], binder_config=self.plan.binder, binder_used=self.used["binder_used"], fallback=bool(self.used["reason"]),
                           label=binder_route.FALLBACK_LABEL if self.used["reason"] else "", fallback_reason=self.used["reason"])
                binder_route.write_pointer(self.cfg, self.plan.work, self.used["binder_used"], self.used["reason"])
                if self.used["reason"]:
                    print(f"[ ok ] {tag} LABEL: {binder_route.FALLBACK_LABEL} ({self.used['reason']})", flush=True)
            write_json(self.marker(u), row)
            self.record(u, wall, "ok")
            print(f"[ ok ] {tag} {wall} s", flush=True)
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("spec")
    ap.add_argument("--from", dest="from_stage")
    ap.add_argument("--only", dest="only")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = cf_common.load_config()
    spec = read_json(a.spec)
    errs = validate_spec(spec, cfg)
    stages = cfg["character"]["stages"]
    for opt in (a.from_stage, a.only):
        if opt and opt not in stages:
            errs.append(f"unknown stage '{opt}', choose from {stages}")
    if errs:
        sys.exit("spec/arguments invalid:\n  " + "\n  ".join(errs))
    plan = Plan(spec, cfg)
    units = plan.units()
    if a.only:
        units = [u for u in units if u["stage"] == a.only]
    elif a.from_stage:
        units = [u for u in units if stages.index(u["stage"]) >= stages.index(a.from_stage)]
    st, loc = cfg["stills"], cfg["local"]
    missing = [f"clip {k}={v}" for k, v in spec["clips"].items() if not os.path.exists(os.path.join(loc["mixamo_clips_dir"], v + st["clip_ext"]))]
    if a.dry_run:
        print(f"DRY RUN {spec['character_id']}: {len(units)} units, work dir {plan.work}")
        sys.path.insert(0, os.path.join(cf_common.REPO, "plugins", "factory", "lib"))
        os.environ.setdefault("FACTORY_HOME", cf_common.REPO)
        try:   # the chain status must never stop a dry run (a checkout without the plugin lib just skips it)
            import cf_chain
            print("  build-order gates (plugin 0.1.24):")
            for gate, ok, why in cf_chain.status(spec["character_id"]):
                print(f"    [{'x' if ok else ' '}] {gate}" + (f"  <- {why}" if why and not ok else ""))
        except Exception as e:
            print(f"  build-order gates: not available ({e!r})")
        if missing:
            print("  note: missing clip files:", ", ".join(missing))
        runner = Runner(plan)
        for u in units:
            state = "DONE (would skip)" if runner.done(u) else "TODO"
            guard = cfg["character"]["guard_match"][u["gpu"]] if u["gpu"] else "none (CPU)"
            print(f"\n[{u['stage']}/{u['part']}] {state}  timeout {u['timeout']} s  guard: {guard}" + (f"  env {u['env']}" if u["env"] else ""))
            print("  " + subprocess.list2cmdline([c.replace(BOUND, plan.bound) for c in u["cmd"]]))
            print("  outputs: " + "; ".join(u["outputs"]))
            if u.get("fallback"):
                fb = u["fallback"]
                print(f"  binder {plan.binder}; if it fails the gate or its prerequisites are missing: {binder_route.FALLBACK_LABEL}\n  fallback: " + subprocess.list2cmdline(fb["cmd"]) + "  env " + str(fb["env"]))
            elif u["stage"] == "bind":
                print(f"  binder {plan.binder}, no fallback")
        return 0
    if any(u["stage"] == "trellis" for u in units):
        cf_common.plugin_gate("chain", spec["character_id"], False)   # sheet complete + Dom's yes before any part mesh
    if missing:
        sys.exit("cannot start, missing clip files (nothing is downloaded):\n  " + "\n  ".join(missing))
    os.makedirs(plan.work, exist_ok=True)
    runner = Runner(plan)
    rc = runner.go(units)
    if rc == 0:
        lib = os.path.join(plan.work, cfg["character"]["library_row_name"])
        write_json(lib, {"id": spec["character_id"], "label": spec.get("label"), "group": spec.get("group"), "file": plan.assembled, "clips": spec["clips"],
                         "stills": plan.stills_pngs(plan.dressed_id), "body_stills": plan.stills_pngs(plan.body_stills_id)})
        print("\n" + runner.table() + f"All requested stages ok. Library fields: {lib}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
