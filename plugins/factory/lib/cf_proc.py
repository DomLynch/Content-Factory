r"""Process-tree helpers for the factory stages (standard library only; plugin 0.1.5).

Why: on 2026-11-01 15:3x the Factory stopped a plugin task but the child generate.py (pid 3628) lived on holding 28 GB GPU + 11 GB shared until it was killed
by hand at 15:52, and every Flux start meanwhile died on the RAM guard. Stopping a stage must stop its whole tree, and a dead server must not leave one behind.

  bind_to_job(pid)      Windows: put the child in a Job Object with KILL_ON_JOB_CLOSE, so the kernel kills the whole tree when the server dies for any reason
                        (TaskStop and crashes included, where no Python code runs). Returns a handle for close_job(); None elsewhere or on any error.
  close_job(handle)     closes the job: every process still inside it is killed.
  kill_tree(pid)        kill a process and everything it started (Windows taskkill /T /F, else the process group). Returns True once pid is gone.
  stage_procs()         [(pid, command line)] of live processes running a stage script (generate.py, flux_kit.py, run_3d.py, run_stills.py), for the lock guard.

Nothing here raises: every function returns an empty or false value when it cannot tell.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time

STAGE_SCRIPTS = ("generate.py", "flux_kit.py", "run_3d.py", "run_stills.py", "run_skin_tokens.py", "run_motion.py", "ardy_generate.py", "run_pixal3d.py", "pixal3d_entry.py")
_STAGE_RE = re.compile(r"(?:%s)\b" % "|".join(re.escape(s) for s in STAGE_SCRIPTS), re.I)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
        k.CloseHandle(ctypes.c_void_p(h))
        return bool(ok) and code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---- kill on close (Windows Job Object) -------------------------------------------------------------------------------------------------------------
def bind_to_job(pid: int):
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IOC(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ro", "wo", "oo", "rb", "wb", "ob")]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IOC), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        job = k.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXT()
        info.Basic.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k.CloseHandle(job)
            return None
        proc = k.OpenProcess(0x0100 | 0x0001, False, pid)   # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not proc:
            k.CloseHandle(job)
            return None
        ok = k.AssignProcessToJobObject(job, proc)
        k.CloseHandle(proc)
        if not ok:
            k.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def close_job(handle) -> None:
    if handle and os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass


# ---- kill a tree -------------------------------------------------------------------------------------------------------------------------------------
def kill_tree(pid: int, wait_s: float = 15.0) -> bool:
    """Kill pid and all its descendants; True when pid is gone. Windows: taskkill /T /F. POSIX: the process group (the stage is started in its own session)."""
    if pid <= 0:
        return True
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20, creationflags=NO_WINDOW)
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    end = time.time() + wait_s
    while time.time() < end:
        if not pid_alive(pid):
            return True
        time.sleep(0.2)
    return not pid_alive(pid)


# ---- find live stage processes -----------------------------------------------------------------------------------------------------------------------
def stage_procs() -> list[tuple[int, str]]:
    """Live processes whose command line runs a factory stage script. Excludes this process, its parent and the lookup itself. [] when it cannot tell."""
    me = {os.getpid(), os.getppid()}
    rows: list[tuple[int, str]] = []
    try:
        if os.name == "nt":
            ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '(generate|flux_kit|run_3d|run_stills|run_skin_tokens|run_motion|ardy_generate|run_pixal3d|pixal3d_entry)\\.py' } "
                  "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True, timeout=float(os.environ.get("FACTORY_PS_TIMEOUT", "8")),
                                 creationflags=NO_WINDOW).stdout.strip()
            data = json.loads(out) if out else []
            for d in data if isinstance(data, list) else [data]:
                rows.append((int(d["ProcessId"]), str(d.get("CommandLine") or "")))
        else:
            for p in os.listdir("/proc"):
                if p.isdigit():
                    try:
                        rows.append((int(p), open(f"/proc/{p}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")))
                    except OSError:
                        pass
    except Exception:
        return []
    only = os.environ.get("FACTORY_TEST_STAGE_TAG", "")   # TEST ONLY: look at nothing but processes whose command line holds this tag
    return [(pid, cmd) for pid, cmd in rows if (not only or only in cmd) and pid not in me and _STAGE_RE.search(cmd) and "Get-CimInstance" not in cmd and "cf_proc" not in cmd]
