"""Watcher and launcher rules as a CLI (plugin 0.1.29; library: plugins/factory/lib/cf_watch.py).

  job_watch.py start --name N --pid PID --deadline-file F [--deadline ISO] --run-dir DIR --who WHO [--reason TEXT] [--poll 10]
        watches PID in the foreground (background it yourself); the deadline is read from F every poll, so change it by editing F.
        The watcher registers itself under state/watchers/N.json; a kill is logged to DIR/kill.log.
  job_watch.py stop --name N         kills the watcher AND its descendants, then the record is dropped
  job_watch.py sweep                  lists watchers still alive (must be empty after a stop)
  job_watch.py launch SCRIPT [args]   runs a write-once snapshot of SCRIPT (state/launchers/<stem>-<sha>.sh), never the editable original;
                                      refuses (exit 9) when another memcheck/generate/pixal3d GPU job process exists
"""
import argparse
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FACTORY_HOME", REPO)
sys.path.insert(0, os.path.join(REPO, "plugins", "factory", "lib"))
import cf_proc  # noqa: E402
import cf_watch  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--name", required=True)
    s.add_argument("--pid", type=int, required=True)
    s.add_argument("--deadline-file", required=True)
    s.add_argument("--deadline", default=None, help="write this ISO time into the deadline file first")
    s.add_argument("--run-dir", required=True)
    s.add_argument("--who", required=True)
    s.add_argument("--reason", default="deadline reached")
    s.add_argument("--poll", type=float, default=10.0)
    t = sub.add_parser("stop")
    t.add_argument("--name", required=True)
    sub.add_parser("sweep")
    l = sub.add_parser("launch")
    l.add_argument("script")
    l.add_argument("args", nargs="*")
    a = ap.parse_args()

    if a.cmd == "start":
        if a.deadline:
            with open(a.deadline_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(a.deadline + "\n")
        cf_watch.register_watcher(a.name, os.getpid(), a.pid, a.deadline_file)
        res = cf_watch.watch(a.pid, a.deadline_file, a.run_dir, a.who, a.reason, poll_s=a.poll)
        print(json.dumps({"watch": a.name, "result": res}))
        cf_watch.sweep()
    elif a.cmd == "stop":
        ok = cf_watch.stop_watcher(a.name)
        print(json.dumps({"stopped": a.name, "gone": ok, "sweep": cf_watch.sweep()}))
        sys.exit(0 if ok else 1)
    elif a.cmd == "sweep":
        print(json.dumps(cf_watch.sweep()))
    elif a.cmd == "launch":
        others = [p for p in cf_proc.stage_procs() if any(k in p[1] for k in ("memcheck_188", "generate.py", "pixal3d_entry", "run_pixal3d", "run_3d"))]
        if others:
            print(f"REFUSED: {len(others)} GPU job process(es) running (pid {others[0][0]}); one GPU job at a time")
            sys.exit(9)
        snap = cf_watch.snapshot_script(a.script)
        print("running snapshot", snap)
        sys.exit(subprocess.call(["bash", snap, *a.args]))


if __name__ == "__main__":
    main()
