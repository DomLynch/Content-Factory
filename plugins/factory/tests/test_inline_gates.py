"""Offline tests for plugin 0.1.23: the token and publish gates run inside run_3d.py / publish.py themselves, so a run outside Claude Code (bench\\*.ps1, a plain terminal) is gated too."""
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


class Inline(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.repo = Path(self.t.name) / "repo"
        for sub in ("tools", "config", "plugins/factory/lib"):
            shutil.copytree(REPO / sub, self.repo / sub, ignore=shutil.ignore_patterns("__pycache__", "config.local.json"))
        loc = {"python": PY, "outputs_dir": str(Path(self.t.name) / "out"), "logs_dir": str(Path(self.t.name) / "logs"), "trellis_repo_dir": str(Path(self.t.name) / "tr"),
               "work_dir": self.t.name}
        (self.repo / "config" / "config.local.json").write_text(json.dumps(loc), encoding="utf-8")

    def run3d(self, **env):
        e = {k: v for k, v in os.environ.items() if k not in ("FACTORY_RAW", "FACTORY_HOME")}
        e.update(env)
        return subprocess.run([PY, str(self.repo / "tools" / "run_3d.py"), "nosuch", "x.png", "--preset", "tokens98k_fresh", "--from-latents", "y.pt"],
                              capture_output=True, text=True, env=e, cwd=str(self.repo))

    def test_run_3d_is_refused_by_the_token_gate_without_a_hook(self):
        r = self.run3d()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("TOKEN GATE", r.stdout)

    def test_factory_raw_is_logged_and_lets_it_through_the_gate(self):
        r = self.run3d(FACTORY_RAW="test-reason")
        self.assertIn("GATE OVERRIDDEN", r.stdout)
        self.assertNotIn("TOKEN GATE: re-bake", r.stderr)

    def test_a_preset_with_no_tier_is_not_gated(self):
        e = dict(os.environ)
        e.pop("FACTORY_RAW", None)
        r = subprocess.run([PY, str(self.repo / "tools" / "run_3d.py"), "nosuch", "x.png", "--preset", "draft_fast"], capture_output=True, text=True, env=e, cwd=str(self.repo))
        self.assertNotIn("TOKEN GATE", r.stdout + r.stderr)

    def test_plugin_gate_helper_fails_open(self):
        code = ("import sys; sys.path.insert(0, r'%s'); import cf_common; cf_common.plugin_gate('nosuchkind', 1); print('allowed')" % (self.repo / "tools"))
        r = subprocess.run([PY, "-c", code], capture_output=True, text=True, cwd=str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("allowed", r.stdout)


if __name__ == "__main__":
    unittest.main()
