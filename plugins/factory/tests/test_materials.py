"""Offline tests for the MATERIALS stage (plugin 0.1.17; standard library only; run: python -m unittest discover plugins/factory/tests -v).
Nothing here downloads, renders or touches a GPU. Blender is a fake launcher (a python script behind a .cmd/.sh wrapper) that writes the files the real scripts would; curl is a
fake that copies a local file. Receipts go to a throw-away repo root (cf_common.REPO is patched), never to the real receipts/ folder.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
sys.path.insert(0, str(PLUGIN / "tests"))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tools" / "materials"))
sys.path.insert(0, str(REPO / "tools" / "ab"))
import blender_guard  # noqa: E402
import cf_common  # noqa: E402
import fetch_materials as fm  # noqa: E402
import run_materials as rm  # noqa: E402
import test_plugin as tp  # noqa: E402

MAPPING = json.loads((REPO / "config" / "materials.json").read_text(encoding="utf-8-sig"))
CONFIG = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))
SHEET_SWATCHES = ["crimson_damask_velvet", "black_fur", "gold_embroidery", "black_leather", "pale_skin", "dark_hair", "steel", "lace", "velvet_lining"]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


class Mapping(unittest.TestCase):
    def test_every_sheet_swatch_is_resolved_or_an_honest_gap(self):
        for s in SHEET_SWATCHES:
            self.assertIn(s, MAPPING["swatches"], s)
            row = MAPPING["swatches"][s]
            self.assertTrue(row["fit"], s)
            if row["material"] is None:
                self.assertTrue(row["fit"].startswith("GAP"), s)
            else:
                self.assertIn(row["material"], MAPPING["materials"])
        self.assertEqual(sorted(k for k, v in MAPPING["swatches"].items() if not v["material"]), ["dark_hair", "lace", "pale_skin"])

    def test_every_set_is_cc0_2k_with_published_numbers(self):
        total = 0
        for aid, m in MAPPING["materials"].items():
            self.assertEqual(m["licence"], "CC0-1.0", aid)
            self.assertIn(m["source"], ("polyhaven", "ambientcg"))
            self.assertTrue(m["url"].startswith("https://"), aid)
            self.assertEqual(m["normal_convention"], "gl", aid)
            self.assertGreater(m["tile_m"], 0, aid)
            self.assertTrue(m["tile_m_basis"], aid)
            if m["source"] == "polyhaven":
                self.assertEqual({"base_color", "normal", "roughness"} - {f["map"] for f in m["files"]}, set(), aid)
                for f in m["files"]:
                    self.assertRegex(f["md5"], r"^[0-9a-f]{32}$")
                    self.assertIn("/2k/", f["url"])
                    self.assertTrue(f["url"].startswith("https://dl.polyhaven.org/"))
                    total += f["bytes"]
            else:
                z = m["zip"]
                self.assertIn("2K-JPG", z["name"])
                self.assertTrue(z["url"].startswith("https://ambientcg.com/get?file="))
                self.assertIsNone(z["md5"])   # ambientCG publishes none: size + zip CRC + sha256 are the checks
                self.assertEqual({"base_color", "normal", "roughness"} - set(m["zip_members"]), set(), aid)
                total += z["bytes"]
        self.assertEqual(total, MAPPING["total_bytes"])
        self.assertLess(total, 1024 * 1024 * 1024)

    def test_parts_point_at_real_swatches(self):
        for part, p in MAPPING["parts"].items():
            self.assertIn(p["swatch"], MAPPING["swatches"], part)
            self.assertTrue(MAPPING["swatches"][p["swatch"]]["material"], part)
            self.assertGreater(p["part_height_m"], 0)
        self.assertEqual(set(MAPPING["parts"]), {"coat", "cloak", "boots"})


class Fetcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.src = self.t / "src"
        self.src.mkdir()
        self.payload = b"scan-bytes" * 1000
        (self.src / "a.jpg").write_bytes(self.payload)
        # a fake curl: copies a local file (the url's last segment from SRC) to the -o path, appending after a .part prefix like a real resume
        self.calls = self.t / "calls.txt"
        fake = self.t / "fakecurl.py"
        fake.write_text(
            "import sys, os, shutil\n"
            f"a = sys.argv[1:]\nopen({str(self.calls)!r}, 'a').write(' '.join(a) + '\\n')\n"
            "out = a[a.index('-o') + 1]; url = a[-1]\n"
            f"data = open(os.path.join({str(self.src)!r}, url.rsplit('/', 1)[1]), 'rb').read()\n"
            "have = os.path.getsize(out) if os.path.exists(out) else 0\n"
            "open(out, 'ab').write(data[have:])\n", encoding="utf-8")
        if os.name == "nt":
            self.curl = str(self.t / "curl.cmd")
            Path(self.curl).write_text(f'@"{sys.executable}" "{fake}" %*\r\n', encoding="utf-8")
        else:
            self.curl = str(self.t / "curl.sh")
            Path(self.curl).write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n', encoding="utf-8")
            os.chmod(self.curl, os.stat(self.curl).st_mode | stat.S_IEXEC)
        self.row = {"asset": "x", "source": "polyhaven", "name": "a.jpg", "url": "https://dl.example/a.jpg", "bytes": len(self.payload), "md5": md5(self.payload), "map": "base_color", "zip": False}

    def tearDown(self):
        self.tmp.cleanup()

    def test_ipv4_resume_and_verify(self):
        folder = str(self.t / "out")
        os.makedirs(folder)
        part = os.path.join(folder, "a.jpg.part")
        Path(part).write_bytes(self.payload[:123])   # a half file from an earlier run
        final = fm.fetch_one(self.row, folder, self.curl)
        self.assertEqual(Path(final).read_bytes(), self.payload)
        self.assertFalse(os.path.exists(part))
        cmd = self.calls.read_text()
        self.assertIn("--ipv4", cmd)
        self.assertIn("-C -", cmd)
        n = len(self.calls.read_text().splitlines())
        fm.fetch_one(self.row, folder, self.curl)   # already verified: not fetched again
        self.assertEqual(len(self.calls.read_text().splitlines()), n)

    def test_a_wrong_checksum_removes_the_file_and_fails(self):
        folder = str(self.t / "out2")
        os.makedirs(folder)
        bad = dict(self.row, md5="0" * 32)
        with self.assertRaises(RuntimeError) as cm:
            fm.fetch_one(bad, folder, self.curl)
        self.assertIn("md5", str(cm.exception))
        self.assertEqual(os.listdir(folder), [])

    def test_zip_crc_and_size(self):
        z = self.t / "s.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("m_Color.jpg", b"c" * 50)
        row = {"zip": True, "bytes": z.stat().st_size, "md5": None}
        self.assertEqual(fm.verify(str(z), row), "")
        self.assertIn("size", fm.verify(str(z), dict(row, bytes=1)))
        junk = self.t / "junk.zip"
        junk.write_bytes(b"not a zip")
        self.assertIn("zip", fm.verify(str(junk), dict(row, bytes=9)))

    def test_list_downloads_nothing_and_no_download_without_approval(self):
        old = fm.MAPPING
        m = {"resolution": "2k", "materials": {"x": {"source": "polyhaven", "asset_id": "x", "licence": "CC0-1.0", "url": "u", "normal_convention": "gl", "tile_m": 1, "tile_m_basis": "t",
                                                      "files": [{"map": "base_color", "name": "a.jpg", "url": self.row["url"], "bytes": self.row["bytes"], "md5": self.row["md5"]}]}}}
        (self.t / "m.json").write_text(json.dumps(m), encoding="utf-8")
        fm.MAPPING = str(self.t / "m.json")
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(fm.main(["--list", "--curl", self.curl]), 0)
                self.assertEqual(fm.main(["--curl", self.curl, "--dest", str(self.t / "d")]), 2)   # no --approved
            self.assertIn("a.jpg", out.getvalue())
            self.assertIn("--approved", err.getvalue())
            self.assertFalse(self.calls.exists())   # curl never ran
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(fm.main(["--approved", "--max-total-mb", "0.000001", "--curl", self.curl, "--dest", str(self.t / "d")]), 2)   # over the cap
            self.assertFalse(self.calls.exists())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(fm.main(["--approved", "--curl", self.curl, "--dest", str(self.t / "d")]), 0)
            man = json.loads((self.t / "d" / "x" / "manifest.json").read_text())
            self.assertEqual((man["name"], man["licence"], man["resolution"], man["files"][0]["sha256"]), ("x", "CC0-1.0", "2k", hashlib.sha256(self.payload).hexdigest()))
            self.assertIn("gl", man["map_conventions"]["normal"])
        finally:
            fm.MAPPING = old


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lock_exclusive_stale_and_live(self):
        lock = str(self.t / "blender_job.lock")
        blender_guard.acquire(lock, 1)
        self.assertEqual(json.loads(Path(lock).read_text())["pid"], os.getpid())
        t0 = time.time()
        with self.assertRaises(blender_guard.LockBusy):   # our own pid is alive: a second taker waits, then gives up
            blender_guard.acquire(lock, 0.4, poll_s=0.1)
        self.assertGreaterEqual(time.time() - t0, 0.35)
        blender_guard.release(lock)
        self.assertFalse(os.path.exists(lock))
        Path(lock).write_text(json.dumps({"pid": 2147480000}), encoding="utf-8")   # dead pid: stale, replaced
        blender_guard.acquire(lock, 1)
        self.assertEqual(json.loads(Path(lock).read_text())["pid"], os.getpid())
        blender_guard.release(lock)
        Path(lock).write_text(json.dumps({"pid": 2147480001}), encoding="utf-8")
        blender_guard.release(lock)   # not ours: left alone
        self.assertTrue(os.path.exists(lock))

    def test_flux_load_window(self):
        d = str(self.t)
        self.assertEqual(blender_guard.flux_loading(d, 60), "")
        log = self.t / "kit_vampire_gpt_sheet_foot_left.txt"
        log.write_text("RESULT foot_left\n", encoding="utf-8")
        now = time.time()
        self.assertIn("started", blender_guard.flux_loading(d, 60, now=now + 5))   # a cell file created 5 s ago
        self.assertEqual(blender_guard.flux_loading(d, 60, now=now + 600), "")      # long ago
        # an older file that was touched just now with a load line as its last event
        os.utime(log, (now - 3600, now - 3600))
        log.write_text("0%|  | 0/16\nLoading checkpoint shards\n 5%|#| 1/16 [00:00]\n", encoding="utf-8")
        self.assertEqual(blender_guard.flux_loading(d, 60, now=now + 5000), "")
        log.write_text("0%|  | 0/16\nLoading checkpoint shards\n", encoding="utf-8")
        t = os.path.getctime(log)
        os.utime(log, (t + 100, t + 100))
        self.assertIn("load is in progress", blender_guard.flux_loading(d, 60, now=t + 120))
        log.write_text("Loading checkpoint shards\n100%|##| 16/16 [00:01<00:00]\n", encoding="utf-8")
        os.utime(log, (t + 100, t + 100))
        self.assertEqual(blender_guard.flux_loading(d, 60, now=t + 120), "")   # progress bars are not events: generating, not loading

    def test_ram_wait_is_bounded(self):
        os.environ["FACTORY_FAKE_FREE_RAM_GB"] = "3"
        try:
            t0 = time.time()
            why = blender_guard.wait_ready(10, str(self.t), 60, 0.3, poll_s=0.1)
            self.assertIn("3.0 GB", why)
            self.assertLess(time.time() - t0, 5)
            os.environ["FACTORY_FAKE_FREE_RAM_GB"] = "12"
            self.assertEqual(blender_guard.wait_ready(10, str(self.t), 60, 0.3), "")
        finally:
            os.environ.pop("FACTORY_FAKE_FREE_RAM_GB", None)


FAKE_BLENDER = r'''
import json, os, sys
sys.path.insert(0, %(tests)r)
import test_plugin as tp
a = sys.argv[1:]
script = a[a.index("--python") + 1]
rest = a[a.index("--") + 1:]
open(%(calls)r, "a").write(os.path.basename(script) + " " + " ".join(rest) + "\n")
name = os.path.basename(script)
if name == "compose_blender.py":
    out = rest[2]
    os.makedirs(out, exist_ok=True)
    tp.png(__import__("pathlib").Path(out) / "side_by_side_turn_000.png", 16, 16)
    json.dump({"status": "ok"}, open(os.path.join(out, "side_by_side.json"), "w"))
else:
    cfg = json.load(open(rest[rest.index("--config") + 1]))
    os.makedirs(cfg["out_dir"], exist_ok=True)
    for ang in cfg["angles_deg"]:
        tp.png(__import__("pathlib").Path(cfg["out_dir"]) / ("turn_%%03d.png" %% ang), 16, 16)
    json.dump({"status": "ok", "label": cfg["label"], "frames": []}, open(os.path.join(cfg["out_dir"], "turntable.json"), "w"))
    if "materials" in cfg:
        json.dump({"material": cfg["materials"]["name"], "meshes": []}, open(os.path.join(cfg["out_dir"], "materials_applied.json"), "w"))
'''


class RunMaterialsWithFakeBlender(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        t = self.t
        # a throw-away repo root: the mapping, a dummy turntable script, an (empty) receipts folder
        (t / "repo" / "config").mkdir(parents=True)
        (t / "repo" / "tools" / "ab").mkdir(parents=True)
        shutil.copy(REPO / "config" / "materials.json", t / "repo" / "config" / "materials.json")
        (t / "repo" / "tools" / "ab" / "turntable_blender.py").write_text("# dummy\n", encoding="utf-8")
        # a throw-away materials dir with a manifest for the coat's set (the files are tiny fakes; their sha256 is in the manifest)
        mat = MAPPING["swatches"]["crimson_damask_velvet"]["material"]
        self.mat_root = t / "materials"
        d = self.mat_root / mat
        d.mkdir(parents=True)
        files, maps = [], {}
        for k in ("base_color", "normal", "roughness", "height"):
            p = d / f"{k}.jpg"
            p.write_bytes(k.encode() * 10)
            files.append({"name": p.name, "map": k, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
            maps[k] = p.name
        (d / "manifest.json").write_text(json.dumps({"files": files, "maps": maps}), encoding="utf-8")
        self.glb = t / "coat.glb"
        self.glb.write_bytes(b"glTF-coat")
        self.calls = t / "blender_calls.txt"
        fake = t / "fake_blender.py"
        fake.write_text(FAKE_BLENDER % {"tests": str(PLUGIN / "tests"), "calls": str(self.calls)}, encoding="utf-8")
        if os.name == "nt":
            self.blender = str(t / "blender.cmd")
            Path(self.blender).write_text(f'@"{sys.executable}" "{fake}" %*\r\n', encoding="utf-8")
        else:
            self.blender = str(t / "blender.sh")
            Path(self.blender).write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n', encoding="utf-8")
            os.chmod(self.blender, os.stat(self.blender).st_mode | stat.S_IEXEC)
        cfg = json.loads(json.dumps(CONFIG))
        cfg["materials"].update(ram_wait_s=0, lock_wait_s=0.3)
        cfg["local"] = {"work_dir": str(t), "logs_dir": str(t / "logs"), "outputs_dir": str(t / "out"), "blender_exe": self.blender, "materials_dir": str(self.mat_root), "machine": {}}
        (t / "logs").mkdir()
        self.cfg = cfg
        self._old = (cf_common.REPO, cf_common.load_config, os.environ.get("FACTORY_FAKE_FREE_RAM_GB"))
        cf_common.REPO = str(t / "repo")
        cf_common.load_config = lambda: self.cfg
        os.environ["FACTORY_FAKE_FREE_RAM_GB"] = "64"

    def tearDown(self):
        cf_common.REPO, cf_common.load_config = self._old[0], self._old[1]
        if self._old[2] is None:
            os.environ.pop("FACTORY_FAKE_FREE_RAM_GB", None)
        else:
            os.environ["FACTORY_FAKE_FREE_RAM_GB"] = self._old[2]
        self.tmp.cleanup()

    def run_main(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = rm.main(["vampire-dracula-coat", str(self.glb), *extra])
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def receipts(self):
        return sorted((self.t / "repo" / "receipts" / "vampire-dracula-coat").glob("materials-*.json"))

    def test_ok_run_renders_baked_then_scanned_then_composes_and_releases_the_lock(self):
        rc, out, err = self.run_main("--part", "coat")
        self.assertEqual(rc, 0, out + err)
        calls = self.calls.read_text().splitlines()
        self.assertEqual([c.split()[0] for c in calls], ["turntable_blender.py", "apply_materials_blender.py", "compose_blender.py"])
        rec = json.loads(self.receipts()[-1].read_text(encoding="utf-8"))
        self.assertEqual((rec["stage"], rec["status"]), ("materials", "ok"))
        self.assertEqual(rec["params"]["swatch"], "crimson_damask_velvet")
        self.assertEqual(rec["params"]["licence"], "CC0-1.0")
        self.assertTrue(any(o["path"].endswith("side_by_side_turn_000.png") for o in rec["outputs"]))
        self.assertFalse((self.t / cf_common.load_config()["materials"]["lock_file"]).exists())   # the lock is gone
        base = json.loads((self.t / "out" / "3d" / "vampire-dracula-coat" / "materials" / "coat" / "baked" / "render_config.json").read_text())
        scan = json.loads((self.t / "out" / "3d" / "vampire-dracula-coat" / "materials" / "coat" / "scanned" / "render_config.json").read_text())
        for k in ("lights", "world", "camera", "resolution", "samples", "engine", "device", "angles_deg", "head_crops"):   # the SAME rig: only glb, out_dir, label, material differ
            self.assertEqual(base[k], scan[k], k)
        self.assertEqual(scan["lights"], CONFIG["ab"]["render"]["lights"])
        self.assertEqual(scan["world"], CONFIG["ab"]["render"]["world"])
        self.assertEqual(scan["camera"], CONFIG["ab"]["render"]["camera"])
        self.assertNotIn("materials", base)
        self.assertEqual(scan["materials"]["normal_convention"], "gl")
        self.assertEqual(scan["materials"]["part_height_m"], MAPPING["parts"]["coat"]["part_height_m"])

    def test_low_ram_refuses_before_any_blender_starts(self):
        os.environ["FACTORY_FAKE_FREE_RAM_GB"] = "8"
        rc, out, err = self.run_main()
        self.assertNotEqual(rc, 0)
        self.assertIn("not started", str(rc) + err)
        self.assertFalse(self.calls.exists())
        self.assertEqual(json.loads(self.receipts()[-1].read_text(encoding="utf-8"))["status"], "fail")

    def test_a_live_lock_makes_it_give_up_without_blender(self):
        lock = self.t / "blender_job.lock"
        lock.write_text(json.dumps({"pid": os.getpid(), "tool": "cloth"}), encoding="utf-8")
        rc, out, err = self.run_main()
        self.assertNotEqual(rc, 0)
        self.assertIn("held by live pid", str(rc))
        self.assertFalse(self.calls.exists())
        self.assertTrue(lock.exists())   # another job's lock is never removed

    def test_a_flux_model_load_blocks_the_start(self):
        (self.t / "logs" / "kit_vampire_gpt_sheet_head_top.txt").write_text("START\n", encoding="utf-8")   # created just now
        rc, out, err = self.run_main()
        self.assertNotEqual(rc, 0)
        self.assertIn("Flux", str(rc))
        self.assertFalse(self.calls.exists())

    def test_gap_swatches_and_missing_sets_stop_the_run(self):
        for sw in ("pale_skin", "dark_hair", "lace"):
            rc, out, err = self.run_main("--swatch", sw)
            self.assertIn("no scanned set", str(rc), sw)
        rc, out, err = self.run_main("--swatch", "black_leather")   # a real swatch whose set is not in the fake materials dir
        self.assertIn("manifest.json is missing", str(rc))
        self.assertFalse(self.calls.exists())

    def test_a_corrupted_map_is_refused(self):
        mat = MAPPING["swatches"]["crimson_damask_velvet"]["material"]
        (self.mat_root / mat / "base_color.jpg").write_bytes(b"tampered")
        rc, out, err = self.run_main()
        self.assertIn("sha256 differs", str(rc))
        self.assertFalse(self.calls.exists())

    def test_plan_starts_nothing(self):
        rc, out, err = self.run_main("--plan")
        self.assertEqual(rc, 0, out + err)
        plan = json.loads(out)
        self.assertEqual((plan["swatch"], plan["problems"]), ("crimson_damask_velvet", []))
        self.assertFalse(self.calls.exists())


class StaticChecks(unittest.TestCase):
    def test_scripts_parse_and_share_one_rig(self):
        for rel in ("tools/materials/apply_materials_blender.py", "tools/materials/compose_blender.py", "tools/materials/fetch_materials.py", "tools/materials/blender_guard.py",
                    "tools/run_materials.py", "tools/ab/turntable_blender.py"):
            ast.parse((REPO / rel).read_text(encoding="utf-8"), rel)
        apply_src = (REPO / "tools" / "materials" / "apply_materials_blender.py").read_text(encoding="utf-8")
        for forbidden in ("lights.new", "'AREA'", '"AREA"', "worlds.new", "cameras.new"):
            self.assertNotIn(forbidden, apply_src, forbidden)   # no second rig: it execs the A/B turntable
        self.assertIn("CF_NO_AUTORUN", apply_src)
        tt = (REPO / "tools" / "ab" / "turntable_blender.py").read_text(encoding="utf-8")
        self.assertIn("def main(post_import=None)", tt)
        self.assertIn('globals().get("CF_NO_AUTORUN")', tt)

    def test_box_uv_in_metres_matches_the_formula(self):
        """The projection rule of apply_materials_blender.box_uv, restated without bpy: u,v = world metres / tile_m on the face's dominant axis."""
        def uv(point, normal, mpu, tile):
            ax = max(range(3), key=lambda i: abs(normal[i]))
            c = [x * mpu / tile for x in point]
            return ((c[1], c[2]), (c[0], c[2]), (c[0], c[1]))[ax]
        # a 1.05 m coat of native height 2.1 units: 0.5 m per unit; a 0.284 m weave: a point 1 unit up the front is 0.5 m = 1.76 tiles
        self.assertAlmostEqual(uv((0, 0, 1.0), (0, -1, 0), 0.5, 0.284)[1], 0.5 / 0.284, places=6)
        self.assertAlmostEqual(uv((0.4, 0.2, 0.0), (0, 0, 1), 0.5, 0.25)[0], 0.8, places=6)


class HooksAndTool(unittest.TestCase):
    def test_ram_gate_uses_10_gb_for_materials_and_6_for_the_rest(self):
        low = dict(FACTORY_FAKE_FREE_RAM_GB=8, FACTORY_HOME=REPO)
        self.assertTrue(tp.denied(tp.run_hook("ram_gate.py", tp.bash("python tools/run_materials.py a coat.glb"), **low)))
        self.assertTrue(tp.denied(tp.run_hook("ram_gate.py", {"tool_name": tp.MCP_P + "materials", "tool_input": {}}, **low)))
        self.assertIsNone(tp.run_hook("ram_gate.py", tp.bash("python tools/run_stills.py a b.glb"), **low))
        ok = dict(FACTORY_FAKE_FREE_RAM_GB=10, FACTORY_HOME=REPO)
        self.assertIsNone(tp.run_hook("ram_gate.py", tp.bash("python tools/run_materials.py a coat.glb"), **ok))
        self.assertIsNone(tp.run_hook("ram_gate.py", tp.bash("python tools/run_materials.py a coat.glb --help"), FACTORY_FAKE_FREE_RAM_GB=1, FACTORY_HOME=REPO))

    def test_plugin_guard_refuses_a_raw_call_and_the_gpu_lock_is_not_required(self):
        sys.path.insert(0, str(PLUGIN / "hooks"))
        import plugin_guard
        why = plugin_guard.decide({"tool_name": "Bash", "tool_input": {"command": "python tools/run_materials.py a coat.glb"}})
        self.assertIn("materials", why)
        self.assertEqual(plugin_guard.decide({"tool_name": "Bash", "tool_input": {"command": "python tools/run_materials.py a coat.glb --help"}}), "")
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / ".gpu_lock"
            lock.write_text(json.dumps({"pid": os.getpid(), "job": "generate.py", "started_utc": "x"}), encoding="utf-8")   # a live GPU job
            self.assertIsNone(tp.run_hook("gpu_lock_guard.py", tp.bash("python tools/run_materials.py a coat.glb"), FACTORY_GPU_LOCK=lock))   # CPU only: not blocked
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        mine = [g for g in hooks if "run_materials" in json.dumps(g) or g["matcher"].endswith("__materials")]
        self.assertEqual(len(mine), 3)
        self.assertNotIn("gpu_lock_guard", json.dumps(mine))
        self.assertIn("plugin_guard.py", json.dumps(mine))
        self.assertIn("ram_gate.py", json.dumps(mine))


class McpMaterials(unittest.TestCase):
    """The materials tool, over real stdio against the fake FACTORY_HOME of the plugin tests (it carries a copy of config.json; materials.json is added here)."""

    talk = tp.McpServer.talk
    call = tp.McpServer.call

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def setUpClass(cls):
        tp.McpServer.setUpClass.__func__(cls)
        shutil.copy(REPO / "config" / "materials.json", cls.home / "config" / "materials.json")
        (cls.home / "characters").mkdir(exist_ok=True)
        shutil.copy(REPO / "characters" / "vampire-dracula.json", cls.home / "characters" / "vampire-dracula.json")
        loc = json.loads((cls.home / "config" / "config.local.json").read_text(encoding="utf-8"))
        loc["materials_dir"] = str(cls.t / "no_materials")
        loc["work_dir"] = str(cls.t)
        (cls.home / "config" / "config.local.json").write_text(json.dumps(loc), encoding="utf-8")

    def test_tool_is_listed_with_its_schema(self):
        out = self.talk([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
        tools = {t["name"]: t for t in out[0]["result"]["tools"]}
        self.assertIn("materials", tools)
        self.assertEqual(tools["materials"]["inputSchema"]["properties"]["part"]["enum"], ["coat", "cloak", "boots"])

    def test_plan_from_a_character_spec_names_the_parts_and_the_gaps(self):
        err, text = self.call("materials", {"character_spec": "characters/vampire-dracula.json"}, FACTORY_FAKE_FREE_RAM_GB=64)
        self.assertFalse(err)
        plan = json.loads(text)
        self.assertEqual({p["part"]: p["material"] for p in plan["parts"]}, {"coat": "quatrefoil_jacquard_fabric", "cloak": "curly_teddy_natural", "boots": "Leather032"})
        self.assertTrue(all(p["on_disk"] is False for p in plan["parts"]))
        self.assertEqual(plan["swatches_without_a_scan"], ["dark_hair", "lace", "pale_skin"])

    def test_refusals_start_nothing(self):
        glb = str(self.t / "mesh.glb")
        for args, needle in (({"asset_id": "a", "glb": glb, "part": "hat"}, "part must be"), ({"asset_id": "a", "glb": glb, "swatch": "pale_skin"}, "no scanned set"),
                             ({"asset_id": "a", "glb": glb, "swatch": "nope"}, "unknown swatch"), ({"asset_id": "a", "glb": glb}, "not on disk"),
                             ({"asset_id": "a", "glb": str(self.t / "missing.glb")}, "not found"), ({"asset_id": "a"}, "required")):
            err, text = self.call("materials", args, FACTORY_FAKE_FREE_RAM_GB=64)
            self.assertTrue(err, args)
            self.assertIn(needle, text, args)
        # a set that is on disk, then low RAM (8 GB: under the 10 GB a Blender job needs, over the 6 GB start limit)
        d = self.t / "no_materials" / "quatrefoil_jacquard_fabric"
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text("{}", encoding="utf-8")
        err, text = self.call("materials", {"asset_id": "a", "glb": glb}, FACTORY_FAKE_FREE_RAM_GB=8)
        self.assertTrue(err)
        self.assertIn("needs 10", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
