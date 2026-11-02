r"""Process-tree helpers for the factory stages (standard library only; plugin 0.1.5).

Why: on 2026-11-01 15:3x the Factory stopped a plugin task but the child generate.py (pid 3628) lived on holding 28 GB GPU + 11 GB shared until it was killed
by hand at 15:52, and every Flux start meanwhile died on the RAM guard. Stopping a stage must stop its whole tree, and a dead server must not leave one behind.

  bind_to_job(pid)      Windows: put the child in a Job Object with KILL_ON_JOB_CLOSE, so the kernel kills the whole tree when the server dies for any reason
                        (TaskStop and crashes included, where no Python code runs). Returns a handle for close_job(); None elsewhere or on any error.
  close_job(handle)     closes the job: every process still inside it is killed.
  kill_tree(pid)        kill a process and everything it started (Windows taskkill /T /F, else the process group). Returns True once pid is gone.
  stage_procs()         [(pid, command line)] of live processes running a stage script (generate.py, flux_kit.py, run_3d.py, run_stills.py, run_ab.py ...), for the lock guard.

Nothing here raises: every function returns an empty or false value when it cannot tell.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time

STAGE_SCRIPTS = ("generate.py", "flux_kit.py", "run_3d.py", "run_stills.py", "run_skin_tokens.py", "run_motion.py", "ardy_generate.py", "kimodo_generate.py", "run_pixal3d.py", "pixal3d_entry.py", "run_ab.py")
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
            ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '(generate|flux_kit|run_3d|run_stills|run_skin_tokens|run_motion|ardy_generate|kimodo_generate|run_pixal3d|pixal3d_entry|run_ab)\\.py' } "
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


def loading_procs(load_stage_s: float) -> list[tuple[int, str, float]]:
    """(pid, command line, age in seconds) of Trellis / Pixal3D generation processes younger than load_stage_s: they are still loading weights. [] when it cannot tell (plugin 0.1.27)."""
    me = {os.getpid(), os.getppid()}
    if os.name != "nt":
        return []
    try:
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '(generate|run_pixal3d|pixal3d_entry)\\.py' -and $_.CommandLine -notmatch 'Get-CimInstance' } "
              "| ForEach-Object { [pscustomobject]@{ProcessId=$_.ProcessId; CommandLine=$_.CommandLine; Age=((Get-Date)-$_.CreationDate).TotalSeconds} } | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                             timeout=float(os.environ.get("FACTORY_PS_TIMEOUT", "8")), creationflags=NO_WINDOW).stdout.strip()
        data = json.loads(out) if out else []
        rows = [(int(d["ProcessId"]), str(d.get("CommandLine") or ""), float(d["Age"])) for d in (data if isinstance(data, list) else [data])]
        return [r for r in rows if r[0] not in me and r[2] < load_stage_s]
    except Exception:
        return []


def power_state() -> dict:
    """Power source and active scheme (plugin 0.1.28). on_battery is True for Win32_Battery BatteryStatus 1 (discharging), 4 (low) or 5 (critical); AC, charging and fully charged are False;
    no battery object (a desktop) is False; None when it cannot tell. FACTORY_FAKE_POWER="battery|Silent" or "ac|Balanced" is for tests only."""
    fake = os.environ.get("FACTORY_FAKE_POWER")
    if fake:
        src, _, scheme = fake.partition("|")
        return {"battery_status": 1 if src == "battery" else 2, "on_battery": src == "battery", "scheme": scheme or None}
    if os.name != "nt":
        return {"battery_status": None, "on_battery": None, "scheme": None}
    try:
        ps = ("$b = Get-CimInstance Win32_Battery | Select-Object -First 1; $s = (powercfg /getactivescheme) -join ' '; "
              "[pscustomobject]@{BatteryStatus=$(if ($b) { [int]$b.BatteryStatus } else { $null }); Scheme=$s} | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                             timeout=float(os.environ.get("FACTORY_PS_TIMEOUT", "15")), creationflags=NO_WINDOW).stdout.strip()
        d = json.loads(out)
        m = re.search(r"\(([^)]*)\)\s*$", str(d.get("Scheme") or "").strip())
        st = d.get("BatteryStatus")
        return {"battery_status": st, "on_battery": (st in (1, 4, 5)) if st is not None else False, "scheme": m.group(1) if m else None}
    except Exception:
        return {"battery_status": None, "on_battery": None, "scheme": None}


def power_reason(state: dict, limits: dict) -> str:
    """Power gate (plugin 0.1.28): a GPU run does not start on battery or on a refused power scheme (limits.power_refuse_schemes, default Silent). '' = may start.
    Cause: at 12:15 the laptop went to battery and Silent, the step time rose 60% and the ROCm run aborted one step from the end of its stage (22 minutes lost)."""
    bad = []
    if state.get("on_battery"):
        bad.append(f"the laptop is on battery (BatteryStatus {state.get('battery_status')})")
    refuse = [x.lower() for x in limits.get("power_refuse_schemes", ["silent"])]
    if state.get("scheme") and state["scheme"].lower() in refuse:
        bad.append(f"the active power scheme is {state['scheme']}")
    return ("POWER GATE: " + " and ".join(bad) + ". A GPU run on battery or in a throttled scheme slows 60% and can abort mid-stage. Plug in and switch to Performance or Turbo, "
            "or pass --allow-battery \"<reason>\" (written to the receipt).") if bad else ""


def ram_share_reason(limits: dict, free_gb: float | None, loading: list | None = None) -> str:
    """RAM-sharing rule (plugin 0.1.27): a cut-out, upscale or Blender job does not start while a Trellis / Pixal3D process is loading, or with available RAM under
    limits.share_min_free_ram_gb. The Factory killed the GPU lane's run three times in one day this way. '' = may start."""
    need = float(limits.get("share_min_free_ram_gb", 8))
    if loading:
        pid, cmd, age = loading[0]
        return (f"RAM-SHARE GATE: a Trellis/Pixal3D process (pid {pid}, started {age:.0f} s ago) is still loading its weights; a cut-out, upscale or Blender job started now takes its RAM. "
                f"Wait until it has finished loading (limits.load_stage_s = {limits.get('load_stage_s', 300)} s).")
    if free_gb is not None and free_gb < need:
        return f"RAM-SHARE GATE: {free_gb:.1f} GB RAM available, under limits.share_min_free_ram_gb = {need:g} GB. A cut-out, upscale or Blender job waits for headroom."
    return ""


def descendants(pid: int) -> set[int]:
    """0.1.14: pids of every process started (directly or not) by pid, for the A/B harness's own-children exclusion. set() when it cannot tell."""
    try:
        pairs: list[tuple[int, int]] = []
        if os.name == "nt":
            ps = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                                 timeout=float(os.environ.get("FACTORY_PS_TIMEOUT", "8")), creationflags=NO_WINDOW).stdout.strip()
            data = json.loads(out) if out else []
            pairs = [(int(d["ProcessId"]), int(d["ParentProcessId"])) for d in (data if isinstance(data, list) else [data])]
        else:
            for p in os.listdir("/proc"):
                if p.isdigit():
                    try:
                        stat = open(f"/proc/{p}/stat").read()
                        pairs.append((int(p), int(stat.rsplit(")", 1)[1].split()[1])))
                    except (OSError, ValueError, IndexError):
                        pass
        kids: dict[int, list[int]] = {}
        for c, par in pairs:
            kids.setdefault(par, []).append(c)
        out_set: set[int] = set()
        todo = [pid]
        while todo:
            for c in kids.get(todo.pop(), []):
                if c not in out_set and c != pid:
                    out_set.add(c)
                    todo.append(c)
        return out_set
    except Exception:
        return set()

