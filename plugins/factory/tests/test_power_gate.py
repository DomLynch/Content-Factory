"""Offline tests for plugin 0.1.28: the power gate (cf_proc.power_state / power_reason, cf_common.plugin_gate("power")). No hardware: FACTORY_FAKE_POWER stands in."""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "plugins" / "factory" / "lib"))
sys.path.insert(0, str(REPO / "tools"))
import cf_common  # noqa: E402

LIM = {"power_refuse_schemes": ["silent"]}


class Power(unittest.TestCase):
    def setUp(self):
        self.cf_proc = importlib.import_module("cf_proc")
        self.old = (cf_common.load_config, cf_common.ALLOW_BATTERY, cf_common.POWER, cf_common.REPO, os.environ.get("FACTORY_FAKE_POWER"))
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        cf_common.load_config = lambda: {"limits": LIM}
        cf_common.REPO = self.t.name
        self.addCleanup(self.restore)

    def restore(self):
        cf_common.load_config, cf_common.ALLOW_BATTERY, cf_common.POWER, cf_common.REPO = self.old[:4]
        if self.old[4] is None:
            os.environ.pop("FACTORY_FAKE_POWER", None)
        else:
            os.environ["FACTORY_FAKE_POWER"] = self.old[4]

    def test_reasons(self):
        r = self.cf_proc.power_reason
        self.assertEqual(r({"on_battery": False, "scheme": "Performance", "battery_status": 2}, LIM), "")
        self.assertEqual(r({"on_battery": None, "scheme": None}, LIM), "")   # cannot tell: allow
        self.assertIn("on battery", r({"on_battery": True, "scheme": "Turbo", "battery_status": 1}, LIM))
        self.assertIn("scheme is Silent", r({"on_battery": False, "scheme": "Silent"}, LIM))
        both = r({"on_battery": True, "scheme": "silent", "battery_status": 1}, LIM)
        self.assertTrue("battery" in both and "Silent".lower() in both.lower() and "--allow-battery" in both)

    def test_fake_state_and_gate(self):
        os.environ["FACTORY_FAKE_POWER"] = "battery|Silent"
        self.assertEqual(self.cf_proc.power_state(), {"battery_status": 1, "on_battery": True, "scheme": "Silent"})
        cf_common.ALLOW_BATTERY = None
        with self.assertRaises(SystemExit) as e:
            cf_common.plugin_gate("power")
        self.assertEqual(e.exception.code, 2)
        cf_common.ALLOW_BATTERY = "Dom: short bind test on the road"
        cf_common.plugin_gate("power")
        rec = json.loads(Path(cf_common.write_receipt({"local": {}}, "a", "flux", tool="t", model={})).read_text())
        self.assertEqual((rec["power"]["on_battery"], rec["power"]["scheme"], rec["power"]["allow_battery"]), (True, "Silent", "Dom: short bind test on the road"))

    def test_ac_passes_and_is_recorded(self):
        os.environ["FACTORY_FAKE_POWER"] = "ac|Performance"
        cf_common.ALLOW_BATTERY = None
        cf_common.plugin_gate("power")
        self.assertEqual((cf_common.POWER["on_battery"], cf_common.POWER["scheme"], cf_common.POWER["allow_battery"]), (False, "Performance", None))

    def test_allow_battery_is_taken_out_of_argv_and_the_entry_scripts_call_the_gate(self):
        old = sys.argv[:]
        try:
            sys.argv = ["x", "kit", "--allow-battery", "why", "row"]
            self.assertEqual(cf_common.pop_allow_stale() or cf_common.ALLOW_BATTERY, "why")
            self.assertEqual(sys.argv, ["x", "kit", "row"])
        finally:
            sys.argv = old
        for rel in ("flux/flux_kit.py", "tools/run_3d.py", "tools/run_pixal3d.py", "tools/upscale/upscale_match.py"):
            self.assertIn('plugin_gate("power")', (REPO / rel).read_text(encoding="utf-8"), rel)
        cfg = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(cfg["limits"]["power_refuse_schemes"], ["silent"])


if __name__ == "__main__":
    unittest.main()
