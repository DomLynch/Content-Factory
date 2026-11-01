"""Offline tests for the Pixal3D tool (plugin 0.1.10). No Pixal3D import, no GPU, no weights: the stage script and the MCP tool are exercised against a throw-away home."""
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

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
os.environ["FACTORY_HOME"] = str(REPO)
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(PLUGIN / "mcp"))
sys.path.insert(0, str(REPO / "tools"))
PY = sys.executable
MCP = "mcp__plugin_factory_factory__"


def png(path: Path, w: int, h: int) -> str:
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80" * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return str(path)


def hook(name, payload):
    r = subprocess.run([PY, str(PLUGIN / "hooks" / name)], input=json.dumps(payload), capture_output=True, text=True,
                       env=dict(os.environ, FACTORY_HOME=str(REPO), FACTORY_TEST_STAGE_TAG="no-such-stage-process", FACTORY_FAKE_FREE_RAM_GB="20"), timeout=40)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else {}


class Wiring(unittest.TestCase):
    def test_config_presets_and_pipeline_label(self):
        c = json.load(open(REPO / "config" / "config.json", encoding="utf-8-sig"))
        self.assertIn("standard", c["pixal3d"]["presets"])
        self.assertEqual(c["pixal3d"]["env"]["ATTN_BACKEND"], "sdpa")
        self.assertIn("PIXAL3D", c["gates"]["pipelines"])

    def test_tool_registered_and_hooks_cover_it(self):
        import server
        self.assertIn("pixal3d_run", server.TOOLS)
        h = json.load(open(PLUGIN / "hooks" / "hooks.json", encoding="utf-8-sig"))
        text = json.dumps(h)
        for script in ("gpu_lock_guard.py", "ram_gate.py", "input_gate.py"):
            self.assertRegex(text, r"pixal3d_run\"[^]]*" + script.replace(".", r"\."))
        self.assertIn("run_pixal3d", text)

    def test_hooks_refuse_through_the_gates(self):
        d = Path(tempfile.mkdtemp())
        lock = d / ".gpu_lock"
        lock.write_text(json.dumps({"pid": os.getpid(), "job": "flux", "started_utc": "now"}))
        env = dict(os.environ, FACTORY_GPU_LOCK=str(lock), FACTORY_HOME=str(REPO), FACTORY_TEST_STAGE_TAG="no-such-stage-process")
        r = subprocess.run([PY, str(PLUGIN / "hooks" / "gpu_lock_guard.py")], input=json.dumps({"tool_name": MCP + "pixal3d_run", "tool_input": {}}),
                           capture_output=True, text=True, env=env, timeout=40)
        self.assertIn("deny", r.stdout)
        small = png(d / "small.png", 512, 512)
        out = hook("input_gate.py", {"tool_name": MCP + "pixal3d_run", "tool_input": {"asset_id": "x", "image": small}})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        out = hook("plugin_guard.py", {"tool_name": "Bash", "tool_input": {"command": "python tools/run_pixal3d.py a b.png"}})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_tool_refuses_unknown_preset_and_missing_file(self):
        import server
        with open(REPO / "config" / "config.json", encoding="utf-8-sig") as f1, open(REPO / "config" / "config.local.example.json", encoding="utf-8-sig") as f2:
            conf = {**json.load(f1), "local": json.load(f2)}
        server.cfg = lambda: conf
        self.assertEqual(server.t_pixal3d_run("a", "nope.png", "no-such-preset")["status"], "refused")
        self.assertEqual(server.t_pixal3d_run("a", str(Path(tempfile.mkdtemp()) / "missing.png"))["status"], "refused")

    def test_pipeline_label(self):
        import cf_gates
        import cf_guard
        d = Path(tempfile.mkdtemp())
        (d / "receipts" / "asset").mkdir(parents=True)
        (d / "receipts" / "asset" / "pixal3d-20261101T000000Z.json").write_text("{}")
        old = cf_guard.HOME
        cf_guard.HOME = d
        try:
            self.assertEqual(cf_gates.asset_pipeline("asset"), "PIXAL3D")
        finally:
            cf_guard.HOME = old


class Entry(unittest.TestCase):
    def test_rmbg_is_rewritten_to_the_mit_birefnet(self):
        import pixal3d_entry
        d = Path(tempfile.mkdtemp())
        (d / "pipeline.json").write_text(json.dumps({"args": {"rembg_model": {"name": "BiRefNet", "args": {"model_name": "briaai/RMBG-2.0"}}}}))
        name = pixal3d_entry.ensure_licence_clean_rembg(str(d), "C:/models/BiRefNet")
        self.assertEqual(name, "C:/models/BiRefNet")
        self.assertTrue((d / "pipeline.json.orig").exists())
        self.assertEqual(pixal3d_entry.ensure_licence_clean_rembg(str(d), "other"), "C:/models/BiRefNet")   # idempotent

    def test_stage_script_refuses_when_paths_are_missing(self):
        root = Path(tempfile.mkdtemp()) / "repo"
        for sub in ("tools", "config"):
            shutil.copytree(REPO / sub, root / sub, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(root / "config" / "config.local.example.json", root / "config" / "config.local.json")
        img = png(root / "in.png", 64, 64)
        r = subprocess.run([PY, str(root / "tools" / "run_pixal3d.py"), "asset", img], capture_output=True, text=True, timeout=60, cwd=str(root))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("missing", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
