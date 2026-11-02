"""Guards for a Blender job on a tight machine (standard library only; plugin 0.1.17). Used by tools/run_materials.py before ANY Blender process starts:
  1. free RAM at least materials.min_free_ram_gb (10 GB: three agents and a Flux sheet share this box),
  2. no Flux cell is loading (a Flux cell needs about 14 GB at model load): the newest kit_*.txt / *queue*.log in the flux log dir was created or showed a START / model-load
     line in the last materials.flux_load_window_s (60 s),
  3. an exclusive lock file (blender_job.lock, created O_EXCL, holds the pid): if it exists and its pid is alive, wait up to materials.lock_wait_s (600 s) and give up.
Every wait is bounded; a refusal names the reason. The lock is always released in a finally (release()). No GPU is touched here or in the Blender job."""
from __future__ import annotations

import ctypes
import glob
import json
import os
import re
import time

BARE_BAR_RE = re.compile(r"^\s*\d+%\|")
LOAD_RE = re.compile(r"(START|Loading (?:checkpoint|pipeline|model|weights)|loading (?:checkpoint|pipeline|model|weights)|from_pretrained|Fetching \d+ files)")


def free_ram_gb() -> float:
    fake = os.environ.get("FACTORY_FAKE_FREE_RAM_GB")   # the plugin's own test hook (same name the hooks use)
    if fake:
        return float(fake)

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 2**30


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259   # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def flux_loading(log_dir: str, window_s: float, now: float | None = None) -> str:
    """'' when no Flux cell is loading; else a sentence naming the log. Looks at the newest kit_*.txt or *queue*.log only."""
    now = time.time() if now is None else now
    files = glob.glob(os.path.join(log_dir, "kit_*.txt")) + glob.glob(os.path.join(log_dir, "*queue*.log")) + glob.glob(os.path.join(log_dir, "*queue*.txt"))
    if not files:
        return ""
    newest = max(files, key=os.path.getmtime)
    born, touched = os.path.getctime(newest), os.path.getmtime(newest)
    if now - born <= window_s:
        return f"a Flux cell started {now - born:.0f} s ago ({os.path.basename(newest)}); its model load needs about 14 GB"
    if now - touched <= window_s:
        try:
            with open(newest, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 4096))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            return ""
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        loads = [i for i, ln in enumerate(lines) if LOAD_RE.search(ln)]
        if loads:
            after = lines[loads[-1] + 1:]
            # a bare tqdm bar (starts with 'NN%|') or a RESULT line after the load line means generation has begun or the cell is done: not loading any more
            if not any(BARE_BAR_RE.match(ln) or ln.startswith("RESULT") for ln in after):
                return f"a Flux model load is in progress ({os.path.basename(newest)}: {lines[loads[-1]][:80]})"
    return ""


class LockBusy(Exception):
    pass


def acquire(lock_path: str, wait_s: float, poll_s: float = 5.0) -> None:
    """Create lock_path exclusively with our pid. If it exists and its pid is alive, wait up to wait_s, then raise LockBusy. A stale lock (dead pid, unreadable) is replaced."""
    deadline = time.time() + wait_s
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = 0
            try:
                with open(lock_path, encoding="utf-8-sig") as f:
                    holder = int(json.load(f).get("pid", 0))
            except (OSError, ValueError, TypeError):
                holder = 0
            if not pid_alive(holder):
                try:
                    os.remove(lock_path)   # stale: the holder is gone
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                raise LockBusy(f"{lock_path} is held by live pid {holder}; waited {wait_s:.0f} s")
            time.sleep(min(poll_s, max(0.1, deadline - time.time())))
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "job": "materials", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f)
        return


def release(lock_path: str) -> None:
    try:
        with open(lock_path, encoding="utf-8-sig") as f:
            mine = int(json.load(f).get("pid", 0)) == os.getpid()
    except (OSError, ValueError, TypeError):
        mine = False
    if mine:
        try:
            os.remove(lock_path)
        except OSError:
            pass


def wait_ready(min_free_gb: float, log_dir: str, window_s: float, wait_s: float, poll_s: float = 5.0) -> str:
    """'' when RAM is enough and no Flux load is in progress, else the last reason after waiting up to wait_s."""
    deadline = time.time() + wait_s
    while True:
        why = ""
        free = free_ram_gb()
        if free < min_free_gb:
            why = f"{free:.1f} GB of RAM is free; a Blender job needs {min_free_gb:.1f} GB"
        else:
            why = flux_loading(log_dir, window_s)
        if not why or time.time() >= deadline:
            return why
        time.sleep(min(poll_s, max(0.1, deadline - time.time())))
