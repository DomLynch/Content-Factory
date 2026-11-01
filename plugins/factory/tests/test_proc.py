"""Offline tests for plugin 0.1.5: stopping a stage stops its whole tree, and an orphan stage process blocks a new GPU start.

The 'stage' is a fake long-running python child whose command line ends in generate.py; nothing real runs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(PLUGIN / "mcp"))
import cf_proc  # noqa: E402

PY = sys.executable


def fake_stage(tag: str, grandchild: bool = False) -> subprocess.Popen:
    """A python process that sleeps; its command line carries generate.py and a unique tag. With grandchild=True it also starts one sleeping child of its own."""
    code = f"import subprocess,sys,time\n# {tag}\n"
    if grandchild:
        code += f"subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)  # {tag}'])\n"
    code += "time.sleep(300)"
    return subprocess.Popen([PY, "-c", code, "generate.py"], creationflags=cf_proc.NO_WINDOW)


def procs_with(tag: str) -> list[int]:
    return [pid for pid, cmd in cf_proc.stage_procs() if tag in cmd]   # stage_procs sees every real stage process too; only ours carry the tag


def cmdline_pids(tag: str) -> list[int]:
    """Every live process whose command line holds the tag (grandchildren have no generate.py in theirs)."""
    if os.name != "nt":
        return [int(p) for p in os.listdir("/proc") if p.isdigit() and tag.encode() in open(f"/proc/{p}/cmdline", "rb").read()]
    ps = f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{tag}*' -and $_.CommandLine -notlike '*Get-CimInstance*' }} | Select-Object -ExpandProperty ProcessId"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=15, creationflags=cf_proc.NO_WINDOW).stdout
    return [int(x) for x in out.split() if x.isdigit()]


def wait_until(pred, secs=15.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.3)
    return pred()


class Base(unittest.TestCase):
    def setUp(self):
        self.tag = "cf" + uuid.uuid4().hex[:12]
        self.kids: list[subprocess.Popen] = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for k in self.kids:
            cf_proc.kill_tree(k.pid, wait_s=5)
        for pid in cmdline_pids(self.tag):
            cf_proc.kill_tree(pid, wait_s=5)

    def start(self, **kw):
        p = fake_stage(self.tag, **kw)
        self.kids.append(p)
        self.assertTrue(wait_until(lambda: p.pid in procs_with(self.tag)), "fake stage not visible to stage_procs")
        return p


class KillTree(Base):
    def test_kill_tree_kills_the_grandchild_too(self):
        p = self.start(grandchild=True)
        self.assertTrue(wait_until(lambda: len(cmdline_pids(self.tag)) >= 2), "grandchild did not start")
        self.assertTrue(cf_proc.kill_tree(p.pid))
        self.assertTrue(wait_until(lambda: not cmdline_pids(self.tag)), f"survivors: {cmdline_pids(self.tag)}")

    @unittest.skipUnless(os.name == "nt", "Job Objects are Windows only")
    def test_job_kills_the_tree_when_the_server_dies_hard(self):
        script = (f"import os,subprocess,sys,time\nsys.path.insert(0,{str(PLUGIN / 'lib')!r})\nimport cf_proc\n"
                  f"p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)  # {self.tag}'])\n"
                  "h=cf_proc.bind_to_job(p.pid)\nprint(p.pid, h is not None, flush=True)\nos._exit(0)\n")
        out = subprocess.run([PY, "-c", script], capture_output=True, text=True, timeout=30).stdout.split()
        self.assertEqual(out[1], "True", out)
        self.assertTrue(wait_until(lambda: not cf_proc.pid_alive(int(out[0]))), "child outlived its dead parent")


class RunStage(Base):
    def test_timeout_kills_the_whole_stage_tree(self):
        import server
        tmp = tempfile.mkdtemp()
        server.cfg = lambda: {"local": {"logs_dir": tmp}, "limits": {}, "character": {"guard_wait_s": 0}}
        code = f"import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c','import time;time.sleep(300)  # {self.tag}'])\ntime.sleep(300)  # {self.tag}"
        rc, _log, _t0 = server.run_stage("test", [PY, "-c", code], timeout=3)
        self.assertEqual(rc, "timeout")
        self.assertTrue(wait_until(lambda: not cmdline_pids(self.tag)), f"survivors: {cmdline_pids(self.tag)}")

    def test_cancel_kills_the_whole_stage_tree(self):
        import server
        import threading
        tmp = tempfile.mkdtemp()
        server.cfg = lambda: {"local": {"logs_dir": tmp}, "limits": {}, "character": {"guard_wait_s": 0}}
        code = f"import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c','import time;time.sleep(300)  # {self.tag}'])\ntime.sleep(300)  # {self.tag}"
        real_wait = subprocess.Popen.wait

        def cancelling_wait(self_, timeout=None):
            time.sleep(2)   # let the stage and its child start, then behave like a stop request arriving
            raise KeyboardInterrupt()
        subprocess.Popen.wait = cancelling_wait
        try:
            with self.assertRaises(KeyboardInterrupt):
                server.run_stage("test", [PY, "-c", code], timeout=60)
        finally:
            subprocess.Popen.wait = real_wait
        self.assertTrue(wait_until(lambda: not cmdline_pids(self.tag)), f"survivors: {cmdline_pids(self.tag)}")
        del threading


class OrphanGuard(Base):
    def hook(self, payload, lock):
        env = {**os.environ, "FACTORY_GPU_LOCK": str(lock), "FACTORY_TEST_STAGE_TAG": self.tag}
        r = subprocess.run([PY, str(PLUGIN / "hooks" / "gpu_lock_guard.py")], input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=40)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def test_orphan_blocks_a_new_start_naming_pid_and_kill_then_clears(self):
        lock = Path(tempfile.mkdtemp()) / ".gpu_lock"   # no lock file: nothing holds the lock
        p = self.start()
        pay = {"tool_name": "Bash", "tool_input": {"command": "python flux/flux_kit.py kit row"}}
        out = self.hook(pay, lock)
        why = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(f"pid {p.pid}", why)
        self.assertIn(f"taskkill /PID {p.pid} /T /F", why)
        cf_proc.kill_tree(p.pid)
        self.assertTrue(wait_until(lambda: p.pid not in procs_with(self.tag)))
        self.assertEqual(self.hook(pay, lock), {})

    def test_dry_run_and_non_start_commands_are_not_blocked(self):
        lock = Path(tempfile.mkdtemp()) / ".gpu_lock"
        self.start()
        self.assertEqual(self.hook({"tool_name": "Bash", "tool_input": {"command": "CF_DRY=1 python flux/flux_kit.py kit row"}}, lock), {})
        self.assertEqual(self.hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, lock), {})


if __name__ == "__main__":
    unittest.main()
