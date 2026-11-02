"""Cloth stage core (plugin 0.1.18), standard library only, no Blender import: the Blender guard (free RAM, exclusive lock file, Flux load window), the pin rule, the settle
detector, the result gate and the orchestration of one Blender run. tools/run_cloth.py is a thin CLI over run(); tests drive run() with a fake Blender.
Everything numeric comes from config/config.json "cloth"; paths from config.local.json (blender_exe, outputs_dir, logs_dir, work_dir, optional blender_lock)."""
import ctypes, fnmatch, glob, json, os, subprocess, sys, time


# ---- guard --------------------------------------------------------------------------------------------------------------------------------------
def free_ram_gb():
    """Free physical RAM in GB; env CLOTH_FAKE_FREE_RAM_GB overrides it (tests only)."""
    fake = os.environ.get("CLOTH_FAKE_FREE_RAM_GB")
    if fake:
        return float(fake)

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS(); m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 2**30


def pid_alive(pid):
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259   # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def flux_loading(logs_dir, g, now=None):
    """True when the newest Flux queue/kit log was written inside the last flux_window_s AND holds a START or model-load marker: a Flux cell is starting or running
    (about 14 GB at model load). Returns the log path or ''. Conservative on purpose: a log that is still growing counts."""
    now = time.time() if now is None else now
    files = []
    for pat in g["flux_log_globs"]:
        files += glob.glob(os.path.join(logs_dir, pat))
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        return ""
    newest = max(files, key=os.path.getmtime)
    if now - os.path.getmtime(newest) > g["flux_window_s"]:
        return ""
    try:
        with open(newest, "rb") as f:
            text = f.read(200000).decode("utf-8", "replace")
    except OSError:
        return ""
    return newest if any(mk in text for mk in g["flux_markers"]) else ""


def lock_path(loc):
    return loc.get("blender_lock") or os.path.join(loc["work_dir"], "blender_job.lock")


class BlenderLock:
    """Exclusive lock file (O_EXCL). If it exists and its pid is alive, wait up to wait_s; a dead pid's file is stale and removed. Context manager."""
    def __init__(self, path, wait_s, sleep=1.0):
        self.path, self.wait_s, self.sleep, self.held = path, wait_s, sleep, False

    def _owner(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return int(json.load(f).get("pid", 0))
        except Exception:
            return 0

    def acquire(self):
        t0 = time.time()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner = self._owner()
                if not pid_alive(owner):
                    try:
                        os.remove(self.path)   # stale: its owner is gone
                    except OSError:
                        pass
                    continue
                if time.time() - t0 >= self.wait_s:
                    raise TimeoutError(f"blender lock {self.path} held by live pid {owner} for {self.wait_s} s")
                time.sleep(self.sleep)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "tool": "cloth", "t": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
            self.held = True
            return self

    def release(self):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *a):
        self.release()


def wait_for_headroom(loc, g, sleep=5.0):
    """Block until free RAM >= min_free_ram_gb and no Flux load is in progress; give up (raises TimeoutError with the reason) after flux_wait_s."""
    t0 = time.time()
    while True:
        free, fl = free_ram_gb(), flux_loading(loc["logs_dir"], g)
        if free >= g["min_free_ram_gb"] and not fl:
            return free
        if time.time() - t0 > g["flux_wait_s"]:
            raise TimeoutError(f"not starting Blender: free RAM {free:.1f} GB (need {g['min_free_ram_gb']})" + (f", Flux is loading/running ({fl})" if fl else ""))
        time.sleep(sleep)


# ---- pure rules (the Blender script uses the same functions by import) -----------------------------------------------------------------------------
def seg_dist(p, a, b):
    ab = [b[i] - a[i] for i in range(3)]
    ap = [p[i] - a[i] for i in range(3)]
    d = sum(x * x for x in ab)
    t = 0.0 if d == 0 else max(0.0, min(1.0, sum(ap[i] * ab[i] for i in range(3)) / d))
    q = [a[i] + t * ab[i] for i in range(3)]
    return sum((p[i] - q[i]) ** 2 for i in range(3)) ** 0.5


def pin_weight(p, segments, pin):
    """The pin rule. p = (x, y, z) world; segments = [(head, tail)] of the pin bones. Returns 0..1."""
    if not segments:
        return 0.0
    top = max(max(h[2], t[2]) for h, t in segments)
    if p[2] < top - pin["height_band_m"]:
        return 0.0
    d = min(seg_dist(p, h, t) for h, t in segments)
    if d > pin["radius_m"]:
        return 0.0
    inner = pin["radius_m"] * pin["soft_inner"]
    if d <= inner or inner >= pin["radius_m"]:
        return 1.0
    return (pin["radius_m"] - d) / (pin["radius_m"] - inner)


def select_pin_bones(names, pin):
    """Lower-case substring match on bone_patterns, minus exclude_patterns."""
    out = []
    for n in names:
        low = n.lower()
        if any(p in low for p in pin["bone_patterns"]) and not any(x in low for x in pin["exclude_patterns"]):
            out.append(n)
    return out


class SettleDetector:
    """Feed one p99 speed per frame; settled when it stays under the threshold for settle_frames in a row after min_frames."""
    def __init__(self, sim):
        self.s, self.run, self.frame, self.settled_at = sim, 0, 0, None

    def feed(self, p99_speed):
        self.frame += 1
        self.run = self.run + 1 if p99_speed < self.s["settle_p99_speed_mps"] else 0
        if self.settled_at is None and self.frame >= self.s["min_frames"] and self.run >= self.s["settle_frames"]:
            self.settled_at = self.frame
        return self.settled_at is not None


def gate_checks(res, gate):
    """Checks on the Blender result json. Returns {name: bool}."""
    pen, cnt, pin = res.get("penetration", {}), res.get("counts", {}), res.get("pins", {})
    return {
        "exported": bool(res.get("exported")),
        "counts_unchanged": cnt.get("verts_out") is not None and cnt.get("verts_out") == cnt.get("verts_sim") and cnt.get("faces_out") == cnt.get("faces_sim"),
        "uvs_kept": cnt.get("uv_layers_out", 0) >= 1 and cnt.get("uv_layers_out", 0) >= min(1, cnt.get("uv_layers_in", 0)),
        "no_nan": res.get("nan_vertices", 1) == 0,
        "not_exploded": res.get("extent_ratio", 99) <= gate["max_extent_over_input"],
        "pins": pin.get("count", 0) >= gate["min_pins"],
        "penetration": pen.get("max_free_m") is not None and pen["max_free_m"] <= gate["max_penetration_m"],
    }


# ---- orchestration ------------------------------------------------------------------------------------------------------------------------------
def _read_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_preset(cl, name):
    name = name or cl["default_preset"]
    names = [k for k in cl["presets"] if not k.startswith("_")]
    if name not in names:
        raise ValueError(f"unknown cloth preset {name!r}; presets: {', '.join(names)}")
    return name, cl["presets"][name]


def run(cfg, asset_id, part, part_mesh, body_glb, *, preset="", mode="rest", max_frames=0, repo=None, receipt=None, runner=subprocess.run, log=print):
    """One cloth run. Returns (exit_code, receipt_path_or_None). receipt(status, error, outs, timing, mem, extra) writes the receipt; runner is subprocess.run (tests pass a fake)."""
    loc, cl = cfg["local"], cfg["cloth"]
    repo = repo or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if mode not in cl["modes"]:
        return 2, receipt("fail", f"unknown mode {mode!r}", extra={"mode": mode}) if receipt else None
    if mode == "clip":
        msg = ("NotImplemented: per-clip cloth simulation (idle/walk baked as shape keys from the Mixamo clips) is not built yet; only mode=rest exists. "
               "Use rest mode and bind the settled mesh with run_gear.py so it follows the skeleton.")
        log(msg)
        return 2, receipt("fail", msg, extra={"mode": mode}) if receipt else None
    preset_name, preset_vals = resolve_preset(cl, preset)
    work = os.path.join(loc["outputs_dir"], "3d", asset_id)
    cwork = os.path.join(work, cl["work_subdir"])
    os.makedirs(cwork, exist_ok=True)
    out_glb = os.path.join(work, f"{cl['glb_prefix']}{part}.glb")
    script = os.path.join(repo, cl["script"])
    argf, resf = os.path.join(cwork, f"{part}_args.json"), os.path.join(cwork, f"{part}_result.json")
    logf = os.path.join(loc["logs_dir"], f"cloth_{asset_id}_{part}.txt")
    cache = os.path.join(cwork, f"{part}_simcache")
    sim = dict(cl["sim"])
    if max_frames:
        sim["max_frames"] = int(max_frames)
    params = {"mode": mode, "part": part, "preset": preset_name, "settings": preset_vals, "collision": cl["collision"], "pin": cl["pin"], "sim": sim, "prep": cl["prep"],
              "gate": cl["gate"], "blender": loc["blender_exe"], "log": logf}
    problems = [f"blender_exe not found: {loc['blender_exe']}"] if not os.path.exists(loc["blender_exe"]) else []
    problems += [f"{n} not found: {p}" for n, p in (("part mesh", part_mesh), ("bound body", body_glb), ("script", script)) if not os.path.exists(p)]
    if problems:
        return 2, receipt("fail", "; ".join(problems), extra={"params": params})
    with open(argf, "w", encoding="utf-8") as f:
        json.dump({"cloth": {**cl, "sim": sim}, "preset": preset_name, "preset_values": preset_vals, "part": part, "body": body_glb, "part_mesh": part_mesh, "out": out_glb,
                   "result": resf, "cache_dir": cache, "rig_name": cfg["bind"]["rig_name"], "body_name": cfg["bind"]["body_name"], "mode": mode,
                   "abort_free_ram_gb": cfg.get("limits", {}).get("guard_min_free_ram_gb", 0)}, f)
    for p in (resf, out_glb):
        if os.path.exists(p):
            os.remove(p)
    t0 = time.time()
    try:
        with BlenderLock(lock_path(loc), cl["guard"]["lock_wait_s"]):
            free0 = wait_for_headroom(loc, cl["guard"])
            log(f"cloth: lock held, free RAM {free0:.1f} GB, starting Blender")
            with open(logf, "w", encoding="utf-8") as lf:
                try:
                    r = runner([loc["blender_exe"], "--background", "--python", script, "--", argf], stdout=lf, stderr=subprocess.STDOUT, timeout=cl["timeout_s"]).returncode
                except subprocess.TimeoutExpired:
                    r = "timeout"
    except TimeoutError as e:
        return 3, receipt("fail", f"guard: {e}", extra={"params": params})
    res = _read_json(resf)
    timing = {"wall_s": round(time.time() - t0, 1)}
    mem = {"lowest_free_ram_gb": res.get("lowest_free_ram_gb")}
    if not res.get("keep_cache") and os.path.isdir(cache) and not sim.get("keep_cache"):
        import shutil
        shutil.rmtree(cache, ignore_errors=True)
    if r != 0 or not os.path.exists(out_glb):
        return 1, receipt("fail", f"blender exit {r}{': ' + res['error'] if res.get('error') else ''}, see log", timing=timing, mem=mem, extra={"params": params, "result": res})
    checks = gate_checks(res, cl["gate"])
    passed = all(checks.values())
    err = "" if passed else "gate failed: " + ",".join(k for k, v in checks.items() if not v)
    path = receipt("ok" if passed else "fail", err, outs=[out_glb], timing=timing, mem=mem, extra={"params": params, "metrics": {**res, "checks": checks, "gate_passed": passed}})
    s, pen = res.get("sim", {}), res.get("penetration", {})
    log(f"cloth {part} {'ok' if passed else 'FAIL'}: settled={s.get('settled')} frames={s.get('frames')} max_penetration_free_m={pen.get('max_free_m')} -> {out_glb}")
    return (0 if passed else 1), path
