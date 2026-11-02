"""Offline tests for the cloth stage (plugin 0.1.18; standard library only; run: python -m unittest discover plugins/factory/tests -v).

No real Blender and no simulation here: the orchestration (tools/cloth/cloth_core.py) runs against a FAKE Blender (a tiny script that writes the result json and an output file), the pin rule,
settle detector, gate, lock file and Flux-window guard are pure functions, and the MCP tool and hooks are tested by their refusals. A real simulation is optional and skipped unless
CF_CLOTH_REAL_BLENDER names a blender executable (it then runs the pure rest-mode pipeline on a small synthetic scene).
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
sys.path.insert(0, str(REPO / "tools" / "cloth"))
sys.path.insert(0, str(PLUGIN / "hooks"))
import cloth_core as core  # noqa: E402

PY = sys.executable
MCP_P = "mcp__plugin_factory_factory__"
CFG = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))
CL = CFG["cloth"]


class PinRule(unittest.TestCase):
    SEG = [((0.0, 0.0, 1.45), (0.0, 0.0, 1.53)), ((0.05, 0.0, 1.43), (0.15, 0.0, 1.43))]

    def test_pins_near_the_neck_and_shoulders_only(self):
        pin = CL["pin"]
        self.assertEqual(core.pin_weight((0.0, 0.1, 1.50), self.SEG, pin), 1.0)        # collar behind the neck
        self.assertEqual(core.pin_weight((0.15, 0.1, 1.46), self.SEG, pin), 1.0)       # on the shoulder
        self.assertEqual(core.pin_weight((0.0, 0.1, 1.2), self.SEG, pin), 0.0)         # too low: free
        self.assertEqual(core.pin_weight((0.6, 0.0, 1.45), self.SEG, pin), 0.0)        # too far from the bones (the T-pose arm): free
        self.assertEqual(core.pin_weight((0, 0, 1.5), [], pin), 0.0)

    def test_soft_inner_falls_off_linearly(self):
        pin = {**CL["pin"], "soft_inner": 0.5, "radius_m": 0.2}
        seg = [((0, 0, 1.5), (0, 0, 1.5))]
        self.assertEqual(core.pin_weight((0.1, 0, 1.5), seg, pin), 1.0)
        self.assertAlmostEqual(core.pin_weight((0.15, 0, 1.5), seg, pin), 0.5)
        self.assertEqual(core.pin_weight((0.21, 0, 1.5), seg, pin), 0.0)

    def test_bone_selection_excludes_helpers(self):
        names = ["mixamorig:Neck", "mixamorig:LeftShoulder", "fk_shoulder_fix_l", "cape_neck_twist", "mixamorig:Hips", "upperarm_twist_01_l"]
        got = core.select_pin_bones(names, CL["pin"])
        self.assertEqual(got, ["mixamorig:Neck", "mixamorig:LeftShoulder", "fk_shoulder_fix_l"])


class Settle(unittest.TestCase):
    def test_settles_only_after_min_frames_and_a_quiet_run(self):
        sim = {**CL["sim"], "min_frames": 5, "settle_frames": 3, "settle_p99_speed_mps": 0.02}
        d = core.SettleDetector(sim)
        speeds = [0.5, 0.01, 0.01, 0.4, 0.01, 0.01, 0.01, 0.01]
        out = [d.feed(s) for s in speeds]
        self.assertEqual(out, [False] * 6 + [True] * 2)   # the spike at frame 4 resets the run; 3 quiet frames (5, 6, 7) complete at frame 7, after min_frames 5
        self.assertEqual(d.settled_at, 7)

    def test_a_noisy_sim_never_settles(self):
        d = core.SettleDetector({**CL["sim"], "min_frames": 1, "settle_frames": 2})
        self.assertFalse(any(d.feed(0.5) for _ in range(50)))
        self.assertIsNone(d.settled_at)


class Gate(unittest.TestCase):
    GOOD = {"exported": True, "counts": {"verts_sim": 100, "verts_out": 100, "faces_sim": 90, "faces_out": 90, "uv_layers_in": 1, "uv_layers_out": 1},
            "nan_vertices": 0, "extent_ratio": 1.1, "pins": {"count": 50}, "penetration": {"max_free_m": 0.004}}

    def test_a_good_result_passes(self):
        self.assertTrue(all(core.gate_checks(self.GOOD, CL["gate"]).values()))

    def test_each_failure_is_named(self):
        def bad(**ch):
            r = json.loads(json.dumps(self.GOOD))
            for k, v in ch.items():
                if isinstance(v, dict):
                    r[k].update(v)
                else:
                    r[k] = v
            return {k for k, ok in core.gate_checks(r, CL["gate"]).items() if not ok}
        self.assertEqual(bad(penetration={"max_free_m": 0.05}), {"penetration"})
        self.assertEqual(bad(pins={"count": 3}), {"pins"})
        self.assertEqual(bad(nan_vertices=4), {"no_nan"})
        self.assertEqual(bad(extent_ratio=9), {"not_exploded"})
        self.assertEqual(bad(counts={"verts_out": 99}), {"counts_unchanged"})
        self.assertEqual(bad(counts={"uv_layers_out": 0}), {"uvs_kept"})
        self.assertEqual(bad(exported=False), {"exported"})


class Guard(unittest.TestCase):
    def test_flux_load_window(self):
        with tempfile.TemporaryDirectory() as d:
            g = CL["guard"]
            self.assertEqual(core.flux_loading(d, g), "")                       # no logs
            log = Path(d) / "kit_vampire_row.txt"
            log.write_text("Loading weights:  5%\n", encoding="utf-8")
            now = time.time()
            self.assertEqual(core.flux_loading(d, g, now=now), str(log))        # fresh log with a model-load marker
            self.assertEqual(core.flux_loading(d, g, now=now + g["flux_window_s"] + 5), "")   # older than the window
            log.write_text("RESULT done\n", encoding="utf-8")
            self.assertEqual(core.flux_loading(d, g, now=time.time()), "")      # fresh but no START or load marker
            (Path(d) / "other.txt").write_text("START", encoding="utf-8")
            self.assertEqual(core.flux_loading(d, g), "")                       # only kit_*/queue logs count

    def test_lock_is_exclusive_and_stale_locks_are_taken_over(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "blender_job.lock")
            with core.BlenderLock(p, 1):
                self.assertTrue(os.path.exists(p))
                with self.assertRaises(TimeoutError):
                    core.BlenderLock(p, 0.3, sleep=0.1).acquire()               # live pid (ours): gives up
            self.assertFalse(os.path.exists(p))                                 # released
            Path(p).write_text(json.dumps({"pid": 2147480000}), encoding="utf-8")   # dead owner
            with core.BlenderLock(p, 1):
                self.assertEqual(json.load(open(p))["pid"], os.getpid())
            Path(p).write_text("not json", encoding="utf-8")                    # unreadable owner = stale
            with core.BlenderLock(p, 1):
                pass

    def test_headroom_waits_then_gives_up_with_the_reason(self):
        with tempfile.TemporaryDirectory() as d:
            g = {**CL["guard"], "flux_wait_s": 0.2}
            os.environ["CLOTH_FAKE_FREE_RAM_GB"] = "4"
            try:
                with self.assertRaises(TimeoutError) as cm:
                    core.wait_for_headroom({"logs_dir": d}, g, sleep=0.05)
                self.assertIn("free RAM 4.0 GB (need 10", str(cm.exception))
                os.environ["CLOTH_FAKE_FREE_RAM_GB"] = "32"
                self.assertEqual(core.wait_for_headroom({"logs_dir": d}, g, sleep=0.05), 32.0)
            finally:
                os.environ.pop("CLOTH_FAKE_FREE_RAM_GB", None)


FAKE = r'''
import json, sys
argf = sys.argv[sys.argv.index("--") + 1]
A = json.load(open(argf, encoding="utf-8"))
mode = A["part"]
res = {"exported": True, "counts": {"verts_sim": 10, "verts_out": 10, "faces_sim": 8, "faces_out": 8, "uv_layers_in": 1, "uv_layers_out": 1}, "nan_vertices": 0, "extent_ratio": 1.0,
       "pins": {"count": 30}, "penetration": {"max_free_m": 0.002}, "sim": {"frames": 60, "settled": True}, "lowest_free_ram_gb": 12.5}
if mode == "badpen":
    res["penetration"]["max_free_m"] = 0.5
if mode != "crash":
    json.dump(res, open(A["result"], "w"))
    open(A["out"], "wb").write(b"glTF")
else:
    sys.exit(7)
'''


class Orchestration(unittest.TestCase):
    """cloth_core.run against a fake Blender: one process, no simulation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        fake = self.t / "fake_blender.py"
        fake.write_text(FAKE, encoding="utf-8")
        # run() calls runner([blender_exe, "--background", "--python", script, "--", argf]); the test runner swaps in python <fake> and passes the same tail
        self.fake = fake
        (self.t / "logs").mkdir()
        (self.t / "body.glb").write_bytes(b"glTF")
        (self.t / "cloak.glb").write_bytes(b"glTF")
        exe = self.t / "blender.exe"
        exe.write_bytes(b"x")
        self.cfg = {"local": {"blender_exe": str(exe), "outputs_dir": str(self.t / "out"), "logs_dir": str(self.t / "logs"), "work_dir": str(self.t)},
                    "cloth": json.loads(json.dumps(CL)), "bind": CFG["bind"], "limits": CFG["limits"]}
        self.cfg["cloth"]["script"] = "tools/cloth/cloth_blender.py"   # exists in the repo; never started (the fake runner replaces Blender)
        self.receipts = []
        os.environ["CLOTH_FAKE_FREE_RAM_GB"] = "32"

    def tearDown(self):
        os.environ.pop("CLOTH_FAKE_FREE_RAM_GB", None)
        self.tmp.cleanup()

    def receipt(self, status, error="", outs=(), timing=None, mem=None, extra=None):
        self.receipts.append({"status": status, "error": error, "outs": list(outs), "extra": extra or {}})
        return f"receipt-{len(self.receipts)}"

    def go(self, part="cloak", **kw):
        def runner(cmd, stdout, stderr, timeout):
            return subprocess.run([PY, str(self.fake)] + cmd[3:], stdout=stdout, stderr=stderr, timeout=timeout)
        return core.run(self.cfg, "asset1", part, str(self.t / "cloak.glb"), str(self.t / "body.glb"), repo=str(REPO), receipt=self.receipt, runner=runner, log=lambda *a: None, **kw)

    def test_ok_run_writes_an_ok_receipt_and_the_output(self):
        code, path = self.go(preset="silk")
        self.assertEqual((code, self.receipts[-1]["status"]), (0, "ok"))
        out = Path(self.receipts[-1]["outs"][0])
        self.assertEqual(out.name, "cloth_cloak.glb")
        self.assertTrue(out.exists())
        x = self.receipts[-1]["extra"]
        self.assertEqual(x["params"]["preset"], "silk")
        self.assertEqual(x["params"]["settings"]["mass"], 0.15)
        self.assertTrue(x["metrics"]["gate_passed"])
        self.assertFalse((self.t / "blender_job.lock").exists())      # the lock is released

    def test_penetration_over_the_gate_fails_with_the_check_named(self):
        code, _ = self.go(part="badpen")
        self.assertEqual(code, 1)
        self.assertIn("penetration", self.receipts[-1]["error"])

    def test_blender_crash_is_a_fail_receipt(self):
        code, _ = self.go(part="crash")
        self.assertEqual((code, self.receipts[-1]["status"]), (1, "fail"))
        self.assertIn("blender exit 7", self.receipts[-1]["error"])

    def test_clip_mode_refuses_with_notimplemented(self):
        code, _ = self.go(mode="clip")
        self.assertEqual(code, 2)
        self.assertIn("NotImplemented", self.receipts[-1]["error"])
        self.assertFalse(self.cfg["local"].get("started"))

    def test_unknown_preset_and_missing_files(self):
        with self.assertRaises(ValueError):
            self.go(preset="tinfoil")
        (self.t / "body.glb").unlink()
        code, _ = self.go()
        self.assertEqual(code, 2)
        self.assertIn("bound body not found", self.receipts[-1]["error"])

    def test_low_ram_gives_up_instead_of_starting_blender(self):
        os.environ["CLOTH_FAKE_FREE_RAM_GB"] = "3"
        self.cfg["cloth"]["guard"]["flux_wait_s"] = 0.2
        started = []
        code, _ = core.run(self.cfg, "asset1", "cloak", str(self.t / "cloak.glb"), str(self.t / "body.glb"), repo=str(REPO), receipt=self.receipt,
                           runner=lambda *a, **k: started.append(1), log=lambda *a: None)
        self.assertEqual((code, started), (3, []))
        self.assertIn("free RAM 3.0 GB", self.receipts[-1]["error"])
        self.assertFalse((self.t / "blender_job.lock").exists())

    def test_a_held_lock_gives_up_after_the_wait(self):
        self.cfg["cloth"]["guard"]["lock_wait_s"] = 0.3
        with core.BlenderLock(core.lock_path(self.cfg["local"]), 1):
            code, _ = self.go()
        self.assertEqual(code, 3)
        self.assertIn("blender lock", self.receipts[-1]["error"])


class Config(unittest.TestCase):
    def test_presets_match_blender_silk_and_cotton(self):
        s, c = CL["presets"]["silk"], CL["presets"]["cotton"]
        self.assertEqual((s["mass"], s["tension_stiffness"], s["bending_stiffness"], s["tension_damping"]), (0.15, 5, 0.05, 0))
        self.assertEqual((c["mass"], c["tension_stiffness"], c["bending_stiffness"], c["tension_damping"]), (0.3, 15, 0.5, 5))
        self.assertIn(CL["default_preset"], CL["presets"])

    def test_guard_numbers_are_the_ones_strategy_ordered(self):
        g = CL["guard"]
        self.assertEqual((g["min_free_ram_gb"], g["lock_wait_s"], g["flux_window_s"]), (10, 600, 60))

    def test_no_gpu_anywhere_in_the_cloth_stage(self):
        for f in ("tools/run_cloth.py", "tools/cloth/cloth_core.py", "tools/cloth/cloth_blender.py"):
            text = (REPO / f).read_text(encoding="utf-8")
            for bad in ("cuda", "gpu_guard", "gpu_lock", "torch"):
                self.assertNotIn(bad, text.lower().replace("no gpu", ""), f"{f}: {bad}")


class Hooks(unittest.TestCase):
    def run_hook(self, name, payload, **env):
        e = dict(os.environ, FACTORY_TEST_STAGE_TAG="no-such-stage-process", FACTORY_HOME=str(REPO), **{k: str(v) for k, v in env.items()})
        r = subprocess.run([PY, str(PLUGIN / "hooks" / name)], input=json.dumps(payload), capture_output=True, text=True, env=e, timeout=60)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def test_ram_gate_denies_cloth_starts_on_low_ram(self):
        out = self.run_hook("ram_gate.py", {"tool_name": MCP_P + "cloth", "tool_input": {}}, FACTORY_FAKE_FREE_RAM_GB=1.0)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        out = self.run_hook("ram_gate.py", {"tool_name": "Bash", "tool_input": {"command": "python tools/run_cloth.py a cloak m.glb b.glb"}}, FACTORY_FAKE_FREE_RAM_GB=1.0)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_plugin_guard_refuses_a_raw_run_cloth_and_names_the_tool(self):
        import plugin_guard
        why = plugin_guard.decide({"tool_name": "Bash", "tool_input": {"command": "python tools/run_cloth.py a cloak m.glb b.glb"}})
        self.assertIn("run_cloth.py", why)
        self.assertIn("cloth", why)
        self.assertEqual(plugin_guard.decide({"tool_name": "Bash", "tool_input": {"command": "python tools/run_cloth.py --help"}}), "")

    def test_hooks_json_wires_cloth_to_the_ram_gate_and_never_the_gpu_lock(self):
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        mcp = [g for g in hooks if g["matcher"] == MCP_P + "cloth"]
        self.assertEqual(len(mcp), 1)
        self.assertEqual([h["command"].split("/")[-1].strip('"') for h in mcp[0]["hooks"]], ["ram_gate.py"])
        for g in hooks:
            for h in g["hooks"]:
                if "run_cloth" in h.get("if", ""):
                    self.assertNotIn("gpu_lock_guard", h["command"])
                    self.assertTrue("ram_gate.py" in h["command"] or "plugin_guard.py" in h["command"])


class McpCloth(unittest.TestCase):
    """The cloth tool's refusals over real stdio, against a fake FACTORY_HOME: nothing is started."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        home = t / "home"
        (home / "config").mkdir(parents=True)
        (home / "receipts").mkdir()
        (home / "config" / "config.json").write_text(json.dumps(CFG), encoding="utf-8")
        loc = {"python": PY, "outputs_dir": str(t / "out"), "logs_dir": str(t / "logs"), "guard_script": str(t / "no.ps1"), "blender_exe": str(t / "none.exe"), "work_dir": str(t)}
        (home / "config" / "config.local.json").write_text(json.dumps(loc), encoding="utf-8")
        (t / "logs").mkdir()
        (t / "m.glb").write_bytes(b"glTF")
        (t / "b.glb").write_bytes(b"glTF")
        cls.home, cls.t = home, t

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def call(self, args, **env):
        e = dict(os.environ, FACTORY_HOME=str(self.home), FACTORY_GPU_LOCK=str(self.t / ".gpu_lock"), **{k: str(v) for k, v in env.items()})
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "cloth", "arguments": args}}
        r = subprocess.run([PY, str(PLUGIN / "mcp" / "server.py")], input=json.dumps(msg) + "\n", capture_output=True, text=True, env=e, timeout=120)
        res = json.loads(r.stdout.splitlines()[0])["result"]
        return res["isError"], res["content"][0]["text"]

    def base(self, **kw):
        return {"asset_id": "a", "part": "cloak", "body_glb": str(self.t / "b.glb"), "mesh": str(self.t / "m.glb"), **kw}

    def test_clip_mode_is_a_notimplemented_refusal(self):
        err, text = self.call(self.base(mode="clip"), FACTORY_FAKE_FREE_RAM_GB=64)
        self.assertTrue(err)
        self.assertIn("NotImplemented", text)

    def test_bad_input_is_refused(self):
        self.assertIn("unknown cloth preset", self.call(self.base(preset="tinfoil"), FACTORY_FAKE_FREE_RAM_GB=64)[1])
        self.assertIn("bound body glb not found", self.call(self.base(body_glb=str(self.t / "none.glb")), FACTORY_FAKE_FREE_RAM_GB=64)[1])
        self.assertIn("part mesh not found", self.call(self.base(mesh=str(self.t / "none.glb")), FACTORY_FAKE_FREE_RAM_GB=64)[1])
        self.assertTrue(self.call({"asset_id": "a"})[0])   # missing arguments

    def test_low_ram_refuses_and_a_live_gpu_lock_does_not(self):
        err, text = self.call(self.base(), FACTORY_FAKE_FREE_RAM_GB=2.0)
        self.assertTrue(err)
        self.assertIn("2.0 GB of RAM is free", text)
        (self.t / ".gpu_lock").write_text(json.dumps({"pid": os.getpid(), "job": "flux"}), encoding="utf-8")
        try:
            err, text = self.call(self.base(), FACTORY_FAKE_FREE_RAM_GB=64)
        finally:
            (self.t / ".gpu_lock").unlink()
        self.assertNotIn("GPU job is already running", text)   # cloth is CPU only: a live GPU lock does not refuse it


@unittest.skipUnless(os.environ.get("CF_CLOTH_REAL_BLENDER"), "set CF_CLOTH_REAL_BLENDER to a blender executable to run the real simulation")
class RealSim(unittest.TestCase):
    def test_real_blender_runs_the_script_header(self):
        r = subprocess.run([os.environ["CF_CLOTH_REAL_BLENDER"], "--background", "--python-expr", "import bpy;print('CLOTHOK', bpy.app.version_string)"], capture_output=True, text=True, timeout=300)
        self.assertIn("CLOTHOK", r.stdout)


if __name__ == "__main__":
    unittest.main()
