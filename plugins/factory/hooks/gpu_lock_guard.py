#!/usr/bin/env python3
"""PreToolUse(Bash) hook: refuse to START a factory GPU job while another one holds the GPU lock with a live pid.

Rule from the owner: one GPU job at a time (two at once froze the laptop twice on 2026-10-31).
tools/gpu_guard.ps1 writes Documents/.gpu_lock as {"pid":..,"job":..,"started_utc":..} while a guarded job runs and removes it when done.
This hook denies a Bash command that starts generate.py, flux_kit.py, run_3d.py, run_character.py or bench/run_kit.ps1 when that lock names a live process.
A stale lock (dead pid), a missing lock, an unreadable lock or any error means ALLOW: a failing check never blocks work.
Commands that set CF_DRY (flux_kit dry run) are allowed. Override the lock path with FACTORY_GPU_LOCK.
STATUS: draft, logic self-checked with synthetic payloads only; not yet exercised inside a real Claude Code session.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# A python/pwsh invocation whose script is one of the GPU entry points (so `cat flux_kit.py` or a git message that mentions it is not matched).
START = re.compile(
    r"(?:\bpython[\w.]*(?:\.exe)?[\"']?\s+(?:-\S+\s+)*[\"']?[^\s;&|]*(?:generate|flux_kit|run_3d|run_character)\.py\b"
    r"|\b(?:powershell|pwsh)(?:\.exe)?\s+[^;&|]*run_kit\.ps1\b"
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


def decide(command: str, lock_text: str | None) -> str:
    """'' to allow, else the refusal reason."""
    if not START.search(command or "") or "CF_DRY" in (command or ""):
        return ""
    if not lock_text:
        return ""
    try:
        lock = json.loads(lock_text.lstrip("\ufeff"))
        pid = int(lock.get("pid", 0))
    except (ValueError, TypeError, AttributeError):
        return ""
    if not pid_alive(pid):
        return ""
    return (f"A factory GPU job is already running (job '{lock.get('job', 'unknown')}', pid {pid}, started {lock.get('started_utc', '?')}). "
            "Rule: one GPU job at a time. Wait for it to finish (the lock disappears), then retry.")


def main() -> None:
    raw = sys.stdin.read() or "{}"
    low = raw.lower()
    if not any(k in low for k in ("generate.py", "flux_kit", "run_3d", "run_character", "run_kit")):
        return  # fast exit
    try:
        payload = json.loads(raw)
        if payload.get("tool_name") != "Bash":
            return
        command = (payload.get("tool_input") or {}).get("command", "")
        lock_text = LOCK.read_text(encoding="utf-8") if LOCK.exists() else None
        reason = decide(command, lock_text)
    except Exception:
        return
    if reason:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
