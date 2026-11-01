"""tools/swatch_crop.py: a crop whose native box is under 512 px on a side is refused (exit 2) unless --allow-small gives a logged reason."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools"))
import cf_common  # noqa: E402

_spec = importlib.util.spec_from_file_location("swatch_crop", REPO / "tools" / "swatch_crop.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

try:
    from PIL import Image
except ImportError:   # the suite installs pillow; skip cleanly without it
    Image = None


class Pure(unittest.TestCase):
    def test_crop_px(self):
        self.assertEqual(sc.crop_px([0.25, 0.5, 0.75, 1.0], 1000, 800), [250, 400, 750, 800])

    def test_512_is_the_floor_on_each_side(self):
        self.assertEqual(sc.small_reason([0, 0, 512, 512]), "")
        self.assertEqual(sc.small_reason([0, 0, 1024, 600]), "")
        self.assertIn("511x600", sc.small_reason([0, 0, 511, 600]))
        self.assertIn("1000x511", sc.small_reason([0, 0, 1000, 511]))
        self.assertIn("260x260", sc.small_reason([10, 10, 270, 270]))


@unittest.skipIf(Image is None, "pillow missing")
class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.out = t / "out"
        self.out.mkdir()
        Image.new("RGB", (1536, 1024), (120, 20, 30)).save(self.out / "src_1536x1024.png")
        self.manifest = t / "swatches.json"
        self.kit = t / "kit.json"
        self.kit.write_text(json.dumps({
            "kit": "k", "seed": 42, "w": 1024, "h": 1024,
            "rows": [
                {"name": "src", "row_id": "src", "w": 1536, "h": 1024, "prompt": "p"},
                {"name": "small", "row_id": "small", "prompt": "p", "swatch_of": {"source_row": "src", "box": [0.1, 0.1, 0.3, 0.3], "size": [1024, 1024]}},
                {"name": "big", "row_id": "big", "prompt": "p", "swatch_of": {"source_row": "src", "box": [0.0, 0.0, 0.5, 0.75], "size": [1024, 1024]}},
            ]}), encoding="utf-8")
        self.cfg = {"local": {"outputs_dir": str(t)}}

    def run_main(self, *extra, only=None):
        args = [str(self.kit), "--out-dir", str(self.out), "--manifest", str(self.manifest), *extra]
        for o in (only or []):
            args += ["--only", o]
        err, outb = io.StringIO(), io.StringIO()
        with mock.patch.object(cf_common, "load_config", return_value=self.cfg), \
             mock.patch.object(cf_common, "write_receipt", return_value=str(self.out / "receipt.json")), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(outb):
            try:
                sc.main(args)
                code = 0
            except SystemExit as e:
                code = e.code
        return code, outb.getvalue(), err.getvalue()

    def test_small_crop_is_refused_with_exit_2_and_nothing_is_written(self):
        code, out, err = self.run_main(only=["small"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR small", err)
        self.assertIn("under 512", err)
        self.assertFalse((self.out / "small_1024x1024.png").exists())
        self.assertFalse(self.manifest.exists())

    def test_dry_run_also_refuses(self):
        code, out, err = self.run_main("--dry-run", only=["small"])
        self.assertEqual(code, 2)
        self.assertNotIn("WOULD CROP", out)

    def test_big_enough_crop_is_made(self):
        code, out, err = self.run_main(only=["big"])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.out / "big_1024x1024.png").exists())
        entry = json.loads(self.manifest.read_text(encoding="utf-8"))["swatches"][0]
        self.assertEqual(entry["native_px"], [768, 768])
        self.assertNotIn("allow_small", entry)

    def test_mixed_run_makes_the_good_one_and_exits_2(self):
        code, out, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertTrue((self.out / "big_1024x1024.png").exists())
        self.assertFalse((self.out / "small_1024x1024.png").exists())

    def test_allow_small_with_a_reason_logs_it(self):
        code, out, err = self.run_main("--allow-small", "hair detail only, proof of the pipeline", only=["small"])
        self.assertEqual(code, 0, err)
        self.assertIn("ALLOW-SMALL small", out)
        self.assertIn("hair detail only", out)
        self.assertTrue((self.out / "small_1024x1024.png").exists())
        entry = json.loads(self.manifest.read_text(encoding="utf-8"))["swatches"][0]
        self.assertEqual(entry["allow_small"], "hair detail only, proof of the pipeline")
        self.assertEqual(entry["native_px"], [307, 205])

    def test_allow_small_without_a_reason_is_an_error(self):
        code, out, err = self.run_main("--allow-small")
        self.assertEqual(code, 2)
        self.assertIn("needs a reason", err)
        self.assertFalse((self.out / "small_1024x1024.png").exists())


if __name__ == "__main__":
    unittest.main()
