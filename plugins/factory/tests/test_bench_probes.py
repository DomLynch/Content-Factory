"""Offline tests for plugin 0.1.23 bench\\flux_multi.py: the expanded plan (CF_DRY, no torch) and the compare step on fake result files. No GPU."""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
PY = sys.executable


def png(path: Path, v: int):
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([v]) * 8 for _ in range(8))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class Multi(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d = Path(self.t.name)
        self.repo = self.d / "repo"
        for sub in ("tools", "config", "plugins/factory/lib", "flux/patches", "bench"):
            shutil.copytree(REPO / sub, self.repo / sub, ignore=shutil.ignore_patterns("__pycache__", "config.local.json"))
        loc = {"python": PY, "outputs_dir": str(self.d / "out"), "logs_dir": str(self.d / "logs"), "work_dir": str(self.d), "flux_pkgs_dir": str(self.d), "flux_model_dir": str(self.d)}
        (self.repo / "config" / "config.local.json").write_text(json.dumps(loc), encoding="utf-8")
        self.ref = self.d / "ref.png"
        png(self.ref, 100)
        self.kit = self.d / "kit.json"
        self.kit.write_text(json.dumps({"kit": "k", "seed": 5, "w": 1024, "h": 1536, "ref": str(self.ref), "ref_prefix": "Edit:", "subject": "s", "style": "st", "rows": [
            {"name": "head_front", "row_id": "head_front", "prompt": "h", "w": 1024, "h": 1024}, {"name": "gear_cuffs", "row_id": "gear_cuffs", "prompt": "c"}]}))

    def engine(self, *args, dry=True):
        env = dict(os.environ, **({"CF_DRY": "1"} if dry else {}))
        return subprocess.run([PY, str(self.repo / "bench" / "flux_multi.py"), *args], capture_output=True, text=True, env=env, cwd=str(self.repo))

    def test_dry_plan_expands_jobs_and_keeps_pngs_in_speed_tests(self):
        plan = [{"row": "head_front", "tag": "base"}, {"row": "gear_cuffs", "tag": "nr", "use_ref": False, "vae_tiling": False, "tunable": "tune", "skip_gc": True}]
        r = self.engine(str(self.kit), "--plan", json.dumps(plan), "--out", str(self.d / "res.json"))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        j = out["jobs"]
        self.assertEqual([x["tag"] for x in j], ["base", "nr"])
        self.assertEqual(j[0]["ref"], str(self.ref))
        self.assertIsNone(j[1]["ref"])
        self.assertEqual((j[1]["vae_tiling"], j[1]["tunable"], j[1]["skip_gc"]), (False, "tune", True))
        for x in j:
            self.assertIn("_speed_tests", x["png"])
            self.assertNotIn("vampire_gpt_sheet", x["png"])
        self.assertFalse((self.d / "res.json").exists())   # a dry run writes no result

    def test_unknown_row_exits_with_the_row_name(self):
        r = self.engine(str(self.kit), "--plan", json.dumps([{"row": "nope"}]), "--out", str(self.d / "res.json"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nope", r.stdout + r.stderr)

    def test_compare_pairs_jobs_and_reports_identity_diff_and_speedup(self):
        a, b, c = self.d / "a.png", self.d / "b.png", self.d / "c.png"
        png(a, 100); png(b, 100); png(c, 104)
        run_a = {"kit_name": "k", "jobs": [{"row": "head_front", "tag": "r", "png": str(a), "sha256": "x1", "gen_s": 100.0, "wall_s": 140.0},
                                            {"row": "gear_cuffs", "tag": "r", "png": str(a), "sha256": "x1", "gen_s": 50.0}]}
        run_b = {"kit_name": "k", "jobs": [{"row": "head_front", "tag": "r", "png": str(b), "sha256": "x1", "gen_s": 90.0, "cache_hits": 3, "cache_misses": 4},
                                            {"row": "gear_cuffs", "tag": "r", "png": str(c), "sha256": "x2", "gen_s": 50.0}]}
        (self.d / "A.json").write_text(json.dumps(run_a))
        (self.d / "B.json").write_text(json.dumps(run_b))
        rec = self.d / "receipts" / "k-head_front-abc"
        rec.mkdir(parents=True)
        (rec / "flux-20261101T000000Z.json").write_text(json.dumps({"status": "ok", "timing": {"generate_s": 67.5, "wall_s": 100.0}}))
        r = self.engine("--compare", str(self.d / "A.json"), str(self.d / "B.json"), str(self.d / "cmp.json"), "--receipts-dir", str(self.d / "receipts"), dry=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        rep = json.loads((self.d / "cmp.json").read_text())
        hf, gc = rep["pairs"]
        self.assertTrue(hf["identical_sha256"])
        self.assertEqual(hf["gen_speedup_pct_b_vs_a"], 10.0)
        self.assertEqual(hf["queue_receipt"]["generate_s"], 67.5)
        self.assertEqual(hf["b"]["cache_hits"], 3)
        self.assertFalse(gc["identical_sha256"])
        self.assertEqual(gc["mean_abs_diff"], 4.0)
        self.assertFalse(rep["pixel_identical_all"])


if __name__ == "__main__":
    unittest.main()
