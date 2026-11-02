#!/usr/bin/env python3
"""PreToolUse hook: free-RAM START gate. Deny a heavy factory start while free RAM is under limits.start_min_free_ram_gb (6 GB in config/config.json).

The tools themselves wait for this headroom (tools/cf_common.wait_for_free_ram, up to 30 minutes) and the mid-run guard kills at 3 GB; this hook says no up front,
so a session does not sit blocked inside a Bash call. Applies to Bash commands that run a factory script and to the factory MCP tools (not factory_status).
Allowed: dry runs (CF_DRY, --dry-run), --help, unreadable RAM, any error. Heavy = generate, flux_kit, run_3d, run_character, run_bind, run_stills, run_gear,
run_textures, run_clean, assemble_character, cutout_cpu, upscale_match, run_kit.ps1, run_cloth (0.1.18, CPU Blender).
Output is the standard PreToolUse deny JSON. Test hooks with FACTORY_FAKE_FREE_RAM_GB (test only).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cf_cmd  # noqa: E402
import cf_guard  # noqa: E402

HEAVY = ("generate.py", "flux_kit.py", "run_3d.py", "run_character.py", "run_bind.py", "run_skin_tokens.py", "run_stills.py", "run_gear.py", "run_textures.py",
         "run_clean.py", "assemble_character.py", "cutout_cpu.py", "upscale_match.py", "run_kit.ps1", "run_motion.py", "ardy_generate.py", "kimodo_generate.py", "run_pixal3d.py", "run_ab.py", "run_materials.py", "run_cloth.py")
MCP_PREFIX = "mcp__plugin_factory_factory__"
MCP_HEAVY = ("flux_render", "trellis_run", "cutout", "bind", "skin_tokens_bind", "stills", "upscale", "motion_generate", "pixal3d_run", "ab_shape", "materials", "cloth")


def is_heavy(payload: dict) -> bool:
    name = payload.get("tool_name") or ""
    inp = payload.get("tool_input") or {}
    if name == "Bash":
        cmd = inp.get("command", "")
        if "CF_DRY" in cmd or "--dry-run" in cmd or "--help" in cmd:
            return False
        return bool(cf_cmd.script_calls(cmd, HEAVY))
    return name.startswith(MCP_PREFIX) and name[len(MCP_PREFIX):] in MCP_HEAVY


def is_materials(payload: dict) -> bool:
    name = payload.get("tool_name") or ""
    if name == "Bash":
        return bool(cf_cmd.script_calls((payload.get("tool_input") or {}).get("command", ""), ("run_materials.py",)))
    return name == MCP_PREFIX + "materials"


def decide(payload: dict, free: float | None) -> str:
    """'' to allow, else the refusal reason."""
    if not is_heavy(payload):
        return ""
    need = None
    if is_materials(payload):   # 0.1.17: a Blender gear-part render shares the box with a Flux sheet: config materials.min_free_ram_gb (10 GB)
        try:
            need = float(cf_guard.config().get("materials", {}).get("min_free_ram_gb", 10))
        except (TypeError, ValueError):
            need = 10.0
    return cf_guard.ram_reason(free, need)


def main() -> None:
    raw = sys.stdin.read() or "{}"
    low = raw.lower()
    if not any(k in low for k in ("generate", "flux_kit", "run_", "assemble_character", "cutout", "upscale", "mcp__plugin_factory")):
        return   # fast exit: nothing factory-related in the payload
    try:
        reason = decide(json.loads(raw), cf_guard.free_ram_gb())
    except Exception:
        return
    if reason:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
