"""Offline tests for plugin 0.1.20: the three sheet rules in the Flux gate (reference first, mirrors, re-roll cap). No GPU, no Flux; receipts are written to a temp home."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_flux import Kit, cf_flux  # noqa: E402


class Sheet(Kit):
    def setUp(self):
        super().setUp()
        self.kit["kit"] = "k"
        self.kit["rows"] += [{"name": "head_profile", "row_id": "head_profile", "group": "head", "prompt": "p"},
                             {"name": "hands_left", "row_id": "hands_left", "group": "hands", "prompt": "l"},
                             {"name": "hands_right", "row_id": "hands_right", "group": "hands", "prompt": "r"}]
        self.path.write_text(json.dumps(self.kit))
        self.old_home = cf_flux.cf_guard.HOME
        cf_flux.cf_guard.HOME = self.d
        self.addCleanup(lambda: setattr(cf_flux.cf_guard, "HOME", self.old_home))

    def receipt(self, rid, status="ok", n=0, row=None):
        row = row or self.row(rid)
        prompt, seed = cf_flux.row_prompt_seed(self.kit, row)
        d = self.d / "receipts" / f"k-{rid}-abc{n}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"flux-2026110{n}T000000Z.json").write_text(json.dumps({"status": status, "params": {"prompt": prompt, "seed": seed, "ref_sha256": cf_flux._sha(self.ref)}}))

    def sheet(self, rid):
        return cf_flux.sheet_reasons(self.kit, self.row(rid), str(self.path))

    def test_siblings_wait_for_the_identity_cell(self):
        self.assertTrue(any("reference first" in x and "head_front" in x for x in self.sheet("head_profile")))
        self.assertEqual(self.sheet("head_front"), [])   # the identity cell itself renders first
        self.receipt("head_front", status="fail")
        self.assertTrue(self.sheet("head_profile"))   # a failed render is not an identity
        self.receipt("head_front", n=1)
        self.assertEqual(self.sheet("head_profile"), [])

    def test_groups_without_an_identity_row_are_not_held(self):
        self.assertEqual(self.sheet("hands_left"), [])
        self.assertEqual(self.sheet("coat"), [])

    def test_kit_identity_map_overrides_the_default(self):
        self.kit["identity"] = {"head": "head_profile"}
        self.assertEqual(self.sheet("head_profile"), [])
        self.assertTrue(any("head_profile" in x for x in self.sheet("head_front")))

    def test_mirror_rows_are_refused(self):
        (self.d / "mirrors.json").write_text(json.dumps({"hands_right": "hands_left"}))
        r = self.sheet("hands_right")
        self.assertTrue(any("mirror of hands_left" in x and "mirror_cell.py" in x for x in r), r)
        self.assertEqual(self.sheet("hands_left"), [])

    def test_third_identical_roll_is_refused(self):
        self.receipt("coat", n=1)
        self.assertEqual(self.sheet("coat"), [])
        self.receipt("coat", n=2)
        r = self.sheet("coat")
        self.assertTrue(any("re-roll cap" in x and "rolled 2 times" in x for x in r), r)

    def test_a_documented_change_makes_a_new_roll(self):
        self.receipt("coat", n=1)
        self.receipt("coat", n=2)
        self.row("coat")["prompt"] = "x, longer cuffs"   # QC prompt_fix
        self.assertEqual(self.sheet("coat"), [])
        self.row("coat")["prompt"] = "x"
        self.kit["seed"] = 7   # new seed
        self.assertEqual(self.sheet("coat"), [])

    def test_check_row_adds_sheet_rules_only_with_a_kit_path(self):
        self.receipt("coat", n=1)
        self.receipt("coat", n=2)
        self.assertEqual(cf_flux.check_row(self.kit, self.row("coat"), {}), [])
        self.assertTrue(cf_flux.check_row(self.kit, self.row("coat"), {}, str(self.path)))

    def test_a_broken_kit_never_blocks(self):
        self.assertEqual(cf_flux.sheet_reasons({}, {}, None), [])


if __name__ == "__main__":
    unittest.main()
