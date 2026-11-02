"""Offline tests for the 0.1.4 gates (python -m unittest discover plugins/factory/tests -v). No stage, GPU, Blender or network.

Refusals are checked against the real vampire receipts in this repo (FACTORY_HOME = the checkout); passing cases use a throw-away home.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
sys.path.insert(0, str(PLUGIN / "lib"))
os.environ["FACTORY_HOME"] = str(REPO)
import cf_guard  # noqa: E402
import cf_gates  # noqa: E402

try:
    from PIL import Image
except ImportError:  # the picture checks are skipped without PIL
    Image = None

PY = sys.executable
MCP = "mcp__plugin_factory_factory__"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def hook(name, payload, home=REPO):
    env = {**os.environ, "FACTORY_HOME": str(home)}
    r = subprocess.run([PY, str(PLUGIN / "hooks" / name)], input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else {}


def denied(out):
    h = out.get("hookSpecificOutput", {})
    return h.get("permissionDecision") == "deny", h.get("permissionDecisionReason", "")


@unittest.skipUnless(Image, "PIL not installed")
class InputGate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def pic(self, name, w, h, subject_h, fill=(200, 30, 30)):
        im = Image.new("RGB", (w, h), (255, 255, 255))
        top = (h - subject_h) // 2
        im.paste(Image.new("RGB", (w // 3, subject_h), fill), (w // 3, top))
        p = str(Path(self.d.name) / name)
        im.save(p)
        return p

    def test_small_subject_refused_with_numbers(self):
        r = cf_gates.input_reason(self.pic("body.png", 1024, 2048, 400))
        self.assertIn("INPUT GATE", r)
        self.assertIn("20%", r)

    def test_landscape_refused(self):
        self.assertIn("landscape", cf_gates.input_reason(self.pic("body.png", 2048, 1024, 1000)))

    def test_small_head_refused(self):
        r = cf_gates.input_reason(self.pic("head_front.png", 800, 800, 500))
        self.assertIn("500 px tall", r)

    def test_good_inputs_pass(self):
        self.assertEqual(cf_gates.input_reason(self.pic("body.png", 1024, 2048, 1800)), "")
        self.assertEqual(cf_gates.input_reason(self.pic("head_front.png", 1024, 1024, 900)), "")

    def test_hook_denies_through_mcp(self):
        p = self.pic("body.png", 1024, 2048, 400)
        bad, why = denied(hook("input_gate.py", {"tool_name": MCP + "trellis_run", "tool_input": {"asset_id": "x", "image": p}}))
        self.assertTrue(bad)
        self.assertIn("INPUT GATE", why)


class TokenGate(unittest.TestCase):
    def test_vampire_rebake_from_20167_tokens_refused(self):
        r = cf_gates.token_reason("vampire-dracula-front", "hero_rebake", True)
        self.assertIn("20167", r)
        self.assertIn("98304", r)

    def test_vampire_shape_sampled_at_49152_is_refused(self):
        # today's faceted vampire shape (reported 49152 hr tokens) must not pass the floor; the repo receipt only holds the older 20167, so mirror it in a fixture
        import tempfile
        d = Path(tempfile.mkdtemp())
        (d / "receipts" / "vamp").mkdir(parents=True)
        (d / "config").mkdir()
        (d / "config" / "config.json").write_text(json.dumps(json.load(open(REPO / "config" / "config.json", encoding="utf-8-sig"))))
        (d / "receipts" / "vamp" / "trellis-20261101T000000Z.json").write_text(json.dumps({"params": {"stage_tokens": {"shape_hr_tokens": 49152}}}))
        cf_guard.HOME = d
        try:
            r = cf_gates.token_reason("vamp", "hero_rebake", True)
        finally:
            cf_guard.HOME = REPO
        self.assertIn("49152", r)
        self.assertIn("floor is 98304", r)
        self.assertEqual(cf_gates.token_reason("vamp", "master_reduced", True), "")   # draft/benchmark preset: no floor

    def test_hook_refuses_bash_rebake(self):
        cmd = "python tools/run_3d.py vampire-dracula-front --preset hero_rebake --from-latents"
        bad, why = denied(hook("gates.py", {"tool_name": "Bash", "tool_input": {"command": cmd}}))
        self.assertTrue(bad)
        self.assertIn("TOKEN GATE", why)

    def test_no_recorded_tokens_refused_and_proof_presets_untouched(self):
        self.assertIn("no recorded token count", cf_gates.token_reason("robot-tokens98k", "hero_rebake", True))
        self.assertEqual(cf_gates.token_reason("vampire-dracula-front", "master_reduced", False), "")

    def test_fresh_master_preset_at_98k_passes(self):
        self.assertEqual(cf_gates.token_reason("x", "tokens98k_fresh", False), "")


class Fixture(unittest.TestCase):
    """A throw-away factory home with one complete asset."""

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.home = Path(self.t.name)
        for sub in ("receipts/char", "characters", "publish", "config"):
            (self.home / sub).mkdir(parents=True)
        (self.home / "config" / "config.json").write_text(json.dumps(json.load(open(REPO / "config" / "config.json", encoding="utf-8-sig"))))
        self.glb = self.write("bound.glb", b"glb-bytes")
        self.still = self.write("head.png", b"png-bytes")
        self.spec = {"id": "char", "label": "C", "group": "G", "rig": "r", "source": "s", "glb": self.glb, "thumb": self.still, "stills": [self.still], "pipeline": "TRELLIS",
                     "datalayer": self.write("datalayer.json", b'{"pieces": ["char"]}')}
        parts = [{"name": "coat", "kind": "gear"}, {"name": "head", "kind": "head"}, {"name": "hands", "kind": "hands"}]
        (self.home / "characters" / "char.json").write_text(json.dumps({"character_id": "char", "parts": parts}))
        self.receipt("trellis-1", {"asset_id": "char", "stage": "trellis", "status": "ok", "params": {"stage_tokens": {"shape_hr_tokens": 90000}}, "outputs": []})
        self.receipt("bind-1", {"asset_id": "char", "stage": "bind", "status": "ok",
                                "params": {"assembly": True, "parts": ["gear_coat.glb", "gear_head.glb", "gear_hands.glb"]},
                                "outputs": [{"path": self.glb, "sha256": sha(self.glb)}]})
        self.receipt("stills-1", {"asset_id": "char", "stage": "stills", "status": "ok", "params": {"lighting_tier": "three_point"},
                                  "inputs": [{"path": self.glb, "sha256": sha(self.glb)}], "outputs": [{"path": self.still, "sha256": sha(self.still)}]})
        os.environ["FACTORY_HOME"] = str(self.home)
        cf_guard.HOME = self.home
        self.addCleanup(self.restore)

    def restore(self):
        os.environ["FACTORY_HOME"] = str(REPO)
        cf_guard.HOME = REPO

    def write(self, name, data):
        p = self.home / name
        p.write_bytes(data)
        return str(p)

    def receipt(self, name, rec):
        (self.home / "receipts" / "char" / f"{name}-20261101T000000Z.json").write_text(json.dumps({"schema": 1, **rec}))


class Publish(Fixture):
    def test_complete_asset_passes_and_prints_readback(self):
        bad, info = cf_gates.publish_reasons(self.spec)
        self.assertEqual(bad, [])
        self.assertTrue(any("READ-BACK publish glb" in i and "hr tokens 90000" in i and "pipeline TRELLIS" in i for i in info), info)

    def test_missing_hands_is_a_parts_refusal(self):
        self.receipt("bind-2", {"asset_id": "char", "stage": "bind", "status": "ok", "params": {"assembly": True, "parts": ["gear_coat.glb"]},
                                "outputs": [{"path": self.glb, "sha256": sha(self.glb)}]})
        for f in (self.home / "receipts" / "char").glob("bind-1-*"):
            os.utime(f, (1, 1))
        bad, _ = cf_gates.publish_reasons(self.spec)
        self.assertTrue(any("PARTS GATE" in b and "hands" in b and "head" in b for b in bad), bad)

    def test_done_needs_the_data_layer_row(self):
        bad, info = cf_gates.publish_reasons({k: v for k, v in self.spec.items() if k != "datalayer"})
        self.assertTrue(any("DATA-LAYER GATE" in b and "no \"datalayer\"" in b for b in bad), bad)
        self.write("datalayer.json", b'{"pieces": ["other"]}')
        self.assertTrue(any("does not mention 'char'" in b for b in cf_gates.publish_reasons(self.spec)[0]))
        self.assertTrue(any("FBX" in i and "PENDING" in i for i in info), info)

    def test_proof_lighting_refused(self):
        f = next((self.home / "receipts" / "char").glob("stills-1-*"))
        r = json.loads(f.read_text())
        r["params"]["lighting_tier"] = "proof"
        f.write_text(json.dumps(r))
        bad, _ = cf_gates.publish_reasons(self.spec)
        self.assertTrue(any("LIGHTING GATE" in b and "'proof'" in b for b in bad), bad)

    def test_changed_glb_refused(self):
        Path(self.glb).write_bytes(b"edited")
        bad, _ = cf_gates.publish_reasons(self.spec)
        self.assertTrue(any("READ-BACK GATE" in b for b in bad), bad)

    def test_pipeline_missing_and_mismatch_refused(self):
        spec = {k: v for k, v in self.spec.items() if k != "pipeline"}
        self.assertTrue(any("no \"pipeline\" key" in b for b in cf_gates.publish_reasons(spec)[0]))
        self.assertTrue(any("show TRELLIS" in b for b in cf_gates.publish_reasons({**self.spec, "pipeline": "sculpt"})[0]))

    def test_kit_without_hands_is_complete_without_hands(self):
        (self.home / "characters" / "char.json").write_text(json.dumps({"character_id": "char", "parts": [{"name": "coat", "kind": "gear"}, {"name": "head", "kind": "head"}]}))
        self.assertEqual(cf_gates.publish_reasons(self.spec)[0], [])

    def test_factory_raw_override_skips_and_logs(self):
        spec = {k: v for k, v in self.spec.items() if k != "pipeline"}
        (self.home / "publish" / "char.json").write_text(json.dumps(spec))
        out = hook("gates.py", {"tool_name": "Bash", "tool_input": {"command": "FACTORY_RAW=odd-case python tools/publish.py char --library L"}}, self.home)
        self.assertEqual(out, {})
        self.assertTrue((self.home / "logs" / "factory_raw_override.log").exists())

    def test_hook_refuses_real_publish_not_dry_run(self):
        spec = {k: v for k, v in self.spec.items() if k != "pipeline"}
        (self.home / "publish" / "char.json").write_text(json.dumps(spec))

        def run(c):
            return hook("gates.py", {"tool_name": "Bash", "tool_input": {"command": c}}, self.home)
        bad, why = denied(run("python tools/publish.py char --library L"))
        self.assertTrue(bad)
        self.assertIn("PIPELINE GATE", why)
        self.assertEqual(run("python tools/publish.py char --library L --dry-run"), {})

    def test_hook_allows_and_prints_readback(self):
        (self.home / "publish" / "char.json").write_text(json.dumps(self.spec))
        out = hook("gates.py", {"tool_name": "Bash", "tool_input": {"command": "python tools/publish.py char --library L"}}, self.home)
        self.assertIn("READ-BACK", out["hookSpecificOutput"]["additionalContext"])


class Stills(Fixture):
    def test_matching_glb_allowed_with_readback(self):
        out = hook("gates.py", {"tool_name": MCP + "stills", "tool_input": {"asset_id": "char", "glb": self.glb}}, self.home)
        self.assertIn("READ-BACK stills input", out["hookSpecificOutput"]["additionalContext"])

    def test_unvouched_glb_refused(self):
        other = self.write("other.glb", b"x")
        bad, why = denied(hook("gates.py", {"tool_name": MCP + "stills", "tool_input": {"asset_id": "char", "glb": other}}, self.home))
        self.assertTrue(bad)
        self.assertIn("not the recorded output", why)


class RealVampire(unittest.TestCase):
    def test_vampire_body_only_is_refused(self):
        self.assertIn("PARTS GATE", cf_gates.parts_reason("vampire-dracula-106"))

    def test_fail_open_on_garbage(self):
        self.assertEqual(hook("gates.py", {"tool_name": "Bash", "tool_input": {"command": 5}}), {})
        self.assertEqual(hook("gates.py", {}), {})


if __name__ == "__main__":
    unittest.main()
