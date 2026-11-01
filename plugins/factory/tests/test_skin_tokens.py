"""Offline tests for plugin 0.1.7: the SkinTokens bind tool (stdlib unittest; run: python -m unittest discover plugins/factory/tests -v).

No GPU, no Blender, no SkinTokens, no checkpoint. run_skin_tokens.py is run against a throw-away repo whose "Blender" and "SkinTokens python" are fake processes that
write canned outputs, so the step order, the gate and the receipt are exercised for real. The hooks are fed synthetic PreToolUse JSON.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
sys.path.insert(0, str(REPO / "tools" / "skin_tokens"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate as sgate  # noqa: E402
from test_plugin import MCP_P, bash, denied, run_hook  # noqa: E402

PY = sys.executable
CFG = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))
GATE, REL = CFG["bind"]["gate"], CFG["skin_tokens"]["relative"]
GOOD = {"edges_measured_ge_5mm": 1000, "p99_stretch": 1.3, "p99_compression_ratio": 0.7,
        "weights": {"zero_weight_vertices": 0, "max_influences": 4, "weight_sum_max_dev": 1e-6}}
CONTROL = {"p99_stretch": 1.355, "p99_compression_ratio": 0.721}


class Gate(unittest.TestCase):
    def test_a_good_result_passes_with_the_bind_gate_numbers(self):
        c = sgate.checks(GOOD, GATE, CONTROL, REL, {"bones_missing": []})
        self.assertTrue(all(c.values()), c)
        self.assertEqual((GATE["p99_stretch_max"], GATE["p99_compression_ratio_min"], GATE["max_influences"]), (2.0, 0.5, 4))   # same numbers as run_bind.py

    def test_each_absolute_clause_fails_alone(self):
        for key, value, check in (("p99_stretch", 2.1, "p99_stretch"), ("p99_compression_ratio", 0.49, "p99_compression_ratio"), ("edges_measured_ge_5mm", 0, "edges_measured_ge_min")):
            self.assertIn(check, sgate.failed(sgate.checks({**GOOD, key: value}, GATE, CONTROL, REL)), key)
        for wk, value, check in (("zero_weight_vertices", 3, "zero_weight_vertices"), ("max_influences", 5, "max_influences"), ("weight_sum_max_dev", 0.01, "weight_sum_dev")):
            m = {**GOOD, "weights": {**GOOD["weights"], wk: value}}
            self.assertIn(check, sgate.failed(sgate.checks(m, GATE, CONTROL, REL)))

    def test_the_relative_to_control_clause_run_bind_skips(self):
        # LESSONS 2026-11-01: compression 0.612 passes the absolute 0.5 floor but misses 0.721 x 0.9 = 0.649
        m = {**GOOD, "p99_stretch": 1.31, "p99_compression_ratio": 0.612}
        c = sgate.checks(m, GATE, CONTROL, REL)
        self.assertTrue(c["p99_compression_ratio"])
        self.assertEqual(sgate.failed(c), ["relative_to_control"])
        self.assertIn("relative_to_control", sgate.failed(sgate.checks({**GOOD, "p99_stretch": 1.51}, GATE, CONTROL, REL)))   # 1.355 x 1.10 = 1.4905
        self.assertTrue(sgate.checks({**GOOD, "p99_compression_ratio": 0.65}, GATE, CONTROL, REL)["relative_to_control"])

    def test_no_control_is_a_failure_not_a_skip(self):
        self.assertIn("relative_to_control", sgate.failed(sgate.checks(GOOD, GATE, None, REL)))

    def test_lost_rig_bones_fail(self):
        self.assertIn("rig_bones_kept", sgate.failed(sgate.checks(GOOD, GATE, CONTROL, REL, {"bones_missing": ["mixamorig:Hips"]})))

    def test_unparsable_control_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({"p99_stretch": 1}), encoding="utf-8")
            with self.assertRaises(ValueError):
                sgate.load_control(str(p))


class Wiring(unittest.TestCase):
    def test_config_and_files_exist(self):
        st = CFG["skin_tokens"]
        for k in ("prep_script", "export_script"):
            self.assertTrue((REPO / st["script_dir"] / st[k]).is_file(), k)
        self.assertTrue((REPO / st["metrics_script"]).is_file())
        self.assertTrue((REPO / "tools" / "run_skin_tokens.py").is_file())
        self.assertTrue((REPO / st["script_dir"] / "patches" / "rocm_sdpa.patch").is_file())
        self.assertIn("{donor}", st["control_json"])
        loc = json.loads((REPO / "config" / "config.local.example.json").read_text(encoding="utf-8-sig"))
        self.assertTrue({"skin_tokens_repo_dir", "skin_tokens_python", "rig_glb"} <= set(loc))

    def test_hooks_json_wires_the_new_tool_and_script(self):
        text = (PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8")
        groups = json.loads(text)["hooks"]["PreToolUse"]
        gpu = [g for g in groups if g["matcher"].startswith(MCP_P) and any("gpu_lock_guard" in h["command"] for h in g["hooks"])]
        ram = [g for g in groups if g["matcher"].startswith(MCP_P) and any("ram_gate" in h["command"] for h in g["hooks"])]
        self.assertTrue(gpu and "skin_tokens_bind" in gpu[0]["matcher"])
        self.assertTrue(ram and "skin_tokens_bind" in ram[0]["matcher"])
        for script in ("gpu_lock_guard.py", "ram_gate.py", "plugin_guard.py"):
            ifs = [h["if"] for g in groups for h in g["hooks"] if script in h["command"] and "if" in h]
            self.assertIn("Bash(*run_skin_tokens*)", ifs, script)
        self.assertIn("PowerShell(*run_skin_tokens*)", [h["if"] for g in groups for h in g["hooks"] if "plugin_guard" in h["command"] and "if" in h])


class Hooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lock = Path(self.tmp.name) / ".gpu_lock"
        self.lock.write_text(json.dumps({"pid": os.getpid(), "job": "flux_kit", "started_utc": "2026-11-01T00:00:00Z"}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_live_gpu_lock_denies_the_tool_and_the_raw_script(self):
        out = run_hook("gpu_lock_guard.py", {"tool_name": MCP_P + "skin_tokens_bind", "tool_input": {"glb": "a.glb"}}, FACTORY_GPU_LOCK=self.lock)
        self.assertTrue(denied(out))
        self.assertIn("already running", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertTrue(denied(run_hook("gpu_lock_guard.py", bash("python tools/run_skin_tokens.py a body.glb"), FACTORY_GPU_LOCK=self.lock)))
        self.assertIsNone(run_hook("gpu_lock_guard.py", bash("cat tools/run_skin_tokens.py"), FACTORY_GPU_LOCK=self.lock))

    def test_low_ram_denies_the_tool_and_the_raw_script(self):
        low = dict(FACTORY_FAKE_FREE_RAM_GB=1.5, FACTORY_HOME=REPO)
        self.assertTrue(denied(run_hook("ram_gate.py", {"tool_name": MCP_P + "skin_tokens_bind", "tool_input": {}}, **low)))
        out = run_hook("ram_gate.py", bash("python tools/run_skin_tokens.py a body.glb"), **low)
        self.assertTrue(denied(out))
        self.assertIn("1.5 GB", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(run_hook("ram_gate.py", bash("python tools/run_skin_tokens.py a body.glb"), FACTORY_FAKE_FREE_RAM_GB=64, FACTORY_HOME=REPO))

    def test_plugin_guard_refuses_a_raw_call_and_allows_dry_run_and_override(self):
        sys.path.insert(0, str(PLUGIN / "hooks"))
        import plugin_guard
        raw = "python tools/run_skin_tokens.py a body.glb"
        why = plugin_guard.decide({"tool_name": "Bash", "tool_input": {"command": raw}})
        self.assertIn("skin_tokens_bind", why)
        self.assertEqual(plugin_guard.decide({"tool_name": "PowerShell", "tool_input": {"command": raw}}) != "", True)
        self.assertEqual(plugin_guard.decide({"tool_name": "Bash", "tool_input": {"command": raw + " --help"}}), "")
        self.assertEqual(plugin_guard.decide({"tool_name": "Bash", "tool_input": {"command": "git log -- tools/run_skin_tokens.py"}}), "")


class McpRefusals(unittest.TestCase):
    """skin_tokens_bind over real stdio against a fake FACTORY_HOME: every refusal happens before any process is started."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        home = t / "home"
        (home / "config").mkdir(parents=True)
        (home / "receipts").mkdir()
        (home / "config" / "config.json").write_text(json.dumps(CFG), encoding="utf-8")
        cls.loc = {"python": PY, "outputs_dir": str(t / "out"), "logs_dir": str(t / "logs"), "guard_script": str(t / "no_guard.ps1"), "blender_exe": str(t / "none.exe"),
                   "rig_glb": str(t / "rig.glb"), "skin_tokens_repo_dir": str(t / "st"), "skin_tokens_python": PY}
        (home / "config" / "config.local.json").write_text(json.dumps(cls.loc), encoding="utf-8")
        (t / "logs").mkdir()
        (t / "mesh.glb").write_bytes(b"glTF")
        (t / "rig.glb").write_bytes(b"glTF")
        cls.home, cls.t, cls.lock = home, t, t / ".gpu_lock"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):   # each test starts with no SkinTokens install and no donor control, whatever ran before
        shutil.rmtree(self.t / "st", ignore_errors=True)
        shutil.rmtree(self.home / "bind", ignore_errors=True)

    def call(self, args, **env):
        e = dict(os.environ, FACTORY_HOME=str(self.home), FACTORY_GPU_LOCK=str(self.lock), **{k: str(v) for k, v in env.items()})
        msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "skin_tokens_bind", "arguments": args}})
        r = subprocess.run([PY, str(PLUGIN / "mcp" / "server.py")], input=msg + "\n", capture_output=True, text=True, env=e, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        res = json.loads(r.stdout.splitlines()[0])["result"]
        return res["isError"], res["content"][0]["text"]

    def test_tool_is_listed_with_glb_required(self):
        e = dict(os.environ, FACTORY_HOME=str(self.home))
        r = subprocess.run([PY, str(PLUGIN / "mcp" / "server.py")], input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n", capture_output=True, text=True, env=e, timeout=60)
        tool = {t["name"]: t for t in json.loads(r.stdout)["result"]["tools"]}["skin_tokens_bind"]
        self.assertEqual(tool["inputSchema"]["required"], ["glb"])
        self.assertIn("skeleton", tool["inputSchema"]["properties"])

    def test_refusals_in_gate_order(self):
        err, text = self.call({"glb": str(self.t / "missing.glb")}, FACTORY_FAKE_FREE_RAM_GB=64)
        self.assertTrue(err)
        self.assertIn("glb not found", text)
        err, text = self.call({"glb": str(self.t / "mesh.glb"), "skeleton": str(self.t / "nope.glb")}, FACTORY_FAKE_FREE_RAM_GB=64)
        self.assertIn("skeleton not found", text)
        self.lock.write_text(json.dumps({"pid": os.getpid(), "job": "generate.py", "started_utc": "x"}), encoding="utf-8")
        try:
            err, text = self.call({"glb": str(self.t / "mesh.glb")}, FACTORY_FAKE_FREE_RAM_GB=64)
        finally:
            self.lock.unlink()
        self.assertTrue(err)
        self.assertEqual(json.loads(text)["status"], "refused")
        self.assertIn("already running", text)
        err, text = self.call({"glb": str(self.t / "mesh.glb")}, FACTORY_FAKE_FREE_RAM_GB=2.0)
        self.assertIn("2.0 GB of RAM is free", text)
        err, text = self.call({"glb": str(self.t / "mesh.glb")}, FACTORY_FAKE_FREE_RAM_GB=64)   # gates pass, SkinTokens is not installed in the fake home
        self.assertTrue(err)
        self.assertIn("SkinTokens checkpoint not found", text)

    def test_missing_donor_control_is_refused(self):
        ckpt = self.t / "st" / CFG["skin_tokens"]["ckpt"]
        ckpt.parent.mkdir(parents=True, exist_ok=True)
        ckpt.write_bytes(b"x")
        err, text = self.call({"glb": str(self.t / "mesh.glb")}, FACTORY_FAKE_FREE_RAM_GB=64)
        self.assertTrue(err)
        self.assertIn("donor control metrics", text)
        self.assertIn("control_tall.metrics.json", text)

    def test_all_gates_pass_but_nothing_runs_unguarded(self):
        ctl = self.home / CFG["skin_tokens"]["control_json"].format(donor="tall")
        ctl.parent.mkdir(parents=True, exist_ok=True)
        ctl.write_text(json.dumps(CONTROL), encoding="utf-8")
        ckpt = self.t / "st" / CFG["skin_tokens"]["ckpt"]
        ckpt.parent.mkdir(parents=True, exist_ok=True)
        ckpt.write_bytes(b"x")
        err, text = self.call({"glb": str(self.t / "mesh.glb")}, FACTORY_FAKE_FREE_RAM_GB=64)
        self.assertTrue(err)
        self.assertIn("guard script not found", text)   # a GPU job never starts without gpu_guard.ps1


FAKE_BLENDER = r'''
import json, os, sys
a = sys.argv[1:]
script = os.path.basename(a[a.index("--python") + 1])
rest = a[a.index("--") + 1:]
mode = os.environ.get("FAKE_MODE", "ok")
def touch(p, data=b"glb"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
if script == "prep_input.py":
    touch(rest[2]); json.dump({"rig_bones": ["Hips", "Spine"]}, open(rest[3], "w"))
elif script == "to_rig_body.py":
    touch(rest[1]); touch(rest[2]); json.dump({"bones_missing": ["Spine"] if mode == "bones" else []}, open(rest[3], "w"))
elif script == "metrics2.py":
    m = {"edges_measured_ge_5mm": 100, "p99_stretch": 1.3, "p99_compression_ratio": 0.612 if mode == "relative" else 0.7, "gate_pass": True,
         "weights": {"zero_weight_vertices": 0, "max_influences": 4, "weight_sum_max_dev": 0.0}}
    json.dump(m, open(rest[1], "w"))
else:
    sys.exit(9)
'''
FAKE_DEMO = r'''
import os, sys
a = sys.argv[1:]
assert "--use_skeleton" in a and os.environ.get("SKINTOKENS_ATTN") == "sdpa"
out = a[a.index("--output") + 1]
if os.environ.get("FAKE_MODE") == "demo_crash":
    sys.exit(3)
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "wb").write(b"glb")
'''


@unittest.skipUnless(os.name == "nt", "run_skin_tokens.py samples RAM with the Windows API (like run_bind.py)")
class RunScriptWithFakeProcesses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.t = t
        repo = t / "repo"
        for rel in ("tools/run_skin_tokens.py", "tools/cf_common.py", "tools/skin_tokens/gate.py", "tools/skin_tokens/prep_input.py", "tools/skin_tokens/to_rig_body.py",
                    "bind/metrics2.py", "config/config.json"):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, repo / rel)
        ctl = repo / CFG["skin_tokens"]["control_json"].format(donor="tall")
        ctl.parent.mkdir(parents=True, exist_ok=True)
        ctl.write_text(json.dumps(CONTROL), encoding="utf-8")
        (t / "fake_blender.py").write_text(FAKE_BLENDER, encoding="utf-8")
        (t / "blender.cmd").write_text(f'@"{PY}" "{t / "fake_blender.py"}" %*\r\n', encoding="utf-8")
        st = t / "st"
        (st / "experiments").mkdir(parents=True)
        (st / "demo.py").write_text(FAKE_DEMO, encoding="utf-8")
        for k in ("ckpt", "vae_ckpt"):
            p = st / CFG["skin_tokens"][k]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
        (t / "rig.glb").write_bytes(b"rig")
        (t / "body.glb").write_bytes(b"body")
        (t / "logs").mkdir()
        loc = {"python": PY, "outputs_dir": str(t / "out"), "logs_dir": str(t / "logs"), "blender_exe": str(t / "blender.cmd"), "rig_glb": str(t / "rig.glb"),
               "skin_tokens_repo_dir": str(st), "skin_tokens_python": PY}
        (repo / "config" / "config.local.json").write_text(json.dumps(loc), encoding="utf-8")
        self.repo = repo

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, mode="ok", skeleton=True):
        cmd = [PY, str(self.repo / "tools" / "run_skin_tokens.py"), "asset1", str(self.t / "body.glb")] + ([str(self.t / "rig.glb")] if skeleton else [])
        r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, FAKE_MODE=mode), timeout=300)
        receipts = sorted((self.repo / "receipts" / "asset1").glob("bind-*.json"))
        self.assertEqual(len(receipts), 1, r.stdout + r.stderr)
        return r, json.loads(receipts[0].read_text(encoding="utf-8"))

    def test_pass_writes_a_standard_bind_receipt(self):
        r, rec = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((rec["schema"], rec["stage"], rec["status"], rec["asset_id"], rec["error"]), (1, "bind", "ok", "asset1", ""))
        for k in ("tool", "model", "inputs", "params", "machine", "timing", "memory", "outputs", "created_utc", "script_commit"):
            self.assertIn(k, rec)
        self.assertEqual(rec["tool"], "run_skin_tokens.py")
        self.assertTrue(all(len(o["sha256"]) == 64 for o in rec["outputs"] + rec["inputs"]))
        self.assertTrue(any(o["path"].endswith("bound_skintokens.glb") for o in rec["outputs"]))
        self.assertFalse(any(o["path"].endswith(os.sep + "bound.glb") for o in rec["outputs"]))   # the donor bind's file is never the output
        m = rec["params"]["metrics"]
        self.assertTrue(m["gate_passed"] and m["checks"]["relative_to_control"] and m["checks"]["rig_bones_kept"])
        self.assertEqual(rec["params"]["gate"], GATE)
        self.assertEqual(rec["params"]["clamp_influences"], 4)

    def test_default_skeleton_is_the_rig_in_config(self):
        r, rec = self.run_it(skeleton=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(rec["params"]["skeleton"], str(self.t / "rig.glb"))

    def test_relative_clause_failure_is_a_failed_receipt(self):
        r, rec = self.run_it("relative")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(rec["status"], "fail")
        self.assertEqual(rec["error"], "gate failed: relative_to_control")
        self.assertTrue(rec["params"]["metrics"]["checks"]["p99_compression_ratio"])   # absolute floor passed; only the relative clause failed

    def test_lost_bones_fail_the_gate(self):
        r, rec = self.run_it("bones")
        self.assertEqual((r.returncode, rec["status"], rec["error"]), (1, "fail", "gate failed: rig_bones_kept"))

    def test_a_crashed_skintokens_step_leaves_a_failed_receipt(self):
        r, rec = self.run_it("demo_crash")
        self.assertEqual(r.returncode == 0, False)
        self.assertEqual(rec["status"], "fail")
        self.assertIn("skintokens step failed (exit 3)", rec["error"])

    def test_missing_install_fails_before_any_step(self):
        (self.t / "st" / "experiments" / "x").mkdir(parents=True, exist_ok=True)
        os.remove(self.t / "st" / CFG["skin_tokens"]["ckpt"])
        r, rec = self.run_it()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("missing: ckpt", rec["error"])


class ServerResult(unittest.TestCase):
    def test_result_for_a_failed_relative_clause_carries_the_warning(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / "config").mkdir()
            (home / "receipts" / "a1").mkdir(parents=True)
            (home / "config" / "config.json").write_text(json.dumps(CFG), encoding="utf-8")
            rec = {"status": "fail", "error": "gate failed: relative_to_control", "outputs": [], "timing": {"wall_s": 3}, "memory": {},
                   "params": {"metrics": {"p99_stretch": 1.3, "p99_compression_ratio": 0.612, "binder": "skin_tokens", "checks": {"relative_to_control": False}}}}
            (home / "receipts" / "a1" / "bind-20261101T000000Z.json").write_text(json.dumps(rec), encoding="utf-8")
            code = ("import sys, json; sys.path.insert(0, %r); import server; res = server.finish('skin_tokens_bind', 0, 'l', 0.0, ['bind'], 'a1'); "
                    "print(json.dumps([res['status'], res['receipt_status']]))" % str(PLUGIN / "mcp"))
            r = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=dict(os.environ, FACTORY_HOME=str(home)), timeout=60)
            self.assertEqual(json.loads(r.stdout), ["fail", "fail"], r.stderr)   # exit 0 does not rescue a failed receipt


if __name__ == "__main__":
    unittest.main(verbosity=2)
