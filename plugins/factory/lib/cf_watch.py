r"""Watcher and launcher rules (plugin 0.1.29; standard library only).

Why: on 2026-11-02 a watcher script survived the stop of its task wrapper, killed a healthy 59-minute GPU export at its OLD deadline, started a rerun, and a launcher
script edited while bash was still running it started a third export. Four rules, each a function here:

  a  deadline in a file      `watch()` re-reads a one-line ISO time file every poll, so moving a deadline is an edit of that file, never a stop and restart.
  b  stop kills the tree     `stop_watcher(name)` kills the watcher AND its descendants (cf_proc.kill_tree); `sweep()` lists watchers still alive (pid files under
                             state/watchers/) so the next check proves no watch process is left.
  c  every kill is logged    `log_kill(run_dir, pid, reason, who)` appends to <run_dir>/kill.log; `watch()` calls it for its own kills.
  d  launchers are write-once `snapshot_script(path)` copies a launcher to state/launchers/<stem>-<sha256[:10]><ext> (read-only) and the copy is what runs, so editing the
                             original while it runs changes nothing (bash re-reads a running script at its old byte offset).

CLI: tools/job_watch.py start|stop|sweep|launch. Nothing here raises on an unreadable file; a failing check allows.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import stat
import time
from pathlib import Path

import cf_guard
import cf_proc


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def read_deadline(path) -> datetime.datetime | None:
    """The deadline in the first line of `path` (ISO 8601, 'YYYY-MM-DDTHH:MM[:SS]', naive = local time), None when absent or unreadable."""
    try:
        line = Path(path).read_text(encoding="utf-8-sig").strip().splitlines()[0].strip()
        d = datetime.datetime.fromisoformat(line)
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    except Exception:
        return None


def log_kill(run_dir, pid: int, reason: str, who: str) -> str:
    """Append '<iso> <who> killed pid <pid>: <reason>' to <run_dir>/kill.log (created if needed); returns the path."""
    p = Path(run_dir)
    p.mkdir(parents=True, exist_ok=True)
    f = p / "kill.log"
    with open(f, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{_now().isoformat(timespec='seconds')} {who} killed pid {pid}: {reason}\n")
    return str(f)


def _dir(sub: str) -> Path:
    d = cf_guard.HOME / "state" / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_script(path) -> str:
    """Copy a launcher to an immutable, content-named file and return its path: run THAT, never the editable original."""
    src = Path(path)
    data = src.read_bytes()
    dest = _dir("launchers") / f"{src.stem}-{hashlib.sha256(data).hexdigest()[:10]}{src.suffix}"
    if not dest.exists():
        shutil.copyfile(src, dest)
        os.chmod(dest, stat.S_IREAD)
    return str(dest)


def register_watcher(name: str, pid: int, target: int, deadline_file: str) -> str:
    f = _dir("watchers") / f"{name}.json"
    f.write_text(json.dumps({"name": name, "pid": pid, "target": target, "deadline_file": str(deadline_file),
                             "started": _now().isoformat(timespec="seconds")}), encoding="utf-8")
    return str(f)


def alive_watchers() -> list[dict]:
    """Registered watchers whose own process is still alive."""
    out = []
    for f in sorted(_dir("watchers").glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            if cf_proc.pid_alive(int(rec["pid"])):
                out.append(rec)
        except Exception:
            continue
    return out


def sweep() -> dict:
    """Remove the records of dead watchers; report the live ones. {'alive': [...], 'removed': [...]} - the next sweep after a stop must show alive == []."""
    alive_names = {r["name"] for r in alive_watchers()}
    removed = []
    for f in sorted(_dir("watchers").glob("*.json")):
        if f.stem not in alive_names:
            f.unlink(missing_ok=True)
            removed.append(f.stem)
    return {"alive": sorted(alive_names), "removed": removed}


def stop_watcher(name: str, kill=cf_proc.kill_tree) -> bool:
    """Stop a watcher by name: kill its whole tree (not just the wrapper that started it) and drop its record. True when it is gone."""
    f = _dir("watchers") / f"{name}.json"
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return True
    gone = bool(kill(int(rec["pid"])))
    if gone:
        f.unlink(missing_ok=True)
    return gone


def watch(pid: int, deadline_file, run_dir, who: str, reason: str, poll_s: float = 10.0, now=_now, sleep=time.sleep,
          alive=cf_proc.pid_alive, kill=cf_proc.kill_tree, max_polls: int | None = None) -> str:
    """Watch `pid` until it exits or the deadline in `deadline_file` passes (re-read every poll). Past the deadline: kill the tree and log it.
    Returns 'exited', 'killed', 'kill_failed' or 'no_deadline_yet' (max_polls reached)."""
    n = 0
    while True:
        if not alive(pid):
            return "exited"
        dl = read_deadline(deadline_file)
        if dl is not None and now() >= dl:
            ok = kill(pid)
            log_kill(run_dir, pid, f"{reason} (deadline {dl.isoformat(timespec='seconds')} from {deadline_file})", who)
            return "killed" if ok else "kill_failed"
        n += 1
        if max_polls is not None and n >= max_polls:
            return "no_deadline_yet"
        sleep(poll_s)
