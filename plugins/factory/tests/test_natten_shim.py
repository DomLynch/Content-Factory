"""Tests for the pure-PyTorch natten shim (plugin 0.1.15) and the NAF switches of tools/pixal3d_entry.py.

Semantics taken from NATTEN's documentation / paper (Hassani et al., Neighborhood Attention Transformer, CVPR 2023; natten docs for na2d):
  - 2-D, non-causal, stride 1; odd kernel size K; each query sees exactly K x K keys and values, never zero padded;
  - near a border the window is SHIFTED to stay inside the map (not truncated); dilation d splits each axis into d interleaved sub-sequences and the window
    lives inside the query's own sub-sequence; every sub-sequence must hold at least K positions (L >= K*d);
  - na2d scales q.k by dim ** -0.5 by default; the legacy na2d_qk returns unscaled logits (NAF multiplies by its own scale).
ASSUMED (no real natten here to compare against): the flattening order of the K*K axis in legacy na2d_qk (row-major used; NAF never reads it), float32
accumulation of half precision inputs, and that `backend=` only selects a kernel and never changes the numbers.
The reference below is written separately from the shim (sub-sequence + clamp, plain Python lists), not from its formula.
The torch tests skip when torch cannot be imported; the window-rule tests need only the standard library.
"""
from __future__ import annotations

import importlib.util
import math
import os
import random
import sys
import types
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
SHIMS = REPO / "tools" / "pixal3d_shims"
sys.path.insert(0, str(REPO / "tools"))

try:
    import torch
except Exception as e:   # noqa: BLE001
    torch = None
    NO_TORCH = f"torch is not importable here ({type(e).__name__}); the numeric shim tests run under the Pixal3D venv python"
else:
    NO_TORCH = ""


def load_window():
    spec = importlib.util.spec_from_file_location("natten_window_pure", SHIMS / "natten" / "_window.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


W = load_window()


def ref_window(length, k, d, i):
    """Documented rule, written as: sub-sequence of i, position p in it, window start clamped into [0, n-k]."""
    sub = list(range(i % d, length, d))
    p = sub.index(i)
    s = min(max(p - k // 2, 0), len(sub) - k)
    return sub[s:s + k]


class WindowRule(unittest.TestCase):
    def test_matches_independent_reference_everywhere(self):
        for length, k, d in [(9, 3, 1), (9, 9, 1), (18, 9, 2), (20, 3, 5), (37, 5, 3), (144, 9, 16), (64, 9, 7)]:
            for i in range(length):
                self.assertEqual(W.window_indices(i, length, k, d), ref_window(length, k, d, i), (length, k, d, i))

    def test_border_is_shifted_not_truncated(self):
        self.assertEqual(W.window_indices(0, 10, 5), [0, 1, 2, 3, 4])
        self.assertEqual(W.window_indices(1, 10, 5), [0, 1, 2, 3, 4])
        self.assertEqual(W.window_indices(5, 10, 5), [3, 4, 5, 6, 7])
        self.assertEqual(W.window_indices(9, 10, 5), [5, 6, 7, 8, 9])
        self.assertEqual(W.window_indices(8, 10, 5), [5, 6, 7, 8, 9])

    def test_dilated_window_stays_in_own_residue_class(self):
        self.assertEqual(W.window_indices(3, 12, 3, 2), [1, 3, 5])
        self.assertEqual(W.window_indices(0, 12, 3, 2), [0, 2, 4])
        self.assertEqual(W.window_indices(11, 12, 3, 2), [7, 9, 11])

    def test_naf_geometry_every_window_valid_at_real_sizes(self):
        # NAF: kernel 9; targets 512/512/1024 with DINOv3 maps 32 (512 px) and 64 (1024 px) -> dilation 16, 8, 16
        for target, feat in [(512, 32), (512, 64), (1024, 64)]:
            d = target // feat
            for i in range(target):
                win = W.window_indices(i, target, 9, d)
                self.assertEqual(len(set(win)), 9)
                self.assertTrue(all(0 <= x < target and x % d == i % d for x in win))
                self.assertEqual(win, sorted(win))
                self.assertIn(i, win)       # with a centred or shifted window the query itself is always inside

    def test_errors_like_natten(self):
        with self.assertRaises(ValueError):
            W.window_start(0, 10, 4)         # even kernel
        with self.assertRaises(ValueError):
            W.window_indices(0, 17, 9, 2)    # L < K*d
        with self.assertRaises(ValueError):
            W.window_indices(0, 5, 3, 0)
        self.assertEqual(W.as_pair(9, "k"), (9, 9))
        self.assertEqual(W.as_pair([3, 5], "k"), (3, 5))
        with self.assertRaises(ValueError):
            W.as_pair([1, 2, 3], "k")


def ref_na2d(q, k, v, kh, kw, dh, dw, scale):
    """q,k,v nested lists [B][H][W][N][D]; returns nested lists [B][H][W][N][Dv]. Straight from the definition."""
    B, H, Wd, N = len(q), len(q[0]), len(q[0][0]), len(q[0][0][0])
    out = []
    for b in range(B):
        rows = []
        for y in range(H):
            row = []
            wy = ref_window(H, kh, dh, y)
            for x in range(Wd):
                wx = ref_window(Wd, kw, dw, x)
                heads = []
                for n in range(N):
                    logits, vals = [], []
                    for yy in wy:
                        for xx in wx:
                            logits.append(scale * sum(a * c for a, c in zip(q[b][y][x][n], k[b][yy][xx][n])))
                            vals.append(v[b][yy][xx][n])
                    m = max(logits)
                    e = [math.exp(l - m) for l in logits]
                    z = sum(e)
                    heads.append([sum(e[i] / z * vals[i][c] for i in range(len(e))) for c in range(len(vals[0]))])
                row.append(heads)
            rows.append(row)
        out.append(rows)
    return out


def rand(shape, seed):
    g = torch.Generator().manual_seed(seed)
    return torch.randn(*shape, generator=g)


@unittest.skipIf(torch is None, NO_TORCH)
class ShimNumerics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SHIMS))
        import natten
        cls.natten = natten
        assert natten.IS_SHIM, f"a real natten shadows the shim: {natten.__file__}"

    def check(self, b, h, w, n, d, dv, k, dil, seed=0):
        kh, kw = (k, k) if isinstance(k, int) else k
        dh, dw = (dil, dil) if isinstance(dil, int) else dil
        q, kk, v = rand((b, h, w, n, d), seed), rand((b, h, w, n, d), seed + 1), rand((b, h, w, n, dv), seed + 2)
        want = ref_na2d(q.tolist(), kk.tolist(), v.tolist(), kh, kw, dh, dw, d ** -0.5)
        got = self.natten.na2d(q, kk, v, kernel_size=k, dilation=dil, stride=1, backend="cutlass-fna")
        self.assertEqual(tuple(got.shape), (b, h, w, n, dv))
        self.assertTrue(torch.allclose(got, torch.tensor(want), atol=2e-5, rtol=1e-4), f"max diff {(got - torch.tensor(want)).abs().max().item()}")

    def test_plain_window(self):
        self.check(1, 7, 8, 2, 4, 3, 3, 1)

    def test_kernel_bigger_than_half_the_map(self):
        self.check(2, 9, 9, 1, 3, 2, 9, 1, seed=5)       # every query sees the whole map

    def test_dilated_non_square(self):
        self.check(1, 12, 18, 2, 3, 3, (3, 5), (2, 3), seed=9)

    def test_naf_style_minimum_edge_case(self):
        # NAF: kernel 9, dilation = target // feature; 144 = 9 * 16 is the smallest legal map for dilation 16 (every sub-sequence exactly K long)
        # reduced here to 18 = 9 * 2, dilation 2, with NAF's per-head dim pattern (4 heads, q/k dim 64 // 4 -> 3 here, v dim 256 -> 2 here)
        self.check(1, 18, 18, 4, 3, 2, 9, 2, seed=13)

    def test_naf_style_dilation_four_and_eight_ratio(self):
        self.check(1, 36, 36, 1, 2, 1, 9, 4, seed=21)    # feature 9 -> target 36
        self.check(1, 72, 72, 1, 1, 1, 9, 8, seed=22)    # feature 9 -> target 72 (the 512/64 ratio)

    def test_legacy_path_equals_na2d_when_used_like_naf(self):
        # NAF legacy_attention: [b n h w d] -> na2d_qk -> * scale -> softmax -> na2d_av
        from natten.functional import na2d_av, na2d_qk
        b, h, w, n, d, k, dil = 1, 18, 18, 4, 5, 9, 2
        q, kk, v = rand((b, h, w, n, d), 31), rand((b, h, w, n, d), 32), rand((b, h, w, n, 6), 33)
        scale = d ** -0.5
        lq, lk, lv = (x.permute(0, 3, 1, 2, 4) for x in (q, kk, v))
        logits = na2d_qk(lq, lk, kernel_size=(k, k), dilation=(dil, dil))
        self.assertEqual(tuple(logits.shape), (b, n, h, w, k * k))
        out = na2d_av((logits * scale).softmax(dim=-1), lv, kernel_size=(k, k), dilation=(dil, dil)).permute(0, 2, 3, 1, 4)
        self.assertTrue(torch.allclose(out, self.natten.na2d(q, kk, v, kernel_size=(k, k), dilation=(dil, dil)), atol=2e-5, rtol=1e-4))
        want = ref_na2d(q.tolist(), kk.tolist(), v.tolist(), k, k, dil, dil, scale)
        self.assertTrue(torch.allclose(out, torch.tensor(want), atol=2e-5, rtol=1e-4))

    def test_row_blocking_changes_nothing(self):
        from natten import _na2d
        q, kk, v = rand((1, 20, 20, 2, 3), 41), rand((1, 20, 20, 2, 3), 42), rand((1, 20, 20, 2, 4), 43)
        a = _na2d.na2d(q, kk, v, 5, 2)
        b = _na2d.na2d(q, kk, v, 5, 2, max_block_elems=1)        # one row per block
        c = _na2d.na2d(q, kk, v, 5, 2, max_block_elems=1000)
        self.assertTrue(torch.equal(a, b) and torch.equal(a, c))

    def test_scale_override_and_dtype_kept(self):
        q, kk, v = rand((1, 6, 6, 1, 4), 51), rand((1, 6, 6, 1, 4), 52), rand((1, 6, 6, 1, 4), 53)
        one = self.natten.na2d(q, kk, v, 3, scale=1.0)
        want = ref_na2d(q.tolist(), kk.tolist(), v.tolist(), 3, 3, 1, 1, 1.0)
        self.assertTrue(torch.allclose(one, torch.tensor(want), atol=2e-5, rtol=1e-4))
        self.assertEqual(self.natten.na2d(q.double(), kk.double(), v.double(), 3).dtype, torch.float64)
        self.assertEqual(self.natten.na2d(q.bfloat16(), kk.bfloat16(), v.bfloat16(), 3).dtype, torch.bfloat16)

    def test_unsupported_features_refuse(self):
        q = rand((1, 6, 6, 1, 4), 61)
        with self.assertRaises(NotImplementedError):
            self.natten.na2d(q, q, q, 3, stride=2)
        with self.assertRaises(NotImplementedError):
            self.natten.na2d(q, q, q, 3, is_causal=True)
        with self.assertRaises(ValueError):
            self.natten.na2d(q, q, q, 4)
        with self.assertRaises(ValueError):
            self.natten.na2d(q, q, q, 3, dilation=3)         # 6 < 3 * 3
        with self.assertRaises(NotImplementedError):
            self.natten.functional.na2d_qk(q.permute(0, 3, 1, 2, 4), q.permute(0, 3, 1, 2, 4), 3, rpb=q)

    def test_version_string_and_both_import_paths(self):
        self.assertTrue(self.natten.__version__)
        from natten import na2d                               # noqa: F401  (recent path)
        from natten.functional import na2d_av, na2d_qk        # noqa: F401  (legacy path NAF tries first)


@unittest.skipIf(torch is None, NO_TORCH)
class EntryNafSwitch(unittest.TestCase):
    def test_plain_upsample_keeps_channels_and_size(self):
        import pixal3d_entry

        class Fake:
            def __init__(self):
                self.naf_model = None

        mod = types.SimpleNamespace(DinoV3ProjFeatureExtractor=Fake)
        pixal3d_entry.install_plain_upsample(mod)
        f = Fake()
        f._load_naf()
        feats = torch.randn(1, 8, 4, 4)
        out = f.naf_model(torch.zeros(1, 3, 64, 64), feats, (16, 16))
        self.assertEqual(tuple(out.shape), (1, 8, 16, 16))
        self.assertEqual(f.naf_model(None, feats.bfloat16(), (8, 8)).dtype, torch.bfloat16)
        keep = f.naf_model
        f._load_naf()
        self.assertIs(f.naf_model, keep)


class EntryAndConfig(unittest.TestCase):
    def test_cache_report_names_missing_and_present(self):
        import tempfile
        import pixal3d_entry
        d = tempfile.mkdtemp()
        self.assertIn("MISSING", pixal3d_entry.naf_cache_report(d))
        os.makedirs(os.path.join(d, "hub", "valeoai_NAF_main"))
        os.makedirs(os.path.join(d, "hub", "checkpoints"))
        open(os.path.join(d, "hub", "valeoai_NAF_main", "hubconf.py"), "w").close()
        open(os.path.join(d, "hub", "checkpoints", "naf_release.pth"), "w").close()
        self.assertNotIn("MISSING", pixal3d_entry.naf_cache_report(d))

    def test_entry_puts_shim_first_and_sets_torch_home(self):
        src = (REPO / "tools" / "pixal3d_entry.py").read_text(encoding="utf-8")
        self.assertIn("sys.path.insert(0, a.shim_dir)", src)
        self.assertLess(src.index("sys.path.insert(0, a.repo)"), src.index("sys.path.insert(0, a.shim_dir)"))   # shim inserted last = first on the path
        self.assertIn('os.environ["TORCH_HOME"] = a.torch_home', src)
        self.assertLess(src.index('os.environ["TORCH_HOME"]'), src.index("import inference as P"))

    def test_config_and_stage_script_carry_naf_params(self):
        import json
        c = json.load(open(REPO / "config" / "config.json", encoding="utf-8-sig"))["pixal3d"]
        self.assertIs(c["naf"], True)
        self.assertEqual(c["naf_off_label"], "ROUGH / NAF-off")
        stage = (REPO / "tools" / "run_pixal3d.py").read_text(encoding="utf-8")
        for needle in ('"naf": naf', '"quality_label": quality_label', '"--no-naf"', "pixal3d_torch_home"):
            self.assertIn(needle, stage)

    def test_shim_package_files_exist(self):
        for rel in ("__init__.py", "functional.py", "_na2d.py", "_window.py"):
            self.assertTrue((SHIMS / "natten" / rel).is_file(), rel)


if __name__ == "__main__":
    unittest.main()
