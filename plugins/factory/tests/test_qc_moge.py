"""tools/qc_moge.py scoring (pure Python lists, always runs) and the optional MoGe block of tools/run_qc.py (needs numpy to import run_qc: skipped without it).
Offline: no model, no torch, no GPU. The model call is replaced by a fake runner; the real `infer_view` / `main` model path is UNRUN and not covered here."""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools"))
import qc_moge as Q  # noqa: E402  (stdlib only)

try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False


def dome_view(n, half=1.0, scale=1.0, faceted=False, radius=0.9, squash=1.0):
    """Synthetic single-view prediction on an n x n grid covering [-half, half]^2, inside a disc: a corrugated surface z = 5 + wave(x) + 0.5 y^2 with wave period 1 and
    maximum slope 0.8. Smooth: wave is a sine. Faceted: wave is a zigzag with the same slopes, i.e. flat facets meeting at creases of about 77 degrees.
    Returns the view dict of qc_moge.align_view."""
    pts, depth, mask = [], [], []
    for j in range(n):
        prow, drow, mrow = [], [], []
        for i in range(n):
            x, y = (i + 0.5) / n * 2 * half - half, (j + 0.5) / n * 2 * half - half
            if faceted:
                z = 5 + 0.8 * (0.5 - abs(x - math.floor(x + 0.5))) + 0.5 * y * y
            else:
                z = 5 + 0.8 / (2 * math.pi) * math.sin(2 * math.pi * x) + 0.5 * y * y
            prow.append((x * scale, y * scale, z * scale))
            drow.append(z * scale)
            mrow.append(x * x + (y / squash) ** 2 < radius * radius)
        pts.append(prow), depth.append(drow), mask.append(mrow)
    return {"points": pts, "depth": depth, "mask": mask, "normal": None}


class Geometry(unittest.TestCase):
    def test_percentile_and_angle(self):
        self.assertAlmostEqual(Q.percentile([0, 10], 50), 5.0)
        self.assertIsNone(Q.percentile([], 50))
        self.assertAlmostEqual(Q.angle_deg((1, 0, 0), (0, 1, 0)), 90.0)
        self.assertAlmostEqual(Q.angle_deg((0, 0, -1), (0, 0, -1)), 0.0)

    def test_plane_normal_faces_camera(self):
        pts = [[(x * 0.1, y * 0.1, 5 + 0.5 * x * 0.1) for x in range(5)] for y in range(5)]
        n = Q.normals_from_points(pts, [[True] * 5 for _ in range(5)])
        self.assertIsNone(n[0][0])   # border pixels have no four valid neighbours
        a = 0.5
        want = (a / math.sqrt(1 + a * a), 0.0, -1 / math.sqrt(1 + a * a))
        for got, w in zip(n[2][2], want):
            self.assertAlmostEqual(got, w, places=6)

    def test_erode_and_bbox_and_resample(self):
        m = [[1 if 1 <= y <= 3 and 2 <= x <= 5 else 0 for x in range(8)] for y in range(6)]
        self.assertEqual(Q.mask_bbox(m), (1, 3, 2, 5))
        self.assertIsNone(Q.mask_bbox([[0, 0], [0, 0]]))
        self.assertEqual(sum(map(sum, Q.erode(m, 1))), 2)   # 3 x 4 block loses its rim: 1 x 2 left
        self.assertEqual(Q.crop_resample(m, (1, 3, 2, 5), 2), [[1, 1], [1, 1]])
        self.assertEqual(Q.square_box((1, 3, 2, 5)), (1, 4, 2, 5))   # 3 x 4 box becomes the 4 x 4 square around the same centre
        self.assertEqual(Q.crop_resample(m, (-1, 0, 0, 1), 2, fill=7), [[7, 7], [m[0][0], m[0][1]]])   # outside the image: the fill value


class Scores(unittest.TestCase):
    def test_faceted_is_higher_than_smooth(self):
        smooth, faceted = Q.score_views(dome_view(120), dome_view(120)), Q.score_views(dome_view(120), dome_view(120, faceted=True))
        self.assertEqual(smooth["status"], "ok")
        self.assertEqual(smooth["normal_source"], "derived from points")
        self.assertAlmostEqual(smooth["faceting"]["faceting_delta_deg"], 0.0, places=3)
        self.assertLess(smooth["faceting"]["render"]["faceting_p99_deg"], 8.0)
        self.assertGreater(faceted["faceting"]["render"]["faceting_p99_deg"], 20.0)
        self.assertGreater(faceted["faceting"]["faceting_delta_deg"], 12.0)
        self.assertGreater(faceted["faceting"]["render"]["crease_fraction"], smooth["faceting"]["render"]["crease_fraction"])

    def test_identical_views_agree_and_scale_cancels(self):
        r = Q.score_views(dome_view(120), dome_view(120, scale=2.0))   # same shape, twice the metric size
        ag = r["agreement"]
        self.assertLess(ag["normal_mean_deg"], 0.01)
        self.assertLess(ag["depth_rel_err"], 1e-6)
        self.assertEqual(ag["mask_iou"], 1.0)

    def test_framing_and_resolution_cancel(self):
        r = Q.score_views(dome_view(120, half=1.0), dome_view(200, half=1.7))   # same subject, more padding, another resolution
        self.assertLess(r["agreement"]["normal_mean_deg"], 3.0)
        self.assertGreater(r["agreement"]["mask_iou"], 0.95)

    def test_different_surface_and_silhouette_score_worse(self):
        same = Q.score_views(dome_view(120), dome_view(120))["agreement"]
        other = Q.score_views(dome_view(120), dome_view(120, faceted=True, squash=0.6))["agreement"]
        self.assertGreater(other["normal_mean_deg"], same["normal_mean_deg"] + 1.0)
        self.assertLess(other["mask_iou"], 0.9)

    def test_model_normals_are_used_when_given(self):
        v = dome_view(60)
        v["normal"] = [[(0.0, 0.0, -1.0)] * 60 for _ in range(60)]   # flat map: zero faceting whatever the points say
        r = Q.score_views(dome_view(60), v)
        self.assertEqual(r["normal_source"], "model")
        self.assertEqual(r["faceting"]["render"]["faceting_p99_deg"], 0.0)

    def test_empty_mask_is_unavailable_not_an_error(self):
        v = dome_view(30)
        v["mask"] = [[False] * 30 for _ in range(30)]
        r = Q.score_views(dome_view(30), v)
        self.assertEqual(r["status"], "unavailable")
        self.assertIn("render", r["reason"])

    def test_too_few_pixels_is_skipped_not_scored(self):
        tiny = dome_view(120, radius=0.02)
        r = Q.score_views(dome_view(120), tiny)
        self.assertIn("skipped", r["faceting"]["render"])
        self.assertIsNone(r["faceting"]["faceting_delta_deg"])


class Worker(unittest.TestCase):
    def test_missing_weights_exit_nonzero_with_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, str(REPO / "tools" / "qc_moge.py"), "--concept", "a.png", "--render", "b.png", "--model", str(Path(d) / "nope")],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("checkpoint not found", r.stderr)

    def test_resolve_checkpoint_dir_or_file(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "model.pt").write_bytes(b"x")
            self.assertEqual(Q.resolve_checkpoint(d), str(Path(d) / "model.pt"))
            self.assertEqual(Q.resolve_checkpoint(str(Path(d) / "model.pt")), str(Path(d) / "model.pt"))


class Config(unittest.TestCase):
    def test_defaults_are_off_and_thresholds_null(self):
        cfg = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))["qc"]
        self.assertIs(cfg["moge"]["enabled"], False)
        self.assertTrue(cfg["moge"]["thresholds"] and all(v is None for v in cfg["moge"]["thresholds"].values()))
        self.assertIsNone(cfg["moge"]["model_dir"])
        self.assertEqual(cfg["moge"]["device"], "cpu")
        ex = json.loads((REPO / "config" / "config.local.example.json").read_text(encoding="utf-8-sig"))
        self.assertIn("moge_python", ex)

    def test_existing_qc_thresholds_unchanged(self):
        cfg = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))["qc"]
        self.assertEqual((cfg["geometry"]["max_faceting_deg"], cfg["thin"]["min_iou"], cfg["texture"]["min_luminance_ratio"]), (25.0, 0.4, 0.6))


@unittest.skipUnless(HAVE_NUMPY, "numpy is not importable in this interpreter, so tools/run_qc.py cannot be imported")
class RunQcMoge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import run_qc
        cls.rq = run_qc

    def cfg(self, **moge):
        base = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))["qc"]
        base["moge"] = {**base["moge"], "enabled": True, **moge}
        return {"qc": base, "local": {"work_dir": "W"}}

    OK = {"status": "ok", "faceting": {"faceting_delta_deg": 12.0}, "agreement": {"normal_mean_deg": 9.0, "depth_rel_err": 0.2, "mask_iou": 0.7}}

    def files(self, d):
        a, b = Path(d) / "c.png", Path(d) / "r.png"
        a.write_bytes(b"x"), b.write_bytes(b"x")
        return str(a), str(b)

    def test_paths_default_under_work_dir(self):
        py, model = self.rq.moge_paths(self.cfg())
        self.assertEqual(Path(py), Path("W") / "Pixal3D-312" / ".venv" / "Scripts" / "python.exe")
        self.assertEqual(Path(model), Path("W") / "models" / "moge-2-vitl")

    def test_fake_runner_scores_and_command(self):
        seen = []
        with tempfile.TemporaryDirectory() as d:
            c, r = self.files(d)
            blk = self.rq.moge_block(self.cfg(), c, r, runner=lambda cmd, t: seen.append((cmd, t)) or dict(self.OK))
        self.assertEqual(blk, self.OK)
        cmd, timeout = seen[0]
        self.assertEqual((timeout, cmd[cmd.index("--device") + 1]), (600, "cpu"))
        self.assertIn("qc_moge.py", cmd[1])

    def test_unavailable_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            c, r = self.files(d)

            def boom(cmd, t):
                raise RuntimeError("no module named moge")
            self.assertEqual(self.rq.moge_block(self.cfg(), c, r, runner=boom)["status"], "unavailable")
            self.assertIn("no module named moge", self.rq.moge_block(self.cfg(), c, r, runner=boom)["reason"])
            self.assertEqual(self.rq.moge_block(self.cfg(), c, r, runner=lambda cmd, t: None)["status"], "unavailable")
            self.assertEqual(self.rq.moge_block(self.cfg(), c, r, runner=lambda cmd, t: {"status": "unavailable", "reason": "empty mask"})["reason"], "empty mask")
            self.assertIn("--render", self.rq.moge_block(self.cfg(), c, "")["reason"])
            self.assertIn("not found", self.rq.moge_block(self.cfg(), c, str(Path(d) / "gone.png"))["reason"])
            # real runner path (runner=None): the venv python and weights of the fake work_dir "W" do not exist
            self.assertIn("moge_python not found", self.rq.moge_block(self.cfg(), c, r)["reason"])

    def test_no_thresholds_never_fails(self):
        self.assertEqual(self.rq.moge_rows(self.OK, self.cfg()["qc"]["moge"]), [])
        self.assertEqual(self.rq.moge_rows({"status": "unavailable", "reason": "x"}, self.cfg(thresholds={"max_normal_mean_deg": 1.0})["qc"]["moge"]), [])

    def test_thresholds_apply_with_the_existing_row_shape(self):
        mq = self.cfg(thresholds={"max_faceting_delta_deg": 10.0, "max_normal_mean_deg": 20.0, "max_depth_rel_err": None, "min_mask_iou": 0.8})["qc"]["moge"]
        rows = {r["metric"]: r for r in self.rq.moge_rows(self.OK, mq)}
        self.assertEqual(set(rows), {"moge_faceting_delta_deg", "moge_normal_mean_deg", "moge_mask_iou"})   # null depth threshold: skipped
        self.assertEqual({k: v["pass"] for k, v in rows.items()}, {"moge_faceting_delta_deg": False, "moge_normal_mean_deg": True, "moge_mask_iou": False})
        self.assertEqual(set(rows["moge_mask_iou"]), {"metric", "value", "op", "limit", "pass"})


if __name__ == "__main__":
    unittest.main()
