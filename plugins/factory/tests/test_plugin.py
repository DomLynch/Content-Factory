"""Offline tests for the factory plugin (standard library only; run: python -m unittest discover plugins/factory/tests -v).

Nothing here runs a factory stage, a GPU job, Blender, Flux or TRELLIS. The MCP server is tested for the JSON-RPC handshake and for its refusals, against a
throw-away FACTORY_HOME whose scripts do not exist (so a tool that wrongly tried to start a job would fail loudly instead of running anything).
The hooks are tested with synthetic PreToolUse JSON on stdin.
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
sys.path.insert(0, str(PLUGIN / "lib"))
import cf_cmd  # noqa: E402

PY = sys.executable
MCP_P = "mcp__plugin_factory_factory__"
PROVEN = {"character-sheet", "trellis-part", "bind-humanoid"}
DRAFT = {"bind-beast", "assemble-character", "stills-and-turntable", "publish-to-library", "environment", "hero-rebake"}


def png(path: Path, w: int, h: int) -> str:
    """A valid grey PNG of the given size (header-correct; small enough to build in memory)."""
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80" * w for _ in range(h))
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    path.write_bytes(data)
    return str(path)


def run_hook(name: str, payload: dict, **env) -> dict | None:
    # hermetic: a real stage process running on this box (the Factory's own job) must not trip the 0.1.5 orphan check
    e = dict(os.environ, FACTORY_TEST_STAGE_TAG="no-such-stage-process", **{k: str(v) for k, v in env.items()})
    r = subprocess.run([PY, str(PLUGIN / "hooks" / name)], input=json.dumps(payload), capture_output=True, text=True, env=e, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def denied(out) -> bool:
    return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"


def bash(cmd: str, **extra) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, **extra}


class Packaging(unittest.TestCase):
    def test_manifests_are_consistent(self):
        plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "factory")
        self.assertEqual(plugin["version"], "0.1.7")
        entry = market["plugins"][0]
        self.assertEqual((entry["name"], entry["source"], entry["version"]), ("factory", "./plugins/factory", plugin["version"]))
        self.assertEqual(market["name"], "content-factory")
        self.assertNotIn("0.1.0-draft", json.dumps([plugin, market]))

    def test_hooks_json_points_at_real_scripts_and_mcp_tools(self):
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        self.assertEqual(hooks[0]["matcher"], "Bash")
        scripts = set()
        for group in hooks:
            for h in group["hooks"]:
                m = re.search(r'hooks/(\w+\.py)', h["command"])
                self.assertTrue(m and (PLUGIN / "hooks" / m.group(1)).is_file(), h["command"])
                scripts.add(m.group(1))
                self.assertIn("${CLAUDE_PLUGIN_ROOT}", h["command"])
        self.assertEqual(scripts, {"gpu_lock_guard.py", "ram_gate.py", "input_gate.py", "no_literals.py", "plugin_guard.py", "gates.py", "qc_gate.py"})
        sys.path.insert(0, str(PLUGIN / "mcp"))
        os.environ.setdefault("FACTORY_HOME", str(REPO))
        import server
        tools = set(server.TOOLS)
        for group in (g for g in hooks[1:] if g["matcher"].startswith(MCP_P)):
            names = group["matcher"][len(MCP_P):].strip("()").split("|")
            self.assertTrue(set(names) <= tools, names)

    def test_mcp_json_follows_the_bonsai_convention(self):
        mcp = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["factory"]
        self.assertEqual(mcp["command"], "python")
        self.assertEqual(mcp["args"], ["${CLAUDE_PLUGIN_ROOT}/mcp/server.py"])
        self.assertIn("FACTORY_HOME", mcp["env"])
        self.assertTrue((PLUGIN / "mcp" / "server.py").is_file())

    def test_commands_have_frontmatter(self):
        for name in ("factory-status", "factory-run", "factory-publish"):
            text = (PLUGIN / "commands" / f"{name}.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\ndescription:"), name)
        self.assertIn("--dry-run", (PLUGIN / "commands" / "factory-run.md").read_text(encoding="utf-8"))
        self.assertIn("--dry-run", (PLUGIN / "commands" / "factory-publish.md").read_text(encoding="utf-8"))

    def test_skills_banner_rule_and_required_dos_donts(self):
        required = ["ONE mesh", "LOOK at every image", "9/10", "BENCHMARK profile", "2048 px or more", "three-point light rig", "below 8/10", "v1, v2, v3"]
        found = {p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md")}
        self.assertEqual(found, PROVEN | DRAFT)
        for name in found:
            text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith(f"---\nname: {name}\ndescription: "), name)
            body = text.split("---\n", 2)[2]
            if name in DRAFT:
                self.assertTrue(body.lstrip().startswith("> **STATUS: DRAFT, not yet run end to end"), name)
            else:
                self.assertNotIn("STATUS: DRAFT", text, name)
            tail = text[text.index("## Do / Don't"):]
            for phrase in required:
                self.assertIn(phrase, tail, f"{name}: missing '{phrase}'")
            self.assertIn("Don't", tail)


class CommandReader(unittest.TestCase):
    def test_only_real_python_invocations_match(self):
        names = ("run_3d.py", "flux_kit.py")
        self.assertEqual(cf_cmd.script_calls("cat flux_kit.py", names), [])
        self.assertEqual(cf_cmd.script_calls('git commit -m "fix run_3d.py"', names), [])
        self.assertEqual(cf_cmd.script_calls("python -u flux/flux_kit.py k.json r 2>&1 | tail -5", names), [("flux_kit.py", ["k.json", "r"])])
        calls = cf_cmd.script_calls(r'"C:\Program Files\py\python.exe" "C:\a b\tools\run_3d.py" id "C:\x y\img.png" --preset p && echo hi', names)
        self.assertEqual(calls, [("run_3d.py", ["id", r"C:\x y\img.png", "--preset", "p"])])


class GpuLockHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lock = Path(self.tmp.name) / ".gpu_lock"

    def tearDown(self):
        self.tmp.cleanup()

    def live(self):
        self.lock.write_text(json.dumps({"pid": os.getpid(), "job": "generate.py", "started_utc": "2026-11-01T00:00:00Z"}), encoding="utf-8")

    def test_live_lock_denies_starts(self):
        self.live()
        for cmd in ("python tools/run_3d.py a b.png", "python -u flux/flux_kit.py kits/k.json row", "python tools/run_character.py characters/x.json",
                    "powershell -File bench/run_kit.ps1 kits/k.json", "python generate.py img.png --preset master"):
            out = run_hook("gpu_lock_guard.py", bash(cmd), FACTORY_GPU_LOCK=self.lock)
            self.assertTrue(denied(out), cmd)
            self.assertIn("already running", out["hookSpecificOutput"]["permissionDecisionReason"])
        for tool in ("flux_render", "trellis_run", "upscale"):
            self.assertTrue(denied(run_hook("gpu_lock_guard.py", {"tool_name": MCP_P + tool, "tool_input": {}}, FACTORY_GPU_LOCK=self.lock)), tool)

    def test_allows(self):
        self.live()
        for cmd in ("cat flux_kit.py", "git log --oneline", "CF_DRY=1 python flux/flux_kit.py kits/k.json row", "python tools/run_bind.py a m.glb"):
            self.assertIsNone(run_hook("gpu_lock_guard.py", bash(cmd), FACTORY_GPU_LOCK=self.lock), cmd)
        self.assertIsNone(run_hook("gpu_lock_guard.py", {"tool_name": MCP_P + "factory_status", "tool_input": {}}, FACTORY_GPU_LOCK=self.lock))
        self.lock.write_text(json.dumps({"pid": 2147480000, "job": "x"}), encoding="utf-8")   # stale
        self.assertIsNone(run_hook("gpu_lock_guard.py", bash("python tools/run_3d.py a b.png"), FACTORY_GPU_LOCK=self.lock))
        self.lock.write_text("not json", encoding="utf-8")   # unreadable
        self.assertIsNone(run_hook("gpu_lock_guard.py", bash("python tools/run_3d.py a b.png"), FACTORY_GPU_LOCK=self.lock))
        self.lock.unlink()   # missing
        self.assertIsNone(run_hook("gpu_lock_guard.py", bash("python tools/run_3d.py a b.png"), FACTORY_GPU_LOCK=self.lock))


class RamGateHook(unittest.TestCase):
    def test_low_ram_denies_heavy_starts(self):
        for cmd in ("python tools/run_bind.py a m.glb", "python tools/run_3d.py a b.png", "python -u flux/flux_kit.py kits/k.json r", "python tools/run_stills.py a b.glb",
                    "python tools/upscale/upscale_match.py a.png b.png", "python tools/run_character.py characters/x.json", "python tools/assemble_character.py a"):
            out = run_hook("ram_gate.py", bash(cmd), FACTORY_FAKE_FREE_RAM_GB=1.5, FACTORY_HOME=REPO)
            self.assertTrue(denied(out), cmd)
            self.assertIn("1.5 GB", out["hookSpecificOutput"]["permissionDecisionReason"])
        for tool in ("flux_render", "trellis_run", "cutout", "bind", "stills", "upscale"):
            self.assertTrue(denied(run_hook("ram_gate.py", {"tool_name": MCP_P + tool, "tool_input": {}}, FACTORY_FAKE_FREE_RAM_GB=1.5, FACTORY_HOME=REPO)), tool)

    def test_the_limit_comes_from_config(self):
        cfg = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))
        need = cfg["limits"]["start_min_free_ram_gb"]
        self.assertEqual(need, 6)
        self.assertTrue(denied(run_hook("ram_gate.py", bash("python tools/run_bind.py a m.glb"), FACTORY_FAKE_FREE_RAM_GB=need - 0.1, FACTORY_HOME=REPO)))
        self.assertIsNone(run_hook("ram_gate.py", bash("python tools/run_bind.py a m.glb"), FACTORY_FAKE_FREE_RAM_GB=need, FACTORY_HOME=REPO))

    def test_allows(self):
        low = dict(FACTORY_FAKE_FREE_RAM_GB=0.5, FACTORY_HOME=REPO)
        for cmd in ("ls", "cat tools/run_3d.py", 'git commit -m "tune run_3d.py"', "python tools/run_character.py characters/x.json --dry-run",
                    "CF_DRY=1 python flux/flux_kit.py kits/k.json r", "python tools/run_3d.py --help"):
            self.assertIsNone(run_hook("ram_gate.py", bash(cmd), **low), cmd)
        self.assertIsNone(run_hook("ram_gate.py", {"tool_name": MCP_P + "factory_status", "tool_input": {}}, **low))
        self.assertIsNone(run_hook("ram_gate.py", bash("python tools/run_3d.py a b.png"), FACTORY_FAKE_FREE_RAM_GB=64, FACTORY_HOME=REPO))


class InputGateHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.small = png(d / "small.png", 512, 700)
        self.thin = png(d / "thin.png", 3000, 900)
        self.big = png(d / "big.png", 1100, 1500)

    def tearDown(self):
        self.tmp.cleanup()

    def test_short_side_under_1024_is_denied(self):
        for cmd in (f'python tools/run_3d.py a "{self.small}"', f'python tools/run_3d.py a "{self.thin}" --preset master_reduced', f'python generate.py "{self.small}" --preset master',
                    f'python tools/cutout_cpu.py "{self.small}" out.png'):
            out = run_hook("input_gate.py", bash(cmd))
            self.assertTrue(denied(out), cmd)
            self.assertIn("1024 px gate", out["hookSpecificOutput"]["permissionDecisionReason"])
        for tool in ("trellis_run", "cutout"):
            self.assertTrue(denied(run_hook("input_gate.py", {"tool_name": MCP_P + tool, "tool_input": {"asset_id": "a", "image": self.small}})), tool)

    def test_allows(self):
        for cmd in (f'python tools/run_3d.py a "{self.big}"', f'python tools/cutout_cpu.py "{self.big}" out.png', "python tools/run_3d.py a missing_file.png",
                    f'python tools/run_3d.py a "{self.small}" --from-latents x.latents.pt --out-name mesh_hero', f'cat "{self.small}"', "python tools/run_bind.py a m.glb"):
            self.assertIsNone(run_hook("input_gate.py", bash(cmd)), cmd)
        self.assertIsNone(run_hook("input_gate.py", {"tool_name": MCP_P + "trellis_run", "tool_input": {"asset_id": "a", "image": self.big}}))
        self.assertIsNone(run_hook("input_gate.py", {"tool_name": MCP_P + "bind", "tool_input": {"asset_id": "a", "mesh": self.small}}))

    def test_relative_path_uses_the_hook_cwd(self):
        self.assertTrue(denied(run_hook("input_gate.py", bash("python tools/run_3d.py a small.png", cwd=self.tmp.name))))
        self.assertIsNone(run_hook("input_gate.py", bash("python tools/run_3d.py a big.png", cwd=self.tmp.name)))


class NoLiteralsHook(unittest.TestCase):
    def test_inline_numbers_are_denied(self):
        for cmd in ("python generate.py img.png --preset master --texture-size 2048", "python generate.py img.png --bake-faces=500000", "python generate.py img.png --attn-auto-gb 28 --preset master",
                    "python -u flux/flux_kit.py kits/k.json row 1536 1024", "python flux/flux_kit.py kits/k.json row --seed 7", r'python C:\x\generate.py a.png --max-tokens 98304 && echo done'):
            out = run_hook("no_literals.py", bash(cmd))
            self.assertTrue(denied(out), cmd)
            self.assertIn("No literals", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_presets_rows_and_other_commands_pass(self):
        for cmd in ("python generate.py img.png --preset hero --device rocm", "python -u flux/flux_kit.py kits/k.json row 2>&1 | tail -5", "python flux/flux_kit.py kits/k.json row",
                    "python tools/run_3d.py a b.png --preset master_reduced", "python tools/run_bind.py a m.glb", "cat generate.py", "python generate.py --help",
                    "CF_DRY=1 python flux/flux_kit.py kits/k.json row"):
            self.assertIsNone(run_hook("no_literals.py", bash(cmd)), cmd)
        self.assertIsNone(run_hook("no_literals.py", {"tool_name": MCP_P + "trellis_run", "tool_input": {"preset": "master_reduced"}}))


class McpServer(unittest.TestCase):
    """The handshake and the refusals, over real stdio, against a fake FACTORY_HOME where no factory script exists."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        home = t / "home"
        (home / "config").mkdir(parents=True)
        (home / "kits").mkdir()
        (home / "receipts").mkdir()
        cfg = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))
        (home / "config" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        loc = {"python": PY, "outputs_dir": str(t / "out"), "logs_dir": str(t / "logs"), "guard_script": str(t / "no_guard.ps1"), "blender_exe": str(t / "none.exe"),
               "cutout_python": PY, "cutout_repo_dir": str(t), "trellis_repo_dir": str(t)}
        (home / "config" / "config.local.json").write_text(json.dumps(loc), encoding="utf-8")
        (t / "logs").mkdir()
        (home / "kits" / "k.json").write_text(json.dumps({"kit": "k", "rows": [{"name": "row1", "prompt": "p"}]}), encoding="utf-8")
        cls.home, cls.t = home, t
        cls.small = png(t / "small.png", 512, 512)
        cls.big = png(t / "big.png", 2048, 1100)
        (t / "mesh.glb").write_bytes(b"glTF")
        cls.lock = t / ".gpu_lock"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def talk(self, messages, **env):
        e = dict(os.environ, FACTORY_HOME=str(self.home), FACTORY_GPU_LOCK=str(self.lock), **{k: str(v) for k, v in env.items()})
        data = "\n".join(m if isinstance(m, str) else json.dumps(m) for m in messages) + "\n"
        r = subprocess.run([PY, str(PLUGIN / "mcp" / "server.py")], input=data, capture_output=True, text=True, env=e, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]

    def call(self, name, args, **env):
        out = self.talk([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}], **env)
        self.assertEqual(len(out), 1)
        res = out[0]["result"]
        return res["isError"], res["content"][0]["text"]

    def test_handshake(self):
        out = self.talk([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": "nope/never"},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "no_such_tool", "arguments": {}}},
            "this is not json",
        ])
        by_id = {m.get("id"): m for m in out}
        init = by_id[1]["result"]
        self.assertEqual(init["protocolVersion"], "2025-03-26")
        self.assertEqual(init["serverInfo"]["name"], "factory")
        self.assertIn("tools", init["capabilities"])
        tools = {t["name"]: t for t in by_id[2]["result"]["tools"]}
        self.assertEqual(set(tools), {"factory_status", "flux_render", "trellis_run", "cutout", "bind", "stills", "upscale", "qc"})
        for t in tools.values():
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(t["description"])
        self.assertEqual(tools["flux_render"]["inputSchema"]["required"], ["kit", "row_id"])
        self.assertEqual(tools["trellis_run"]["inputSchema"]["required"], ["asset_id", "image"])
        self.assertEqual(by_id[3]["result"], {})
        self.assertEqual(by_id[4]["error"]["code"], -32601)
        self.assertEqual(by_id[5]["error"]["code"], -32602)
        self.assertEqual(by_id[None]["error"]["code"], -32700)   # the bad line gets a parse error and the server stays up
        self.assertEqual(len(out), 6)                               # the notification got no reply

    def test_factory_status_is_read_only_and_reports_the_lock(self):
        err, text = self.call("factory_status", {}, FACTORY_FAKE_FREE_RAM_GB=64)
        self.assertFalse(err)
        st = json.loads(text)
        self.assertEqual((st["gpu"], st["free_ram_gb"], st["start_min_free_ram_gb"], st["ram_ok_to_start"]), ("free", 64.0, 6.0, True))
        self.lock.write_text(json.dumps({"pid": os.getpid(), "job": "generate.py", "started_utc": "x"}), encoding="utf-8")
        try:
            st = json.loads(self.call("factory_status", {}, FACTORY_FAKE_FREE_RAM_GB=2)[1])
        finally:
            self.lock.unlink()
        self.assertTrue(st["gpu"].startswith("busy"))
        self.assertTrue(st["lock"]["pid_alive"])
        self.assertFalse(st["ram_ok_to_start"])

    def test_live_lock_refuses_every_gpu_tool(self):
        self.lock.write_text(json.dumps({"pid": os.getpid(), "job": "flux_kit", "started_utc": "2026-11-01T00:00:00Z"}), encoding="utf-8")
        try:
            for name, args in (("flux_render", {"kit": "kits/k.json", "row_id": "row1"}), ("trellis_run", {"asset_id": "a", "image": self.big}),
                               ("upscale", {"src": self.big, "dst": str(self.t / "up.png")})):
                err, text = self.call(name, args, FACTORY_FAKE_FREE_RAM_GB=64)
                self.assertTrue(err, name)
                self.assertEqual(json.loads(text)["status"], "refused")
                self.assertIn("already running", text, name)
        finally:
            self.lock.unlink()

    def test_low_ram_refuses_every_heavy_tool(self):
        for name, args in (("flux_render", {"kit": "kits/k.json", "row_id": "row1"}), ("trellis_run", {"asset_id": "a", "image": self.big}), ("cutout", {"asset_id": "a", "image": self.big}),
                           ("bind", {"asset_id": "a", "mesh": str(self.t / "mesh.glb")}), ("stills", {"asset_id": "a", "glb": str(self.t / "mesh.glb")}),
                           ("upscale", {"src": self.big, "dst": str(self.t / "up.png")})):
            err, text = self.call(name, args, FACTORY_FAKE_FREE_RAM_GB=2.0)
            self.assertTrue(err, name)
            self.assertIn("2.0 GB of RAM is free", text, name)
            self.assertIn("needs 6.0 GB", text, name)

    def test_small_image_is_refused(self):
        for name in ("trellis_run", "cutout"):
            err, text = self.call(name, {"asset_id": "a", "image": self.small}, FACTORY_FAKE_FREE_RAM_GB=64)
            self.assertTrue(err)
            self.assertIn("short side 512 px is under the 1024 px gate", text)

    def test_bad_input_is_refused_before_anything_starts(self):
        self.assertIn("unknown preset", self.call("trellis_run", {"asset_id": "a", "image": self.big, "preset": "bogus"})[1])
        self.assertIn("not found", self.call("trellis_run", {"asset_id": "a", "image": str(self.t / "missing.png")})[1])
        self.assertIn("is not in k.json", self.call("flux_render", {"kit": "kits/k.json", "row_id": "nope"})[1])
        self.assertTrue(self.call("flux_render", {"kit": "kits/k.json"})[0])   # missing argument

    def test_a_stale_lock_and_free_ram_do_not_refuse_but_nothing_unguarded_runs(self):
        self.lock.write_text(json.dumps({"pid": 2147480000, "job": "old"}), encoding="utf-8")
        try:
            err, text = self.call("trellis_run", {"asset_id": "a", "image": self.big, "preset": "master_reduced"}, FACTORY_FAKE_FREE_RAM_GB=64)
        finally:
            self.lock.unlink()
        self.assertTrue(err)
        self.assertIn("guard script not found", text)   # passed every gate, then refused to start a GPU job without gpu_guard.ps1; no job ran

    def test_result_carries_the_receipt_path(self):
        sys.path.insert(0, str(PLUGIN / "mcp"))
        old = os.environ.get("FACTORY_HOME")
        os.environ["FACTORY_HOME"] = str(self.home)
        try:
            import importlib
            import cf_guard
            import server
            importlib.reload(cf_guard)
            importlib.reload(server)
            rec = self.home / "receipts" / "a1"
            rec.mkdir()
            (rec / "trellis-20261101T000000Z.json").write_text(json.dumps({"status": "ok", "error": "", "outputs": [{"path": "x/mesh.glb"}], "timing": {"wall_s": 5}, "memory": {"lowest_free_ram_gb": 9}}), encoding="utf-8")
            res = server.finish("trellis_run", 0, "log.txt", 0.0, ["cutout", "trellis"], "a1")
            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["receipt"].endswith("trellis-20261101T000000Z.json"))
            self.assertEqual(res["outputs"], ["x/mesh.glb"])
            self.assertEqual(server.finish("trellis_run", 1, "log.txt", 0.0, ["cutout", "trellis"], "a1")["status"], "fail")   # exit code failure wins over an ok receipt
            self.assertEqual(server.finish("trellis_run", 0, "log.txt", 9e12, ["trellis"], "a1")["status"], "fail")            # no fresh receipt: not done
        finally:
            if old is None:
                os.environ.pop("FACTORY_HOME", None)
            else:
                os.environ["FACTORY_HOME"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)


class PluginGuard(unittest.TestCase):
    """hooks/plugin_guard.py: a raw stage-script call is refused, plugin-spawned (token), logged override, dry run and reading are allowed."""

    def _decide(self, tool, cmd):
        sys.path.insert(0, str(PLUGIN / "hooks"))
        import plugin_guard
        return plugin_guard.decide({"tool_name": tool, "tool_input": {"command": cmd}})

    def test_raw_call_refused_on_bash_and_powershell(self):
        self.assertIn("plugin", self._decide("Bash", "python tools/run_3d.py a.png"))
        self.assertIn("plugin", self._decide("PowerShell", "python C:/x/flux_kit.py kit row"))

    def test_reading_dry_run_and_other_tools_allowed(self):
        self.assertEqual(self._decide("Bash", "cat tools/run_3d.py; git commit -m run_3d"), "")
        self.assertEqual(self._decide("Bash", "python tools/run_3d.py --help"), "")
        self.assertEqual(self._decide("Read", "python tools/run_3d.py a.png"), "")

    def test_forged_token_refused(self):
        self.assertNotEqual(self._decide("Bash", "FACTORY_PLUGIN_TOKEN=" + "a" * 32 + " python tools/run_bind.py a"), "")


class QcGateHook(unittest.TestCase):
    """hooks/qc_gate.py: `stills` needs a passing QC receipt for the same GLB; FACTORY_RAW is the logged override; other tools are untouched."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name)
        (cls.home / "config").mkdir()
        (cls.home / "config" / "config.json").write_text(json.dumps({"plugin_guard": {"override_log": "logs/o.log"}}), encoding="utf-8")
        cls.glb = cls.home / "m.glb"
        cls.glb.write_bytes(b"glTF-one")
        import hashlib
        cls.sha = hashlib.sha256(b"glTF-one").hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def receipt(self, asset, stamp, overall, status="ok", sha=None, failed=()):
        d = self.home / "receipts" / asset
        d.mkdir(parents=True, exist_ok=True)
        (d / f"qc-{stamp}.json").write_text(json.dumps({"status": status, "params": {"overall": overall, "failed": list(failed)},
                                                        "inputs": [{"path": str(self.glb), "sha256": sha or self.sha}]}), encoding="utf-8")

    def call(self, asset="a1", tool="stills", **env):
        return run_hook("qc_gate.py", {"tool_name": MCP_P + tool, "tool_input": {"asset_id": asset, "glb": str(self.glb)}}, FACTORY_HOME=self.home, **env)

    def test_no_receipt_denies(self):
        out = self.call("never_qc")
        self.assertTrue(denied(out))
        self.assertIn("qc", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_latest_receipt_decides(self):
        self.receipt("a2", "20261101T000000Z", "pass")
        self.assertIsNone(self.call("a2"))
        self.receipt("a2", "20261101T010000Z", "fail", failed=["near_black_pct"])   # a newer failing QC wins over the older pass
        out = self.call("a2")
        self.assertTrue(denied(out))
        self.assertIn("near_black_pct", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.receipt("a2", "20261101T020000Z", "pass")
        self.assertIsNone(self.call("a2"))

    def test_a_pass_for_a_different_file_is_refused(self):
        self.receipt("a3", "20261101T000000Z", "pass", sha="0" * 64)
        self.assertTrue(denied(self.call("a3")))

    def test_a_failed_run_is_not_a_pass(self):
        self.receipt("a4", "20261101T000000Z", "fail", status="fail")
        self.assertTrue(denied(self.call("a4")))

    def test_override_is_logged_and_other_tools_are_untouched(self):
        self.assertIsNone(self.call("a5", FACTORY_RAW="hand-checked in Blender"))
        log = (self.home / "logs" / "o.log").read_text(encoding="utf-8")
        self.assertIn("hand-checked in Blender", log)
        self.assertIn("a5", log)
        self.assertIsNone(self.call("a6", tool="bind"))
        self.assertIsNone(self.call("a6", tool="qc"))


try:
    import numpy as np
    import scipy  # noqa: F401
    import trimesh  # noqa: F401
    HAVE_QC_LIBS = True
except ImportError:
    HAVE_QC_LIBS = False


@unittest.skipUnless(HAVE_QC_LIBS, "numpy, scipy and trimesh are not importable in this interpreter")
class RunQc(unittest.TestCase):
    """tools/run_qc.py metrics on tiny synthetic meshes (no GLB, no renderer)."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO / "tools"))
        import run_qc
        cls.q = run_qc
        cls.cfg = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))["qc"]
        v = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], dtype=float)
        f = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4], [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]])
        cls.cube = (v, f, None, None)

    def test_closed_cube_is_clean_and_faceted(self):
        g = self.q.geometry_metrics([self.cube])
        self.assertEqual((g["triangles"], g["non_manifold_edges"], g["boundary_edges"], g["loose_shells"]), (12, 0, 0, 1))
        self.assertAlmostEqual(g["faceting_score"], 90.0 * 12 / 18, delta=0.5)   # 12 of the 18 edges are 90 degree creases, the 6 diagonals are flat

    def test_open_cube_and_two_shells(self):
        v, f, _, _ = self.cube
        g = self.q.geometry_metrics([(v, f[:-2], None, None)])
        self.assertEqual(g["boundary_edges"], 4)
        two = self.q.geometry_metrics([self.cube, (v + 5.0, f, None, None)])
        self.assertEqual(two["loose_shells"], 2)

    def test_uv_stretch_and_degenerate(self):
        P = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]] * 3, dtype=float)
        Q = np.array([[[0, 0], [1, 0], [0, 1]], [[0, 0], [4, 0], [0, 1]], [[0, 0], [0, 0], [0, 0]]], dtype=float)
        r, degenerate, counted = self.q.uv_stretch(P, Q)
        self.assertEqual((degenerate, counted), (1, 3))
        self.assertAlmostEqual(float(r[0]), 1.0, places=6)
        self.assertAlmostEqual(float(r[1]), 4.0, places=6)

    def test_black_texture_fails_and_linear_flag(self):
        from PIL import Image
        v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], dtype=float)
        f = np.array([[0, 1, 2], [1, 3, 2]])
        uv = v[:, :2].copy()
        with tempfile.TemporaryDirectory() as d:
            concept = Path(d) / "c.png"
            rgba = np.zeros((64, 64, 4), np.uint8)
            rgba[16:48, 16:48] = (200, 120, 60, 255)
            Image.fromarray(rgba).save(concept)
            tex = np.full((64, 64, 3), 3, np.uint8)   # almost black base colour
            part = (v, f, uv, tex)
            m = {"texture": self.q.texture_metrics([part], str(concept), self.cfg["texture"]), "geometry": self.q.geometry_metrics([part]),
                 "thin": self.q.silhouette_metrics([part], str(concept), self.cfg["thin"])}
        self.assertGreater(m["texture"]["near_black_pct"], 99)
        self.assertEqual(m["texture"]["colour_flag"], "linear-not-encoded")
        rows, _ = self.q.verdict(m, self.cfg, None)
        failed = {r["metric"] for r in rows if not r["pass"]}
        self.assertTrue({"near_black_pct", "luminance_ratio"} <= failed, failed)
        self.assertAlmostEqual(m["thin"]["coverage_ratio"], 1.0, delta=0.1)   # square mesh against a square matte
        self.assertGreater(m["thin"]["iou"], 0.9)

    def test_missing_inputs_are_skipped_not_failed(self):
        m = {"geometry": self.q.geometry_metrics([self.cube]), "texture": {"skipped": "x"}, "thin": {"skipped": "x"}}
        rows, skipped = self.q.verdict(m, self.cfg, None)
        self.assertIn("triangles", skipped)
        self.assertIn("near_black_pct", skipped)
        self.assertEqual({r["metric"] for r in rows if not r["pass"]}, {"faceting_score"})   # only the cube's 60 degree mean crease fails; skipped metrics never fail
        rows, _ = self.q.verdict(m, self.cfg, 6)   # a budget of 6 triangles: the 12-triangle cube is over it
        self.assertFalse(next(r for r in rows if r["metric"] == "triangles")["pass"])
