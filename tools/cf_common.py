"""Shared helpers: config loading and the receipt writer (schema v1). A receipt is the definition of done for a stage."""
import ctypes, datetime, hashlib, json, os, socket, subprocess, sys, threading, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def load_config():
    cfg = _read(os.path.join(REPO, "config", "config.json"))
    local = os.path.join(REPO, "config", "config.local.json")
    if not os.path.exists(local):
        raise SystemExit("config/config.local.json is missing: copy config.local.example.json and fill in this machine's paths")
    cfg["local"] = _read(local)
    return cfg


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def slug_id(kit, row_id, prompt, seed):
    short = hashlib.sha1(f"{prompt}|{seed}".encode("utf-8")).hexdigest()[:6]
    return f"{kit}-{row_id}-{short}"


def script_commit():
    try:
        return subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


class FreeRamWatch(threading.Thread):
    """Samples free physical RAM every 2 s and keeps the lowest value (GB)."""
    def __init__(self):
        super().__init__(daemon=True)
        self.lowest = None
        self._stop_flag = False

    def _free_gb(self):
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS(); m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullAvailPhys / 2**30

    def run(self):
        while not self._stop_flag:
            v = self._free_gb()
            self.lowest = v if self.lowest is None else min(self.lowest, v)
            time.sleep(2)

    def stop(self):
        self._stop_flag = True
        return None if self.lowest is None else round(self.lowest, 1)


def write_receipt(cfg, asset_id, stage, *, tool, model, inputs=(), params=None, timing=None, memory=None, outputs=(), status="ok", error=""):
    """inputs/outputs are file paths; they are stored with sha256. Returns the receipt path."""
    now = datetime.datetime.now(datetime.timezone.utc)
    rec = {
        "schema": 1, "asset_id": asset_id, "stage": stage, "tool": tool, "script_commit": script_commit(),
        "model": model,
        "inputs": [{"path": p, "sha256": sha256(p)} for p in inputs if os.path.exists(p)],
        "params": params or {},
        "machine": {"hostname": socket.gethostname(), **cfg["local"].get("machine", {})},
        "timing": timing or {}, "memory": memory or {},
        "outputs": [{"path": p, "sha256": sha256(p)} for p in outputs if os.path.exists(p)],
        "status": status, "error": error, "created_utc": now.isoformat(timespec="seconds"),
    }
    d = os.path.join(REPO, "receipts", asset_id)
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, f"{stage}-{now.strftime('%Y%m%dT%H%M%SZ')}")
    n = 0
    while True:   # two receipts of one asset and stage in the same second used to overwrite each other (CI caught it): b, c, ... sort after the plain name; "x" makes the create atomic across processes
        path = base + (chr(ord("a") + n - 1) if 0 < n < 26 else f"z{n}" if n else "") + ".json"
        try:
            f = open(path, "x", encoding="utf-8", newline="\n")
        except FileExistsError:
            n += 1
            continue
        with f:
            json.dump(rec, f, indent=2)
        return path


def wait_for_free_ram(cfg, what=''):
    """Block until free RAM is at least limits.start_min_free_ram_gb (pre-start gate: heavy jobs only start with headroom). Gives up after start_wait_max_s."""
    lim = cfg['limits']; need = lim.get('start_min_free_ram_gb', 0); w = FreeRamWatch(); t0 = time.time()
    while need and w._free_gb() < need:
        if time.time() - t0 > lim.get('start_wait_max_s', 1800):
            raise SystemExit(f'free RAM stayed under {need} GB for {lim.get("start_wait_max_s", 1800)} s: not starting {what}')
        time.sleep(10)


def plugin_gate(kind, *args):
    """Run a plugin gate inside the script itself, so a run from bench\\*.ps1 or a plain terminal is gated too (the PreToolUse hooks stay the second layer).
    kind 'token' (asset_id, preset, from_latents) or 'publish' (spec dict). A refusal prints its numbers and exits 2; FACTORY_RAW=<reason> in the environment
    logs the override and lets it through; any error in the gate itself allows (fail open, same as the hooks)."""
    try:
        os.environ.setdefault("FACTORY_HOME", REPO)
        lib = os.path.join(REPO, "plugins", "factory", "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import cf_gates, cf_guard
        if kind == "chain":
            import cf_chain
            bad = cf_chain.part_mesh_reasons(*args)
        else:
            bad = [r for r in [cf_gates.token_reason(*args)] if r] if kind == "token" else list(cf_gates.publish_reasons(*args)[0])
    except Exception:
        return
    if not bad:
        return
    raw = os.environ.get("FACTORY_RAW")
    if raw:
        cf_guard.raw_override("FACTORY_RAW=" + raw, "gates")
        print("GATE OVERRIDDEN (FACTORY_RAW, logged): " + " | ".join(bad), flush=True)
        return
    print("\n".join(bad), flush=True)
    sys.exit(2)
