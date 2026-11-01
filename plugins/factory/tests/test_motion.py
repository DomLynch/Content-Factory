"""Offline tests for plugin 0.1.8 `motion_generate` (standard library only; run: python -m unittest discover plugins/factory/tests -v).

No GPU, no ARDY, no Llama, no network. The pipeline is exercised with FAKE processes: a fake ardy_generate.py and a fake retarget script run in the real subprocess
machinery of tools/run_motion.py, against a throw-away repo root (cf_common.REPO is pointed at a temp folder, so nothing is written into this checkout).
The one test that needs real Blender is skipped unless Blender is installed and CF_TEST_BLENDER=1.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tools" / "motion"))
sys.path.insert(0, str(PLUGIN / "lib"))
import cf_common  # noqa: E402
import prepare_text_encoder as pte  # noqa: E402
import run_motion  # noqa: E402

PY = sys.executable
MCP_P = "mcp__plugin_factory_factory__"
CFG = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))
M = CFG["motion"]
PRE = M["bone_prefix"]
GATED = "meta-llama/Meta-Llama-3-8B-Instruct"


def make_glb(path: Path, *, bones=None, anim_name="clip", channels=None, keys=20, skin=False) -> str:
    """A minimal GLB shaped like a Mixamo clip: bone-named nodes, one animation, no mesh."""
    bones = bones if bones is not None else [PRE + n for n in ["Hips"] + M["retarget"]["required_bones"][1:]]
    nodes = [{"name": b} for b in bones]
    ch = channels if channels is not None else 2 * len(bones)
    j = {"asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}], "nodes": nodes,
         "accessors": [{"count": keys, "componentType": 5126, "type": "SCALAR"}],
         "animations": [{"name": anim_name, "samplers": [{"input": 0, "output": 0}],
                         "channels": [{"sampler": 0, "target": {"node": i % max(1, len(nodes)), "path": "rotation"}} for i in range(ch)]}]}
    if skin:
        j["skins"] = [{"joints": [0]}]
    blob = json.dumps(j).encode()
    blob += b" " * (-len(blob) % 4)
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + 8 + len(blob)) + struct.pack("<II", len(blob), 0x4E4F534A) + blob)
    return str(path)


def fake_local(t: Path, **over) -> dict:
    """A config.local.json whose motion paths all exist (as empty or tiny files)."""
    ck = t / "ardy" / M["ardy_model_dir"]
    ck.mkdir(parents=True, exist_ok=True)
    for f in ("config.yaml", "denoiser.safetensors", "tokenizer.safetensors", "LICENSE"):
        (ck / f).write_bytes(b"x")
    enc = t / "enc" / "McGill-NLP"
    mirror = t / "mirror"
    mirror.mkdir(exist_ok=True)
    (mirror / "config.json").write_text("{}", encoding="utf-8")
    (mirror / "LICENSE").write_text("Meta Llama 3 Community License", encoding="utf-8")
    (mirror / "model-00001-of-00001.safetensors").write_bytes(b"w")
    (mirror / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "model-00001-of-00001.safetensors"}}), encoding="utf-8")
    for a in pte.ADAPTERS:
        d = enc / a
        d.mkdir(parents=True, exist_ok=True)
        (d / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": GATED, "r": 16}), encoding="utf-8")
        (d / "adapter_model.safetensors").write_bytes(b"a")
    for f in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (enc / pte.ADAPTERS[0] / f).write_text("{}", encoding="utf-8")
    (t / "clips").mkdir(exist_ok=True)
    (t / "rig.glb").write_bytes(b"glTF")
    (t / "blender.exe").write_bytes(b"x")
    (t / "logs").mkdir(exist_ok=True)
    loc = {"python": PY, "outputs_dir": str(t / "out"), "logs_dir": str(t / "logs"), "blender_exe": str(t / "blender.exe"), "rig_glb": str(t / "rig.glb"),
           "mixamo_clips_dir": str(t / "clips"), "motion_python": PY, "ardy_repo_dir": str(t), "ardy_checkpoints_dir": str(t / "ardy"), "text_encoders_dir": str(t / "enc"),
           "llama_mirror_dir": str(mirror), "guard_script": str(t / "no_guard.ps1"), "machine": {"name": "test"}}
    loc.update(over)
    return loc


def ns(**kw) -> argparse.Namespace:
    d = dict(asset_id="a", text="A person walks forward.", duration=None, seed=None, clip_id="", in_place=False, install=False, device="gpu", encoder_device="", backend="ardy")
    d.update(kw)
    return argparse.Namespace(**d)


class Problems(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.cfg = dict(CFG, local=fake_local(self.t))

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_complete_setup_has_no_problems(self):
        self.assertEqual(run_motion.problems(self.cfg, ns(), 64.0), [])

    def test_each_refusal_names_its_reason(self):
        cases = [(ns(text="  "), "text is empty"), (ns(text="x" * 301), "limit is 300"), (ns(duration=0.2), "outside 1 to 10"), (ns(duration=60), "outside 1 to 10"),
                 (ns(duration=float("nan")), "outside 1 to 10"), (ns(clip_id="../evil"), "not a plain file name"), (ns(backend="kimodo"), "not enabled"),
                 (ns(encoder_device="cpu"), "needs about 24 GB")]
        for args, needle in cases:
            probs = run_motion.problems(self.cfg, args, 10.0)
            self.assertTrue(any(needle in p for p in probs), (needle, probs))

    def test_missing_weights_mirror_or_rig_are_named(self):
        (self.t / "ardy" / M["ardy_model_dir"] / "denoiser.safetensors").unlink()
        (self.t / "mirror" / "model-00001-of-00001.safetensors").unlink()
        (self.t / "rig.glb").unlink()
        text = " | ".join(run_motion.problems(self.cfg, ns(), 64.0))
        self.assertIn("denoiser.safetensors", text)
        self.assertIn("mirror shard missing", text)
        self.assertIn("rig_glb not found", text)

    def test_install_never_overwrites(self):
        cid = "ardy-walk"
        (self.t / "clips" / (cid + ".glb")).write_bytes(b"old")
        probs = run_motion.problems(self.cfg, ns(install=True, clip_id=cid), 64.0)
        self.assertTrue(any("would overwrite" in p for p in probs), probs)

    def test_default_clip_id_is_a_stable_plain_name(self):
        a = run_motion.default_clip_id(M, "A person walks forward!", 0)
        self.assertEqual(a, run_motion.default_clip_id(M, "A person walks forward!", 0))
        self.assertNotEqual(a, run_motion.default_clip_id(M, "A person walks forward!", 1))
        self.assertRegex(a, r"^ardy-a-person-walks-forward-[0-9a-f]{6}$")
        self.assertTrue(run_motion.CLIP_ID.match(a))


class TextEncoderPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.loc = fake_local(self.t)

    def tearDown(self):
        self.tmp.cleanup()

    def cfgs(self):
        return [self.t / "enc" / "McGill-NLP" / a / "adapter_config.json" for a in pte.ADAPTERS]

    def test_apply_points_both_adapters_at_the_local_mirror_and_is_idempotent(self):
        self.assertTrue(pte.check(self.loc["text_encoders_dir"], self.loc["llama_mirror_dir"]))   # not ready yet
        self.assertEqual(pte.apply(self.loc["text_encoders_dir"], self.loc["llama_mirror_dir"]), [])
        for p in self.cfgs():
            d = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(os.path.abspath(d["base_model_name_or_path"]), os.path.abspath(self.loc["llama_mirror_dir"]))
            self.assertEqual(d["r"], 16)   # nothing else changed
            self.assertEqual(json.loads(Path(str(p) + ".orig").read_text(encoding="utf-8"))["base_model_name_or_path"], GATED)
        before = [p.read_text(encoding="utf-8") for p in self.cfgs()]
        self.assertEqual(pte.apply(self.loc["text_encoders_dir"], self.loc["llama_mirror_dir"]), [])
        self.assertEqual(before, [p.read_text(encoding="utf-8") for p in self.cfgs()])
        self.assertEqual(pte.check(self.loc["text_encoders_dir"], self.loc["llama_mirror_dir"]), [])

    def test_an_unfinished_mirror_is_refused_and_nothing_is_touched(self):
        (self.t / "mirror" / "model-00001-of-00001.safetensors").unlink()
        probs = pte.apply(self.loc["text_encoders_dir"], self.loc["llama_mirror_dir"])
        self.assertTrue(any("shard missing" in p for p in probs), probs)
        self.assertFalse(any(Path(str(p) + ".orig").exists() for p in self.cfgs()))
        self.assertEqual(json.loads(self.cfgs()[0].read_text(encoding="utf-8"))["base_model_name_or_path"], GATED)

    def test_an_unexpected_base_is_left_alone(self):
        self.cfgs()[0].write_text(json.dumps({"base_model_name_or_path": "somebody/else"}), encoding="utf-8")
        probs = pte.apply(self.loc["text_encoders_dir"], self.loc["llama_mirror_dir"])
        self.assertTrue(any("not touching it" in p for p in probs), probs)

    def test_ardy_generate_steers_only_offline_local_encoder_env(self):
        text = (REPO / "tools" / "motion" / "ardy_generate.py").read_text(encoding="utf-8")
        for needle in ('TEXT_ENCODER_MODE"] = "local"', 'HF_HUB_OFFLINE"] = "1"', "TEXT_ENCODERS_DIR", "TEXT_ENCODER_DEVICE", "meta-llama/Meta-Llama-3-8B-Instruct"):
            self.assertIn(needle, text)
        self.assertNotIn("HF_TOKEN", text.replace("never needs, reads or sends a token", ""))   # the wrapper never touches a token
        self.assertNotIn("login(", text)


class ClipCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_mixamo_shaped_clip_passes(self):
        self.assertEqual(run_motion.check_clip(make_glb(self.t / "ok.glb"), M, 40), [])

    def test_bad_clips_are_named(self):
        for kw, needle in (({"anim_name": "Walk"}, "named 'Walk'"), ({"bones": ["Armature"]}, "no mixamorig"), ({"bones": [PRE + "Spine"]}, "no mixamorig:Hips"),
                           ({"skin": True}, "skin or mesh"), ({"channels": 4}, "only 4 channels"), ({"keys": 3}, "3 keys for 40")):
            probs = run_motion.check_clip(make_glb(self.t / "bad.glb", **kw), M, 40)
            self.assertTrue(any(needle in p for p in probs), (kw, probs))
        (self.t / "junk.glb").write_bytes(b"not a glb")
        self.assertIn("clip unreadable", run_motion.check_clip(str(self.t / "junk.glb"), M, 40)[0])


FAKE_GEN = r'''
import json, sys, os
a = sys.argv
out = a[a.index("--out-dir") + 1]
if os.environ.get("FAKE_GEN_FAIL"):
    print("boom"); sys.exit(7)
os.makedirs(out, exist_ok=True)
J = 27
doc = {"schema": 1, "source": "ardy", "model": "core40", "text": a[a.index("--text") + 1], "seed": 0, "fps": 20.0, "frames": 40, "skeleton": os.environ.get("FAKE_SKEL", "cskel27"),
       "joints": ["j%d" % i for i in range(J)], "parents": [-1] + [0] * (J - 1), "rest_positions": [[0, 0, 0]] * J,
       "global_rot": [[[1, 0, 0, 0, 1, 0, 0, 0, 1]] * J] * 40, "root_positions": [[0, 1.0, 0]] * 40}
if os.environ.get("FAKE_NAN"):
    doc["root_positions"][3] = [float("nan"), 0, 0]
json.dump(doc, open(os.path.join(out, "motion.json"), "w"))
open(os.path.join(out, "ardy_motion.npz"), "wb").write(b"npz")
print("seen args:", " ".join(a[1:]))
'''
FAKE_RETARGET = r'''
import json, sys, os
sys.path.insert(0, os.environ["FAKE_TESTS_DIR"])
from test_motion import make_glb
from pathlib import Path
A = json.load(open(sys.argv[sys.argv.index("--") + 1]))
if os.environ.get("FAKE_RETARGET_FAIL"):
    sys.exit(9)
os.makedirs(os.path.dirname(A["out_glb"]), exist_ok=True)
make_glb(Path(A["out_glb"]), keys=int(os.environ.get("FAKE_KEYS", "40")))
json.dump({"frames": 40, "mapped_bones": ["Hips"]}, open(A["result"], "w"))
'''


class Pipeline(unittest.TestCase):
    """tools/run_motion.py end to end with fake ARDY and fake Blender processes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        (self.t / "config").mkdir()
        (self.t / "config" / "config.json").write_text(json.dumps(CFG), encoding="utf-8")
        self.loc = fake_local(self.t)
        # a fake blender wrapper: `blender --background --python <script> -- <args>` runs the script with the same python
        if os.name == "nt":
            self.blender = self.t / "blender.cmd"
            self.blender.write_text(f'@echo off\r\n"{PY}" %3 -- %5\r\n', encoding="utf-8")
        else:
            self.blender = self.t / "blender.sh"
            self.blender.write_text(f'#!/bin/sh\nexec "{PY}" "$3" -- "$5"\n', encoding="utf-8")
            self.blender.chmod(0o755)
        self.loc["blender_exe"] = str(self.blender)
        (self.t / "config" / "config.local.json").write_text(json.dumps(self.loc), encoding="utf-8")
        tools = self.t / "faketools" / "motion"
        tools.mkdir(parents=True)
        (tools / "ardy_generate.py").write_text(FAKE_GEN, encoding="utf-8")
        (tools / "retarget_blender.py").write_text(FAKE_RETARGET, encoding="utf-8")
        self.saved = (cf_common.REPO, run_motion.HERE, dict(os.environ))
        cf_common.REPO = str(self.t)
        run_motion.HERE = str(self.t / "faketools")
        os.environ["FAKE_TESTS_DIR"] = str(Path(__file__).resolve().parent)

    def tearDown(self):
        cf_common.REPO, run_motion.HERE = self.saved[0], self.saved[1]
        os.environ.clear()
        os.environ.update(self.saved[2])
        self.tmp.cleanup()

    def go(self, *argv):
        with self.assertRaises(SystemExit) as cm:
            run_motion.main(["asset1", *argv])
        return cm.exception.code

    def receipts(self):
        return sorted((self.t / "receipts" / "asset1").glob("motion-*.json"))

    def test_ok_run_writes_a_clip_a_receipt_and_installs_without_overwriting(self):
        code = self.go("A person walks forward.", "--clip-id", "ardy-walk-1", "--install", "--seed", "3", "--duration", "4")
        self.assertEqual(code, 0)
        rec = json.loads(self.receipts()[-1].read_text(encoding="utf-8"))
        self.assertEqual((rec["schema"], rec["stage"], rec["status"], rec["error"]), (1, "motion", "ok", ""))
        self.assertEqual(rec["model"]["id"], "nvidia/" + M["ardy_model_dir"])
        self.assertEqual(rec["params"]["seed"], 3)
        self.assertEqual(rec["params"]["clip_id"], "ardy-walk-1")
        self.assertIn("Built with Meta Llama 3", rec["params"]["notice"])
        self.assertIn("NVIDIA Open Model", rec["params"]["weights_licence"])
        self.assertEqual(len(rec["params"]["llama_mirror_licence_sha256"]), 64)
        outs = {Path(o["path"]).name: o["sha256"] for o in rec["outputs"]}
        self.assertEqual(set(outs), {"ardy-walk-1.glb", "motion.json", "ardy_motion.npz"})
        self.assertTrue(all(len(h) == 64 for h in outs.values()))
        self.assertTrue(any(Path(i["path"]).name == "denoiser.safetensors" for i in rec["inputs"]))   # the weights are hashed into the receipt
        self.assertIn('stills.clips', rec["params"]["stills_use"])
        self.assertTrue((self.t / "clips" / "ardy-walk-1.glb").is_file())
        self.assertEqual(run_motion.check_clip(str(self.t / "clips" / "ardy-walk-1.glb"), M, 40), [])
        self.assertTrue(rec["timing"]["wall_s"] >= 0 and "lowest_free_ram_gb" in rec["memory"])
        # the same clip id again with --install must refuse before anything runs
        self.assertEqual(self.go("A person walks forward.", "--clip-id", "ardy-walk-1", "--install"), "cannot generate motion:\n  --install would overwrite an existing clip: "
                         + str(self.t / "clips" / "ardy-walk-1.glb"))
        self.assertEqual(json.loads(self.receipts()[-1].read_text(encoding="utf-8"))["status"], "fail")

    def test_the_run_passes_the_encoder_settings_and_no_token(self):
        os.environ["HF_TOKEN"] = "must-not-reach-the-child"
        self.go("A person jumps.", "--encoder-device", "cuda")
        text = (self.t / "logs" / "motion_gen_asset1.txt").read_text(encoding="utf-8")
        for needle in ("--text-encoder-device cuda", "--text-encoders-dir " + self.loc["text_encoders_dir"], "--model core40", "--device gpu"):
            self.assertIn(needle, text)
        self.assertNotIn("must-not-reach", text)
        # the adapters now point at the local mirror
        base = json.loads((self.t / "enc" / "McGill-NLP" / pte.ADAPTERS[0] / "adapter_config.json").read_text(encoding="utf-8"))["base_model_name_or_path"]
        self.assertEqual(os.path.abspath(base), os.path.abspath(self.loc["llama_mirror_dir"]))

    def failure(self, env, needle):
        os.environ.update(env)
        code = self.go("A person walks forward.")
        self.assertNotEqual(code, 0)
        rec = json.loads(self.receipts()[-1].read_text(encoding="utf-8"))
        self.assertEqual(rec["status"], "fail")
        self.assertIn(needle, rec["error"])
        return rec

    def test_generation_failure_is_a_failed_receipt(self):
        self.failure({"FAKE_GEN_FAIL": "1"}, "ARDY generation exit 7")

    def test_wrong_skeleton_and_nan_are_failed_receipts(self):
        self.failure({"FAKE_SKEL": "somaskel77"}, "skeleton somaskel77 is not cskel27")
        os.environ.pop("FAKE_SKEL")
        self.failure({"FAKE_NAN": "1"}, "NaN")

    def test_retarget_failure_and_a_thin_clip_are_failed_receipts(self):
        self.failure({"FAKE_RETARGET_FAIL": "1"}, "retarget exit 9")
        os.environ.pop("FAKE_RETARGET_FAIL")
        self.failure({"FAKE_KEYS": "3"}, "3 keys for 40")


class McpMotion(unittest.TestCase):
    """The tool through the real MCP server: refusals before anything starts, and the receipt/result plumbing, against a fake FACTORY_HOME."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        home = t / "home"
        (home / "config").mkdir(parents=True)
        (home / "receipts").mkdir()
        shutil.copytree(REPO / "tools", home / "tools", ignore=shutil.ignore_patterns("__pycache__", "upscale", "texture"))
        (home / "config" / "config.json").write_text(json.dumps(CFG), encoding="utf-8")
        loc = fake_local(t)
        (home / "config" / "config.local.json").write_text(json.dumps(loc), encoding="utf-8")
        cls.t, cls.home, cls.loc = t, home, loc
        cls.lock = t / ".gpu_lock"

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

    def test_tool_is_listed_with_text_required(self):
        msgs = [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        r = subprocess.run([PY, str(PLUGIN / "mcp" / "server.py")], input=json.dumps(msgs[0]) + "\n", capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, FACTORY_HOME=str(self.home)))
        tool = {x["name"]: x for x in json.loads(r.stdout)["result"]["tools"]}["motion_generate"]
        self.assertEqual(tool["inputSchema"]["required"], ["text"])
        for k in ("text", "duration", "seed", "clip_id", "install", "device", "encoder_device"):
            self.assertIn(k, tool["inputSchema"]["properties"])

    def test_live_gpu_lock_refuses_but_cpu_device_does_not_need_the_lock(self):
        self.lock.write_text(json.dumps({"pid": os.getpid(), "job": "flux_kit", "started_utc": "2026-11-01T00:00:00Z"}), encoding="utf-8")
        try:
            err, text = self.call({"text": "A person walks forward."}, FACTORY_FAKE_FREE_RAM_GB=64)
            self.assertTrue(err)
            self.assertEqual(json.loads(text)["status"], "refused")
            self.assertIn("already running", text)
            print("\nREFUSAL (live GPU lock):", json.loads(text)["error"][:160])
            err, text = self.call({"text": "A person walks forward.", "device": "cpu", "duration": 99}, FACTORY_FAKE_FREE_RAM_GB=64)
            self.assertNotIn("already running", text)   # passed the lock gate, then refused on the duration instead
            self.assertIn("outside 1 to 10", text)
        finally:
            self.lock.unlink()

    def test_low_ram_refuses(self):
        err, text = self.call({"text": "A person walks forward."}, FACTORY_FAKE_FREE_RAM_GB=2.0)
        self.assertTrue(err)
        self.assertIn("2.0 GB of RAM is free", text)
        self.assertIn("needs 6.0 GB", text)
        print("REFUSAL (low RAM):", json.loads(text)["error"][:160])

    def test_input_refusals_start_nothing(self):
        for args, needle in (({"text": ""}, "text is empty"), ({"text": "x" * 400}, "limit is 300"), ({"text": "A person walks.", "duration": 0}, "outside 1 to 10"),
                             ({"text": "A person walks.", "backend": "kimodo"}, "not enabled"), ({"text": "A person walks.", "device": "tpu"}, "device must be gpu or cpu"),
                             ({"text": "A person walks.", "encoder_device": "cpu"}, "needs about 24 GB")):
            err, text = self.call(args, FACTORY_FAKE_FREE_RAM_GB=20)
            self.assertTrue(err, args)
            self.assertEqual(json.loads(text)["status"], "refused")
            self.assertIn(needle, text, args)
        self.assertTrue(self.call({}, FACTORY_FAKE_FREE_RAM_GB=64)[0])   # text is required

    def test_missing_weights_are_refused_with_the_path(self):
        shutil.copy(self.loc["rig_glb"], str(self.t / "rig_backup.glb"))
        Path(self.loc["rig_glb"]).unlink()
        try:
            err, text = self.call({"text": "A person walks forward."}, FACTORY_FAKE_FREE_RAM_GB=64)
        finally:
            shutil.copy(str(self.t / "rig_backup.glb"), self.loc["rig_glb"])
        self.assertTrue(err)
        self.assertIn("rig_glb not found", text)

    def test_cpu_run_goes_through_run_stage_and_finish_with_a_fake_script(self):
        # replace tools/run_motion.py by a fake that writes a receipt, to prove the server's plumbing (no real stage, no guard on cpu)
        fake = self.home / "tools" / "run_motion.py"
        orig = fake.read_text(encoding="utf-8")
        fake.write_text(os.linesep.join([
            "import sys, json, os",
            "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))",
            "import _orig_run_motion as _o",
            "problems = _o.problems",
            "if __name__ == '__main__':",
            "    d = os.path.join(r'" + str(self.home) + "', 'receipts', sys.argv[1]); os.makedirs(d, exist_ok=True)",
            "    rec = {'schema': 1, 'asset_id': sys.argv[1], 'stage': 'motion', 'status': 'ok', 'error': '', 'outputs': [{'path': 'x/clip.glb'}], 'timing': {'wall_s': 1},",
            "           'memory': {'lowest_free_ram_gb': 9}, 'params': {'clip_id': 'ardy-x', 'stills_use': 'set stills.clips.walk', 'frames': 80, 'fps': 20.0, 'installed_clip': ''}}",
            "    json.dump(rec, open(os.path.join(d, 'motion-20261101T000000Z.json'), 'w'))",
        ]) + os.linesep, encoding="utf-8")
        (self.home / "tools" / "_orig_run_motion.py").write_text(orig, encoding="utf-8")
        try:
            err, text = self.call({"text": "A person walks forward.", "asset_id": "assetX", "device": "cpu"}, FACTORY_FAKE_FREE_RAM_GB=64)
        finally:
            fake.write_text(orig, encoding="utf-8")
        self.assertFalse(err, text)
        res = json.loads(text)
        self.assertEqual((res["status"], res["clip_id"], res["frames"]), ("ok", "ardy-x", 80))
        self.assertTrue(res["receipt"].endswith("motion-20261101T000000Z.json"))
        self.assertIn("Llama 3 Community License", res["licences"])
        self.assertIn("THIRD_PARTY_NOTICES.md", res["licences"])


class MotionHooks(unittest.TestCase):
    def run_hook(self, name, payload, **env):
        e = dict(os.environ, FACTORY_TEST_STAGE_TAG="no-such-stage-process", **{k: str(v) for k, v in env.items()})
        r = subprocess.run([PY, str(PLUGIN / "hooks" / name)], input=json.dumps(payload), capture_output=True, text=True, env=e, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def denied(self, out):
        return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_hooks_json_covers_the_motion_stage(self):
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        flat = [(g["matcher"], h) for g in hooks for h in g["hooks"]]
        for script in ("gpu_lock_guard.py", "ram_gate.py", "plugin_guard.py"):
            for pat in ("Bash(*run_motion*)", "Bash(*ardy_generate*)"):
                self.assertTrue(any(script in h["command"] and h.get("if") == pat for _, h in flat), (script, pat))
        self.assertTrue(any(m == MCP_P + "(flux_render|trellis_run|upscale|motion_generate)" for m, _ in flat))
        self.assertTrue(any(m == MCP_P + "(flux_render|trellis_run|cutout|bind|stills|upscale|motion_generate)" for m, _ in flat))
        for _, h in flat:
            self.assertLessEqual(h["timeout"], 15)   # cheap and fail-open like the others

    def test_gpu_lock_denies_motion_starts_on_a_live_lock_but_not_cpu_runs(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / ".gpu_lock"
            lock.write_text(json.dumps({"pid": os.getpid(), "job": "generate.py", "started_utc": "x"}), encoding="utf-8")
            for cmd in ("python tools/run_motion.py a 'A person walks.'", "python tools/motion/ardy_generate.py --text x"):
                self.assertTrue(self.denied(self.run_hook("gpu_lock_guard.py", {"tool_name": "Bash", "tool_input": {"command": cmd}}, FACTORY_GPU_LOCK=lock)), cmd)
            self.assertTrue(self.denied(self.run_hook("gpu_lock_guard.py", {"tool_name": MCP_P + "motion_generate", "tool_input": {"text": "x"}}, FACTORY_GPU_LOCK=lock)))
            self.assertIsNone(self.run_hook("gpu_lock_guard.py", {"tool_name": MCP_P + "motion_generate", "tool_input": {"text": "x", "device": "cpu"}}, FACTORY_GPU_LOCK=lock))
            self.assertIsNone(self.run_hook("gpu_lock_guard.py", {"tool_name": "Bash", "tool_input": {"command": "cat tools/run_motion.py"}}, FACTORY_GPU_LOCK=lock))

    def test_ram_gate_denies_low_ram_for_motion(self):
        low = dict(FACTORY_FAKE_FREE_RAM_GB=1.5, FACTORY_HOME=REPO)
        for payload in ({"tool_name": "Bash", "tool_input": {"command": "python tools/run_motion.py a 'x'"}}, {"tool_name": MCP_P + "motion_generate", "tool_input": {}}):
            self.assertTrue(self.denied(self.run_hook("ram_gate.py", payload, **low)), payload)
        self.assertIsNone(self.run_hook("ram_gate.py", {"tool_name": "Bash", "tool_input": {"command": "python tools/run_motion.py --help"}}, **low))
        self.assertIsNone(self.run_hook("ram_gate.py", {"tool_name": MCP_P + "motion_generate", "tool_input": {}}, FACTORY_FAKE_FREE_RAM_GB=64, FACTORY_HOME=REPO))

    def test_plugin_guard_refuses_raw_motion_scripts(self):
        sys.path.insert(0, str(PLUGIN / "hooks"))
        import plugin_guard
        for cmd in ("python tools/run_motion.py a 'A person walks.'", "python C:/x/tools/motion/ardy_generate.py --text x"):
            why = plugin_guard.decide({"tool_name": "Bash", "tool_input": {"command": cmd}})
            self.assertIn("motion_generate", why)
        for cmd in ("python tools/run_motion.py --help", "cat tools/run_motion.py", "git commit -m 'tune run_motion.py'"):
            self.assertEqual(plugin_guard.decide({"tool_name": "Bash", "tool_input": {"command": cmd}}), "", cmd)


class Packaging(unittest.TestCase):
    def test_skill_frontmatter_quotes_the_colon_description(self):
        text = (PLUGIN / "skills" / "motion-generate" / "SKILL.md").read_text(encoding="utf-8")
        head = text.split("---\n")[1]
        self.assertTrue(head.startswith("name: motion-generate\ndescription: \""), head[:80])
        self.assertTrue(head.rstrip().endswith('"'))
        self.assertIn("Built with Meta Llama 3", text)
        self.assertIn("STATUS: DRAFT, not yet run end to end", text)

    def test_notices_keep_the_nvidia_and_llama_lines(self):
        text = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("Licensed by NVIDIA Corporation under the NVIDIA Open Model License", text)
        self.assertIn("Built with Meta Llama 3", text)
        self.assertIn("Apache License 2.0", text)
        self.assertIn("Meta Llama 3 Community License", text)

    def test_config_local_example_has_the_motion_paths(self):
        ex = json.loads((REPO / "config" / "config.local.example.json").read_text(encoding="utf-8"))
        for k in ("motion_python", "ardy_repo_dir", "ardy_checkpoints_dir", "text_encoders_dir", "llama_mirror_dir", "blender_exe", "rig_glb", "mixamo_clips_dir"):
            self.assertIn(k, ex)
        self.assertIn("Motion-312", ex["motion_python"])
        self.assertNotIn("Trellis-3-312", ex["motion_python"])   # never install into the Trellis venv


@unittest.skipUnless(os.environ.get("CF_TEST_BLENDER") == "1" and (REPO / "plugins" / "factory" / "tests" / "fixtures" / "motion_core27_synthetic.json").exists(), "needs Blender and CF_TEST_BLENDER=1")
class RealBlender(unittest.TestCase):
    def test_retarget_a_synthetic_core27_motion_onto_the_rig(self):
        loc = json.loads((REPO / "config" / "config.local.json").read_text(encoding="utf-8-sig"))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "clip.glb"
            args = Path(d) / "args.json"
            args.write_text(json.dumps({"motion": str(Path(__file__).parent / "fixtures" / "motion_core27_synthetic.json"), "rig_glb": loc["rig_glb"], "out_glb": str(out),
                                        "result": str(Path(d) / "res.json"), "cfg": M, "in_place": False}), encoding="utf-8")
            r = subprocess.run([loc["blender_exe"], "--background", "--python", str(REPO / "tools" / "motion" / "retarget_blender.py"), "--", str(args)],
                               capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:])
            self.assertEqual(run_motion.check_clip(str(out), M, 40), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
