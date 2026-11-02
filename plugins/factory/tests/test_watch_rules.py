"""Offline tests for plugin 0.1.29: watcher deadline file, tree kill + sweep, kill log, write-once launchers, the stubs-on-PYTHONPATH gate. No GPU, no real watchers."""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "plugins" / "factory" / "lib"))
sys.path.insert(0, str(REPO / "tools"))
import cf_guard  # noqa: E402
import cf_proc  # noqa: E402
import cf_watch  # noqa: E402

D = datetime.datetime


class Watch(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d = Path(self.t.name)
        self.old = cf_guard.HOME
        cf_guard.HOME = self.d
        self.addCleanup(lambda: setattr(cf_guard, "HOME", self.old))
        self.dl = self.d / "deadline.txt"

    def clock(self, times):
        it = iter(times)
        last = [times[-1]]

        def now():
            try:
                last[0] = next(it)
            except StopIteration:
                pass
            return last[0]
        return now

    def test_deadline_is_read_from_the_file_every_poll_so_moving_it_is_an_edit(self):
        self.dl.write_text("2026-11-02T15:15\n")
        polls = []
        alive = lambda pid: True
        killed = []

        def sleep(s):
            polls.append(s)
            if len(polls) == 2:   # the owner moves the deadline later while the watcher runs: no stop, no restart
                self.dl.write_text("2026-11-02T15:30\n")

        now = self.clock([D(2026, 11, 2, 15, 10), D(2026, 11, 2, 15, 12), D(2026, 11, 2, 15, 16), D(2026, 11, 2, 15, 29), D(2026, 11, 2, 15, 31)])
        res = cf_watch.watch(7, self.dl, self.d / "run", "Hooks Dev", "test", poll_s=1, now=now, sleep=sleep, alive=alive, kill=lambda p: killed.append(p) or True)
        self.assertEqual((res, killed), ("killed", [7]))
        self.assertEqual(len(polls), 4)   # it survived 15:16 because the file said 15:30; it killed at 15:31

    def test_exit_before_the_deadline_kills_nothing_and_a_kill_is_logged(self):
        self.dl.write_text("2026-11-02T15:15")
        res = cf_watch.watch(7, self.dl, self.d / "run", "me", "r", now=lambda: D(2026, 11, 2, 15, 0), sleep=lambda s: None, alive=lambda p: False, kill=lambda p: self.fail("killed"))
        self.assertEqual(res, "exited")
        cf_watch.watch(9, self.dl, self.d / "run", "Hooks Dev", "RAM triage", now=lambda: D(2026, 11, 2, 16, 0), sleep=lambda s: None, alive=lambda p: True, kill=lambda p: True)
        line = (self.d / "run" / "kill.log").read_text()
        self.assertTrue("Hooks Dev killed pid 9: RAM triage" in line and "deadline 2026-11-02T15:15:00" in line, line)

    def test_missing_deadline_file_never_kills(self):
        res = cf_watch.watch(7, self.d / "nope.txt", self.d / "run", "me", "r", sleep=lambda s: None, alive=lambda p: True, kill=lambda p: self.fail("killed"), max_polls=3)
        self.assertEqual(res, "no_deadline_yet")
        self.assertIsNone(cf_watch.read_deadline(self.d / "nope.txt"))
        (self.d / "bad.txt").write_text("not a time")
        self.assertIsNone(cf_watch.read_deadline(self.d / "bad.txt"))

    def test_stop_kills_the_tree_and_the_sweep_proves_nothing_is_left(self):
        cf_watch.register_watcher("w1", os.getpid(), 123, str(self.dl))   # a live pid: ours
        self.assertEqual([w["name"] for w in cf_watch.alive_watchers()], ["w1"])
        seen = []
        ok = cf_watch.stop_watcher("w1", kill=lambda pid: seen.append(pid) or True)
        self.assertTrue(ok and seen == [os.getpid()])   # kill_tree (the whole tree) was asked for, not just the wrapper
        self.assertEqual(cf_watch.sweep(), {"alive": [], "removed": []})
        cf_watch.register_watcher("dead", 2 ** 22 + 12345, 1, str(self.dl))
        self.assertEqual(cf_watch.sweep()["removed"], ["dead"])

    def test_launcher_snapshot_is_content_named_and_read_only_so_editing_the_original_changes_nothing(self):
        orig = self.d / "run.sh"
        orig.write_text("echo one\n")
        a = cf_watch.snapshot_script(orig)
        orig.write_text("echo two\n")   # the editable original changes while a snapshot may be running
        b = cf_watch.snapshot_script(orig)
        self.assertNotEqual(a, b)
        self.assertEqual(Path(a).read_text(), "echo one\n")
        self.assertEqual(cf_watch.snapshot_script(orig), b)
        with self.assertRaises(PermissionError):
            open(a, "w")


class StubPath(unittest.TestCase):
    def test_stubs_dir_with_an_o_voxel_stub_on_pythonpath_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            stubs = Path(t) / "stubs"
            (stubs / "o_voxel").mkdir(parents=True)
            (stubs / "o_voxel" / "__init__.py").write_text("")
            r = cf_proc.stub_path_reason(pythonpath=os.pathsep.join([str(Path(t) / "TRELLIS.2"), str(stubs)]))
            self.assertTrue("STUB PATH GATE" in r and "stubs" in r, r)
            self.assertEqual(cf_proc.stub_path_reason(pythonpath=str(Path(t) / "TRELLIS.2")), "")
            self.assertEqual(cf_proc.stub_path_reason(pythonpath="", sys_path=[]), "")
            plain = Path(t) / "libs"
            (plain / "o_voxel").mkdir(parents=True)
            (plain / "o_voxel" / "__init__.py").write_text("")
            self.assertEqual(cf_proc.stub_path_reason(pythonpath=str(plain)), "")   # only a dir named stubs counts

    def test_entry_scripts_call_the_gate_and_guard_logs_kills(self):
        for rel in ("tools/run_3d.py", "tools/run_pixal3d.py"):
            self.assertIn('plugin_gate("pythonpath")', (REPO / rel).read_text(encoding="utf-8"), rel)
        self.assertIn("-KillLog", (REPO / "tools" / "gpu_guard.ps1").read_text(encoding="utf-8"))
        cfg = json.loads((REPO / "plugins" / "factory" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(cfg["version"], "0.1.29")


if __name__ == "__main__":
    unittest.main()
