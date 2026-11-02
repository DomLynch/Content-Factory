"""Offline tests for plugin 0.1.27: the latest-models gate (cf_models + cf_common.plugin_gate("models")). No network: the upstream lookup is replaced by a fake."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "plugins" / "factory" / "lib"))
sys.path.insert(0, str(REPO / "tools"))
import cf_guard  # noqa: E402
import cf_models  # noqa: E402
import cf_common  # noqa: E402


def git(d, *a):
    return subprocess.run(["git", "-C", str(d), "-c", "user.email=t@t", "-c", "user.name=t", *a], capture_output=True, text=True, check=True).stdout.strip()


class Models(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d = Path(self.t.name)
        self.old_home, self.old_repo = cf_guard.HOME, cf_common.REPO
        cf_guard.HOME = self.d
        cf_common.REPO = str(self.d)
        self.addCleanup(lambda: (setattr(cf_guard, "HOME", self.old_home), setattr(cf_common, "REPO", self.old_repo),
                                 setattr(cf_common, "MODELS_CHECK", None), setattr(cf_common, "ALLOW_STALE", None)))
        self.repo = self.d / "clone"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "a").write_text("1")
        git(self.repo, "add", "a")
        git(self.repo, "commit", "-qm", "one")
        self.first = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "a").write_text("2")
        git(self.repo, "commit", "-qam", "two")
        self.second = git(self.repo, "rev-parse", "HEAD")
        self.cfg = {"local": {"clone": str(self.repo)}, "models": {"registry": [
            {"name": "ToolA", "stages": ["flux"], "installed": {"kind": "git", "local": "clone"}, "upstream": {"kind": "github_head", "repo": "o/a"}},
            {"name": "ToolB", "stages": ["upscale"], "installed": {"kind": "pinned", "version": "1.2.0"}, "upstream": {"kind": "github_release", "repo": "o/b"}}]}}

    def fake(self, mapping):
        calls = []

        def f(up):
            calls.append(up["repo"])
            v = mapping[up["repo"]]
            if isinstance(v, Exception):
                raise v
            return v
        f.calls = calls
        return f

    def test_git_current_when_upstream_is_the_installed_commit_or_its_ancestor(self):
        self.assertEqual(cf_models.check(self.cfg, "flux", self.fake({"o/a": self.second, "o/b": "v1"}))[0]["state"], "current")
        (self.d / "state" / "models_upstream.json").unlink()
        self.assertEqual(cf_models.check(self.cfg, "flux", self.fake({"o/a": self.first, "o/b": "v1"}))[0]["state"], "current")   # upstream is behind the install

    def test_git_behind_when_upstream_commit_is_unknown_to_the_clone(self):
        r = cf_models.check(self.cfg, "flux", self.fake({"o/a": "f" * 40, "o/b": "v1"}))[0]
        self.assertEqual(r["state"], "behind")
        self.assertTrue(cf_models.refusals([r]) and "--allow-stale" in cf_models.refusals([r])[0])

    def test_a_list_of_local_keys_takes_the_first_one_set(self):
        self.cfg["local"]["other"] = str(self.repo)
        self.cfg["local"]["unset"] = None
        self.cfg["models"]["registry"][0]["installed"]["local"] = ["unset", "missing_key", "other"]
        self.assertEqual(cf_models.check(self.cfg, "flux", self.fake({"o/a": self.second, "o/b": "v1"}))[0]["installed"], self.second)

    def test_only_rows_of_the_stage_are_checked_and_versions_compare_numerically(self):
        f = self.fake({"o/a": self.second, "o/b": "v1.10.0"})
        r = cf_models.check(self.cfg, "upscale", f)
        self.assertEqual([x["name"] for x in r], ["ToolB"])
        self.assertEqual(r[0]["state"], "behind")   # 1.2.0 < 1.10.0
        self.assertEqual(f.calls, ["o/b"])

    def test_unknown_installed_is_unverified_not_refused(self):
        self.cfg["models"]["registry"][1]["installed"]["version"] = None
        r = cf_models.check(self.cfg, "upscale", self.fake({"o/a": "x", "o/b": "v9"}))[0]
        self.assertEqual(r["state"], "unverified")
        self.assertEqual(cf_models.refusals([r]), [])

    def test_lookup_is_cached_for_24h_and_cache_serves_when_the_network_is_down(self):
        f = self.fake({"o/a": self.second, "o/b": "v1"})
        cf_models.check(self.cfg, "flux", f)
        cf_models.check(self.cfg, "flux", f)
        self.assertEqual(f.calls, ["o/a"])   # second call came from the cache
        down = self.fake({"o/a": OSError("no network"), "o/b": OSError("no network")})
        cache = json.loads((self.d / "state" / "models_upstream.json").read_text())
        for v in cache.values():
            v["t"] -= 3 * 24 * 3600   # stale cache, network down: still used
        (self.d / "state" / "models_upstream.json").write_text(json.dumps(cache))
        r = cf_models.check(self.cfg, "flux", down)[0]
        self.assertEqual((r["state"], r["source"], r["verified"]), ("current", "cache", False))
        (self.d / "state" / "models_upstream.json").unlink()
        r = cf_models.check(self.cfg, "flux", down)[0]
        self.assertEqual((r["state"], r["upstream"]), ("unverified", None))   # nothing cached: allowed, marked

    def gate(self, fake, *stages):
        orig, orig_cfg = cf_models.fetch_upstream, cf_common.load_config
        cf_common.load_config = lambda: self.cfg
        cf_models.fetch_upstream = fake
        try:
            cf_common.plugin_gate("models", *stages)
        finally:
            cf_models.fetch_upstream, cf_common.load_config = orig, orig_cfg

    def test_gate_refuses_a_stale_tool_and_allow_stale_lets_it_through_and_is_written_to_the_receipt(self):
        stale = self.fake({"o/a": "f" * 40, "o/b": "v1"})
        with self.assertRaises(SystemExit) as e:
            self.gate(stale, "flux")
        self.assertEqual(e.exception.code, 2)
        (self.d / "state" / "models_upstream.json").unlink()
        cf_common.ALLOW_STALE = "Dom: pinned for the vampire run"
        self.gate(stale, "flux")
        rp = cf_common.write_receipt({"local": {}}, "asset-1", "flux", tool="t", model={"id": "m"})
        rec = json.loads(Path(rp).read_text())
        self.assertEqual(rec["models_check"]["allow_stale"], "Dom: pinned for the vampire run")
        self.assertEqual(rec["models_check"]["rows"][0]["state"], "behind")
        self.assertEqual(rec["models_check"]["rows"][0]["installed"], self.second)

    def test_allow_stale_is_taken_out_of_argv(self):
        old = sys.argv[:]
        try:
            sys.argv = ["x", "kit.json", "--allow-stale", "because", "row"]
            self.assertEqual(cf_common.pop_allow_stale(), "because")
            self.assertEqual(sys.argv, ["x", "kit.json", "row"])
            sys.argv = ["x", "--allow-stale=why", "kit"]
            self.assertEqual(cf_common.pop_allow_stale(), "why")
            self.assertEqual(sys.argv, ["x", "kit"])
        finally:
            sys.argv = old

    def test_the_real_registry_covers_the_first_rows_and_holds_no_versions(self):
        reg = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))["models"]["registry"]
        names = " ".join(r["name"] for r in reg)
        for n in ("TRELLIS.2", "Pixal3D", "Flux klein", "BiRefNet", "Real-ESRGAN"):
            self.assertIn(n, names)
        stages = {s for r in reg for s in r["stages"]}
        self.assertTrue({"flux", "trellis", "pixal3d", "cutout", "upscale"} <= stages)
        self.assertTrue(all(len(str(r["installed"].get("version") or "")) == 0 for r in reg))   # no model numbers in the repo


class Quality(unittest.TestCase):
    """Quality first (plugin 0.1.27): the default presets are the hero settings; a speed preset needs --speed-reason, which lands in the receipt."""
    def setUp(self):
        self.cfg = json.loads((REPO / "config" / "config.json").read_text(encoding="utf-8-sig"))
        self.old = (cf_common.load_config, cf_common.SPEED_REASON, cf_common.QUALITY, cf_common.REPO)
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        cf_common.load_config = lambda: self.cfg
        cf_common.REPO = self.t.name
        self.addCleanup(lambda: (setattr(cf_common, "load_config", self.old[0]), setattr(cf_common, "SPEED_REASON", self.old[1]),
                                 setattr(cf_common, "QUALITY", self.old[2]), setattr(cf_common, "REPO", self.old[3])))

    def test_defaults_are_the_hero_settings_and_not_speed_presets(self):
        t = self.cfg["trellis"]["presets"][self.cfg["character"]["default_preset"]]
        self.assertEqual((t["texture_size"], t["bake_faces"]), (8192, 2000000))
        self.assertNotIn(self.cfg["character"]["default_preset"], self.cfg["quality"]["speed_presets"]["trellis"])
        self.assertNotIn(self.cfg["pixal3d"]["default_preset"], self.cfg["quality"]["speed_presets"]["pixal3d"])
        self.assertEqual(self.cfg["gates"]["preset_tier"].get(self.cfg["character"]["default_preset"]), "hero")   # the floor applies to the default

    def test_a_speed_preset_is_refused_without_a_reason_and_recorded_with_one(self):
        cf_common.SPEED_REASON = None
        with self.assertRaises(SystemExit) as e:
            cf_common.plugin_gate("quality", "trellis", "master_reduced")
        self.assertEqual(e.exception.code, 2)
        cf_common.plugin_gate("quality", "trellis", self.cfg["character"]["default_preset"])   # the default needs nothing
        self.assertIsNone(cf_common.QUALITY)
        cf_common.SPEED_REASON = "Dom: bind test only"
        cf_common.plugin_gate("quality", "trellis", "master_reduced")
        rec = json.loads(Path(cf_common.write_receipt({"local": {}}, "a", "trellis", tool="t", model={})).read_text())
        self.assertEqual(rec["quality"], {"speed_preset": "master_reduced", "tool": "trellis", "reason": "Dom: bind test only"})

    def test_speed_reason_is_taken_out_of_argv(self):
        old = sys.argv[:]
        try:
            sys.argv = ["x", "a", "--speed-reason", "why not", "b"]
            self.assertEqual(cf_common.pop_speed_reason(), "why not")
            self.assertEqual(sys.argv, ["x", "a", "b"])
        finally:
            sys.argv = old


if __name__ == "__main__":
    unittest.main()
