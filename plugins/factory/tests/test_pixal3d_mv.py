"""Offline tests for the Pixal3D multi-view input (plugin 0.1.20). Fake views (PIL), no torch, no GPU, no weights, no Pixal3D import.

fixtures/mv_example_transforms.json is a verbatim copy of upstream assets/mv_images/example/transforms.json (TencentARC/Pixal3D, MIT): the camera rig inference_mv.py documents.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
os.environ["FACTORY_HOME"] = str(REPO)
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(PLUGIN / "mcp"))
sys.path.insert(0, str(REPO / "tools"))
import pixal3d_mv as MV  # noqa: E402

PY = sys.executable
MCP = "mcp__plugin_factory_factory__"
FIXTURE = json.load(open(PLUGIN / "tests" / "fixtures" / "mv_example_transforms.json", encoding="utf-8"))


def cutout(path: Path, w=1024, h=1024, subj_w=500, subj_h=820, alpha=True, opaque=False, mode_rgb=False) -> str:
    """A fake cut-out: an ellipse of the given size, transparent elsewhere."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0 if not opaque else 255))
    d = ImageDraw.Draw(im)
    d.ellipse(((w - subj_w) // 2, (h - subj_h) // 2, (w + subj_w) // 2, (h + subj_h) // 2), fill=(200, 120, 90, 255))
    if mode_rgb:
        im = im.convert("RGB")
    im.save(path)
    return str(path)


def head_views(d: Path, **kw) -> dict:
    return {c: cutout(d / f"{c}.png", **kw) for c in ("head_front", "head_profile", "head_three_quarter", "head_back")}


class Mapping(unittest.TestCase):
    def test_angles_and_order(self):
        rows = MV.plan(["head_back", "head_profile", "head_front", "head_three_quarter"])
        self.assertEqual([(r["cell"], r["angle_deg"]) for r in rows],
                         [("head_front", 0), ("head_three_quarter", 45), ("head_profile", 90), ("head_back", 180)])

    def test_facing_right_goes_to_270_and_315(self):
        rows = MV.plan(["head_front", "head_profile", "head_three_quarter"], {"head_profile": "right", "head_three_quarter": "right"})
        self.assertEqual({r["cell"]: r["angle_deg"] for r in rows}, {"head_front": 0, "head_three_quarter": 315, "head_profile": 270})

    def test_body_rows(self):
        rows = MV.plan(["body_underlayer_front", "body_underlayer_back", "body_underlayer_left", "body_underlayer_right"])
        self.assertEqual({r["cell"]: r["angle_deg"] for r in rows},
                         {"body_underlayer_front": 0, "body_underlayer_left": 90, "body_underlayer_back": 180, "body_underlayer_right": 270})
        flags = [f for r in rows for f in r["flags"] if "three-quarters" in f]
        self.assertTrue(flags, "the right body cell reads as a three-quarter: flagged")

    def test_three_quarter_can_be_left_out(self):
        rows = MV.plan(["head_front", "head_profile", "head_three_quarter", "head_back"], three_quarter=False)
        self.assertEqual([r["angle_deg"] for r in rows], [0, 90, 180])

    def test_refusals(self):
        with self.assertRaises(MV.MVError):
            MV.plan(["head_profile", "head_back"])                       # no front
        with self.assertRaises(MV.MVError):
            MV.plan(["head_front", "body_underlayer_back"])              # two parts in one run
        with self.assertRaises(MV.MVError):
            MV.plan(["head_front"])                                      # one view
        with self.assertRaises(MV.MVError):
            MV.plan(["head_front", "hand_open_left"])                    # not a view cell
        with self.assertRaises(MV.MVError):
            MV.plan(["head_front", "body_underlayer_three_quarter"])     # mixed; also: facing unknown for it
        with self.assertRaises(MV.MVError):
            MV.plan(["body_underlayer_front", "body_underlayer_three_quarter"])   # no default facing: refuses instead of guessing


class Transforms(unittest.TestCase):
    def test_matches_the_upstream_example_for_the_four_orbit_angles(self):
        for fr in FIXTURE["frames"]:
            ang = int(fr["name"][4:])
            got = MV.transform_matrix(ang, MV.DISTANCE)
            for r in range(4):
                for c in range(4):
                    self.assertAlmostEqual(got[r][c], fr["transform_matrix"][r][c], places=9, msg=(ang, r, c))
        self.assertAlmostEqual(MV.CAMERA_ANGLE_X, FIXTURE["camera_angle_x"], places=12)
        self.assertAlmostEqual(MV.DISTANCE, FIXTURE["frames"][0]["transform_matrix"][1][3] * -1, places=9)
        self.assertEqual(MV.MESH_SCALE, FIXTURE["mesh_scale"])

    def test_loader_checks(self):
        self.assertEqual(MV.check_transforms(FIXTURE), [])
        self.assertTrue(MV.check_transforms({"frames": []}))
        bad = json.loads(json.dumps(FIXTURE))
        bad["frames"][0]["transform_matrix"] = FIXTURE["frames"][1]["transform_matrix"]
        self.assertIn("canonical front", " ".join(MV.check_transforms(bad)))
        del bad["camera_angle_x"]
        self.assertIn("camera_angle_x", " ".join(MV.check_transforms(bad)))

    def test_distance_is_the_norm_of_the_translation(self):
        for ang in (0, 45, 90, 180, 270, 315):
            m = MV.transform_matrix(ang)
            self.assertAlmostEqual(math.sqrt(sum(m[r][3] ** 2 for r in range(3))), MV.DISTANCE, places=9)


class Assemble(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_four_head_views_written_like_the_upstream_input(self):
        m = MV.assemble(head_views(self.d), str(self.d / "out"))
        self.assertEqual(m["angles_deg"], [0, 45, 90, 180])
        meta = json.load(open(self.d / "out" / "transforms.json", encoding="utf-8"))
        self.assertEqual(set(meta), {"camera_angle_x", "mesh_scale", "frames"})
        self.assertEqual(MV.check_transforms(meta), [])
        for fr in meta["frames"]:
            self.assertEqual(set(fr), {"file_path", "name", "transform_matrix"})
            im = Image.open(self.d / "out" / fr["file_path"])
            self.assertEqual((im.mode, im.size), ("RGBA", (1024, 1024)))
            box = im.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
            self.assertAlmostEqual((box[3] - box[1]) / 1024, MV.SUBJECT_FRAC, delta=0.01)
            self.assertAlmostEqual((box[1] + box[3]) / 2, 512, delta=1.5)
            self.assertAlmostEqual((box[0] + box[2]) / 2, 512, delta=1.5)
        self.assertEqual(meta["frames"][0]["file_path"], "view00_azim000.png")

    def test_views_of_different_subject_scale_get_the_same_height(self):
        v = head_views(self.d)
        v["head_back"] = cutout(self.d / "back_small.png", subj_w=400, subj_h=700)
        m = MV.assemble(v, str(self.d / "out"))
        hs = []
        for x in m["views"]:
            im = Image.open(self.d / "out" / x["file"])
            b = im.getchannel("A").point(lambda t: 255 if t > 16 else 0).getbbox()
            hs.append(b[3] - b[1])
        self.assertLessEqual(max(hs) - min(hs), 2)

    def test_mirror_is_opt_in_and_flagged(self):
        v = head_views(self.d)
        del v["head_three_quarter"]
        plain = MV.assemble(v, str(self.d / "o1"))
        self.assertEqual(plain["angles_deg"], [0, 90, 180])
        self.assertFalse(plain["mirror_profile"])
        self.assertNotIn("mirrored_profile", [a["flag"] for a in plain["assumptions"]])
        mir = MV.assemble(v, str(self.d / "o2"), mirror_profile=True)
        self.assertEqual(mir["angles_deg"], [0, 90, 180, 270])
        self.assertIn("mirrored_profile", [a["flag"] for a in mir["assumptions"]])
        self.assertEqual([x["mirrored_from"] for x in mir["views"]], [None, None, None, "head_profile"])
        a, b = Image.open(self.d / "o2" / "view01_azim090.png"), Image.open(self.d / "o2" / "view03_azim270.png")
        self.assertEqual(list(a.transpose(Image.Transpose.FLIP_LEFT_RIGHT).getdata())[:4000], list(b.getdata())[:4000])

    def test_flags_include_the_three_quarter_and_facing_assumptions(self):
        m = MV.assemble(head_views(self.d), str(self.d / "out"))
        flags = {(a.get("cell"), a["flag"]) for a in m["assumptions"]}
        self.assertIn(("head_three_quarter", "three_quarter_nominal_angle"), flags)
        self.assertIn(("head_profile", "profile_facing_left_by_eye"), flags)

    def test_folder_and_mapping_inputs(self):
        v = head_views(self.d)
        folder = self.d / "sheet"
        folder.mkdir()
        for c, p in v.items():
            shutil.copy(p, folder / f"{c}_1024x1024.png")
        a = MV.parse_spec(str(folder))
        self.assertEqual(sorted(a["views"]), sorted(v))
        mp = self.d / "map.json"
        mp.write_text(json.dumps({"views": v, "facing": {"head_profile": "right"}, "three_quarter": False}))
        b = MV.parse_spec(str(mp))
        self.assertEqual(b["facing"], {"head_profile": "right"})
        m = MV.assemble(str(mp), str(self.d / "o3"))
        self.assertEqual(m["angles_deg"], [0, 180, 270])      # right-facing profile, no three-quarter
        (folder / "head_front_2.png").write_bytes(b"x")
        with self.assertRaises(MV.MVError):
            MV.parse_spec(str(folder))                         # two files for one cell

    def test_refusals_carry_numbers(self):
        def refused(views, needle):
            with self.assertRaises(MV.MVError) as cm:
                MV.assemble(views, str(self.d / "x"))
            self.assertIn(needle, str(cm.exception))
            return str(cm.exception)

        v = head_views(self.d)
        rgb = dict(v, head_back=cutout(self.d / "rgb.png", mode_rgb=True))
        self.assertIn("NO alpha", refused(rgb, "head_back"))
        opaque = dict(v, head_back=cutout(self.d / "op.png", opaque=True))
        self.assertIn("alpha min 255", refused(opaque, "fully opaque"))
        big = dict(v, head_back=cutout(self.d / "big.png", w=1536, h=1536, subj_w=900, subj_h=1100))
        msg = refused(big, "differ in size")
        self.assertIn("1536x1536", msg)
        self.assertIn("1024x1024", msg)
        small = {c: cutout(self.d / f"s_{c}.png", w=512, h=512, subj_w=250, subj_h=410) for c in v}
        self.assertIn("512", refused(small, "gate"))
        tiny = dict(v, head_back=cutout(self.d / "t.png", subj_w=300, subj_h=400))
        self.assertIn("subject", refused(tiny, "INPUT GATE"))
        wide = {c: cutout(self.d / f"w_{c}.png", subj_w=900, subj_h=700) for c in v}
        self.assertIn("too wide", refused(wide, "view"))
        empty = dict(v, head_back=str(self.d / "nothere.png"))
        refused(empty, "file not found")
        landscape = {c: cutout(self.d / f"l_{c}.png", w=1536, h=1024, subj_w=600, subj_h=900) for c in v}
        refused(landscape, "landscape")
        self.assertFalse((self.d / "x").exists(), "a refusal writes nothing")


class Weights(unittest.TestCase):
    def test_missing_lists_exact_files_and_sizes(self):
        d = Path(tempfile.mkdtemp())
        why = MV.weights_reason(str(d))
        for name in ("ckpts/ss_flow_img_dit_1_3B_64_bf16_mv.safetensors", "ckpts/slat_flow_img2shape_dit_1_3B_512_bf16_mv.safetensors",
                     "ckpts/slat_flow_img2shape_dit_1_3B_1024_bf16_mv.safetensors", "ckpts/slat_flow_imgshape2tex_dit_1_3B_1024_bf16_mv.safetensors",
                     "ckpts/ss_flow_img_dit_1_3B_64_bf16_mv.json", "pipeline_mv.json"):
            self.assertIn(name, why)
        self.assertIn("absent", why)
        self.assertEqual(len(MV.weights_missing(str(d))), len(MV.MV_FILES))

    def test_short_file_is_missing_and_full_size_passes(self):
        d = Path(tempfile.mkdtemp())
        os.environ["FACTORY_TEST_MV_WEIGHT_SCALE"] = "0.000001"
        try:
            for rel, need in MV.MV_FILES.items():
                p = d.joinpath(*rel.split("/"))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x" * (int(need * 1e-6) + 1))
            self.assertEqual(MV.weights_reason(str(d)), "")
            ss = d / "ckpts" / "ss_flow_img_dit_1_3B_64_bf16_mv.safetensors"
            ss.write_bytes(b"x" * 10)
            why = MV.weights_reason(str(d))
            self.assertIn("ss_flow_img_dit_1_3B_64_bf16_mv.safetensors", why)
            self.assertIn("10 bytes", why)
        finally:
            del os.environ["FACTORY_TEST_MV_WEIGHT_SCALE"]

    def test_pipeline_mv_rembg_rewrite_is_idempotent(self):
        d = Path(tempfile.mkdtemp())
        (d / "pipeline_mv.json").write_text(json.dumps({"args": {"rembg_model": {"name": "BiRefNet", "args": {"model_name": "briaai/RMBG-2.0"}}}}))
        self.assertEqual(MV.rewrite_mv_pipeline(str(d), "C:/m/BiRefNet"), "C:/m/BiRefNet")
        self.assertTrue((d / "pipeline_mv.json.orig").exists())
        self.assertEqual(MV.rewrite_mv_pipeline(str(d), "other"), "C:/m/BiRefNet")


class Entry(unittest.TestCase):
    def test_entry_has_mv_dir_and_hands_over(self):
        text = (REPO / "tools" / "pixal3d_entry.py").read_text(encoding="utf-8")
        self.assertIn('"--mv-dir"', text)
        self.assertIn("pixal3d_mv.run_entry(a)", text)

    def test_run_entry_uses_local_models_and_the_upstream_function(self):
        d = Path(tempfile.mkdtemp())
        (d / "pipeline_mv.json").write_text(json.dumps({"args": {"rembg_model": {"name": "BiRefNet", "args": {"model_name": "briaai/RMBG-2.0"}}}}))
        calls = {}
        fake = types.ModuleType("inference_mv")
        fake.IMAGE_COND_CONFIGS = {"ss": {"model_name": "hub/dino"}, "tex_1024": {"model_name": "hub/dino"}}
        fake.run_inference = lambda **kw: calls.update(kw)
        natten = types.ModuleType("natten")
        natten.__version__ = "shim"
        saved = {k: sys.modules.get(k) for k in ("inference_mv", "natten")}
        sys.modules["inference_mv"], sys.modules["natten"] = fake, natten
        try:
            a = types.SimpleNamespace(model_dir=str(d), birefnet_dir="C:/m/BiRefNet", dino_dir="C:/m/dino", mv_dir="C:/in", output="C:/out.glb", seed=7,
                                      low_vram=True, resolution=1024, no_naf=False)
            MV.run_entry(a)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.assertEqual(calls, {"views_dir": "C:/in", "output_path": "C:/out.glb", "seed": 7, "model_path": str(d), "low_vram": True, "resolution": 1024})
        self.assertTrue(all(c["model_name"] == "C:/m/dino" for c in fake.IMAGE_COND_CONFIGS.values()))
        self.assertIn("C:/m/BiRefNet", (d / "pipeline_mv.json").read_text())

    @unittest.skipUnless(shutil.which("python") and __import__("importlib").util.find_spec("torch"), "torch not installed")
    def test_plain_upsample_takes_an_int_size(self):
        import torch
        ns = types.SimpleNamespace(DinoV3ProjFeatureExtractor=type("X", (), {}))
        MV.install_plain_upsample_mv(ns)
        o = ns.DinoV3ProjFeatureExtractor()
        o.naf_model = None
        o._load_naf()
        self.assertEqual(tuple(o.naf_model(None, torch.zeros(1, 4, 8, 8), 32).shape), (1, 4, 32, 32))


class ExportHook(unittest.TestCase):
    def test_mv_model_dir_resolution(self):
        self.assertEqual(MV.mv_model_dir({"local": {"work_dir": "W"}, "pixal3d": {}}), os.path.join("W", "Pixal3D-run"))
        self.assertEqual(MV.mv_model_dir({"local": {"work_dir": "W"}, "pixal3d": {"mv_model_dir": "X"}}), "X")
        self.assertEqual(MV.mv_model_dir({"local": {"work_dir": "W", "pixal3d_mv_model_dir": "Y"}, "pixal3d": {"mv_model_dir": "X"}}), "Y")

    def test_default_imports_nothing_and_a_module_gets_install_called(self):
        c = json.load(open(REPO / "config" / "config.json", encoding="utf-8-sig"))["pixal3d"]
        self.assertEqual(c["export"]["module"], "")
        self.assertEqual(MV.install_export(cfg={}), "")
        d = Path(tempfile.mkdtemp())
        (d / "fake_exporter_mv.py").write_text("CALLED = []\ndef install():\n    CALLED.append(1)\n")
        sys.path_before = list(sys.path)
        try:
            self.assertEqual(MV.install_export(cfg={"module": "fake_exporter_mv", "path": str(d)}), "fake_exporter_mv")
            import fake_exporter_mv
            self.assertEqual(fake_exporter_mv.CALLED, [1])
        finally:
            sys.path[:] = sys.path_before
            sys.modules.pop("fake_exporter_mv", None)

    def test_run_entry_installs_export_before_the_upstream_call(self):
        order = []
        d = Path(tempfile.mkdtemp())
        (d / "pipeline_mv.json").write_text(json.dumps({"args": {"rembg_model": {"name": "BiRefNet", "args": {"model_name": "C:/local"}}}}))
        fake = types.ModuleType("inference_mv")
        fake.IMAGE_COND_CONFIGS = {}
        fake.run_inference = lambda **kw: order.append("run")
        natten = types.ModuleType("natten")
        natten.__version__ = "shim"
        orig = MV.install_export
        MV.install_export = lambda M=None, cfg=None: order.append("export") or ""
        saved = {k: sys.modules.get(k) for k in ("inference_mv", "natten")}
        sys.modules["inference_mv"], sys.modules["natten"] = fake, natten
        try:
            MV.run_entry(types.SimpleNamespace(model_dir=str(d), birefnet_dir="b", dino_dir="x", mv_dir="i", output="o", seed=1, low_vram=False, resolution=-1, no_naf=False))
        finally:
            MV.install_export = orig
            for k, v in saved.items():
                sys.modules.pop(k, None) if v is None else sys.modules.__setitem__(k, v)
        self.assertEqual(order, ["export", "run"])


class Wiring(unittest.TestCase):
    def test_config_block(self):
        c = json.load(open(REPO / "config" / "config.json", encoding="utf-8-sig"))["pixal3d"]["multiview"]
        self.assertAlmostEqual(c["camera_angle_x"], FIXTURE["camera_angle_x"], places=12)
        self.assertEqual(c["facing"]["head_profile"], "left")

    def test_tool_schema_and_refusals(self):
        import server
        spec = server.TOOLS["pixal3d_run"]
        props = spec[2]
        self.assertIn("views", props)
        self.assertEqual(spec[3], ["asset_id"])
        with open(REPO / "config" / "config.json", encoding="utf-8-sig") as f1, open(REPO / "config" / "config.local.example.json", encoding="utf-8-sig") as f2:
            conf = {**json.load(f1), "local": json.load(f2)}
        d = Path(tempfile.mkdtemp())
        conf["local"]["pixal3d_mv_model_dir"] = str(d / "no-models")
        server.cfg = lambda: conf
        res = server.t_pixal3d_run("a", views=head_views(d))
        self.assertEqual(res["status"], "refused")
        self.assertIn("ss_flow_img_dit_1_3B_64_bf16_mv.safetensors", res["error"])
        self.assertEqual(len(res["missing"]), len(MV.MV_FILES))
        self.assertEqual(server.t_pixal3d_run("a")["status"], "refused")   # neither image nor views

    def test_tool_refuses_a_bad_view_before_the_gpu(self):
        import server
        d = Path(tempfile.mkdtemp())
        with open(REPO / "config" / "config.json", encoding="utf-8-sig") as f1, open(REPO / "config" / "config.local.example.json", encoding="utf-8-sig") as f2:
            conf = {**json.load(f1), "local": json.load(f2)}
        conf["local"]["pixal3d_mv_model_dir"] = str(d / "models")
        os.environ["FACTORY_TEST_MV_WEIGHT_SCALE"] = "0.000001"
        try:
            for rel, need in MV.MV_FILES.items():
                p = (d / "models").joinpath(*rel.split("/"))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x" * (int(need * 1e-6) + 1))
            server.cfg = lambda: conf
            v = head_views(d)
            v["head_back"] = cutout(d / "rgb.png", mode_rgb=True)
            res = server.t_pixal3d_run("a", views=v)
            self.assertEqual(res["status"], "refused")
            self.assertIn("NO alpha", res["error"])
        finally:
            del os.environ["FACTORY_TEST_MV_WEIGHT_SCALE"]

    def test_input_gate_hook_checks_each_view(self):
        d = Path(tempfile.mkdtemp())
        v = head_views(d)
        v["head_back"] = cutout(d / "small.png", w=512, h=512, subj_w=250, subj_h=410)
        r = subprocess.run([PY, str(PLUGIN / "hooks" / "input_gate.py")], input=json.dumps({"tool_name": MCP + "pixal3d_run", "tool_input": {"asset_id": "x", "views": v}}),
                           capture_output=True, text=True, env=dict(os.environ, FACTORY_HOME=str(REPO)), timeout=40)
        self.assertIn("deny", r.stdout)
        self.assertIn("small.png", r.stdout)


class StageScript(unittest.TestCase):
    """run_pixal3d.py --views against a throw-away home with a stub entry: receipt params, refusal on missing weights."""

    def make_root(self):
        root = Path(tempfile.mkdtemp()) / "repo"
        for sub in ("tools", "config", "plugins"):
            shutil.copytree(REPO / sub, root / sub, ignore=shutil.ignore_patterns("__pycache__", "tests"))
        loc = json.load(open(root / "config" / "config.local.example.json", encoding="utf-8-sig"))
        work = root.parent / "work"
        for sub in ("Pixal3D", "models/Pixal3D", "Pixal3D-run", "models/dinov3-vitl16-pretrain-lvd1689m", "models/moge-2-vitl", "models/BiRefNet", "outputs", "logs"):
            (work / sub).mkdir(parents=True, exist_ok=True)
        loc.update(work_dir=str(work), outputs_dir=str(work / "outputs"), logs_dir=str(work / "logs"), pixal3d_python=sys.executable, pixal3d_repo_dir=str(work / "Pixal3D"), pixal3d_model_dir=str(work / "models" / "Pixal3D"),
                   pixal3d_dino_dir=str(work / "models" / "dinov3-vitl16-pretrain-lvd1689m"), pixal3d_moge_dir=str(work / "models" / "moge-2-vitl"), pixal3d_birefnet_dir=str(work / "models" / "BiRefNet"), pixal3d_torch_home=str(work / "models" / "torch-hub"))
        (root / "config" / "config.local.json").write_text(json.dumps(loc))
        (root / "tools" / "pixal3d_entry.py").write_text(
            "import sys\nout = sys.argv[2]\nopen(out, 'wb').write(b'glb')\nopen(out + '.args', 'w').write(' '.join(sys.argv))\n")
        return root, work

    def run_stage(self, root, *args, env=None):
        return subprocess.run([PY, str(root / "tools" / "run_pixal3d.py"), *args], capture_output=True, text=True, timeout=120, cwd=str(root),
                              env=dict(os.environ, FACTORY_HOME=str(root), FACTORY_FAKE_FREE_RAM_GB="20", **(env or {})))

    def test_refuses_without_mv_weights(self):
        root, work = self.make_root()
        v = head_views(root.parent)
        r = self.run_stage(root, "asset", "--views", json.dumps({"views": v}))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ss_flow_img_dit_1_3B_64_bf16_mv.safetensors", r.stderr + r.stdout)

    def test_multiview_receipt(self):
        root, work = self.make_root()
        for rel, need in MV.MV_FILES.items():
            p = (work / "Pixal3D-run").joinpath(*rel.split("/"))   # default mv_model_dir = work_dir/Pixal3D-run
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * (int(need * 1e-6) + 1))
        v = head_views(root.parent)
        r = self.run_stage(root, "asset", "--views", json.dumps({"views": v}), "--no-naf", env={"FACTORY_TEST_MV_WEIGHT_SCALE": "0.000001"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        rec = json.load(open(next((root / "receipts" / "asset").glob("pixal3d-*.json")), encoding="utf-8"))
        p = rec["params"]
        self.assertEqual(rec["status"], "ok")
        self.assertEqual((p["mode"], p["pipeline"], p["num_views"]), ("multiview", "PIXAL3D", 4))
        self.assertEqual(p["angles_deg"], [0, 45, 90, 180])
        self.assertTrue(all(len(x["source_sha256"]) == 64 and len(x["file_sha256"]) == 64 for x in p["views"]))
        self.assertIn("three_quarter_nominal_angle", [a["flag"] for a in p["assumptions"]])
        self.assertFalse(p["mirror_profile"])
        self.assertEqual(p["camera"]["distance"], MV.DISTANCE)
        self.assertEqual(len(rec["inputs"]), 4)
        self.assertTrue(rec["outputs"][0]["path"].endswith("pixal3d_mv_standard.glb"))
        args = open(rec["outputs"][0]["path"] + ".args", encoding="utf-8").read()
        self.assertIn("--mv-dir", args)
        self.assertIn("view00_azim000.png", args)
        self.assertIn(str(work / "Pixal3D-run"), args)          # --model-dir is the MV folder, not models/Pixal3D
        self.assertEqual(p["export_module"], "upstream cumesh")


if __name__ == "__main__":
    unittest.main()
