"""Offline tests for plugin 0.1.6: the Flux quality policy (part A) and the reference-stream cache logic (part B).

Nothing here runs Flux, a GPU job or diffusers. The cache is tested with fake tensors; the torch wrapper is not exercised (torch is not installed in the test python).
"""
from __future__ import annotations

import importlib.util
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
sys.path.insert(0, str(PLUGIN / "lib"))
os.environ["FACTORY_HOME"] = str(REPO)
import cf_flux  # noqa: E402

PY = sys.executable
MCP = "mcp__plugin_factory_factory__"


def png(path: Path, w: int, h: int) -> str:
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80" * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return str(path)


class Kit(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d = Path(self.t.name)
        self.ref = png(self.d / "ref.png", 1000, 1500)
        self.kit = {"kit": "k", "age": "fifties", "complexion": "pale", "seed": 1, "w": 1024, "h": 1536, "ref": self.ref, "ref_prefix": "p", "subject": "", "style": "s", "rows": [
            {"name": "head_front", "row_id": "head_front", "group": "head", "prompt": "x"},
            {"name": "coat", "row_id": "coat", "part": "gear", "prompt": "x"},
            {"name": "mat_fur", "row_id": "mat_fur", "prompt": "x"},
            {"name": "hands_open", "row_id": "hands_open", "prompt": "x"},
            {"name": "feet_a", "row_id": "feet_a", "group": "hands_feet", "prompt": "x"},
            {"name": "sky", "row_id": "sky", "prompt": "x", "use_ref": False}]}
        self.path = self.d / "kit.json"
        self.path.write_text(json.dumps(self.kit))

    def row(self, rid):
        return next(r for r in self.kit["rows"] if r["row_id"] == rid)

    def reasons(self, rid, env=None):
        return cf_flux.check_row(self.kit, self.row(rid), env or {})


class Policy(Kit):
    def test_row_type_from_field_group_part_or_name(self):
        types = {r["row_id"]: cf_flux.row_type(self.kit, r) for r in self.kit["rows"]}
        self.assertEqual(types, {"head_front": "head", "coat": "gear", "mat_fur": "swatch", "hands_open": "hands", "feet_a": "hands", "sky": "other"})
        self.assertEqual(cf_flux.row_type({"row_type": "body"}, {"name": "x", "group": "gear"}), "gear")   # a row's own group beats the kit default
        self.assertEqual(cf_flux.row_type({"row_type": "body"}, {"name": "x"}), "body")
        self.assertEqual(cf_flux.row_type({}, {"name": "x", "row_type": "Head"}), "head")

    def test_defaults_pass(self):
        for rid in ("head_front", "coat", "mat_fur", "hands_open", "feet_a", "sky"):
            self.assertEqual(self.reasons(rid), [], rid)

    def test_steps_below_16_refused_for_every_row_type(self):
        for rid in ("head_front", "coat", "mat_fur", "hands_open", "sky"):
            r = self.reasons(rid, {"CF_STEPS": "8"})
            self.assertTrue(any("8 steps" in x and "floor is 16" in x for x in r), (rid, r))

    def test_resolution_below_the_row_refused(self):
        r = self.reasons("head_front", {"FACTORY_FLUX_W": "768", "FACTORY_FLUX_H": "1152"})
        self.assertTrue(any("768x1152" in x and "1024x1536" in x for x in r), r)
        self.assertEqual(self.reasons("head_front", {"FACTORY_FLUX_W": "2048", "FACTORY_FLUX_H": "3072"}), [])

    def test_body_head_hands_need_the_full_size_reference(self):
        for rid in ("head_front", "hands_open", "feet_a"):
            r = self.reasons(rid, {"CF_REF_LONG": "768"})
            self.assertTrue(any("from 1500 to 768" in x and "full-size reference (1000x1500)" in x for x in r), (rid, r))
        self.assertEqual(self.reasons("head_front", {"CF_REF_LONG": "1500"}), [])   # a cap at or above the real size changes nothing
        self.assertEqual(self.reasons("head_front", {"CF_REF_LONG": "0"}), [])   # 0 = full size, as in flux_kit_fast.py

    def test_gear_and_swatch_may_downscale_to_640(self):
        for rid in ("coat", "mat_fur"):
            self.assertEqual(self.reasons(rid, {"CF_REF_LONG": "640"}), [], rid)
            r = self.reasons(rid, {"CF_REF_LONG": "512"})
            self.assertTrue(any("to 512 px" in x and "is 640" in x for x in r), (rid, r))

    def test_row_level_steps_and_resolution_come_from_the_kit_json(self):
        self.row("coat")["steps"] = 4
        self.row("coat")["w"] = 512   # the row's own size is the baseline: a smaller row is the kit author's call, not a downscale of the run
        r = self.reasons("coat")
        self.assertEqual(len(r), 1)
        self.assertIn("4 steps", r[0])

    def test_receipt_fields(self):
        spec = cf_flux.run_spec(self.kit, self.row("head_front"), {})
        f = cf_flux.receipt_fields(spec, self.ref, "native", "child", False, True, True)
        self.assertEqual(f["steps"], 16)
        self.assertEqual(f["ref_size"], [1000, 1500])
        self.assertEqual(len(f["ref_sha256"]), 64)
        self.assertEqual(cf_flux.receipt_reason({"params": {"reference": self.ref, **f}}), "")
        self.assertIn("steps", cf_flux.receipt_reason({"params": {"reference": self.ref}}))
        self.assertEqual(cf_flux.receipt_reason({"params": {"reference": None, "steps": 16, "attention_backend": "native", "process_mode": "child", "cached_ref_conds": False, "w": 1, "h": 1, "vae_tiling": True, "vae_slicing": True}}), "")


class Hook(Kit):
    def hook(self, payload):
        r = subprocess.run([PY, str(PLUGIN / "hooks" / "gates.py")], input=json.dumps(payload), capture_output=True, text=True, env=dict(os.environ, FACTORY_HOME=str(REPO)), timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def test_mcp_flux_render_refused_and_allowed(self):
        self.row("head_front")["steps"] = 8
        self.path.write_text(json.dumps(self.kit))
        out = self.hook({"tool_name": MCP + "flux_render", "tool_input": {"kit": str(self.path), "row_id": "head_front"}})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("8 steps", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(self.hook({"tool_name": MCP + "flux_render", "tool_input": {"kit": str(self.path), "row_id": "coat"}}), {})

    def test_bash_inline_switch_refused_and_raw_override_allowed(self):
        cmd = f"CF_REF_LONG=512 python flux/flux_kit.py {self.path.as_posix()} head_front"
        out = self.hook({"tool_name": "Bash", "tool_input": {"command": cmd}})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("full-size reference", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(self.hook({"tool_name": "Bash", "tool_input": {"command": "FACTORY_RAW=odd " + cmd}}), {})
        self.assertEqual(self.hook({"tool_name": "Bash", "tool_input": {"command": f"python flux/flux_kit.py {self.path.as_posix()} head_front"}}), {})


class FluxKitExit2(Kit):
    """flux_kit.py itself refuses with exit 2 (policy runs before any heavy import)."""

    def make_repo(self):
        root = self.d / "repo"
        for sub in ("flux", "tools", "config"):
            shutil.copytree(REPO / sub, root / sub, ignore=shutil.ignore_patterns("__pycache__", "patches"))
        (root / "plugins" / "factory").mkdir(parents=True)
        shutil.copytree(PLUGIN / "lib", root / "plugins" / "factory" / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(root / "config" / "config.local.example.json", root / "config" / "config.local.json")
        return root

    def run_kit(self, root, row, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith("FACTORY_")}
        e.update(env, CF_DRY="1", FACTORY_HOME=str(root))
        return subprocess.run([PY, str(root / "flux" / "flux_kit.py"), str(self.path), row], capture_output=True, text=True, env=e, timeout=60)

    def test_exit_2_with_numbers_then_override_logs(self):
        root = self.make_repo()
        ok = self.run_kit(root, "head_front")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        bad = self.run_kit(root, "head_front", CF_STEPS="8")
        self.assertEqual(bad.returncode, 2, bad.stdout + bad.stderr)
        self.assertIn("8 steps", bad.stdout)
        raw = self.run_kit(root, "head_front", CF_STEPS="8", FACTORY_RAW="test-case")
        self.assertEqual(raw.returncode, 0, raw.stdout + raw.stderr)
        self.assertTrue((root / "logs" / "factory_raw_override.log").exists())


# ---- part B: the cache logic with fake tensors ---------------------------------------------------------------------------------------------------
def load_patch():
    spec = importlib.util.spec_from_file_location("flux3_ref_cache", REPO / "flux" / "patches" / "flux3_ref_cache.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FT:
    """A fake tensor: bytes in, the same bytes out of detach/contiguous/cpu/numpy/tobytes."""

    def __init__(self, data: bytes, shape=(1,), dtype="bf16"):
        self.data, self.shape, self.dtype, self.device = data, shape, dtype, "cuda:0"

    def detach(self):
        return self

    contiguous = cpu = numpy = detach

    def tobytes(self):
        return self.data


class Cache(unittest.TestCase):
    def setUp(self):
        self.m = load_patch()
        self.calls = 0

    def block(self, x, mod, rot):
        self.calls += 1
        return FT(x.data + b"+" + mod.data + b"+" + rot[0].data)

    def test_same_inputs_hit_and_return_the_identical_object(self):
        c = self.m.RefStreamCache()
        x, mod, rot = FT(b"ref"), FT(b"m0"), (FT(b"cos"), FT(b"sin"))
        a = c.run([self.block, self.block], x, mod, rot)
        b = c.run([self.block, self.block], FT(b"ref"), FT(b"m0"), (FT(b"cos"), FT(b"sin")))
        self.assertIs(a, b)
        self.assertEqual((c.hits, c.misses, self.calls), (1, 1, 2))

    def test_other_timestep_reference_dtype_or_shape_never_hits(self):
        c = self.m.RefStreamCache()
        base = c.run([self.block], FT(b"ref"), FT(b"m0"), (FT(b"cos"), FT(b"sin")))
        for x, mod in ((FT(b"ref"), FT(b"m1")), (FT(b"ref2"), FT(b"m0")), (FT(b"ref", dtype="f16"), FT(b"m0")), (FT(b"ref", shape=(2,)), FT(b"m0"))):
            out = c.run([self.block], x, mod, (FT(b"cos"), FT(b"sin")))
            self.assertIsNot(out, base)
        self.assertEqual(c.hits, 0)

    def test_output_equals_the_uncached_chain_byte_for_byte(self):
        c = self.m.RefStreamCache()
        args = (FT(b"ref"), FT(b"m0"), (FT(b"cos"), FT(b"sin")))
        expect = self.block(self.block(args[0], args[1], args[2]), args[1], args[2]).data
        got = c.run([self.block, self.block], *args)
        again = c.run([self.block, self.block], *args)
        self.assertEqual((got.data, again.data), (expect, expect))

    def test_lru_evicts_the_oldest(self):
        c = self.m.RefStreamCache(max_entries=2)
        for i in range(3):
            c.run([self.block], FT(b"r%d" % i), FT(b"m"), (FT(b"c"), FT(b"s")))
        self.assertEqual(len(c.store), 2)
        c.run([self.block], FT(b"r0"), FT(b"m"), (FT(b"c"), FT(b"s")))
        self.assertEqual(c.hits, 0)   # r0 was evicted, so this was a miss

    def test_install_if_enabled_is_off_without_the_flag(self):
        self.assertIsNone(self.m.install_if_enabled(object(), {"flux": {"cache_ref_conds": False}}))
        self.assertIsNone(self.m.install_if_enabled(object(), {}))


if __name__ == "__main__":
    unittest.main()
