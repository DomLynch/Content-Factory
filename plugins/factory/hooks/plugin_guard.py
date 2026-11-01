#!/usr/bin/env python3
"""PreToolUse hook: factory stages run through the plugin (mandatory-plugin rule, 0.1.1).

Deny a Bash or PowerShell command that runs a factory stage script directly (generate, flux_kit, run_3d, run_character, run_bind, run_stills, run_gear,
run_textures, run_clean, assemble_character, cutout_cpu, upscale_match, run_kit.ps1). The plugin's MCP tools start the same scripts themselves and give each
child a short-lived token (cf_guard.issue_nonce), so they never reach this hook.
Allowed: dry runs (CF_DRY, --dry-run, --help), a command that carries a live token (FACTORY_PLUGIN_TOKEN=<hex>), and a deliberate override written
FACTORY_RAW=<reason> in the command, which is appended to the override log (config plugin_guard.override_log). Reading, grepping or committing a script
never matches (cf_cmd.script_calls only matches `python <script>` / `-File <script>.ps1`). Any error allows the command (fail open).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cf_cmd  # noqa: E402
import cf_guard  # noqa: E402

STAGES = ("generate.py", "flux_kit.py", "run_3d.py", "run_character.py", "run_bind.py", "run_skin_tokens.py", "run_stills.py", "run_gear.py", "run_textures.py",
          "run_clean.py", "assemble_character.py", "cutout_cpu.py", "upscale_match.py", "run_kit.ps1", "run_motion.py", "ardy_generate.py", "kimodo_generate.py", "run_pixal3d.py", "run_ab.py")
SHELLS = ("Bash", "PowerShell")
TOOLS = {"generate.py": "flux_render/trellis_run", "flux_kit.py": "flux_render", "run_3d.py": "trellis_run", "run_bind.py": "bind", "run_skin_tokens.py": "skin_tokens_bind", "run_stills.py": "stills",
         "cutout_cpu.py": "cutout", "upscale_match.py": "upscale", "run_motion.py": "motion_generate", "ardy_generate.py": "motion_generate", "kimodo_generate.py": "motion_generate", "run_pixal3d.py": "pixal3d_run", "run_ab.py": "ab_shape"}


def decide(payload: dict) -> str:
    """'' to allow, else the refusal reason."""
    if payload.get("tool_name") not in SHELLS:
        return ""
    cmd = (payload.get("tool_input") or {}).get("command", "")
    calls = cf_cmd.script_calls(cmd, STAGES)
    if not calls or "CF_DRY" in cmd or "--dry-run" in cmd or "--help" in cmd:
        return ""
    if calls[0][0] == "run_ab.py" and "--decide" in cmd:
        return ""   # 0.1.14: --decide only applies the decision rule to a scorecard (no engine, no GPU)
    m = re.search(r"FACTORY_PLUGIN_TOKEN[=:]\s*['\"]?([0-9a-f]{16,64})", cmd)
    if m and cf_guard.nonce_valid(m.group(1)):
        return ""
    m = re.search(r"FACTORY_RAW[=:]\s*['\"]?([^'\"\s;&|]+)", cmd)
    if m:
        try:
            log = cf_guard.guard_path("override_log")
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "reason": m.group(1), "scripts": [c[0] for c in calls]}) + "\n")
        except Exception:
            pass
        return ""
    name = calls[0][0]
    use = TOOLS.get(name, "the factory plugin tools")
    return (f"Factory stage scripts run through the Content-Factory plugin, not by hand ({name}). Use the factory MCP tool ({use}; also factory_status first) so the "
            "GPU lock, RAM gate, 1024 px input gate and receipts apply. A logged exception: put FACTORY_RAW=<reason> in the command.")


def main() -> None:
    raw = sys.stdin.read() or "{}"
    if not any(k in raw for k in (".py", ".ps1")):
        return   # fast exit: nothing that could be a stage script
    try:
        reason = decide(json.loads(raw))
    except Exception:
        return
    if reason:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()