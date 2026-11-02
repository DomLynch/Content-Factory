"""Offline tests for plugin 0.1.26 (vampire v2 learnings): ref_max_px, dependency holds, supplied rows, head yaw, body-after-head order, keep_awake. No GPU."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_flux import png, cf_flux, REPO  # noqa: E402
from test_sheet_rules import Sheet  # noqa: E402
import test_chain  # noqa: E402
Chain = test_chain.Chain


class RefCap(Sheet):
    def test_default_caps_body_and_head_refs_at_the_render_long_edge(self):
        big = png(self.d / "big.png", 3000, 4000)   # a 4K identity reference
        self.kit["ref"] = big
        spec = cf_flux.run_spec(self.kit, self.row("head_front"), {})
        self.assertEqual(spec["ref_max_px"], 1536)   # render 1024x1536
        self.assertEqual(cf_flux.policy_reasons(spec), [])   # capping at the render edge is allowed, no refusal
        self.assertEqual(cf_flux.run_spec(self.kit, self.row("coat"), {})["ref_max_px"], None)   # gear keeps its own 640 floor

    def test_a_cap_below_the_render_edge_is_refused_and_a_row_key_sets_it(self):
        r = self.reasons("head_front", {"CF_REF_MAX_PX": "800"})
        self.assertTrue(any("render long edge" in x and "1536" in x for x in r), r)
        self.row("head_front")["ref_max_px"] = 2000
        self.assertEqual(cf_flux.run_spec(self.kit, self.row("head_front"), {})["ref_max_px"], 2000)


class Holds(Sheet):
    def test_needs_holds_until_the_dependency_has_a_verdict(self):
        self.row("head_profile")["needs"] = ["hands_left"]
        r = self.sheet("head_profile")
        self.assertTrue(cf_flux.is_hold([x for x in r if "(hold)" in x]), r)
        self.assertTrue(any("HOLD" in x and "hands_left" in x for x in r))
        self.assertFalse(cf_flux.is_hold(["SHEET RULE (mirror): x"]))
        self.verdict("hands_left", "PASS")
        self.assertFalse(any("(hold)" in x for x in self.sheet("head_profile")))

    def test_supplied_rows_are_not_rendered(self):
        self.row("hands_left")["supplied_by"] = "Dom"
        self.assertTrue(any("supplied by Dom" in x for x in self.sheet("hands_left")))


class Yaw(Chain):
    def test_head_row_yaw_must_match_the_measured_yaw(self):
        self.w("kits/k_kit.json", {"kit": "k", "rows": [{"name": "head_left", "prompt": "x", "yaw_deg": -90}]})
        self.manifest({"head_left": {"verdict": "PASS"}})
        self.assertTrue(any("yaw not measured" in x for x in self.chain.sheet_reasons("k")))
        self.manifest({"head_left": {"verdict": "PASS", "yaw_measured": -20}})
        self.assertTrue(any("yaw -20" in x for x in self.chain.sheet_reasons("k")))
        self.manifest({"head_left": {"verdict": "PASS", "yaw_measured": -85}})
        self.assertEqual(self.chain.sheet_reasons("k"), [])


class Order(Chain):
    def test_body_mesh_waits_for_the_head_mesh(self):
        self.w("characters/hero.json", {"character_id": "hero-1", "asset_prefix": "hero", "sheet_kit": "k", "parts": [{"name": "head"}, {"name": "body"}]})
        spec = json.loads((self.repo / "characters" / "hero.json").read_text())
        self.assertTrue(any("waits for the head mesh 'hero-head'" in x for x in self.chain.order_reasons("hero-body", spec, "body")))
        self.assertEqual(self.chain.order_reasons("hero-head", spec, "head"), [])
        self.w("receipts/hero-head/trellis-20261101T000000Z.json", {"status": "ok"})
        self.assertEqual(self.chain.order_reasons("hero-body", spec, "body"), [])


class KeepAwake(unittest.TestCase):
    def test_never_raises(self):
        sys.path.insert(0, str(REPO / "tools"))
        import cf_common
        cf_common.keep_awake()


if __name__ == "__main__":
    unittest.main()
