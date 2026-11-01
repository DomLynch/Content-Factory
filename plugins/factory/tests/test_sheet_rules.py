"""Offline tests for the sheet rules in the Flux gate (plugin 0.1.20, verdict-based since 0.1.22): reference first, mirror_of, re-roll cap. No GPU, no Flux; receipts and manifests are written to a temp home."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_flux import Kit, cf_flux, PLUGIN, REPO, PY, MCP  # noqa: E402


class Sheet(Kit):
    def setUp(self):
        super().setUp()
        self.kit["kit"] = "k"
        self.kit["rows"] += [{"name": "head_profile", "row_id": "head_profile", "group": "head", "prompt": "p"},
                             {"name": "hands_left", "row_id": "hands_left", "group": "hands", "prompt": "l"},
                             {"name": "hands_right", "row_id": "hands_right", "group": "hands", "prompt": "r", "mirror_of": "hands_left"}]
        self.path.write_text(json.dumps(self.kit))
        self.old_home = cf_flux.cf_guard.HOME
        cf_flux.cf_guard.HOME = self.d
        self.addCleanup(lambda: setattr(cf_flux.cf_guard, "HOME", self.old_home))

    def receipt(self, rid, status="ok", n=0):
        prompt, seed = cf_flux.row_prompt_seed(self.kit, self.row(rid))
        d = self.d / "receipts" / f"k-{rid}-abc{n}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"flux-2026110{n}T000000Z.json").write_text(json.dumps({"status": status, "params": {"prompt": prompt, "seed": seed, "ref_sha256": cf_flux._sha(self.ref)}}))

    def verdict(self, rid, v):
        (self.d / "manifests").mkdir(exist_ok=True)
        p = self.d / "manifests" / "k.json"
        m = json.loads(p.read_text()) if p.exists() else {"rows": {}}
        m["rows"].setdefault(rid, {})["verdict"] = v
        p.write_text(json.dumps(m))

    def pick(self, rid, by="Dom", sha="s1", candidates=("s1",)):
        p = self.d / "manifests" / "k.json"
        m = json.loads(p.read_text()) if p.exists() else {"rows": {}}
        e = m["rows"].setdefault(rid, {})
        e.update(pick=sha, picked_by=by, candidates=[{"sha256": c} for c in candidates])
        p.write_text(json.dumps(m))

    def sheet(self, rid):
        return cf_flux.sheet_reasons(self.kit, self.row(rid), str(self.path))

    def test_siblings_wait_for_a_pass_verdict_on_the_identity_cell(self):
        self.assertTrue(any("reference first" in x and "head_front" in x and "'none'" in x for x in self.sheet("head_profile")))
        self.assertEqual(self.sheet("head_front"), [])   # the identity cell itself renders first
        self.receipt("head_front")   # an ok render only proves non-black: not a pass
        self.assertTrue(self.sheet("head_profile"))
        self.verdict("head_front", "WEAK")
        self.assertTrue(any("'WEAK'" in x for x in self.sheet("head_profile")))
        self.verdict("head_front", "PASS")
        self.assertTrue(any("Dom's pick" in x for x in self.sheet("head_profile")))   # QC PASS alone is not enough
        self.pick("head_front")
        self.assertEqual(self.sheet("head_profile"), [])

    def test_the_pick_must_be_Doms_and_current(self):
        self.verdict("head_front", "PASS")
        self.pick("head_front", by="Hooks Dev")
        self.assertTrue(any("not Dom" in x for x in self.sheet("head_profile")))
        self.pick("head_front", by="Dom", sha="s1", candidates=("s1", "s2"))   # re-rendered after the pick
        self.assertTrue(any("stale" in x for x in self.sheet("head_profile")))
        self.pick("head_front", by="Dom", sha="s2", candidates=("s1", "s2"))
        self.assertEqual(self.sheet("head_profile"), [])

    def test_only_Doms_explicit_words_in_the_kit_waive_the_pick_and_not_the_qc_verdict(self):
        self.kit["skip_identity_review"] = "yes"
        self.verdict("head_front", "PASS")
        self.assertTrue(any("Dom's pick" in x for x in self.sheet("head_profile")))   # 'yes' is not the words
        self.kit["skip_identity_review"] = "Dom 2026-11-02: skip identity review"
        self.assertEqual(self.sheet("head_profile"), [])
        self.verdict("head_front", "WEAK")
        self.assertTrue(any("QC verdict" in x for x in self.sheet("head_profile")))   # the waiver never waives QC

    def test_humanoid_head_rows_need_age_and_complexion(self):
        del self.kit["age"]
        r = self.sheet("head_front")
        self.assertTrue(any("no age" in x for x in r) and not any("complexion" in x for x in r), r)
        self.kit["complexion"] = ""
        r = self.sheet("head_front")
        self.assertTrue(any("age and complexion" in x for x in r), r)
        self.kit["humanoid"] = False
        self.assertEqual(self.sheet("head_front"), [])   # a creature kit opts out
        self.assertEqual(self.sheet("coat"), [])   # gear rows never needed them

    def test_a_kit_without_an_identity_cell_is_refused_with_the_reason(self):
        self.kit["rows"] = [r for r in self.kit["rows"] if r["row_id"] != "head_front"]
        r = self.sheet("head_profile")
        self.assertTrue(any("no identity cell head_front" in x and "identity" in x for x in r), r)

    def test_groups_without_an_identity_default_are_not_held(self):
        self.assertEqual(self.sheet("hands_left"), [])
        self.assertEqual(self.sheet("coat"), [])

    def test_kit_identity_map_overrides_the_default(self):
        self.kit["identity"] = {"head": "head_profile"}
        self.assertEqual(self.sheet("head_profile"), [])
        self.assertTrue(any("head_profile" in x for x in self.sheet("head_front")))

    def test_mirror_of_rows_are_refused(self):
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


class Wiring(Sheet):
    def hook(self, payload):
        r = subprocess.run([PY, str(PLUGIN / "hooks" / "gates.py")], input=json.dumps(payload), capture_output=True, text=True,
                           env=dict(os.environ, FACTORY_HOME=str(self.d)))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def test_mcp_flux_render_path_applies_the_sheet_rules(self):
        out = self.hook({"tool_name": MCP + "flux_render", "tool_input": {"kit": str(self.path), "row_id": "head_profile"}, "cwd": str(self.d)})
        self.assertIn("reference first", json.dumps(out))
        self.verdict("head_front", "PASS")
        self.pick("head_front")
        out = self.hook({"tool_name": MCP + "flux_render", "tool_input": {"kit": str(self.path), "row_id": "head_profile"}, "cwd": str(self.d)})
        self.assertNotIn("SHEET RULE", json.dumps(out))

    def test_manifest_records_a_verdict(self):
        import shutil   # manifest.py needs config.local.json (absent in CI): run it in a throw-away copy of the repo
        repo = self.d / "repo"
        for sub in ("tools", "config", "plugins/factory/lib"):
            shutil.copytree(REPO / sub, repo / sub, ignore=shutil.ignore_patterns("__pycache__", "config.local.json"))
        (repo / "config" / "config.local.json").write_text(json.dumps({"outputs_dir": str(self.d / "out")}), encoding="utf-8")
        run = lambda *a: subprocess.run([PY, str(repo / "tools" / "manifest.py"), str(self.path), *a], capture_output=True, text=True, cwd=str(repo))
        r = run("--verdict", "nosuchrow", "PASS")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown row or verdict", r.stdout + r.stderr)
        r = run("--verdict", "head_front", "PASS", "--score", "9", "--prompt-fix", "x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = json.loads((repo / "manifests" / "k.json").read_text())["rows"]["head_front"]
        self.assertEqual((row["verdict"], row["score"], row["prompt_fix"]), ("PASS", 9, "x"))

    def test_server_version_follows_plugin_json(self):
        src = (PLUGIN / "mcp" / "server.py").read_text(encoding="utf-8")
        self.assertIn('plugin.json', src)
        self.assertNotIn('SERVER_VERSION = "0.1.9"', src)


if __name__ == "__main__":
    unittest.main()
