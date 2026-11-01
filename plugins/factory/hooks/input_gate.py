#!/usr/bin/env python3
"""PreToolUse hook: input gate (0.1.4: 1024 px short side, subject >= 60% of frame height, portrait or square, head parts >= 512 px; lib/cf_gates.input_reason).

First gate, kept as the original docstring says: Deny a TRELLIS or cut-out call whose input image has a short side under 1024 px.

Rule: docs/asset-standard.md section 1 (each view >= 1024 px on the short side before any run); tools/cutout_cpu.py enforces the same GATE_PX itself.
The pipeline also wants inputs upscaled to 2048 px or more (LESSONS.md, 2026-11-01), so the message says to upscale, not to retry.
Covers Bash commands that run run_3d.py, generate.py or cutout_cpu.py (first image argument; skipped for run_3d --from-latents) and the factory MCP tools
trellis_run and cutout (argument `image`). Allowed: a file that cannot be found or read (the tool will fail on its own), any error.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cf_cmd  # noqa: E402
import cf_gates  # noqa: E402
import cf_guard  # noqa: E402

SCRIPTS = ("run_3d.py", "run_pixal3d.py", "generate.py", "cutout_cpu.py")
MCP_PREFIX = "mcp__plugin_factory_factory__"


def images(payload: dict) -> list[str]:
    name = payload.get("tool_name") or ""
    inp = payload.get("tool_input") or {}
    cwd = payload.get("cwd")
    out = []
    if name == "Bash":
        for script, args in cf_cmd.script_calls(inp.get("command", ""), SCRIPTS):
            if script == "run_3d.py" and "--from-latents" in args:
                continue
            img = cf_cmd.first_image(args)
            if img:
                out.append(cf_cmd.resolve(img, cwd))
    elif name.startswith(MCP_PREFIX) and name[len(MCP_PREFIX):] in ("trellis_run", "pixal3d_run", "cutout") and inp.get("image"):
        out.append(cf_cmd.resolve(str(inp["image"]), cwd))
    return out


def decide(payload: dict) -> str:
    """'' to allow, else the refusal reason."""
    if payload.get("tool_name") == "Bash" and "FACTORY_RAW" in (payload.get("tool_input") or {}).get("command", "") and cf_guard.raw_override(payload["tool_input"]["command"], "input"):
        return ""
    for img in images(payload):
        reason = cf_guard.small_image_reason(img) or cf_gates.input_reason(img)
        if reason:
            return reason
    return ""


def main() -> None:
    raw = sys.stdin.read() or "{}"
    low = raw.lower()
    if not any(k in low for k in ("run_3d", "run_pixal3d", "generate.py", "cutout", "trellis_run", "pixal3d_run")):
        return
    try:
        reason = decide(json.loads(raw))
    except Exception:
        return
    if reason:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
