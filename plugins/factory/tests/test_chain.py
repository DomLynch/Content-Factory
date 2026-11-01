"""Offline tests for plugin 0.1.24: the build order as a chain of gates (cf_chain, record_approval.py, manifest --consistency, run_3d.py / run_character gates). No GPU."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
PY = sys.executable
sys.path.insert(0, str(REPO / "plugins" / "factory" / "lib"))
try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


class Chain(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.repo = Path(self.t.name) / "repo"
        for sub in ("tools", "config", "plugins/factory/lib"):
            shutil.copytree(REPO / sub, self.repo / sub, ignore=shutil.ignore_patterns("__pycache__", "config.local.json"))
        loc = {"python": PY, "outputs_dir": str(Path(self.t.name) / "out"), "logs_dir": str(Path(self.t.name) / "logs"), "trellis_repo_dir": str(Path(self.t.name) / "tr"),
               "work_dir": self.t.name}
        (self.repo / "config" / "config.local.json").write_text(json.dumps(loc), encoding="utf-8")
        self.w("characters/hero.json", {"character_id": "hero-1", "asset_prefix": "hero", "sheet_kit": "k", "parts": [{"name": "coat"}]})
        self.w("kits/k_kit.json", {"kit": "k", "rows": [{"name": "head_front", "prompt": "x"}, {"name": "hand_l", "prompt": "x"}, {"name": "hand_r", "prompt": "x", "mirror_of": "hand_l"}]})
        # import the chain module against the temp home
        for m in [m for m in sys.modules if m in ("cf_chain", "cf_gates", "cf_guard", "cf_flux")]:
            del sys.modules[m]
        os.environ["FACTORY_HOME"] = str(self.repo)
        sys.path.insert(0, str(self.repo / "plugins" / "factory" / "lib"))
        import cf_chain
        self.chain = cf_chain
        self.addCleanup(lambda: (sys.path.remove(str(self.repo / "plugins" / "factory" / "lib")), os.environ.pop("FACTORY_HOME", None)))

    def w(self, rel, obj):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj), encoding="utf-8")

    def manifest(self, rows, consistency=True):
        self.w("manifests/k.json", {"schema": 1, "kit": "k", "rows": rows, **({"consistency": {"file": "pass2.json"}} if consistency else {})})

    GOOD = {"head_front": {"verdict": "PASS"}, "hand_l": {"verdict": "WEAK", "verdict_note": "thumb odd, accepted"}}

    def test_no_manifest_blocks_the_sheet_gate(self):
        r = self.chain.sheet_reasons("k")
        self.assertTrue(r and "no manifests/k.json" in r[0], r)

    def test_complete_sheet_passes_and_mirror_counts_through_its_source(self):
        self.manifest(self.GOOD)
        self.assertEqual(self.chain.sheet_reasons("k"), [])

    def test_reject_unreviewed_and_unnoted_weak_block(self):
        self.manifest({"head_front": {"verdict": "REJECT"}, "hand_l": {"verdict": "WEAK"}})
        r = self.chain.sheet_reasons("k")[0]
        self.assertIn("head_front: REJECT", r)
        self.assertIn("hand_l: WEAK without a note", r)
        self.assertIn("hand_r: WEAK without a note", r)   # the mirror follows its source
        self.manifest({"head_front": {"verdict": "PASS"}})
        self.assertIn("hand_l: no verdict", self.chain.sheet_reasons("k")[0])

    def test_missing_consistency_pass_blocks(self):
        self.manifest(self.GOOD, consistency=False)
        self.assertTrue(any("consistency pass" in x for x in self.chain.sheet_reasons("k")))

    def test_part_mesh_needs_sheet_then_approval(self):
        r = self.chain.part_mesh_reasons("hero-coat")
        self.assertIn("CHAIN (1 sheet)", r[0])
        self.manifest(self.GOOD)
        r = self.chain.part_mesh_reasons("hero-coat")
        self.assertIn("CHAIN (2 approval)", r[0])
        self.w("receipts/hero-1/approval-20261101T000000Z.json", {"status": "ok", "params": {"approved_by": "Dom", "kit": "k"}})
        self.assertEqual(self.chain.part_mesh_reasons("hero-coat"), [])

    def test_assets_outside_any_character_are_outside_the_chain_and_a_spec_without_sheet_kit_is_flagged(self):
        self.assertEqual(self.chain.part_mesh_reasons("robot-tokens98k"), [])
        self.w("characters/hero.json", {"character_id": "hero-1", "asset_prefix": "hero", "parts": []})
        r = self.chain.part_mesh_reasons("hero-coat")
        self.assertTrue(r and "no `sheet_kit`" in r[0], r)

    def test_cutout_receipt_required_with_cutout_png(self):
        self.manifest(self.GOOD)
        self.w("receipts/hero-1/approval-20261101T000000Z.json", {"status": "ok", "params": {"approved_by": "Dom", "kit": "k"}})
        r = self.chain.part_mesh_reasons("hero-coat", True)
        self.assertIn("CHAIN (3 cutout)", r[0])
        png = self.repo / "cut.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.w("receipts/hero-coat/cutout-20261101T000000Z.json", {"status": "ok", "outputs": [{"path": str(png)}]})
        self.assertEqual(self.chain.part_mesh_reasons("hero-coat", True), [])   # an unreadable image cannot be measured: not blocked by the input gate

    @unittest.skipUnless(HAVE_PIL, "needs Pillow")
    def test_cutout_that_fails_the_input_gate_blocks(self):
        from PIL import Image
        self.manifest(self.GOOD)
        self.w("receipts/hero-1/approval-20261101T000000Z.json", {"status": "ok", "params": {"approved_by": "Dom", "kit": "k"}})
        png = self.repo / "cut.png"
        im = Image.new("RGBA", (200, 100), (0, 0, 0, 0))   # landscape cut-out with a real subject
        im.paste((255, 0, 0, 255), (50, 20, 150, 80))
        im.save(png)
        self.w("receipts/hero-coat/cutout-20261101T000000Z.json", {"status": "ok", "outputs": [{"path": str(png)}]})
        r = self.chain.part_mesh_reasons("hero-coat", True)
        self.assertTrue(r and "landscape" in r[0], r)

    def test_status_lists_satisfied_gates_and_the_pending_ones(self):
        self.manifest(self.GOOD)
        rows = self.chain.status("hero-1")
        by = {g: ok for g, ok, _ in rows}
        self.assertTrue(by["1 sheet complete + consistency"])
        self.assertFalse(by["2 Dom's yes on the contact sheet"])
        self.assertFalse(by["3 cutout: coat"])
        self.assertTrue(any("pending (not gated yet): cleanup" in g for g, _, _ in rows))

    def run_tool(self, *args, **env):
        e = {k: v for k, v in os.environ.items() if k not in ("FACTORY_RAW", "FACTORY_HOME")}
        e.update(env)
        return subprocess.run([PY, *map(str, args)], capture_output=True, text=True, env=e, cwd=str(self.repo))

    def test_run_3d_is_refused_without_the_sheet(self):
        r = self.run_tool(self.repo / "tools" / "run_3d.py", "hero-coat", "x.png")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("CHAIN (1 sheet)", r.stdout)

    def test_factory_raw_overrides_and_is_logged(self):
        r = self.run_tool(self.repo / "tools" / "run_3d.py", "hero-coat", "x.png", FACTORY_RAW="test")
        self.assertIn("GATE OVERRIDDEN", r.stdout)

    def test_record_approval_refuses_an_incomplete_sheet_and_writes_when_complete(self):
        kit = self.repo / "kits" / "k_kit.json"
        r = self.run_tool(self.repo / "tools" / "record_approval.py", kit, "--by", "Dom")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sheet is not complete", r.stdout + r.stderr)
        self.manifest(self.GOOD)
        r = self.run_tool(self.repo / "tools" / "record_approval.py", kit, "--by", "Dom", "--note", "yes in chat")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.chain.part_mesh_reasons("hero-coat"), [])   # the written receipt satisfies gate 2

    def test_manifest_records_consistency_and_keeps_it_on_the_next_run(self):
        rev = self.repo / "pass2.json"
        rev.write_text(json.dumps({"reviewer": "qc", "counts": {"PASS": 1}, "cells": []}))
        kit = self.repo / "kits" / "k_kit.json"
        r = self.run_tool(self.repo / "tools" / "manifest.py", kit, "--consistency", rev)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.run_tool(self.repo / "tools" / "manifest.py", kit, "--verdict", "head_front", "PASS")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = json.loads((self.repo / "manifests" / "k.json").read_text())
        self.assertEqual(m["consistency"]["reviewer"], "qc")
        self.assertEqual(m["rows"]["head_front"]["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
