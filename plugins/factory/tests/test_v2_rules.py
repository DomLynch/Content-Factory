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


class Policy(Sheet):
    """The qc_pass_pct policy (Strategy 2026-11-02): structural FAIL -> REJECT, cosmetic -> WEAK, one re-roll then accept as-is."""
    def run_manifest(self, *a):
        import shutil, subprocess
        repo = self.d / "repo"
        if not repo.exists():
            for sub in ("tools", "config", "plugins/factory/lib"):
                shutil.copytree(REPO / sub, repo / sub, ignore=shutil.ignore_patterns("__pycache__", "config.local.json"))
            (repo / "config" / "config.local.json").write_text(json.dumps({"outputs_dir": str(self.d / "out")}), encoding="utf-8")
        r = subprocess.run([sys.executable, str(repo / "tools" / "manifest.py"), str(self.path), *a], capture_output=True, text=True, cwd=str(repo))
        m = repo / "manifests" / "k.json"
        return r, (json.loads(m.read_text())["rows"] if m.exists() else {})

    def test_judge_needs_all_structural_checks_then_derives_the_verdict(self):
        r, _ = self.run_manifest("--judge", "head_front")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("identity_match", r.stdout + r.stderr)
        for c in ("identity_match", "yaw_in_tolerance", "no_band_artefact"):
            self.run_manifest("--check", "head_front", c, "true")
        self.run_manifest("--cosmetic", "head_front", "hair length")
        _, rows = self.run_manifest("--judge", "head_front")
        self.assertEqual(rows["head_front"]["verdict"], "WEAK")   # cosmetic never fails
        self.run_manifest("--check", "head_front", "yaw_in_tolerance", "false")
        _, rows = self.run_manifest("--judge", "head_front")
        self.assertEqual((rows["head_front"]["verdict"], rows["head_front"]["fail_count"]), ("REJECT", 1))
        r, _ = self.run_manifest("--accept-as-is", "head_front")
        self.assertNotEqual(r.returncode, 0)   # not before the re-roll
        _, rows = self.run_manifest("--judge", "head_front")
        _, rows = self.run_manifest("--accept-as-is", "head_front")
        self.assertEqual(rows["head_front"]["verdict"], "WEAK")
        self.assertIn("accepted as-is", rows["head_front"]["verdict_note"])


class MakeKit(unittest.TestCase):
    def test_emits_the_new_row_keys_and_copies_age_and_complexion(self):
        sys.path.insert(0, str(REPO / "tools"))
        import make_kit
        spec = json.loads((REPO / "characters" / "vampire-dracula-sheet.json").read_text(encoding="utf-8-sig"))
        spec.update(age="fifties", complexion="pale", row_overrides={"hands": {"supplied_by": "Dom", "file": "h.png"}})
        tpl = json.loads((REPO / "config" / "character_sheet_template.json").read_text(encoding="utf-8-sig"))
        kit = make_kit.build(spec, tpl)
        rows = {r["row_id"]: r for r in kit["rows"]}
        self.assertEqual((kit["age"], kit["complexion"]), ("fifties", "pale"))
        self.assertEqual((rows["head_profile"]["yaw_deg"], rows["head_profile"]["ref_mode"], rows["head_front"]["ref_mode"]), (90, "sheet", "identity"))
        self.assertEqual(rows["body_front"]["needs"], [])
        self.assertEqual((rows["hands"]["supplied_by"], rows["hands"]["file"]), ("Dom", "h.png"))
        self.assertNotIn("yaw_deg", rows["body_front"])


class KeepAwake(unittest.TestCase):
    def test_never_raises(self):
        sys.path.insert(0, str(REPO / "tools"))
        import cf_common
        cf_common.keep_awake()


if __name__ == "__main__":
    unittest.main()
