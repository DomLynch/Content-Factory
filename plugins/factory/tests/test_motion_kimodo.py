"""Offline tests for plugin 0.1.12: the Kimodo backend of `motion_generate` and the local LLM2Vec text-encoder path (standard library only).

No GPU, no Kimodo, no ARDY, no Llama, no torch, no network, no venv. Fake processes run in the real subprocess machinery of tools/run_motion.py (like test_motion.py);
the encoder guards are exercised against fake `torch` and fake package modules injected into sys.modules.
Nothing here proves a real Kimodo generation: the backend is DRAFT until a receipt from a GPU run exists.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_motion as tm  # noqa: E402  (module import only: its TestCase classes are not re-collected here)

PLUGIN, REPO, PY, M, CFG = tm.PLUGIN, tm.REPO, tm.PY, tm.M, tm.CFG
cf_common, pte, run_motion = tm.cf_common, tm.pte, tm.run_motion
REAL = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))["motion"]   # untouched by test_motion's fake sizes
LLAMA = "meta-llama/Meta-Llama-3-8B-Instruct"
FAKE_KIMODO = (tm.FAKE_GEN.replace("ardy_motion.npz", "kimodo_motion.npz").replace('"source": "ardy"', '"source": "kimodo"').replace("J = 27", "J = 77")
               .replace('"cskel27"', '"somaskel77"').replace('"fps": 20.0', '"fps": 30.0').replace('"model": "core40"', '"model": "Kimodo-SOMA-RP-v1.2"'))


def kns(**kw):
    return tm.ns(backend="kimodo", **kw)


def root2d(frames=(0, 30, 60)):
    return {"type": "root2d", "frame_indices": list(frames), "smooth_root_2d": [[0.0, 0.0], [0.5, 0.0], [1.0, 0.1]][:len(frames)]}


def fullbody(frame=60, joints=77, typ="fullbody", **extra):
    return {"type": typ, "frame_indices": [frame], "root_positions": [[0.0, 0.96, 1.5]], "local_joints_rot": [[[0.0, 0.0, 0.0]] * joints], **extra}


class KimodoProblems(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.cfg = dict(CFG, local=tm.fake_local(self.t))
        self.kck = self.t / "models" / "kimodo" / M["kimodo_model_dir"]

    def tearDown(self):
        self.tmp.cleanup()

    def probs(self, args=None, ram=64.0):
        return run_motion.problems(self.cfg, args or kns(), ram)

    def test_a_complete_setup_under_work_dir_has_no_problems_and_needs_no_ardy_keys(self):
        self.assertEqual(self.probs(), [])
        for k in ("motion_python", "ardy_repo_dir", "ardy_checkpoints_dir"):
            self.cfg["local"].pop(k)
        self.assertEqual(self.probs(), [])                                  # Kimodo does not need the ARDY paths
        self.assertTrue(any("motion_python" in p for p in self.probs(tm.ns())))   # and ARDY still does

    def test_defaults_come_from_work_dir_and_keys_override(self):
        sp = run_motion.backend_spec(M, self.cfg["local"], "kimodo")
        self.assertTrue(sp["python"].startswith(str(self.t / "Kimodo-312")), sp["python"])
        self.assertEqual(sp["repo"], str(self.t / "kimodo"))
        self.assertEqual(sp["ckpt"], str(self.t / "models" / "kimodo"))
        sp = run_motion.backend_spec(M, dict(self.cfg["local"], kimodo_python="X:/py.exe", kimodo_repo_dir="X:/k", kimodo_checkpoints_dir="X:/c"), "kimodo")
        self.assertEqual((sp["python"], sp["repo"], sp["ckpt"]), ("X:/py.exe", "X:/k", "X:/c"))
        self.cfg["local"].pop("work_dir")
        self.assertTrue(any("no kimodo_python and no work_dir" in p for p in self.probs()))

    def test_missing_venv_is_refused_with_the_path_and_the_pin(self):
        Path(run_motion.backend_spec(M, self.cfg["local"], "kimodo")["python"]).unlink()
        text = " | ".join(self.probs())
        self.assertIn("Kimodo venv python not found", text)
        self.assertIn(str(self.t / "Kimodo-312"), text)
        self.assertIn("transformers==5.1.0", text)
        self.assertIn("never creates it", text)

    def test_missing_or_short_weights_are_refused_with_the_numbers(self):
        (self.kck / "model.safetensors").write_bytes(b"short")
        cfg = dict(self.cfg, motion=dict(M, kimodo_weights_bytes=1133177724))
        text = " | ".join(run_motion.problems(cfg, kns(), 64.0))
        self.assertIn("are 5 bytes, expected 1133177724", text)
        self.assertIn("model.safetensors", text)
        (self.kck / "model.safetensors").unlink()
        (self.kck / "config.yaml").unlink()
        text = " | ".join(self.probs())
        self.assertIn("Kimodo weights file missing: " + str(self.kck / "model.safetensors"), text)
        self.assertIn("config.yaml", text)

    def test_missing_repo_and_stats_are_named(self):
        shutil.rmtree(self.t / "kimodo")
        shutil.rmtree(self.kck / "stats")
        text = " | ".join(self.probs())
        self.assertIn("kimodo_repo_dir has no kimodo/scripts/generate.py", text)
        self.assertIn("no stats/motion", text)

    def test_text_encoder_refusals_apply_to_kimodo_too(self):
        (self.t / "mirror" / "model-00001-of-00001.safetensors").write_bytes(b"12345")        # the config says 1 byte
        (self.t / "enc" / "McGill-NLP" / pte.ADAPTERS[1] / "adapter_model.safetensors").unlink()
        text = " | ".join(self.probs())
        self.assertIn("mirror shard model-00001-of-00001.safetensors is 5 bytes, the repo's is 1", text)
        self.assertIn(f"adapter file missing: McGill-NLP/{pte.ADAPTERS[1]}/adapter_model.safetensors", text)
        self.assertTrue(any("needs about 24 GB" in p for p in self.probs(kns(encoder_device="cpu"), 10.0)))

    def test_kimodo_clip_id_is_prefixed_and_one_sentence_per_segment(self):
        cid = run_motion.default_clip_id(M, "A person walks forward.", 0, "kimodo")
        self.assertRegex(cid, r"^kimodo-a-person-walks-forward-[0-9a-f]{6}$")
        self.assertRegex(run_motion.default_clip_id(M, "A person walks forward.", 0), r"^ardy-")
        self.assertEqual(run_motion.sentence_count("A person walks. Then turns. "), 2)
        text = " | ".join(self.probs(kns(text="A person walks. Then turns. Then sits.", duration=4.0)))
        self.assertIn("3 sentences give 12.0 s, over 10.0 s", text)
        self.assertEqual(self.probs(kns(text="A person walks. Then turns.", duration=4)), [])


class Constraints(unittest.TestCase):
    N = 120   # 4 s x 30 fps

    def cp(self, raw, n=None):
        return run_motion.constraint_problems(raw, M, n or self.N)

    def test_valid_sets_pass(self):
        self.assertEqual(self.cp([root2d(), fullbody(), fullbody(typ="left-hand", frame=0), fullbody(typ="end-effector", joint_names=["right_foot"], frame=119)]), [])
        self.assertEqual(self.cp([fullbody(joints=30)]), [])

    def test_each_invalid_shape_is_named(self):
        bad = [([], "non-empty JSON list"), ({"type": "root2d"}, "non-empty JSON list"), (["x"], "is not an object"), ([{"type": "teleport", "frame_indices": [0]}], "is not one of"),
               ([{"type": "root2d"}], "frame_indices must be a non-empty list"), ([dict(root2d(), frame_indices=[0, 0, 5])], "unique"),
               ([root2d(frames=(0, 30, 120))], "0 to 119"), ([root2d(frames=(-1, 30, 60))], "0 to 119"),
               ([dict(root2d(), smooth_root_2d=[[0, 0]])], "pairs [x, z]"), ([dict(root2d(), smooth_root_2d=[[0, 0], [0, float("nan")], [1, 1]])], "pairs [x, z]"),
               ([dict(root2d(), global_root_heading=[[1.0]] * 3)], "global_root_heading"),
               ([fullbody(joints=29)], "local_joints_rot must be 1 x (30 or 77)"), ([dict(fullbody(), root_positions=[[0, 1]])], "root_positions must be 1 triples"),
               ([fullbody(typ="end-effector")], "joint_names must be a non-empty list"), ([fullbody(typ="end-effector", joint_names=[""])], "joint_names"),
               ([root2d()] * 9, "limit is 8")]
        for raw, needle in bad:
            probs = self.cp(raw)
            self.assertTrue(any(needle in p for p in probs), (needle, probs))

    def test_a_file_is_read_and_unreadable_ones_are_named(self):
        with tempfile.TemporaryDirectory() as d:
            ok = Path(d) / "c.json"
            ok.write_text(json.dumps([root2d()]), encoding="utf-8")
            self.assertEqual(run_motion.read_constraints(str(ok), M), ([root2d()], []))
            self.assertIn("not found", run_motion.read_constraints(str(Path(d) / "no.json"), M)[1][0])
            ok.write_text("{oops", encoding="utf-8")
            self.assertIn("not JSON", run_motion.read_constraints(str(ok), M)[1][0])
            ok.write_text(" " * 500000, encoding="utf-8")
            self.assertIn("limit is 400000", run_motion.read_constraints(str(ok), M)[1][0])

    def test_constraints_go_through_problems_for_kimodo_only_and_one_sentence(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            cfg = dict(CFG, local=tm.fake_local(t))
            self.assertEqual(run_motion.problems(cfg, kns(constraints=[root2d()], duration=4), 64.0), [])
            self.assertIn("only supported by backend 'kimodo'", " | ".join(run_motion.problems(cfg, tm.ns(constraints=[root2d()]), 64.0)))
            self.assertIn("one-sentence text", " | ".join(run_motion.problems(cfg, kns(constraints=[root2d()], text="A person walks. Then stops."), 64.0)))
            self.assertIn("0 to 59", " | ".join(run_motion.problems(cfg, kns(constraints=[root2d(frames=(0, 30, 60))], duration=2), 64.0)))   # 2 s x 30 fps


class KimodoPipeline(unittest.TestCase):
    """tools/run_motion.py --backend kimodo end to end with fake Kimodo and fake Blender processes."""

    def setUp(self):
        tm.Pipeline.setUp(self)
        (self.t / "faketools" / "motion" / "kimodo_generate.py").write_text(FAKE_KIMODO, encoding="utf-8")
        self.loc["kimodo_python"] = PY   # a runnable python in place of the (never created) Kimodo-312 venv
        (self.t / "config" / "config.local.json").write_text(json.dumps(self.loc), encoding="utf-8")

    tearDown = tm.Pipeline.tearDown
    go = tm.Pipeline.go
    receipts = tm.Pipeline.receipts
    failure = tm.Pipeline.failure

    def test_ok_run_records_the_backend_and_hashes_weights_and_constraints(self):
        con = self.t / "con.json"
        con.write_text(json.dumps([root2d(), fullbody(frame=60)]), encoding="utf-8")
        code = self.go("A person walks forward.", "--backend", "kimodo", "--constraints", str(con), "--clip-id", "kimodo-walk-1", "--install", "--seed", "3", "--duration", "4")
        self.assertEqual(code, 0)
        rec = json.loads(self.receipts()[-1].read_text(encoding="utf-8"))
        self.assertEqual((rec["stage"], rec["status"], rec["error"]), ("motion", "ok", ""))
        self.assertEqual(rec["params"]["backend"], "kimodo")
        self.assertTrue(rec["params"]["constraints"])
        self.assertEqual(rec["params"]["text_encoder_name"], LLAMA)
        self.assertEqual(rec["model"]["id"], "nvidia/" + M["kimodo_model_dir"])
        self.assertIn("Kimodo", rec["tool"])
        self.assertIn("NVIDIA Open Model License", rec["params"]["weights_licence"])
        self.assertIn("nv-tlabs/kimodo", rec["params"]["code_licence"])
        self.assertIn("Licensed by NVIDIA Corporation under the NVIDIA Open Model License", rec["params"]["notice"])
        self.assertIn("Built with Meta Llama 3", rec["params"]["notice"])
        self.assertEqual(rec["params"]["skeleton"], "somaskel77")
        self.assertEqual(rec["params"]["fps"], 30.0)
        names = {Path(o["path"]).name for o in rec["outputs"]}
        self.assertEqual(names, {"kimodo-walk-1.glb", "motion.json", "kimodo_motion.npz"})
        ins = {Path(i["path"]).name for i in rec["inputs"]}
        self.assertTrue({"model.safetensors", "config.yaml", "LICENSE", "constraints.json"} <= ins, ins)   # weights, licence and the constraints are hashed into the receipt
        self.assertTrue((self.t / "clips" / "kimodo-walk-1.glb").is_file())
        log = (self.t / "logs" / "motion_gen_asset1.txt").read_text(encoding="utf-8")
        for needle in ("--model Kimodo-SOMA-RP-v1.2", "--kimodo-repo " + str(self.t / "kimodo"), "--checkpoints-dir " + str(self.t / "models" / "kimodo"), "--llama-id " + LLAMA,
                       "--fps 30", "--diffusion-steps 100", "--constraints " + str(self.t / "out" / "motion" / "asset1" / "kimodo-walk-1" / "constraints.json"),
                       "--text-encoders-dir " + self.loc["text_encoders_dir"], "--device gpu"):
            self.assertIn(needle, log)
        self.assertNotIn("--ardy-repo", log)
        self.assertEqual(os.path.abspath(json.loads((self.t / "enc" / "McGill-NLP" / pte.ADAPTERS[0] / "adapter_config.json").read_text(encoding="utf-8"))["base_model_name_or_path"]),
                         os.path.abspath(self.loc["llama_mirror_dir"]))

    def test_default_clip_id_and_no_constraints_flag_without_constraints(self):
        self.assertEqual(self.go("A person jumps.", "--backend", "kimodo", "--encoder-device", "cuda"), 0)
        log = (self.t / "logs" / "motion_gen_asset1.txt").read_text(encoding="utf-8")
        self.assertNotIn("--constraints", log)
        self.assertIn("--text-encoder-device cuda", log)
        rec = json.loads(self.receipts()[-1].read_text(encoding="utf-8"))
        self.assertTrue(rec["params"]["clip_id"].startswith("kimodo-a-person-jumps-"))

    def test_kimodo_failures_are_failed_receipts_with_the_backend_named(self):
        os.environ["FAKE_GEN_FAIL"] = "1"
        self.assertNotEqual(self.go("A person walks forward.", "--backend", "kimodo"), 0)
        rec = json.loads(self.receipts()[-1].read_text(encoding="utf-8"))
        self.assertEqual((rec["status"], rec["params"]["backend"]), ("fail", "kimodo"))
        self.assertIn("Kimodo generation exit 7", rec["error"])
        os.environ.pop("FAKE_GEN_FAIL")
        os.environ["FAKE_SKEL"] = "cskel27"
        self.go("A person walks forward.", "--backend", "kimodo")
        self.assertIn("skeleton cskel27 is not somaskel77", json.loads(self.receipts()[-1].read_text(encoding="utf-8"))["error"])

    def test_an_unfinished_llama_shard_stops_before_anything_runs(self):
        (self.t / "mirror" / "model-00001-of-00001.safetensors").write_bytes(b"12345")
        code = self.go("A person walks forward.", "--backend", "kimodo")
        self.assertIn("mirror shard model-00001-of-00001.safetensors is 5 bytes, the repo's is 1", code)
        self.assertFalse((self.t / "logs" / "motion_gen_asset1.txt").exists())   # the generator never started
        self.assertEqual(json.loads(self.receipts()[-1].read_text(encoding="utf-8"))["status"], "fail")


class McpKimodo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        home = t / "home"
        (home / "config").mkdir(parents=True)
        (home / "receipts").mkdir()
        shutil.copytree(REPO / "tools", home / "tools", ignore=shutil.ignore_patterns("__pycache__", "upscale", "texture"))
        (home / "config" / "config.json").write_text(json.dumps(CFG), encoding="utf-8")
        cls.loc = tm.fake_local(t)
        (home / "config" / "config.local.json").write_text(json.dumps(cls.loc), encoding="utf-8")
        cls.t, cls.home, cls.lock = t, home, t / ".gpu_lock"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def call(self, args, **env):
        e = dict(os.environ, FACTORY_HOME=str(self.home), FACTORY_GPU_LOCK=str(self.lock), **{k: str(v) for k, v in env.items()})
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "motion_generate", "arguments": args}}
        r = subprocess.run([PY, str(PLUGIN / "mcp" / "server.py")], input=json.dumps(msg) + "\n", capture_output=True, text=True, env=e, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        res = json.loads(r.stdout.splitlines()[0])["result"]
        return res["isError"], res["content"][0]["text"]

    def test_the_tool_schema_has_backend_and_constraints(self):
        r = subprocess.run([PY, str(PLUGIN / "mcp" / "server.py")], input=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n", capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, FACTORY_HOME=str(self.home)))
        tool = {x["name"]: x for x in json.loads(r.stdout)["result"]["tools"]}["motion_generate"]
        props = tool["inputSchema"]["properties"]
        self.assertEqual(props["backend"]["enum"], ["ardy", "kimodo"])
        self.assertIn("constraints", props)
        self.assertEqual(tool["inputSchema"]["required"], ["text"])

    def test_refusals_pass_through_the_gates_with_numbers_and_paths(self):
        # live GPU lock -> refused before the backend is even looked at
        self.lock.write_text(json.dumps({"pid": os.getpid(), "job": "flux_kit", "started_utc": "2026-11-01T00:00:00Z"}), encoding="utf-8")
        try:
            err, text = self.call({"text": "A person walks forward.", "backend": "kimodo"}, FACTORY_FAKE_FREE_RAM_GB=64)
            self.assertTrue(err)
            self.assertIn("already running", text)
            print("\nREFUSAL (kimodo, live GPU lock):", json.loads(text)["error"][:150])
            err, text = self.call({"text": "A person walks forward.", "backend": "kimodo", "device": "cpu", "duration": 99}, FACTORY_FAKE_FREE_RAM_GB=64)
            self.assertNotIn("already running", text)   # a CPU run does not need the lock
            self.assertIn("outside 1 to 10", text)
        finally:
            self.lock.unlink()
        # low RAM
        err, text = self.call({"text": "A person walks forward.", "backend": "kimodo"}, FACTORY_FAKE_FREE_RAM_GB=2.0)
        self.assertIn("2.0 GB of RAM is free", text)
        # weights of the wrong size (the server's config says 1 byte; make it 5)
        w = Path(self.loc["work_dir"]) / "models" / "kimodo" / M["kimodo_model_dir"] / "model.safetensors"
        w.write_bytes(b"12345")
        try:
            err, text = self.call({"text": "A person walks forward.", "backend": "kimodo"}, FACTORY_FAKE_FREE_RAM_GB=64)
        finally:
            w.write_bytes(b"k")
        self.assertTrue(err)
        self.assertEqual(json.loads(text)["status"], "refused")
        self.assertIn("are 5 bytes, expected 1", text)
        self.assertIn(str(w), json.loads(text)["error"])
        print("REFUSAL (kimodo, wrong weight size):", json.loads(text)["error"][:230])
        # missing venv
        py = Path(run_motion.backend_spec(M, self.loc, "kimodo")["python"])
        py.rename(str(py) + ".off")
        try:
            err, text = self.call({"text": "A person walks forward.", "backend": "kimodo"}, FACTORY_FAKE_FREE_RAM_GB=64)
        finally:
            Path(str(py) + ".off").rename(py)
        self.assertIn("Kimodo venv python not found", text)
        print("REFUSAL (kimodo, no venv):", json.loads(text)["error"][:260])

    def test_constraints_are_validated_before_anything_starts(self):
        for args, needle in (({"constraints": [root2d(frames=(0, 30, 999))]}, "0 to 119"), ({"constraints": [{"type": "x", "frame_indices": [0]}]}, "is not one of"),
                             ({"constraints": "no/such/file.json"}, "constraints file not found"), ({"constraints": [root2d()], "backend": "ardy"}, "only supported by backend 'kimodo'")):
            err, text = self.call({"text": "A person walks forward.", "backend": "kimodo", **args}, FACTORY_FAKE_FREE_RAM_GB=64)
            self.assertTrue(err, args)
            self.assertEqual(json.loads(text)["status"], "refused")
            self.assertIn(needle, text, args)

    def test_a_kimodo_cpu_call_passes_constraints_and_backend_to_the_script(self):
        # a fake run_motion.py records its argv, to prove the server's plumbing (inline constraints become a file, the backend flag is passed, licences name Kimodo)
        fake = self.home / "tools" / "run_motion.py"
        orig = fake.read_text(encoding="utf-8")
        (self.home / "tools" / "_orig_run_motion.py").write_text(orig, encoding="utf-8")
        fake.write_text(os.linesep.join([
            "import sys, json, os",
            "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))",
            "import _orig_run_motion as _o",
            "problems = _o.problems",
            "if __name__ == '__main__':",
            "    d = os.path.join(r'" + str(self.home) + "', 'receipts', sys.argv[1]); os.makedirs(d, exist_ok=True)",
            "    con = sys.argv[sys.argv.index('--constraints') + 1]",
            "    rec = {'schema': 1, 'asset_id': sys.argv[1], 'stage': 'motion', 'status': 'ok', 'error': '', 'outputs': [{'path': 'x/clip.glb'}], 'timing': {'wall_s': 1},",
            "           'memory': {'lowest_free_ram_gb': 9}, 'params': {'clip_id': 'kimodo-x', 'stills_use': 'u', 'frames': 120, 'fps': 30.0, 'installed_clip': '', 'argv': sys.argv[1:], 'con': json.load(open(con))}}",
            "    json.dump(rec, open(os.path.join(d, 'motion-20261101T000001Z.json'), 'w'))",
        ]) + os.linesep, encoding="utf-8")
        try:
            err, text = self.call({"text": "A person walks forward.", "asset_id": "assetK", "device": "cpu", "backend": "kimodo", "constraints": [root2d()]}, FACTORY_FAKE_FREE_RAM_GB=64)
        finally:
            fake.write_text(orig, encoding="utf-8")
        self.assertFalse(err, text)
        res = json.loads(text)
        self.assertEqual((res["status"], res["clip_id"], res["fps"]), ("ok", "kimodo-x", 30.0))
        self.assertIn("Kimodo code Apache-2.0", res["licences"])
        self.assertIn("Built with Meta Llama 3", res["licences"])
        self.assertIn("never run on a GPU", res["tier_note"])
        p = json.loads(Path(res["receipt"]).read_text(encoding="utf-8"))["params"]
        self.assertIn("kimodo", p["argv"])
        self.assertEqual(p["con"], [root2d()])


class KimodoHooks(unittest.TestCase):
    run_hook = tm.MotionHooks.run_hook
    denied = tm.MotionHooks.denied

    def test_hooks_json_covers_kimodo_generate_for_bash_and_powershell(self):
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        flat = [(g["matcher"], h) for g in hooks for h in g["hooks"]]
        for script in ("gpu_lock_guard.py", "ram_gate.py", "plugin_guard.py"):
            for shell in ("Bash", "PowerShell"):
                self.assertTrue(any(script in h["command"] and h.get("if") == f"{shell}(*kimodo_generate*)" and m == shell and h["timeout"] <= 15 for m, h in flat), (script, shell))

    def test_gpu_lock_ram_gate_and_plugin_guard_refuse_raw_kimodo_starts(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / ".gpu_lock"
            lock.write_text(json.dumps({"pid": os.getpid(), "job": "generate.py", "started_utc": "x"}), encoding="utf-8")
            cmd = "python tools/motion/kimodo_generate.py --text x"
            self.assertTrue(self.denied(self.run_hook("gpu_lock_guard.py", {"tool_name": "Bash", "tool_input": {"command": cmd}}, FACTORY_GPU_LOCK=lock)))
            self.assertIsNone(self.run_hook("gpu_lock_guard.py", {"tool_name": "Bash", "tool_input": {"command": "cat tools/motion/kimodo_generate.py"}}, FACTORY_GPU_LOCK=lock))
        low = dict(FACTORY_FAKE_FREE_RAM_GB=1.5, FACTORY_HOME=REPO)
        self.assertTrue(self.denied(self.run_hook("ram_gate.py", {"tool_name": "Bash", "tool_input": {"command": cmd}}, **low)))
        sys.path.insert(0, str(PLUGIN / "hooks"))
        import plugin_guard
        self.assertIn("motion_generate", plugin_guard.decide({"tool_name": "Bash", "tool_input": {"command": cmd}}))
        self.assertEqual(plugin_guard.decide({"tool_name": "Bash", "tool_input": {"command": "git commit -m 'kimodo_generate.py notes'"}}), "")

    def test_a_running_kimodo_stage_is_found_by_the_process_sweep(self):
        sys.path.insert(0, str(PLUGIN / "lib"))
        import cf_proc
        self.assertIn("kimodo_generate.py", cf_proc.STAGE_SCRIPTS)
        self.assertTrue(cf_proc._STAGE_RE.search("python.exe -u C:/x/tools/motion/kimodo_generate.py --text hi"))


class RetargetTable(unittest.TestCase):
    SRC = REPO / "tools" / "motion" / "retarget_blender.py"

    def table(self):
        for n in ast.parse(self.SRC.read_text(encoding="utf-8")).body:
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "SKELETON_TABLES":
                return ast.literal_eval(n.value)
        self.fail("SKELETON_TABLES not found in retarget_blender.py")

    def test_the_builtin_tables_equal_the_config_tables(self):
        cfg = REAL["retarget"]["skeletons"]
        for name, spec in self.table().items():
            self.assertEqual({k: v for k, v in cfg[name].items() if k != "_comment"}, spec, name)
        self.assertIn("UNRUN", self.SRC.read_text(encoding="utf-8"))

    def test_every_soma_source_name_exists_in_kimodos_77_joint_list(self):
        defs = Path(os.path.expanduser("~")) / "Documents" / "kimodo" / "kimodo" / "skeleton" / "definitions.py"
        if not defs.is_file():
            self.skipTest("Kimodo checkout not on this machine")
        text = defs.read_text(encoding="utf-8")
        start = text.index("class SOMASkeleton77")
        joints = set(re.findall(r'\("(\w+)", (?:None|"\w+")\)', text[start:text.index("@property", start)]))
        self.assertEqual(len(joints), 77)
        spec = self.table()["somaskel77"]
        want = set(spec["center"].values())
        for side in ("Left", "Right"):
            want |= {side + n for n in spec["direct"]} | {side + s for s in spec["renamed_sided"].values()}
            want |= {f"{side}Hand{f}{i}" for f in ("Thumb", "Index", "Middle", "Ring", "Pinky") for i in (1, 2, 3)}
        self.assertEqual(sorted(want - joints), [])


class FakeModule(types.ModuleType):
    pass


class EncoderGuards(unittest.TestCase):
    """encoder_patch.patch() against fake torch and a fake package: the chat-template name is forced and cpu stays cpu."""

    def setUp(self):
        sys.path.insert(0, str(REPO / "tools" / "motion"))
        self.saved = set(sys.modules)
        self.had_torch = sys.modules.get("torch")
        torch = FakeModule("torch")
        torch.cuda = types.SimpleNamespace(is_available=lambda: True)
        sys.modules["torch"] = torch

        class Cfg: _name_or_path = "/local/llama-mirror"

        class Inner: config = Cfg()

        class LLM2Vec:
            model = Inner()

            @classmethod
            def from_pretrained(cls, *a, **k):
                return cls()

        class Feat:
            def __init__(self, dev="cpu"): self.dev = dev
            def to(self, dev): return Feat(dev)

        class Enc:
            moved = []

            def to(self, device=None, *rest, **kw):
                Enc.moved.append(str(device))
                return self

            def __call__(self, text):
                return Feat(), [1]

        for name in ("fakepkg", "fakepkg.model", "fakepkg.model.llm2vec"):
            sys.modules[name] = FakeModule(name)
        l2v, wrap = FakeModule("fakepkg.model.llm2vec.llm2vec"), FakeModule("fakepkg.model.llm2vec.llm2vec_wrapper")
        l2v.LLM2Vec, wrap.LLM2VecEncoder = LLM2Vec, Enc
        sys.modules[l2v.__name__], sys.modules[wrap.__name__] = l2v, wrap
        self.l2v, self.Enc = l2v, Enc

    def tearDown(self):
        for name in set(sys.modules) - self.saved:
            del sys.modules[name]
        if self.had_torch is not None:
            sys.modules["torch"] = self.had_torch

    def test_the_llama_name_is_forced_after_loading_and_cpu_stays_cpu(self):
        import encoder_patch
        encoder_patch.patch("fakepkg", LLAMA, "cpu")
        m = self.l2v.LLM2Vec.from_pretrained("anything")
        self.assertEqual(m.model.config._name_or_path, LLAMA)
        enc = self.Enc()
        enc.to("cuda")
        self.assertEqual(self.Enc.moved[-1], "cpu")
        feat, _ = enc("hi")
        self.assertEqual(feat.dev, "cuda")   # the embeddings go to the model device after a CPU encode

    def test_without_cpu_pinning_the_device_is_left_alone(self):
        import encoder_patch
        encoder_patch.patch("fakepkg", LLAMA, "")
        self.Enc().to("cuda")
        self.assertEqual(self.Enc.moved[-1], "cuda")


class Packaging(unittest.TestCase):
    def test_real_config_has_the_repo_shard_sizes_and_the_pinned_names(self):
        self.assertEqual(REAL["llama_shard_bytes"], {"model-00001-of-00004.safetensors": 4976698672, "model-00002-of-00004.safetensors": 4999802720,
                                                     "model-00003-of-00004.safetensors": 4915916176, "model-00004-of-00004.safetensors": 1168138808})
        self.assertEqual(REAL["text_encoder_name"], LLAMA)
        self.assertEqual(REAL["backends_enabled"], ["ardy", "kimodo"])
        self.assertEqual(REAL["backend"], "ardy")
        self.assertEqual((REAL["kimodo_model_dir"], REAL["kimodo_venv_name"], REAL["kimodo_expected_skeleton"]), ("Kimodo-SOMA-RP-v1.2", "Kimodo-312", "somaskel77"))

    def test_a_short_shard_message_carries_both_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            mirror = Path(d)
            (mirror / "config.json").write_text("{}", encoding="utf-8")
            (mirror / "LICENSE").write_text("x", encoding="utf-8")
            shards = REAL["llama_shard_bytes"]
            (mirror / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {f"w{i}": s for i, s in enumerate(shards)}}), encoding="utf-8")
            for s in shards:
                (mirror / s).write_bytes(b"x" * 7)
            probs = pte.mirror_problems(str(mirror), shards)
            self.assertEqual(len(probs), 4)
            self.assertIn("mirror shard model-00002-of-00004.safetensors is 7 bytes, the repo's is 4999802720", " | ".join(probs))
            self.assertEqual(pte.mirror_problems(str(mirror)), [])   # without sizes only presence is checked (the old behaviour)

    def test_example_local_config_has_the_kimodo_venv_and_never_the_trellis_one(self):
        ex = json.loads((REPO / "config" / "config.local.example.json").read_text(encoding="utf-8"))
        self.assertIn("Kimodo-312", ex["kimodo_python"])
        self.assertNotEqual(ex["kimodo_python"], ex["motion_python"])
        self.assertNotIn("Trellis", ex["kimodo_python"])

    def test_skill_yaml_parses_keeps_the_draft_banner_and_documents_both_backends(self):
        text = (PLUGIN / "skills" / "motion-generate" / "SKILL.md").read_text(encoding="utf-8")
        head = text.split("---\n")[1]
        self.assertTrue(head.startswith("name: motion-generate\ndescription: \""))
        self.assertTrue(head.rstrip().endswith('"'))
        try:
            import yaml
        except ImportError:
            yaml = None
        if yaml:
            meta = yaml.safe_load(head)
            self.assertEqual(meta["name"], "motion-generate")
            self.assertIn("Kimodo", meta["description"])
        else:
            desc = head.split('description: "', 1)[1].rstrip()[:-1]
            self.assertNotIn('"', desc.replace('\\"', ""))   # one quoted scalar, no stray quote inside
        for needle in ("STATUS: DRAFT, not yet run end to end", "backend", "Kimodo-312", "LLM2Vec", "meta-llama/Meta-Llama-3-8B-Instruct", "Built with Meta Llama 3", "constraints"):
            self.assertIn(needle, text)

    def test_notices_cover_kimodo_and_llama_licences(self):
        text = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("Licensed by NVIDIA Corporation under the NVIDIA Open Model License", text)
        self.assertIn("Built with Meta Llama 3", text)
        self.assertIn("Kimodo-SOMA-RP-v1.2", text)
        self.assertIn("987057aca9b5c6f338cadfcc2666d74a093c43e6fd02173c5d9ed63ec66cc53d", text)
        self.assertIn("Meta Llama 3 Community License", text)
        self.assertIn("Apache License 2.0", text)

    def test_generators_never_touch_a_token_and_steer_only_offline_local_env(self):
        for f, pkg in (("kimodo_generate.py", "kimodo"), ("ardy_generate.py", "ardy")):
            text = (REPO / "tools" / "motion" / f).read_text(encoding="utf-8")
            for needle in ('TEXT_ENCODER_MODE"] = "local"', 'HF_HUB_OFFLINE"] = "1"', "TEXT_ENCODERS_DIR", "TEXT_ENCODER_DEVICE", "meta-llama/Meta-Llama-3-8B-Instruct"):
                self.assertIn(needle, text, (f, needle))
            self.assertNotIn("login(", text)
            self.assertNotIn("HF_TOKEN", text.replace("never needs, reads or sends a token", ""))
        self.assertIn('CHECKPOINT_DIR"] = a.checkpoints_dir', (REPO / "tools" / "motion" / "kimodo_generate.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
