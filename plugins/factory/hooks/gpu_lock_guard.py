#!/usr/bin/env python3
"""PreToolUse(Bash) hook: refuse to START a factory GPU job while another one holds the GPU lock with a live pid.

Rule from the owner: one GPU job at a time (two at once froze the laptop twice on 2026-10-31).
tools/gpu_guard.ps1 writes Documents/.gpu_lock as {"pid":..,"job":..,"started_utc":..} while a guarded job runs and removes it when done.
This hook denies a Bash command that starts generate.py, flux_kit.py, run_3d.py, run_character.py or bench/run_kit.ps1 when that lock names a live process.
A stale lock (dead pid), a missing lock, an unreadable lock or any error means ALLOW: a failing check never blocks work.
Commands that set CF_DRY (flux_kit dry run) are allowed. Override the lock path with FACTORY_GPU_LOCK.
Also guards the factory MCP tools flux_render, trellis_run and upscale (GPU tools).
Tested offline with synthetic payloads (plugins/factory/tests/test_plugin.py); not yet exercised inside a live Claude Code session.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# A python/pwsh invocation whose script is one of the GPU entry points (so `cat flux_kit.py` or a git message that mentions it is not matched).
START = re.compile(
    r"(?:\bpython[\w.]*(?:\.exe)?[\"']?\s+(?:-\S+\s+)*[\"']?[^\s;&|]*(?:generate|flux_kit|run_3d|run_character|run_skin_tokens|run_motion|ardy_generate|kimodo_generate|run_pixal3d|run_ab|flux_multi|compile_probe)\.py\b"
    r"|\b(?:powershell|pwsh)(?:\.exe)?\s+[^;&|]*(?:run_kit|loaded_diag|vae_tiling_probe|cache_multi|tunableop_probe|compile_probe|ref_cache_ab)\.ps1\b"
    r"|(?:^|[;&|]\s*)\.?[\/]?[^\s;&|]*run_kit\.ps1\b)",
    re.I,
)
LOCK = Path(os.environ.get("FACTORY_GPU_LOCK") or Path.home() / "Documents" / ".gpu_lock")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def orphan_reason() -> str:
    """0.1.5: no live lock, but a stage script (generate.py, flux_kit.py, run_3d.py, run_stills.py) is still running: an orphan from a stopped task still holds the GPU."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        import cf_proc
        procs = cf_proc.stage_procs()
    except Exception:
        return ""
    if not procs:
        return ""
    pid, cmd = procs[0]
    more = f" (+{len(procs) - 1} more)" if len(procs) > 1 else ""
    return (f"A factory stage process is still running with no GPU lock: pid {pid}{more}: {cmd[:160]}. It is probably an orphan from a stopped task and still holds the GPU. "
            f"Kill its whole tree with: taskkill /PID {pid} /T /F (then retry), or wait if it is a job you started.")


def decide(command: str, lock_text: str | None) -> str:
    """'' to allow, else the refusal reason."""
    if not START.search(command or "") or "CF_DRY" in (command or ""):
        return ""
    if re.search(r"run_ab\.py", command) and ("--collect" in command or "--decide" in command):
        return ""   # 0.1.14: the A/B collect and decide steps are CPU only (bind, QC, Blender, scorecard)
    if not lock_text:
        return orphan_reason()
    try:
        lock = json.loads(lock_text.lstrip("\ufeff"))
        pid = int(lock.get("pid", 0))
    except (ValueError, TypeError, AttributeError):
        return orphan_reason()
    if not pid_alive(pid):
        return orphan_reason()
    return (f"A factory GPU job is already running (job '{lock.get('job', 'unknown')}', pid {pid}, started {lock.get('started_utc', '?')}). "
            "Rule: one GPU job at a time. Wait for it to finish (the lock disappears), then retry.")


MCP_GPU = tuple("mcp__plugin_factory_factory__" + t for t in ("flux_render", "trellis_run", "upscale", "skin_tokens_bind", "motion_generate", "pixal3d_run", "ab_shape"))


def main() -> None:
    raw = sys.stdin.read() or "{}"
    low = raw.lower()
    if not any(k in low for k in ("generate.py", "flux_kit", "run_3d", "run_character", "run_skin_tokens", "run_kit", "run_motion", "ardy_generate", "kimodo_generate", "run_pixal3d", "run_ab", "mcp__plugin_factory")):
        return  # fast exit
    try:
        payload = json.loads(raw)
        lock_text = LOCK.read_text(encoding="utf-8") if LOCK.exists() else None
        if payload.get("tool_name") in MCP_GPU:
            if payload.get("tool_name", "").endswith("motion_generate") and (payload.get("tool_input") or {}).get("device") == "cpu":
                return   # motion_generate on the CPU does not touch the GPU (it still passes the RAM gate)
            if payload.get("tool_name", "").endswith("ab_shape") and (payload.get("tool_input") or {}).get("collect"):
                return   # ab_shape collect=true runs no GPU half (it still passes the RAM gate)
            reason = decide("python generate.py", lock_text)   # an MCP GPU tool is a GPU start: same rule as the entry-point scripts
        elif payload.get("tool_name") == "Bash":
            reason = decide((payload.get("tool_input") or {}).get("command", ""), lock_text)
        else:
            return
    except Exception:
        return
    if reason:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
