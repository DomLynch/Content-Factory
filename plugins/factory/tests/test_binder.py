"""Offline tests for plugin 0.1.21: SkinTokens as the default binder, the labelled donor fallback, the flip helper (stdlib unittest; run: python -m unittest discover plugins/factory/tests -v).

No GPU, no Blender, no SkinTokens, no donors. Every bind script is a fake process that writes a file or exits non-zero, or a patched stage runner, so the ROUTING
(which script runs, in which order, with which environment, what the receipt/marker/result say) is exercised for real and nothing heavy ever starts.
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
sys.path.insert(0, str(REPO / "tools"))
import binder_route  # noqa: E402

PY = sys.executable
CFG_PATH = REPO / "config" / "config.json"
CFG = json.loads(CFG_PATH.read_text(encoding="utf-8-sig"))
FALLBACK = "FALLBACK donor (MakeHuman weights)"


def fresh_cfg(binder, fallback=True, **local):
    cfg = json.loads(json.dumps(CFG))
    cfg["bind"]["binder"] = binder
    cfg["bind"]["fallback_to_donor"] = fallback
    cfg["local"] = local
    return cfg


class ConfigShape(unittest.TestCase):
    def test_binder_keys(self):
        b = CFG["bind"]
        self.assertIn(b["binder"], binder_route.BINDERS)   # which one ships is the owner's switch (tools/set_default_binder.py); only the shape is pinned
        self.assertIsInstance(b["fallback_to_donor"], bool)
        self.assertEqual(binder_route.BINDERS, ("skin_tokens", "donor"))
        self.assertEqual(CFG["character"]["guard_match"]["skin_tokens"], "run_skin_tokens")   # run_character.py starts its GPU guard with this
        self.assertEqual(binder_route.FALLBACK_LABEL, FALLBACK)

    def test_unknown_binder_is_an_error_not_a_silent_choice(self):
        with self.assertRaises(ValueError):
            binder_route.binder(fresh_cfg("skin_token"))
        self.assertEqual(binder_route.binder(fresh_cfg("donor")), "donor")
        self.assertEqual(binder_route.binder(fresh_cfg("skin_tokens")), "skin_tokens")

    def test_fallback_flag(self):
        self.assertTrue(binder_route.fallback_allowed(fresh_cfg("skin_tokens", True)))
        self.assertFalse(binder_route.fallback_allowed(fresh_cfg("skin_tokens", False)))

    def test_example_local_config_still_parses_and_labels_donors_as_fallback_only(self):
        ex = json.loads((REPO / "config" / "config.local.example.json").read_text(encoding="utf-8-sig"))
        self.assertIn("donors_dir", ex)
        self.assertIn("FALLBACK", ex["_donors_dir_note"])


class SetDefaultBinder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "config.json"
        shutil.copy(CFG_PATH, self.cfg)
        self.original = self.cfg.read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, *args):
        r = subprocess.run([PY, str(REPO / "tools" / "set_default_binder.py"), *args, "--config", str(self.cfg)], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def value(self):
        return json.loads(self.cfg.read_text(encoding="utf-8-sig"))["bind"]["binder"]

    def test_flip_both_ways_is_idempotent_and_touches_only_the_value(self):
        start = self.value()
        other = "donor" if start == "skin_tokens" else "skin_tokens"
        rc, out, _ = self.run_it(other)
        self.assertEqual((rc, out, self.value()), (0, f"binder: {start} -> {other}", other))
        once = self.cfg.read_bytes()
        rc, out, _ = self.run_it(other)   # second run: no change at all
        self.assertEqual((rc, out), (0, f"binder already {other}"))
        self.assertEqual(self.cfg.read_bytes(), once)
        rc, out, _ = self.run_it(start)   # and back: byte-identical to the original file
        self.assertEqual((rc, self.value()), (0, start))
        self.assertEqual(self.cfg.read_bytes(), self.original)
        self.assertEqual(len(once), len(self.original) + len(other) - len(start))   # only the value's characters changed

    def test_the_whole_config_still_parses_and_other_keys_are_untouched(self):
        self.run_it("skin_tokens")
        new = json.loads(self.cfg.read_text(encoding="utf-8-sig"))
        old = json.loads(json.dumps(CFG))
        new["bind"]["binder"] = old["bind"]["binder"]
        self.assertEqual(new, old)

    def test_bad_value_or_arguments_change_nothing(self):
        for args in (("skintokens",), ("",), ()):
            rc, _, err = self.run_it(*args)
            self.assertEqual(rc, 2, args)
            self.assertTrue(err)
            self.assertEqual(self.cfg.read_bytes(), self.original)

    def test_a_config_without_the_key_is_refused(self):
        self.cfg.write_text(json.dumps({"bind": {"gate": {}}}), encoding="utf-8")
        rc, _, err = self.run_it("donor")
        self.assertEqual(rc, 2)
        self.assertIn("missing", err)

    def test_default_target_is_the_repo_config_and_it_is_not_modified_by_these_tests(self):
        self.assertEqual(CFG_PATH.read_bytes().count(b'"binder"'), 1)


class Prerequisites(unittest.TestCase):
    def test_missing_pieces_are_named_and_a_complete_install_is_ready(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            st = CFG["skin_tokens"]
            loc = {"blender_exe": str(t / "blender.exe"), "rig_glb": str(t / "rig.glb"), "skin_tokens_repo_dir": str(t / "st"), "skin_tokens_python": str(t / "py.exe")}
            cfg = fresh_cfg("skin_tokens", **loc)
            missing = binder_route.skin_tokens_missing(cfg, str(t))
            for name in ("blender_exe", "skeleton", "skin_tokens_repo_dir", "skin_tokens_python", "ckpt", "vae_ckpt", "control"):
                self.assertTrue(any(m.startswith(name + ":") for m in missing), (name, missing))
            for p in (t / "blender.exe", t / "rig.glb", t / "py.exe"):
                p.write_bytes(b"x")
            for rel in (st["ckpt"], st["vae_ckpt"], st["control_json"].format(donor=st["control_donor"])):
                for base in (t / "st", t):
                    (base / rel).parent.mkdir(parents=True, exist_ok=True)
                    (base / rel).write_bytes(b"x")
            self.assertEqual(binder_route.skin_tokens_missing(cfg, str(t)), [])

    def test_unset_local_paths_count_as_missing(self):
        missing = binder_route.skin_tokens_missing(fresh_cfg("skin_tokens"), str(REPO))
        self.assertTrue(any("(not set)" in m for m in missing))


class Pointer(unittest.TestCase):
    def test_pointer_names_the_bound_body_and_defaults_follow_the_binder(self):
        with tempfile.TemporaryDirectory() as d:
            work = os.path.join(d, "w")
            for which, name in (("skin_tokens", "bound_skintokens.glb"), ("donor", "bound.glb")):
                cfg = fresh_cfg(which)
                self.assertEqual(binder_route.resolve_bound(cfg, work), os.path.join(work, name))   # no pointer yet: the configured binder's file
            cfg = fresh_cfg("skin_tokens")
            binder_route.write_pointer(cfg, work, "donor", "SkinTokens failed")
            self.assertEqual(binder_route.resolve_bound(cfg, work), os.path.join(work, "bound_skintokens.glb"))   # pointer names a GLB that does not exist yet: the configured binder's file
            Path(work, "bound.glb").write_bytes(b"g")
            self.assertEqual(binder_route.resolve_bound(cfg, work), os.path.join(work, "bound.glb"))   # the fallback's file wins once it exists
            row = json.loads(Path(work, binder_route.POINTER).read_text(encoding="utf-8"))
            self.assertEqual((row["binder_used"], row["fallback"], row["label"]), ("donor", True, FALLBACK))
            binder_route.write_pointer(cfg, work, "skin_tokens")
            Path(work, "bound_skintokens.glb").write_bytes(b"g")
            self.assertEqual(binder_route.resolve_bound(cfg, work), os.path.join(work, "bound_skintokens.glb"))
            self.assertEqual(json.loads(Path(work, binder_route.POINTER).read_text(encoding="utf-8"))["label"], "skin_tokens")


FAKE_STAGE = r'''
import os, sys
out, mode = sys.argv[1], sys.argv[2]
open(out + ".ran", "w").write(os.environ.get("BIND_FALLBACK_REASON", ""))
if mode != "ok":
    sys.exit(1)
open(out, "wb").write(b"glb")
'''


class RunCharacterRouting(unittest.TestCase):
    """tools/run_character.py: the bind unit per binder value, run in-process with the stage commands swapped for fake scripts."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO / "tools"))
        import run_character
        cls.rc = run_character
        cls.spec = json.loads((REPO / "characters" / "vampire-dracula.json").read_text(encoding="utf-8-sig"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        (self.t / "logs").mkdir()
        (self.t / "stage.py").write_text(FAKE_STAGE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def cfg(self, binder, fallback=True, prereqs=True):
        t = self.t
        loc = {"python": PY, "outputs_dir": str(t / "out"), "logs_dir": str(t / "logs"), "guard_script": str(t / "none.ps1")}
        cfg = fresh_cfg(binder, fallback, **loc)
        cfg["skin_tokens"]["control_json"] = str(t / "control_{donor}.json")
        if prereqs:
            for name in ("blender.exe", "rig.glb", "py.exe", "control_tall.json"):
                (t / name).write_bytes(b"x")
            for rel in (cfg["skin_tokens"]["ckpt"], cfg["skin_tokens"]["vae_ckpt"]):
                (t / "st" / rel).parent.mkdir(parents=True, exist_ok=True)
                (t / "st" / rel).write_bytes(b"x")
            cfg["local"].update(blender_exe=str(t / "blender.exe"), rig_glb=str(t / "rig.glb"), skin_tokens_repo_dir=str(t / "st"), skin_tokens_python=str(t / "py.exe"))
        return cfg

    def bind_unit(self, cfg):
        plan = self.rc.Plan(self.spec, cfg)
        u = next(u for u in plan.units() if u["stage"] == "bind")
        return plan, u

    def fake(self, u, st_mode="ok", donor_mode="ok"):
        """Swap the real commands for fake scripts: the same output paths, no GPU guard."""
        u["cmd"] = [PY, str(self.t / "stage.py"), u["outputs"][0], st_mode] if u["outputs"][0].endswith("bound_skintokens.glb") else [PY, str(self.t / "stage.py"), u["outputs"][0], donor_mode]
        u["gpu"] = None
        if u.get("fallback"):
            fb = u["fallback"]
            fb["cmd"] = [PY, str(self.t / "stage.py"), fb["outputs"][0], donor_mode]
        os.makedirs(os.path.dirname(u["outputs"][0]), exist_ok=True)
        return u

    def go(self, plan, u):
        runner = self.rc.Runner(plan)
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = runner.go([u])
        marker = json.loads(Path(runner.marker(u)).read_text(encoding="utf-8")) if os.path.exists(runner.marker(u)) else None
        return rc, marker, buf.getvalue(), runner

    def test_plan_for_donor_binder_is_todays_unit(self):
        plan, u = self.bind_unit(self.cfg("donor"))
        self.assertEqual(os.path.basename(u["cmd"][1]), "run_bind.py")
        self.assertIsNone(u["fallback"])
        self.assertIsNone(u["gpu"])
        self.assertEqual(os.path.basename(u["outputs"][0]), "bound.glb")
        self.assertEqual(u["env"], {"BIND_FORCE_DONOR": CFG["character"]["default_donor"]})

    def test_plan_for_skin_tokens_binder_is_a_gpu_unit_with_a_donor_fallback(self):
        plan, u = self.bind_unit(self.cfg("skin_tokens"))
        self.assertEqual(os.path.basename(u["cmd"][1]), "run_skin_tokens.py")
        self.assertEqual(u["gpu"], "skin_tokens")
        self.assertEqual(os.path.basename(u["outputs"][0]), "bound_skintokens.glb")
        self.assertEqual(os.path.basename(u["fallback"]["cmd"][1]), "run_bind.py")
        self.assertEqual(os.path.basename(u["fallback"]["outputs"][0]), "bound.glb")
        _, off = self.bind_unit(self.cfg("skin_tokens", fallback=False))
        self.assertIsNone(off["fallback"])

    def test_downstream_stages_take_the_bound_body_from_the_binder_not_a_fixed_name(self):
        plan = self.rc.Plan(self.spec, self.cfg("skin_tokens"))
        units = plan.units()
        stills = next(u for u in units if u["stage"] == "stills_body")
        self.assertIn(self.rc.BOUND, stills["cmd"])
        self.assertTrue(all(self.rc.BOUND in u["cmd"] for u in units if u["stage"] == "gear"))
        self.assertTrue(plan.bound.endswith("bound_skintokens.glb"))
        plan_d = self.rc.Plan(self.spec, self.cfg("donor"))
        self.assertTrue(plan_d.bound.endswith("bound.glb"))

    def test_skin_tokens_pass_runs_no_donor_and_records_the_binder(self):
        cfg = self.cfg("skin_tokens")
        plan, u = self.bind_unit(cfg)
        self.fake(u)
        rc, marker, _, _ = self.go(plan, u)
        self.assertEqual(rc, 0)
        self.assertEqual((marker["binder_used"], marker["fallback"], marker["label"]), ("skin_tokens", False, ""))
        self.assertFalse(os.path.exists(u["fallback"]["outputs"][0] + ".ran"))   # the donor never ran
        self.assertTrue(plan.bound.endswith("bound_skintokens.glb"))

    def test_skin_tokens_failure_falls_back_to_the_labelled_donor_bind(self):
        cfg = self.cfg("skin_tokens")
        plan, u = self.bind_unit(cfg)
        self.fake(u, st_mode="fail")
        rc, marker, out, _ = self.go(plan, u)
        self.assertEqual(rc, 0)
        self.assertEqual((marker["binder_used"], marker["fallback"], marker["label"]), ("donor", True, FALLBACK))
        self.assertIn("SkinTokens bind failed", marker["fallback_reason"])
        self.assertIn(FALLBACK, out)
        reason = Path(u["fallback"]["outputs"][0] + ".ran").read_text(encoding="utf-8")   # the donor script got the reason through BIND_FALLBACK_REASON
        self.assertEqual(reason, marker["fallback_reason"])
        self.assertTrue(plan.bound.endswith("bound.glb"))   # later stages read the donor GLB
        self.assertTrue(os.path.exists(plan.bound))
        self.assertEqual(json.loads(Path(plan.work, binder_route.POINTER).read_text(encoding="utf-8"))["label"], FALLBACK)

    def test_missing_prerequisites_skip_straight_to_the_fallback(self):
        cfg = self.cfg("skin_tokens", prereqs=False)
        plan, u = self.bind_unit(cfg)
        self.fake(u)
        rc, marker, out, _ = self.go(plan, u)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(u["outputs"][0] + ".ran"))   # SkinTokens (and its GPU guard) never started
        self.assertEqual((marker["binder_used"], marker["label"]), ("donor", FALLBACK))
        self.assertIn("prerequisites missing", marker["fallback_reason"])

    def test_no_fallback_when_disabled(self):
        cfg = self.cfg("skin_tokens", fallback=False)
        plan, u = self.bind_unit(cfg)
        self.fake(u, st_mode="fail")
        rc, marker, out, _ = self.go(plan, u)
        self.assertEqual(rc, 1)
        self.assertIsNone(marker)
        self.assertFalse(os.path.exists(os.path.join(plan.work, "bound.glb.ran")))
        self.assertFalse(os.path.exists(os.path.join(plan.work, binder_route.POINTER)))

    def test_donor_binder_marker_is_not_a_fallback(self):
        cfg = self.cfg("donor")
        plan, u = self.bind_unit(cfg)
        self.fake(u)
        rc, marker, _, runner = self.go(plan, u)
        self.assertEqual((rc, marker["binder_used"], marker["fallback"], marker["label"]), (0, "donor", False, ""))
        self.assertTrue(runner.done(u))

    def test_a_bind_done_with_the_other_binder_is_not_done(self):
        cfg = self.cfg("donor")
        plan, u = self.bind_unit(cfg)
        self.fake(u)
        self.go(plan, u)
        cfg2 = self.cfg("skin_tokens")
        plan2, u2 = self.bind_unit(cfg2)
        self.assertFalse(self.rc.Runner(plan2).done(u2))   # changing bind.binder re-binds instead of skipping on the old marker

    def test_dry_run_prints_the_binder_and_the_fallback(self):
        t = self.t
        repo = t / "repo"
        for rel in ("tools/run_character.py", "tools/cf_common.py", "tools/binder_route.py", "config/config.json", "characters/vampire-dracula.json"):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, repo / rel)
        shutil.copytree(REPO / "kits", repo / "kits")
        (repo / "config" / "config.local.json").write_text(json.dumps({"python": PY, "outputs_dir": str(t / "out"), "logs_dir": str(t / "logs"), "mixamo_clips_dir": str(t / "clips")}), encoding="utf-8")
        script = str(repo / "tools" / "run_character.py")
        spec = str(repo / "characters" / "vampire-dracula.json")
        outs = {}
        for which in ("donor", "skin_tokens"):
            r = subprocess.run([PY, str(REPO / "tools" / "set_default_binder.py"), which, "--config", str(repo / "config" / "config.json")], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = subprocess.run([PY, script, spec, "--dry-run", "--only", "bind"], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            outs[which] = r.stdout
        self.assertIn("run_bind.py", outs["donor"])
        self.assertIn("binder donor, no fallback", outs["donor"])
        self.assertIn("run_skin_tokens.py", outs["skin_tokens"])
        self.assertIn(FALLBACK, outs["skin_tokens"])
        self.assertIn("guard: run_skin_tokens", outs["skin_tokens"])


class RunBindLabels(unittest.TestCase):
    """The real tools/run_bind.py, stopped by its own preflight (no Blender configured): the failed receipt still carries the binder label."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.t = t
        repo = t / "repo"
        for rel in ("tools/run_bind.py", "tools/cf_common.py", "tools/binder_route.py", "config/config.json"):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, repo / rel)
        (t / "logs").mkdir()
        (t / "mesh.glb").write_bytes(b"glTF")
        loc = {"python": PY, "outputs_dir": str(t / "out"), "logs_dir": str(t / "logs"), "blender_exe": str(t / "none.exe"), "rig_glb": str(t / "none.glb"), "donors_dir": str(t / "none")}
        (repo / "config" / "config.local.json").write_text(json.dumps(loc), encoding="utf-8")
        self.repo = repo

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, **env):
        e = {k: v for k, v in os.environ.items() if k != binder_route.REASON_ENV}
        e.update(env)
        r = subprocess.run([PY, str(self.repo / "tools" / "run_bind.py"), "a1", str(self.t / "mesh.glb")], capture_output=True, text=True, env=e, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        files = sorted((self.repo / "receipts" / "a1").glob("bind-*.json"))
        self.assertEqual(len(files), 1, r.stdout + r.stderr)
        return r, json.loads(files[0].read_text(encoding="utf-8"))

    def test_plain_donor_bind_is_labelled_donor_not_fallback(self):
        _, rec = self.run_it()
        p = rec["params"]
        self.assertEqual((p["binder_used"], p["label"]), ("donor", binder_route.DONOR_LABEL))
        self.assertNotIn("fallback_reason", p)
        self.assertNotIn("FALLBACK", json.dumps(rec))

    def test_fallback_bind_is_labelled_fallback_with_the_reason(self):
        r, rec = self.run_it(BIND_FALLBACK_REASON="SkinTokens bind failed (exit 1)")
        p = rec["params"]
        self.assertEqual((p["binder_used"], p["label"]), ("donor", FALLBACK))
        self.assertEqual(p["fallback_reason"], "SkinTokens bind failed (exit 1)")
        self.assertIn(FALLBACK, r.stdout)


DRIVER = r'''
import json, os, sys
home, sc, plugin = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
os.environ["FACTORY_HOME"] = home
sys.path.insert(0, os.path.join(plugin, "mcp"))
import server
calls = []
def run_stage(tool, cmd, *, cwd=None, env=None, timeout=0, guard=None):
    calls.append({"tool": tool, "script": os.path.basename(cmd[1]), "env": dict(env or {}), "guard": guard})
    return sc["rc"].get(tool, 0), "log.txt", 0.0
def finish(tool, rc, log, t0, stages, asset_id=None, extra=None):
    r = {"tool": tool, "status": "ok" if rc == 0 else "fail", "receipt": None, "error": "" if rc == 0 else "gate failed: relative_to_control", "log": log}
    r.update(extra or {})
    return r
server.run_stage, server.finish = run_stage, finish
server.gate = lambda gpu, heavy, image=None: sc.get("lock", "") if gpu else ""
res = server.t_bind("a1", os.path.join(home, "mesh.glb"), **sc.get("args", {}))
print(json.dumps({"result": res, "calls": calls, "schema": server.TOOLS["bind"][2]}))
'''


class McpBindRouting(unittest.TestCase):
    """plugins/factory/mcp/server.py t_bind: which stage runs, in which order, with which environment; the stage runner is replaced by a recorder."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.t = Path(cls.tmp.name)
        (cls.t / "driver.py").write_text(DRIVER, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def home(self, name, binder, fallback=True, prereqs=True):
        home = self.t / name
        (home / "config").mkdir(parents=True)
        (home / "tools").mkdir()
        shutil.copy(REPO / "tools" / "binder_route.py", home / "tools" / "binder_route.py")
        cfg = json.loads(json.dumps(CFG))
        cfg["bind"]["binder"], cfg["bind"]["fallback_to_donor"] = binder, fallback
        (home / "config" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        (home / "mesh.glb").write_bytes(b"glTF")
        loc = {"python": PY, "outputs_dir": str(home / "out"), "logs_dir": str(home / "logs"), "guard_script": str(home / "g.ps1")}
        if prereqs:
            for n in ("blender.exe", "rig.glb", "py.exe"):
                (home / n).write_bytes(b"x")
            for rel in (cfg["skin_tokens"]["ckpt"], cfg["skin_tokens"]["vae_ckpt"]):
                (home / "st" / rel).parent.mkdir(parents=True, exist_ok=True)
                (home / "st" / rel).write_bytes(b"x")
            ctl = home / cfg["skin_tokens"]["control_json"].format(donor=cfg["skin_tokens"]["control_donor"])
            ctl.parent.mkdir(parents=True, exist_ok=True)
            ctl.write_text("{}", encoding="utf-8")
            loc.update(blender_exe=str(home / "blender.exe"), rig_glb=str(home / "rig.glb"), skin_tokens_repo_dir=str(home / "st"), skin_tokens_python=str(home / "py.exe"))
        (home / "config" / "config.local.json").write_text(json.dumps(loc), encoding="utf-8")
        return home

    def drive(self, home, **sc):
        sc.setdefault("rc", {})
        r = subprocess.run([PY, str(self.t / "driver.py"), str(home), json.dumps(sc), str(PLUGIN)], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.splitlines()[-1])

    def test_donor_binder_runs_only_the_donor_bind_today_s_behaviour(self):
        out = self.drive(self.home("d1", "donor"))
        self.assertEqual([c["tool"] for c in out["calls"]], ["bind"])
        self.assertEqual(out["calls"][0]["script"], "run_bind.py")
        self.assertEqual(out["calls"][0]["env"], {"BIND_FORCE_DONOR": CFG["character"]["default_donor"]})
        res = out["result"]
        self.assertEqual((res["status"], res["binder_used"], res["label"]), ("ok", "donor", binder_route.DONOR_LABEL))
        self.assertNotIn("fallback_reason", res)

    def test_skin_tokens_binder_runs_skin_tokens_and_no_donor(self):
        out = self.drive(self.home("s1", "skin_tokens"))
        self.assertEqual([c["tool"] for c in out["calls"]], ["skin_tokens_bind"])
        self.assertEqual(out["calls"][0]["script"], "run_skin_tokens.py")
        self.assertEqual(out["calls"][0]["guard"], "run_skin_tokens")   # a GPU job never starts without its guard
        self.assertEqual((out["result"]["status"], out["result"]["binder_used"]), ("ok", "skin_tokens"))
        self.assertNotIn("label", out["result"])

    def test_a_skin_tokens_gate_failure_falls_back_and_says_so(self):
        out = self.drive(self.home("s2", "skin_tokens"), rc={"skin_tokens_bind": 1})
        self.assertEqual([c["tool"] for c in out["calls"]], ["skin_tokens_bind", "bind"])
        donor = out["calls"][1]
        self.assertEqual(donor["script"], "run_bind.py")
        self.assertIn("SkinTokens bind failed", donor["env"][binder_route.REASON_ENV])
        res = out["result"]
        self.assertEqual((res["binder_used"], res["label"]), ("donor", FALLBACK))
        self.assertEqual(res["fallback_reason"], donor["env"][binder_route.REASON_ENV])
        self.assertEqual(res["skin_tokens_attempt"]["status"], "fail")
        self.assertIn("relative_to_control", res["skin_tokens_attempt"]["error"])

    def test_no_fallback_when_disabled_the_failure_is_returned(self):
        out = self.drive(self.home("s3", "skin_tokens", fallback=False), rc={"skin_tokens_bind": 1})
        self.assertEqual([c["tool"] for c in out["calls"]], ["skin_tokens_bind"])
        self.assertEqual((out["result"]["status"], out["result"]["binder_used"]), ("fail", "skin_tokens"))

    def test_missing_prerequisites_go_straight_to_the_labelled_donor(self):
        out = self.drive(self.home("s4", "skin_tokens", prereqs=False))
        self.assertEqual([c["tool"] for c in out["calls"]], ["bind"])   # SkinTokens never started
        res = out["result"]
        self.assertEqual((res["binder_used"], res["label"]), ("donor", FALLBACK))
        self.assertIn("prerequisites missing", res["fallback_reason"])
        self.assertIn("ckpt", res["fallback_reason"])

    def test_missing_prerequisites_without_fallback_refuse(self):
        out = self.drive(self.home("s5", "skin_tokens", fallback=False, prereqs=False))
        self.assertEqual(out["calls"], [])
        self.assertEqual(out["result"]["status"], "refused")
        self.assertIn("prerequisites missing", out["result"]["error"])

    def test_a_busy_gpu_refuses_and_never_switches_binder(self):
        out = self.drive(self.home("s6", "skin_tokens"), lock="a GPU job is already running (generate.py)")
        self.assertEqual(out["calls"], [])
        self.assertEqual(out["result"]["status"], "refused")
        self.assertIn("already running", out["result"]["error"])

    def test_the_binder_argument_overrides_config_and_is_validated(self):
        home = self.home("s7", "skin_tokens")
        out = self.drive(home, args={"binder": "donor"})
        self.assertEqual([c["tool"] for c in out["calls"]], ["bind"])
        self.assertEqual(out["result"]["label"], binder_route.DONOR_LABEL)
        out = self.drive(self.home("s8", "donor"), args={"binder": "skin_tokens"})
        self.assertEqual([c["tool"] for c in out["calls"]], ["skin_tokens_bind"])
        out = self.drive(self.home("s9", "donor"), args={"binder": "mixamo"})
        self.assertEqual((out["result"]["status"], out["calls"]), ("refused", []))
        self.assertEqual(out["schema"]["binder"]["enum"], ["skin_tokens", "donor"])   # TOOLS["bind"][2] is the properties dict

    def test_a_bad_config_value_refuses_instead_of_guessing(self):
        out = self.drive(self.home("s10", "skin_token"))
        self.assertEqual((out["result"]["status"], out["calls"]), ("refused", []))
        self.assertIn("bind.binder", out["result"]["error"])


class Docs(unittest.TestCase):
    def test_makehuman_is_named_only_where_it_describes_the_labelled_fallback(self):
        """Prose rule of 0.1.21: MakeHuman/MPFB stay only in the fallback description, the credit, the label and the code of the fallback binder itself."""
        allowed_dirs = {"bind", "receipts"}   # the donor binder's own scripts and old receipts are unchanged
        hits = []
        for p in list(REPO.glob("**/*.md")) + list(REPO.glob("**/*.json")):
            rel = p.relative_to(REPO)
            if rel.parts[0] in allowed_dirs | {".git"} or p.name == "config.local.json":   # config.local.json is the machine's own gitignored paths (donors_dir)
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
                low = line.lower()
                if ("makehuman" in low or "mpfb" in low) and p.name != "test_binder.py":
                    hits.append((rel.as_posix(), n, "fallback" in low or "FALLBACK" in line))
        for rel, n, ok in hits:
            self.assertTrue(ok, f"{rel}:{n} mentions MakeHuman outside the labelled fallback description")


if __name__ == "__main__":
    unittest.main(verbosity=2)
