"""Offline tests for the Pixal3D vs TRELLIS A/B harness (plugin 0.1.14; stdlib unittest). No engine, no GPU, no Blender, no model: the stage scripts run_3d, run_pixal3d, run_bind and
run_qc are replaced by fake scripts in a throw-away copy of the repo, and the stage-process / lock / RAM lookups are injected or faked by environment."""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
os.environ["FACTORY_HOME"] = str(REPO)
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(PLUGIN / "mcp"))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tools" / "ab"))
import ab_core  # noqa: E402
import cf_proc  # noqa: E402

PY = sys.executable
MCP = "mcp__plugin_factory_factory__"
CFG = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))
AB = CFG["ab"]
SCORING = AB["scoring"]


def png(path: Path, w: int = 64, h: int = 64, rgba: bool = False) -> str:
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + (b"\x80\x40\x20\xff" if rgba else b"\x80") * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if rgba else 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return str(path)


def fake_glb(path, ext):
    doc = {"asset": {"version": "2.0"}, "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}], "accessors": [{"min": [0.0, 0.0, 0.0], "max": [float(x) for x in ext]}]}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    Path(path).write_bytes(b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js)) + struct.pack("<I4s", len(js), b"JSON") + js)


def hook(name, payload, **env):
    e = dict(os.environ, FACTORY_HOME=str(REPO), FACTORY_TEST_STAGE_TAG="no-such-stage-process", **{k: str(v) for k, v in env.items()})
    r = subprocess.run([PY, str(PLUGIN / "hooks" / name)], input=json.dumps(payload), capture_output=True, text=True, env=e, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def denied(out):
    return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"


FAKE_COMMON = '''
import json, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common
def opt(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None
def glb(path, ext):
    doc = {"asset": {"version": "2.0"}, "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}], "accessors": [{"min": [0.0, 0.0, 0.0], "max": [float(x) for x in ext]}]}
    js = json.dumps(doc).encode(); js += b" " * (-len(js) % 4)
    open(path, "wb").write(b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js)) + struct.pack("<I4s", len(js), b"JSON") + js)
def plan():
    return json.load(open(os.path.join(cf_common.REPO, "fake_plan.json")))
'''
FAKE_ENGINE = '''
from fake_common import *
asset, image, engine = sys.argv[1], sys.argv[2], "%(engine)s"
cfg = cf_common.load_config()
open(os.path.join(cf_common.REPO, "engine_calls.log"), "a").write(engine + " " + " ".join(sys.argv[1:]) + "\\n")
if plan().get("fail", {}).get(engine):
    print("Traceback (most recent call last):\\n  File sparse.py, line 9, in conv\\nRuntimeError: flex_gemm sparse conv failed on ROCm"); sys.exit(1)
d = os.path.join(cfg["local"]["outputs_dir"], "3d", asset); os.makedirs(d, exist_ok=True)
out = os.path.join(d, "%(glb)s"); glb(out, plan()["ext"][engine])
seed = opt("--seed")
cf_common.write_receipt(cfg, asset, "%(stage)s", tool="fake", model={"id": "fake"}, inputs=[image], params={"seed": int(seed) if seed else None, "preset": opt("--preset"), "naf": "--no-naf" not in sys.argv},
                        timing={"wall_s": plan()["wall"][engine]}, memory={"lowest_free_ram_gb": 20.0}, outputs=[out])
'''
FAKE_BIND = '''
from fake_common import *
asset, mesh = sys.argv[1], sys.argv[2]
cfg = cf_common.load_config()
p = plan()["bind"]["pixal3d" if asset.endswith("-pixal3d") else "trellis"]
open(os.path.join(cf_common.REPO, "bind_calls.log"), "a").write(asset + " donor=" + str(os.environ.get("BIND_FORCE_DONOR")) + "\\n")
m = {"gate_passed": p["pass"], "p99_stretch": p["stretch"], "p99_compression_ratio": p["comp"], "bind_best_gate": "GATE_OK" if p["rel"] else "GATE_FAIL",
     "binder": "donor", "donor": os.environ.get("BIND_FORCE_DONOR"), "checks": {"p99_stretch": p["pass"]}}
cf_common.write_receipt(cfg, asset, "bind", tool="fake", model={"id": "fake"}, inputs=[mesh], params={"metrics": m}, timing={"wall_s": 2.0}, status="ok" if p["pass"] else "fail")
sys.exit(0 if p["pass"] else 1)
'''
FAKE_QC = '''
from fake_common import *
asset, glbp = sys.argv[1], sys.argv[2]
cfg = cf_common.load_config()
cf_common.write_receipt(cfg, asset, "qc", tool="fake", model={"id": "none"}, inputs=[glbp], params={"overall": "pass", "failed": [], "metrics": {"texture": {"near_black_pct": 0.5}}})
'''


class Rig:
    """A throw-away copy of the harness with fake stage scripts."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.root = root = t / "repo"
        (root / "tools" / "ab").mkdir(parents=True)
        (root / "config").mkdir()
        shutil.copytree(PLUGIN / "lib", root / "plugins" / "factory" / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        for f in ("cf_common.py", "run_ab.py"):
            shutil.copy(REPO / "tools" / f, root / "tools" / f)
        for f in (REPO / "tools" / "ab").glob("*.py"):
            shutil.copy(f, root / "tools" / "ab" / f.name)
        shutil.copy(REPO / "config" / "config.json", root / "config" / "config.json")
        (root / "config" / "config.local.json").write_text(json.dumps({"python": PY, "outputs_dir": str(t / "out"), "logs_dir": str(t / "logs"), "blender_exe": str(t / "no_blender.exe"),
                                                                       "machine": {"name": "test"}}), encoding="utf-8")
        (t / "logs").mkdir()
        (root / "tools" / "fake_common.py").write_text(FAKE_COMMON, encoding="utf-8")
        (root / "tools" / "run_3d.py").write_text(FAKE_ENGINE % {"engine": "trellis", "glb": "mesh.glb", "stage": "trellis"}, encoding="utf-8")
        (root / "tools" / "run_pixal3d.py").write_text(FAKE_ENGINE % {"engine": "pixal3d", "glb": "pixal3d_standard.glb", "stage": "pixal3d"}, encoding="utf-8")
        (root / "tools" / "run_bind.py").write_text(FAKE_BIND, encoding="utf-8")
        (root / "tools" / "run_qc.py").write_text(FAKE_QC, encoding="utf-8")
        self.png = png(t / "in.png", rgba=True)
        self.lock = t / ".gpu_lock"
        self.plan()

    def plan(self, **kw):
        p = {"ext": {"trellis": [2, 2, 0.3], "pixal3d": [2, 2, 0.3]}, "wall": {"trellis": 500.0, "pixal3d": 700.0},
             "bind": {"trellis": {"pass": True, "stretch": 1.5, "comp": 0.7, "rel": True}, "pixal3d": {"pass": True, "stretch": 1.4, "comp": 0.72, "rel": True}}}
        p.update(kw)
        (self.root / "fake_plan.json").write_text(json.dumps(p), encoding="utf-8")

    def run(self, asset, *args, png_path=None, **env):
        e = dict(os.environ, FACTORY_HOME=str(self.root), FACTORY_GPU_LOCK=str(self.lock), FACTORY_TEST_STAGE_TAG="no-such-stage-process", FACTORY_FAKE_FREE_RAM_GB="64")
        e.update({k: str(v) for k, v in env.items()})
        r = subprocess.run([PY, str(self.root / "tools" / "run_ab.py"), asset, png_path or self.png, *args], capture_output=True, text=True, env=e, timeout=300)
        return r.returncode, r.stdout + r.stderr

    def log(self, name):
        p = self.root / name
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def scorecard(self, asset):
        loc = json.loads((self.root / "config" / "config.local.json").read_text(encoding="utf-8"))
        return json.loads((Path(loc["outputs_dir"]) / "ab" / asset / "scorecard.json").read_text(encoding="utf-8"))

    def receipts(self, asset, stage):
        return sorted((self.root / "receipts" / asset).glob(f"{stage}-*.json")) if (self.root / "receipts" / asset).exists() else []

    def close(self):
        self.tmp.cleanup()


class Refusal(unittest.TestCase):
    """alive = a live stage process not our own, a live GPU lock, or free RAM under the start limit; only a failing lookup fails open."""

    def test_a_live_stage_process_refuses_and_names_it(self):
        why = ab_core.alive_reason(procs=[(4242, "python tools/run_motion.py clip")], lock_text=None, free=64.0, own={1})
        self.assertTrue(why.startswith("REFUSED"))
        self.assertIn("4242", why)
        self.assertIn("Nothing was started", why)

    def test_our_own_children_and_the_guard_watcher_do_not_refuse(self):
        procs = [(10, "python tools/run_ab.py a x.png"), (11, "python tools/run_3d.py a-trellis x.png"), (12, "python generate.py cut.png"),
                 (13, "powershell -File tools/gpu_guard.ps1 -Match generate.py -MinFreeRamGB 3")]
        self.assertEqual(ab_core.alive_reason(procs=procs, lock_text=None, free=64.0, own={10, 11, 12}), "")
        self.assertIn("pid 12", ab_core.alive_reason(procs=procs, lock_text=None, free=64.0, own={10, 11}))   # a stage process that is not ours still refuses

    def test_a_live_lock_refuses_unless_it_is_ours(self):
        holder = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])   # a live pid that is neither this process nor its parent
        self.addCleanup(holder.kill)
        lock = json.dumps({"pid": holder.pid, "job": "flux_kit", "started_utc": "2026-11-01T00:00:00Z"})
        self.assertIn("already running", ab_core.alive_reason(procs=[], lock_text=lock, free=64.0, own={1}))
        self.assertEqual(ab_core.alive_reason(procs=[], lock_text=lock, free=64.0, own={holder.pid}), "")
        self.assertEqual(ab_core.alive_reason(procs=[], lock_text=lock, free=64.0, own=lambda: {holder.pid}), "")   # own may be a callable
        stale = json.dumps({"pid": 2147480000, "job": "old"})
        self.assertEqual(ab_core.alive_reason(procs=[], lock_text=stale, free=64.0, own={1}), "")
        self.assertEqual(ab_core.alive_reason(procs=[], lock_text="not json", free=64.0, own={1}), "")   # unreadable lock: lookup error, fail open

    def test_low_ram_refuses(self):
        why = ab_core.alive_reason(procs=[], lock_text=None, free=2.5, own={1})
        self.assertIn("2.5 GB", why)
        self.assertEqual(ab_core.alive_reason(procs=[], lock_text=None, free=None, own={1}), "")   # RAM unreadable: fail open

    def test_a_failing_lookup_fails_open_but_a_seen_process_never_does(self):
        old = cf_proc.stage_procs

        def boom():
            raise OSError("lookup failed")
        cf_proc.stage_procs = boom
        try:
            self.assertEqual(ab_core.alive_reason(lock_text=None, free=64.0, own={1}), "")
        finally:
            cf_proc.stage_procs = old
        cf_proc.stage_procs = lambda: [(77, "python flux_kit.py kit row")]
        try:
            self.assertIn("pid 77", ab_core.alive_reason(lock_text=None, free=64.0, own={1}))
        finally:
            cf_proc.stage_procs = old

    def test_descendants_finds_a_child(self):
        child = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])
        try:
            self.assertIn(child.pid, cf_proc.descendants(os.getpid()))
        finally:
            child.kill()
            child.wait()

    def test_run_ab_is_a_stage_name(self):
        self.assertIn("run_ab.py", cf_proc.STAGE_SCRIPTS)
        self.assertTrue(cf_proc._STAGE_RE.search("python C:/x/tools/run_ab.py a b.png"))

    def test_the_half_refuses_end_to_end_and_starts_nothing(self):
        rig = Rig()
        try:
            holder = subprocess.Popen([PY, "-c", "import time; time.sleep(120)"])   # a live process that is not run_ab's parent or child
            self.addCleanup(holder.kill)
            rig.lock.write_text(json.dumps({"pid": holder.pid, "job": "flux_kit", "started_utc": "now"}), encoding="utf-8")
            rc, out = rig.run("asset1", "--engines", "trellis")
            self.assertEqual(rc, 3)
            self.assertIn("REFUSED: A factory GPU job is already running", out)
            self.assertEqual(rig.log("engine_calls.log"), [])
            self.assertEqual(rig.receipts("asset1", "ab"), [])
            rig.lock.unlink()
            rc, out = rig.run("asset1", "--engines", "pixal3d", FACTORY_FAKE_FREE_RAM_GB="1.0")
            self.assertEqual(rc, 3)
            self.assertIn("1.0 GB of RAM is free", out)
            self.assertEqual(rig.log("engine_calls.log"), [])
        finally:
            rig.close()


class Pure(unittest.TestCase):
    def test_matched_camera_and_light_config_is_identical_for_both_meshes(self):
        a, b = ab_core.matched_render_config(AB), ab_core.matched_render_config(AB)
        self.assertEqual(a, b)
        self.assertEqual(ab_core.config_hash(a), ab_core.config_hash(b))
        self.assertEqual(set(a["lights"]), {"key", "fill", "rim"})
        self.assertEqual(a["angles_deg"], [0, 45, 90, 135, 180, 270])
        self.assertEqual((a["engine"], a["device"]), ("CYCLES", "CPU"))
        self.assertNotIn("glb", a)   # the mesh and the output folder are the only per-mesh inputs, added by run_ab.do_render
        a["samples"] += 1
        self.assertNotEqual(ab_core.config_hash(a), ab_core.config_hash(b))

    def test_blind_key_is_random_recorded_and_invertible(self):
        k0, k1 = ab_core.new_blind_key(choice=lambda: 0), ab_core.new_blind_key(choice=lambda: 1)
        self.assertEqual(k0, {"A": "trellis", "B": "pixal3d"})
        self.assertEqual(k1, {"A": "pixal3d", "B": "trellis"})
        self.assertEqual(ab_core.label_map(k1), {"pixal3d": "A", "trellis": "B"})
        seen = {tuple(ab_core.new_blind_key().items()) for _ in range(60)}
        self.assertEqual(len(seen), 2)   # both mappings occur

    def scores(self, px, tr, key=None):
        key = key or {"A": "pixal3d", "B": "trellis"}
        crit = SCORING["criteria"]
        return {"A": dict(zip(crit, px)), "B": dict(zip(crit, tr))}, key

    def test_decision_rule_truth_table(self):
        cases = [
            ((8, 8, 5, 5), (7, 7, 5, 5), True, "WIN"),        # +1 on two criteria exactly, bind no worse
            ((8, 8, 5, 5), (7, 7, 5, 5), False, "NO-WIN"),    # bind worse
            ((8, 8, 5, 5), (7, 7, 5, 5), None, "NO-WIN"),     # bind not measured
            ((8, 7.5, 5, 5), (7, 7, 5, 5), True, "NO-WIN"),   # +1 on one criterion only
            ((9, 9, 9, 9), (5, 5, 5, 5), True, "WIN"),
            ((9, 9, 9, 9), (5, 5, 5, 5), False, "NO-WIN"),
            ((6, 6, 6, 6), (7, 7, 7, 7), True, "NO-WIN"),     # TRELLIS better
            ((7.9, 7.9, 5, 5), (7, 7, 5, 5), True, "NO-WIN"), # just under the margin
            ((10, 5, 5, 5), (0, 5, 5, 5), True, "NO-WIN"),    # one huge win is still one criterion
        ]
        for px, tr, bind_ok, want in cases:
            s, key = self.scores(px, tr)
            self.assertEqual(ab_core.decide(s, key, bind_ok, SCORING)["verdict"], want, (px, tr, bind_ok))

    def test_decision_follows_the_blind_key_not_the_label(self):
        s, key = self.scores((8, 8, 5, 5), (7, 7, 5, 5), {"A": "trellis", "B": "pixal3d"})   # A is TRELLIS here, so Pixal3D (B) scored LOWER
        self.assertEqual(ab_core.decide(s, key, True, SCORING)["verdict"], "NO-WIN")
        s, key = self.scores((7, 7, 5, 5), (8, 8, 5, 5), {"A": "trellis", "B": "pixal3d"})
        self.assertEqual(ab_core.decide(s, key, True, SCORING)["verdict"], "WIN")

    def test_incomplete_or_invalid_scores_are_no_win(self):
        s, key = self.scores((8, 8, None, 5), (7, 7, 5, 5))
        d = ab_core.decide(s, key, True, SCORING)
        self.assertEqual(d["verdict"], "NO-WIN")
        self.assertIn("not filled", d["reason"])
        s, key = self.scores((11, 8, 5, 5), (7, 7, 5, 5))
        self.assertIn("outside", ab_core.decide(s, key, True, SCORING)["reason"])
        self.assertEqual(ab_core.decide({}, key, True, SCORING)["verdict"], "NO-WIN")

    def test_bind_no_worse(self):
        good = {"measured": True, "gate_passed": True, "p99_stretch": 1.5, "p99_compression_ratio": 0.7, "bind_best_gate": "GATE_OK"}
        self.assertEqual(ab_core.bind_no_worse(dict(good), dict(good))[0], True)
        self.assertEqual(ab_core.bind_no_worse({**good, "p99_stretch": 1.6}, good)[0], False)
        self.assertEqual(ab_core.bind_no_worse({**good, "p99_compression_ratio": 0.6}, good)[0], False)
        self.assertEqual(ab_core.bind_no_worse({**good, "gate_passed": False}, good)[0], False)
        self.assertEqual(ab_core.bind_no_worse({**good, "bind_best_gate": "GATE_FAIL"}, good)[0], False)
        self.assertEqual(ab_core.bind_no_worse({**good, "p99_stretch": 1.0}, {**good, "gate_passed": False, "bind_best_gate": "GATE_FAIL"})[0], True)   # better than a failing TRELLIS
        self.assertEqual(ab_core.bind_no_worse({"measured": False}, good)[0], None)

    def test_tpose_check_from_the_glb_box(self):
        d = Path(tempfile.mkdtemp())
        fake_glb(d / "t.glb", (1.9, 2.0, 0.3))
        fake_glb(d / "a.glb", (1.0, 2.0, 0.4))
        (d / "junk.glb").write_bytes(b"not a glb")
        self.assertEqual(ab_core.tpose_reason(d / "t.glb", 0.8), "")
        why = ab_core.tpose_reason(d / "a.glb", 0.8)
        self.assertIn("T-pose", why)
        self.assertIn("0.50", why)
        self.assertEqual(ab_core.tpose_reason(d / "junk.glb", 0.8), "")   # cannot tell: not blocked

    def test_scorecard_shape_and_markdown_is_blind(self):
        key = {"A": "pixal3d", "B": "trellis"}
        bind = {"measured": True, "gate_passed": True, "p99_stretch": 1.4, "p99_compression_ratio": 0.7, "bind_best_gate": "GATE_OK"}
        eng = {e: {"label": l, "pipeline": e.upper(), "wall_s": 100.0, "over_budget": False, "lowest_free_ram_gb": 9.0, "bind": dict(bind), "qc": {"overall": "pass"}, "images": {"face_crop": f"{l}/face.png"}}
               for l, e in key.items()}
        sc = ab_core.new_scorecard(asset_id="x", png="p.png", png_sha="ab" * 32, seed=42, key=key, engines=eng, scoring=SCORING, consistency_info={}, status="complete")
        self.assertEqual(set(sc["blind_scores"]), {"A", "B"})
        self.assertEqual(sc["blind_scores"]["A"], {c: None for c in SCORING["criteria"]})
        self.assertEqual(sc["labels"], {"pixal3d": "A", "trellis": "B"})
        d = ab_core.apply_to_scorecard(sc)
        self.assertEqual(d["verdict"], "NO-WIN")
        self.assertIn("not filled", d["reason"])
        md = ab_core.render_markdown(sc)
        for c in SCORING["criteria"]:
            self.assertIn(c, md)
        self.assertNotIn("| pixal3d", md.lower().replace("pixal3d wins", ""))
        self.assertNotIn("trellis |", md.lower())
        old = json.loads(json.dumps(sc))
        old["blind_scores"]["A"]["face"] = 7
        merged = ab_core.merge_existing(ab_core.new_scorecard(asset_id="x", png="p.png", png_sha="ab" * 32, seed=42, key=key, engines=eng, scoring=SCORING, consistency_info={}, status="complete"), old)
        self.assertEqual(merged["blind_scores"]["A"]["face"], 7)   # re-collecting keeps a human's scores


class EndToEnd(unittest.TestCase):
    """run_ab.py with fake engines, bind and QC: halves, either alone, collect from receipts, refusal of mismatched halves."""

    def setUp(self):
        self.rig = Rig()

    def tearDown(self):
        self.rig.close()

    def test_each_half_alone_then_collect(self):
        r = self.rig
        rc, out = r.run("body1", "--engines", "trellis", "--seed", "42")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(r.receipts("body1", "ab")), 1)
        self.assertEqual(len(r.receipts("body1-trellis", "trellis")), 1)
        self.assertEqual(r.receipts("body1-pixal3d", "pixal3d"), [])   # the other half was not run
        rc, out = r.run("body1", "--collect", "--no-render")
        self.assertEqual(rc, 4, out)
        self.assertIn("pixal3d half has not finished", out)
        rc, out = r.run("body1", "--engines", "pixal3d", "--seed", "42")
        self.assertEqual(rc, 0, out)
        calls = r.log("engine_calls.log")
        self.assertEqual([c.split()[0] for c in calls], ["trellis", "pixal3d"])
        self.assertIn("--preset tokens98k_fresh", calls[0])
        self.assertIn("--preset standard", calls[1])
        self.assertTrue(all("--seed 42" in c for c in calls))
        rc, out = r.run("body1", "--collect", "--no-render", "--seed", "42")
        self.assertEqual(rc, 0, out)
        sc = r.scorecard("body1")
        self.assertEqual(sc["status"], "complete")
        self.assertEqual(set(sc["engines"]), {"trellis", "pixal3d"})
        self.assertEqual({e: v["pipeline"] for e, v in sc["engines"].items()}, {"trellis": "TRELLIS", "pixal3d": "PIXAL3D"})
        self.assertEqual({e: v["engine_asset"] for e, v in sc["engines"].items()}, {"trellis": "body1-trellis", "pixal3d": "body1-pixal3d"})
        self.assertEqual(sorted(sc["blind_key"]), ["A", "B"])
        self.assertEqual(sorted(sc["blind_key"].values()), ["pixal3d", "trellis"])
        self.assertEqual(sc["consistency"]["same_png"], True)
        self.assertEqual(sc["consistency"]["same_seed"], True)
        self.assertEqual(sc["consistency"]["same_donor"], True)
        self.assertEqual(sc["engines"]["trellis"]["wall_s"], 500.0)
        self.assertFalse(sc["engines"]["trellis"]["over_budget"])
        self.assertTrue(sc["engines"]["pixal3d"]["over_budget"])   # 700 s > 600 s budget: reported, not hidden
        for e in ("trellis", "pixal3d"):
            b = sc["engines"][e]["bind"]
            self.assertTrue(b["measured"])
            self.assertEqual(b["donor"], AB["donor"])
            self.assertEqual(sc["engines"][e]["qc"]["overall"], "pass")
            self.assertEqual(sc["engines"][e]["render"]["status"], "skipped")
        self.assertEqual(sorted(l.split("donor=")[1] for l in r.log("bind_calls.log")), [AB["donor"], AB["donor"]])   # the same donor for both
        self.assertTrue(all(v is None for row in sc["blind_scores"].values() for v in row.values()))
        self.assertEqual(sc["decision"]["verdict"], "NO-WIN")
        self.assertTrue(sc["decision"]["bind_no_worse"])
        self.assertEqual(r.receipts("body1", "ab")[-1].name.startswith("ab-"), True)
        mds = list((Path(r.root).parent / "out" / "ab" / "body1").glob("scorecard.md"))
        self.assertEqual(len(mds), 1)
        # fill the blind scores and apply the rule through the CLI
        key = sc["blind_key"]
        px_label = [l for l, e in key.items() if e == "pixal3d"][0]
        tr_label = "A" if px_label == "B" else "B"
        sc["blind_scores"][px_label] = {"face": 8, "hair": 8, "colour_match": 6, "bake_faults": 6}
        sc["blind_scores"][tr_label] = {"face": 7, "hair": 7, "colour_match": 6, "bake_faults": 6}
        p = mds[0].parent / "scorecard.json"
        p.write_text(json.dumps(sc), encoding="utf-8")
        out = subprocess.run([PY, str(r.root / "tools" / "run_ab.py"), "--decide", str(p)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, FACTORY_HOME=str(r.root), FACTORY_TEST_STAGE_TAG="x")).stdout
        self.assertTrue(out.startswith("WIN"), out)
        # re-collect keeps the key and the filled scores
        rc, _ = r.run("body1", "--collect", "--no-render", "--seed", "42")
        self.assertEqual(rc, 0)
        again = r.scorecard("body1")
        self.assertEqual(again["blind_key"], key)
        self.assertEqual(again["blind_scores"][px_label]["face"], 8)

    def test_both_runs_trellis_first_then_collects(self):
        rc, out = self.rig.run("body2", "--no-render")
        self.assertEqual(rc, 0, out)
        self.assertEqual([c.split()[0] for c in self.rig.log("engine_calls.log")], ["trellis", "pixal3d"])
        self.assertEqual(self.rig.scorecard("body2")["status"], "complete")
        self.assertEqual(len(self.rig.receipts("body2", "ab")), 3)   # two halves and the collect

    def test_a_worse_pixal3d_bind_fails_the_rule_even_with_better_scores(self):
        r = self.rig
        r.plan(bind={"trellis": {"pass": True, "stretch": 1.5, "comp": 0.7, "rel": True}, "pixal3d": {"pass": False, "stretch": 9.0, "comp": 0.3, "rel": False}})
        rc, out = r.run("body3", "--no-render")
        self.assertEqual(rc, 0, out)
        sc = r.scorecard("body3")
        self.assertEqual(sc["engines"]["pixal3d"]["bind"]["status"], "fail")
        self.assertTrue(sc["engines"]["pixal3d"]["bind"]["measured"])   # a gate fail still carries its numbers
        self.assertFalse(sc["decision"]["bind_no_worse"])

    def test_mismatched_halves_are_refused(self):
        r = self.rig
        self.assertEqual(r.run("body4", "--engines", "trellis", "--seed", "42")[0], 0)
        self.assertEqual(r.run("body4", "--engines", "pixal3d", "--seed", "7")[0], 0)
        rc, out = r.run("body4", "--collect", "--no-render", "--seed", "42")
        self.assertEqual(rc, 4)
        self.assertIn("not comparable", out)
        self.assertIn("seed 7", out)
        other = png(Path(r.tmp.name) / "other.png", 32, 32, rgba=True)
        rc, out = r.run("body4", "--collect", "--no-render", "--seed", "7", png_path=other)
        self.assertEqual(rc, 4)
        self.assertIn("different png", out)

    def test_bind_refuses_a_mesh_that_is_not_a_body_tpose(self):
        r = self.rig
        r.plan(ext={"trellis": [2, 2, 0.3], "pixal3d": [1.0, 2, 0.4]})   # the Pixal3D mesh is A-posed
        self.assertEqual(r.run("body5", "--no-render")[0], 0)
        sc = r.scorecard("body5")
        self.assertFalse(sc["engines"]["pixal3d"]["bind"]["measured"])
        self.assertIn("T-pose", sc["engines"]["pixal3d"]["bind"]["skipped"])
        self.assertEqual(sc["decision"]["bind_no_worse"], None)   # the rule cannot be met without the bind numbers
        rc, out = r.run("body5", "--collect", "--no-render", "--bind", "yes")
        self.assertEqual(rc, 5)
        self.assertIn("bind refused", out)
        rc, _ = r.run("body5", "--collect", "--no-render", "--bind", "no")
        self.assertEqual(rc, 0)

    def test_the_input_must_be_the_cutout_rgba_png(self):
        r = self.rig
        flat = png(Path(r.tmp.name) / "flat.png", 64, 64)   # grayscale, no alpha: a raw render, not the Factory's cut-out
        rc, out = r.run("body6", "--engines", "trellis", png_path=flat)
        self.assertEqual(rc, 2)
        self.assertIn("no alpha channel", out)
        self.assertEqual(r.log("engine_calls.log"), [])
        self.assertEqual(ab_core.rgba_reason(r.png), "")
        self.assertIn("not a png", ab_core.rgba_reason(str(PLUGIN / "hooks" / "hooks.json")))

    def test_both_engines_get_the_same_cutout_trellis_skips_its_own(self):
        r = self.rig
        self.assertEqual(r.run("body7", "--no-render")[0], 0)
        tr, px = r.log("engine_calls.log")
        self.assertIn(f"--cutout-png {r.png}", tr)   # TRELLIS: the supplied cut-out, no second background removal
        self.assertNotIn("--cutout-png", px)          # Pixal3D gets the png as-is (alpha present: no rembg)
        self.assertIn(r.png, px)
        half = json.loads(r.receipts("body7", "ab")[0].read_text(encoding="utf-8"))["params"]
        self.assertEqual(half["cutout_png"], r.png)

    def test_a_pixal3d_crash_is_recorded_with_its_traceback_and_never_retried(self):
        r = self.rig
        r.plan(fail={"pixal3d": True})
        self.assertEqual(r.run("body8", "--engines", "trellis")[0], 0)
        rc, out = r.run("body8", "--engines", "pixal3d")
        self.assertEqual(rc, 1)
        self.assertEqual(len([c for c in r.log("engine_calls.log") if c.startswith("pixal3d")]), 1)   # one attempt, no retry
        half = json.loads(r.receipts("body8", "ab")[-1].read_text(encoding="utf-8"))
        self.assertEqual(half["status"], "fail")
        self.assertTrue(any("Traceback" in l for l in half["params"]["log_tail"]))
        self.assertIn("flex_gemm sparse conv failed on ROCm", half["error"])
        rc, out = r.run("body8", "--collect", "--no-render")
        self.assertEqual(rc, 6, out)
        sc = r.scorecard("body8")
        self.assertEqual(sc["status"], "half_failed")
        self.assertIn("pixal3d", sc["failures"])
        self.assertTrue(any("RuntimeError" in l for l in sc["failures"]["pixal3d"]["log_tail"]))
        self.assertEqual(sc["decision"]["verdict"], "NO-WIN")
        self.assertEqual(len(r.log("engine_calls.log")), 2)   # collect ran no engine
        r.plan()   # the crash is fixed elsewhere; a NEW good run of the half supersedes the failed one
        self.assertEqual(r.run("body8", "--engines", "pixal3d")[0], 0)
        self.assertEqual(r.run("body8", "--collect", "--no-render")[0], 0)

    def test_naf_off_is_rough_and_never_decisive(self):
        r = self.rig
        self.assertEqual(r.run("body9", "--engines", "trellis")[0], 0)
        self.assertEqual(r.run("body9", "--engines", "pixal3d", "--naf", "off")[0], 0)
        self.assertIn("--no-naf", [c for c in r.log("engine_calls.log") if c.startswith("pixal3d")][0])
        half = json.loads(r.receipts("body9", "ab")[-1].read_text(encoding="utf-8"))["params"]
        self.assertEqual((half["naf"], half["quality_label"]), (False, "ROUGH / NAF-off"))
        self.assertEqual(r.run("body9", "--collect", "--no-render")[0], 0)
        sc = r.scorecard("body9")
        self.assertEqual((sc["naf"], sc["rough"], sc["quality_label"]), (False, True, "ROUGH / NAF-off"))
        self.assertEqual(sc["decision"]["verdict"], "ROUGH")
        md = (Path(r.tmp.name) / "out" / "ab" / "body9" / "scorecard.md").read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# ROUGH / NAF-off"))
        crit = SCORING["criteria"]
        key = sc["blind_key"]
        px_label = [l for l, e in key.items() if e == "pixal3d"][0]
        sc["blind_scores"] = {px_label: dict.fromkeys(crit, 9), ("A" if px_label == "B" else "B"): dict.fromkeys(crit, 3)}   # would be a clear WIN with NAF on
        p = Path(r.tmp.name) / "out" / "ab" / "body9" / "scorecard.json"
        p.write_text(json.dumps(sc), encoding="utf-8")
        out = subprocess.run([PY, str(r.root / "tools" / "run_ab.py"), "--decide", str(p)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, FACTORY_HOME=str(r.root), FACTORY_TEST_STAGE_TAG="x")).stdout
        self.assertEqual(out.strip(), "ROUGH: not decisive, rerun with NAF on")
        self.assertNotIn("WIN", out)
        d = ab_core.decide(sc["blind_scores"], key, True, SCORING, rough=True)
        self.assertEqual(d["verdict"], "ROUGH")

    def test_naf_on_is_the_default_and_adds_no_flag(self):
        r = self.rig
        self.assertEqual(r.run("body10", "--no-render")[0], 0)
        self.assertNotIn("--no-naf", " ".join(r.log("engine_calls.log")))
        self.assertTrue(r.scorecard("body10")["naf"])
        self.assertFalse(r.scorecard("body10")["rough"])


class Render(unittest.TestCase):
    """The Blender turntable script is UNRUN: only its source and the keys it reads are checked here."""

    def test_script_reads_only_keys_the_config_has(self):
        import ast
        src = (REPO / "tools" / "ab" / "turntable_blender.py").read_text(encoding="utf-8")
        ast.parse(src)
        r = ab_core.matched_render_config(AB)
        for k in ("engine", "device", "samples", "resolution", "angles_deg", "world", "lights", "camera", "face_crop"):
            self.assertIn(k, r)
        for k in ("lens_mm", "frame_margin", "height_frac"):
            self.assertIn(k, r["camera"])
        for k in ("lens_mm", "frame_margin", "height_frac"):
            self.assertIn(k, r["face_crop"])
        for name in ("key", "fill", "rim"):
            self.assertEqual(set(r["lights"][name]), {"energy", "location", "size"})
        for key in ('cfg["glb"]', 'cfg["out_dir"]', 'cfg["config_hash"]', 'cfg["glb_sha256"]', 'cfg["label"]'):
            self.assertIn(key, src)
        self.assertIn("turntable.json", src)

    def test_contact_sheet_has_the_two_blind_columns(self):
        import contact_sheet_ab
        d = Path(tempfile.mkdtemp())
        imgs = {l: [png(d / f"{l}{i}.png", 40, 60) for i in range(2)] for l in ("A", "B")}
        eng = {"pixal3d": {"images": {"frames": imgs["A"], "face_crop": imgs["A"][0], "hair_crop": None}}, "trellis": {"images": {"frames": imgs["B"], "face_crop": imgs["B"][0], "hair_crop": None}}}
        out = contact_sheet_ab.make(eng, {"pixal3d": "A", "trellis": "B"}, str(d))
        self.assertTrue(out and Path(out).exists() and Path(out).name.startswith("contact_sheet"))
        self.assertIsNone(contact_sheet_ab.make({"pixal3d": {"images": {}}}, {"pixal3d": "A"}, str(d)))


class Wiring(unittest.TestCase):
    def test_tool_is_registered_with_the_requested_arguments(self):
        import server
        fn, desc, props, required, gpu, heavy = server.TOOLS["ab_shape"]
        self.assertEqual(set(props), {"png", "asset_id", "seed", "engines", "collect", "bind", "naf"})
        self.assertEqual(required, ["png", "asset_id"])
        self.assertTrue(gpu and heavy)
        self.assertEqual(props["engines"]["enum"], ["both", "trellis", "pixal3d"])

    def test_config_presets_are_the_ones_the_decision_rule_names(self):
        self.assertEqual(AB["engines"]["trellis"]["preset"], "tokens98k_fresh")
        self.assertEqual(AB["engines"]["pixal3d"]["preset"], "standard")
        self.assertEqual(AB["order"], ["trellis", "pixal3d"])
        self.assertIn("98304", CFG["trellis"]["presets"]["tokens98k_fresh"]["extra"])
        self.assertIn(AB["engines"]["pixal3d"]["preset"], CFG["pixal3d"]["presets"])
        self.assertEqual((SCORING["win_margin"], SCORING["win_criteria_needed"]), (1.0, 2))
        self.assertEqual(AB["engines"]["trellis"]["asset_suffix"] != AB["engines"]["pixal3d"]["asset_suffix"], True)

    def test_run_3d_cutout_png_and_pixal3d_no_naf_are_in_the_scripts(self):
        r3 = (REPO / "tools" / "run_3d.py").read_text(encoding="utf-8")
        self.assertIn('--cutout-png', r3)
        self.assertIn('shutil.copyfile(a.cutout_png, cut)', r3)
        self.assertIn('"cutout_receipt": a.cutout_receipt', r3)
        px = (REPO / "tools" / "run_pixal3d.py").read_text(encoding="utf-8")
        self.assertIn('--no-naf', px)
        self.assertIn('"quality_label"', px)
        self.assertIn("ROUGH / NAF-off", px)
        self.assertIn("--no-naf", (REPO / "tools" / "pixal3d_entry.py").read_text(encoding="utf-8"))   # the natten-shim branch adds the entry flag

    def test_run_3d_passes_the_seed_only_when_given(self):
        src = (REPO / "tools" / "run_3d.py").read_text(encoding="utf-8")
        self.assertIn('--seed", type=int, default=None', src)
        self.assertIn('cmd += ["--seed", str(a.seed)]', src)

    def test_tool_refusals(self):
        import server
        import cf_guard
        with open(REPO / "config" / "config.local.example.json", encoding="utf-8-sig") as f2:
            conf = {**CFG, "local": json.load(f2)}
        old_cfg, old_lock, old_procs = server.cfg, cf_guard.LOCK, cf_proc.stage_procs
        server.cfg = lambda: conf
        d = Path(tempfile.mkdtemp())
        good = png(d / "big.png", 1100, 1500, rgba=True)
        os.environ["FACTORY_FAKE_FREE_RAM_GB"] = "64"
        try:
            self.assertIn("engines must be", server.t_ab_shape(good, "a", engines="nope")["error"])
            self.assertIn("not found", server.t_ab_shape(str(d / "missing.png"), "a")["error"])
            cf_guard.LOCK = d / ".gpu_lock"
            cf_guard.LOCK.write_text(json.dumps({"pid": os.getpid(), "job": "flux_kit", "started_utc": "now"}), encoding="utf-8")
            res = server.t_ab_shape(good, "a", engines="pixal3d")
            self.assertEqual(res["status"], "refused")
            self.assertIn("already running", res["error"])
            self.assertEqual(server.t_ab_shape(good, "a", engines="trellis", collect=True, bind="maybe")["status"], "refused")
            self.assertIn("naf must be", server.t_ab_shape(good, "a", naf="maybe")["error"])
            self.assertIn("no alpha channel", server.t_ab_shape(png(d / "flat.png", 1100, 1500), "a")["error"])
            cf_guard.LOCK.unlink()
            cf_proc.stage_procs = lambda: [(4242, "python flux_kit.py kits/sheet.json row")]
            res = server.t_ab_shape(good, "a", engines="both")
            self.assertEqual(res["status"], "refused")
            self.assertIn("4242", res["error"])
            self.assertIn("REFUSED", res["error"])
        finally:
            server.cfg, cf_guard.LOCK, cf_proc.stage_procs = old_cfg, old_lock, old_procs
            os.environ.pop("FACTORY_FAKE_FREE_RAM_GB", None)

    def test_hooks_cover_the_new_tool_and_script(self):
        text = (PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8")
        self.assertRegex(text, r"ab_shape\"[^]]*gpu_lock_guard\.py[^]]*ram_gate\.py[^]]*input_gate\.py")
        self.assertIn("Bash(*run_ab*)", text)
        self.assertIn("PowerShell(*run_ab*)", text)
        d = Path(tempfile.mkdtemp())
        lock = d / ".gpu_lock"
        lock.write_text(json.dumps({"pid": os.getpid(), "job": "flux_kit", "started_utc": "now"}), encoding="utf-8")
        self.assertTrue(denied(hook("gpu_lock_guard.py", {"tool_name": "Bash", "tool_input": {"command": "python tools/run_ab.py a x.png --engines trellis"}}, FACTORY_GPU_LOCK=lock)))
        self.assertIsNone(hook("gpu_lock_guard.py", {"tool_name": "Bash", "tool_input": {"command": "python tools/run_ab.py a x.png --collect"}}, FACTORY_GPU_LOCK=lock))
        self.assertTrue(denied(hook("gpu_lock_guard.py", {"tool_name": MCP + "ab_shape", "tool_input": {"png": "x.png", "asset_id": "a", "engines": "trellis"}}, FACTORY_GPU_LOCK=lock)))
        self.assertIsNone(hook("gpu_lock_guard.py", {"tool_name": MCP + "ab_shape", "tool_input": {"png": "x.png", "asset_id": "a", "collect": True}}, FACTORY_GPU_LOCK=lock))
        self.assertTrue(denied(hook("ram_gate.py", {"tool_name": MCP + "ab_shape", "tool_input": {"png": "x.png", "asset_id": "a"}}, FACTORY_FAKE_FREE_RAM_GB=1.0)))
        self.assertTrue(denied(hook("ram_gate.py", {"tool_name": "Bash", "tool_input": {"command": "python tools/run_ab.py a x.png --collect"}}, FACTORY_FAKE_FREE_RAM_GB=1.0)))
        self.assertTrue(denied(hook("plugin_guard.py", {"tool_name": "Bash", "tool_input": {"command": "python tools/run_ab.py a x.png --engines pixal3d"}})))
        self.assertIsNone(hook("plugin_guard.py", {"tool_name": "Bash", "tool_input": {"command": "python tools/run_ab.py --decide out/scorecard.json"}}))
        small = png(d / "small.png", 512, 512)
        self.assertTrue(denied(hook("input_gate.py", {"tool_name": MCP + "ab_shape", "tool_input": {"png": small, "asset_id": "a"}})))
        self.assertTrue(denied(hook("input_gate.py", {"tool_name": "Bash", "tool_input": {"command": f'python tools/run_ab.py a "{small}"'}})))
        self.assertIsNone(hook("input_gate.py", {"tool_name": MCP + "ab_shape", "tool_input": {"png": small, "asset_id": "a", "collect": True}}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
