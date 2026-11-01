r"""Shared checks for the factory hooks and the factory MCP server (standard library only).

One place for: the GPU lock, free RAM, the start-RAM limit from config/config.json, and the image short-side gate.
Nothing here starts a job. Every function returns '' (or None) when it cannot tell, because a failing check must never block work by itself.

Environment (all optional):
  FACTORY_HOME       the Content-Factory checkout (default <home>\Documents\Content-Factory)
  FACTORY_GPU_LOCK   the lock file (default <home>\Documents\.gpu_lock, written by tools/gpu_guard.ps1)
  FACTORY_FAKE_FREE_RAM_GB   TEST ONLY: pretend this much RAM is free
"""
from __future__ import annotations

import json
import os
import struct
from pathlib import Path

HOME = Path(os.environ.get("FACTORY_HOME") or Path.home() / "Documents" / "Content-Factory")
LOCK = Path(os.environ.get("FACTORY_GPU_LOCK") or Path.home() / "Documents" / ".gpu_lock")
DEFAULT_START_MIN_GB = 6.0   # config/config.json -> limits.start_min_free_ram_gb at the time of writing
GATE_PX = 1024               # docs/asset-standard.md section 1 and tools/cutout_cpu.py GATE_PX


def read_json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def config() -> dict:
    try:
        return read_json(HOME / "config" / "config.json")
    except Exception:
        return {}


def start_min_free_gb() -> float:
    try:
        return float(config().get("limits", {}).get("start_min_free_ram_gb", DEFAULT_START_MIN_GB))
    except (TypeError, ValueError):
        return DEFAULT_START_MIN_GB


def free_ram_gb() -> float | None:
    fake = os.environ.get("FACTORY_FAKE_FREE_RAM_GB")
    if fake:
        return float(fake)
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))   # same call as tools/cf_common.py FreeRamWatch
            return round(m.ullAvailPhys / 2**30, 1)
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return round(int(line.split()[1]) / 1048576, 1)
    except Exception:
        pass
    return None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
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
        return True
    except OSError:
        return False


def lock_reason(lock_text: str | None) -> str:
    """'' unless the lock names a live pid (a stale, missing or unreadable lock means the GPU is free)."""
    if not lock_text:
        return ""
    try:
        lock = json.loads(lock_text.lstrip("﻿"))
        pid = int(lock.get("pid", 0))
    except (ValueError, TypeError, AttributeError):
        return ""
    if not pid_alive(pid):
        return ""
    return (f"A factory GPU job is already running (job '{lock.get('job', 'unknown')}', pid {pid}, started {lock.get('started_utc', '?')}). "
            "Rule: one GPU job at a time. Wait for it to finish (the lock disappears), then retry.")


def read_lock_text() -> str | None:
    try:
        return LOCK.read_text(encoding="utf-8") if LOCK.exists() else None
    except Exception:
        return None


def ram_reason(free: float | None, need: float | None = None) -> str:
    need = start_min_free_gb() if need is None else need
    if free is None or free >= need:
        return ""
    return (f"Only {free} GB of RAM is free; a heavy factory start needs {need} GB (config/config.json limits.start_min_free_ram_gb). "
            "Wait for the running job to finish or for RAM to free up, then retry.")


def image_size(path) -> tuple[int, int] | None:
    """(width, height) of a PNG or JPEG from its header, or via PIL if importable; None when it cannot be read."""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b"\xff":
                        b = f.read(1)
                    while b == b"\xff":
                        b = f.read(1)
                    if not b:
                        break
                    marker = b[0]
                    if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                        continue
                    seg = f.read(2)
                    if len(seg) < 2:
                        break
                    (length,) = struct.unpack(">H", seg)
                    if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                        data = f.read(5)
                        h, w = struct.unpack(">HH", data[1:5])
                        return w, h
                    f.seek(length - 2, 1)
    except Exception:
        pass
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def small_image_reason(path, gate: int = GATE_PX) -> str:
    """'' if the short side is at least gate px or unknown; else the refusal."""
    size = image_size(path)
    if size is None:
        return ""
    short = min(size)
    if short >= gate:
        return ""
    return (f"Input image {path} is {size[0]}x{size[1]}: short side {short} px is under the {gate} px gate (docs/asset-standard.md section 1). "
            "Upscale it to 2048 px or more first (tools/upscale/upscale_match.py, colour-matched), look at it, then retry.")
