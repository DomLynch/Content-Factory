#!/usr/bin/env python3
"""PreToolUse hook: the token, read-back, parts, lighting and pipeline gates of plugin 0.1.4 (checks live in lib/cf_gates.py).

Covers `run_3d.py` (token gate), the factory MCP tools trellis_run (token gate) and stills (read-back: the GLB must be the recorded output of an ok receipt),
the Flux quality policy (cf_flux.py) for flux_kit.py, flux_kit_fast.py and the flux_render tool,
and a real (not --dry-run) `publish.py` (parts, lighting, read-back and pipeline gates against publish/<asset_id>.json).
A Bash/PowerShell command with FACTORY_RAW=<reason> skips the gates and is logged (the MCP tools have no such door).
A refusal names the number that failed. An allowed stills or publish call gets the read-back line as context. Any error allows the call (fail open).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cf_cmd  # noqa: E402
import cf_flux  # noqa: E402
import cf_gates  # noqa: E402
import cf_guard  # noqa: E402

MCP_PREFIX = "mcp__plugin_factory_factory__"


def _flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def decide(payload: dict) -> tuple[str, str]:
    """(refusal, context): refusal '' to allow; context is the read-back text to show on allow."""
    name = payload.get("tool_name") or ""
    inp = payload.get("tool_input") or {}
    cwd = payload.get("cwd")
    info = []
    if name == "Bash" or name == "PowerShell":
        cmd = inp.get("command", "")
        if "FACTORY_RAW" in cmd and cf_guard.raw_override(cmd, "gates"):
            return "", ""
        for script, args in cf_cmd.script_calls(cmd, ("run_3d.py", "publish.py", "flux_kit.py", "flux_kit_fast.py")):
            if script.startswith("flux_kit"):
                pos = [a for a in args if not a.startswith("-") and "=" not in a]
                found = cf_flux.kit_row(cf_cmd.resolve(pos[0], cwd), pos[1]) if len(pos) > 1 else None
                if found:
                    bad = cf_flux.check_row(found[0], found[1], cf_flux.inline_env(cmd), cf_cmd.resolve(pos[0], cwd))
                    if bad:
                        return " | ".join(bad), ""
            elif script == "run_3d.py":
                pos = [a for a in args if not a.startswith("-")]
                preset = _flag(args, "--preset") or cf_guard.config().get("character", {}).get("default_preset", "")
                if pos:
                    why = cf_gates.token_reason(pos[0], preset, "--from-latents" in args)
                    if why:
                        return why, ""
            elif script == "publish.py" and "--dry-run" not in args:
                pos = [a for a in args if not a.startswith("-")]
                repo = _flag(args, "--repo")
                spec_path = Path(cf_cmd.resolve(repo, cwd) if repo else cf_guard.HOME) / "publish" / f"{pos[0]}.json" if pos else None
                spec = cf_gates._read(spec_path) if spec_path else None
                if spec:
                    bad, lines = cf_gates.publish_reasons(spec)
                    if bad:
                        return " | ".join(bad), ""
                    info += lines
    elif name == MCP_PREFIX + "flux_render" and inp.get("kit") and inp.get("row_id"):
        kit = str(inp["kit"])
        found = cf_flux.kit_row(kit if Path(kit).is_absolute() else str(cf_guard.HOME / kit), str(inp["row_id"]))
        bad = cf_flux.check_row(found[0], found[1], {}) if found else []
        if bad:
            return " | ".join(bad), ""
    elif name == MCP_PREFIX + "trellis_run" and inp.get("asset_id"):
        preset = inp.get("preset") or cf_guard.config().get("character", {}).get("default_preset", "")
        why = cf_gates.token_reason(str(inp["asset_id"]), preset, False)
        if why:
            return why, ""
    elif name == MCP_PREFIX + "stills" and inp.get("glb"):
        why, line = cf_gates.stills_readback(cf_cmd.resolve(str(inp["glb"]), cwd))
        if why:
            return why, ""
        if line:
            info.append(line)
    return "", "\n".join(info)


def main() -> None:
    raw = sys.stdin.read() or "{}"
    low = raw.lower()
    if not any(k in low for k in ("run_3d", "publish.py", "trellis_run", "__stills", "flux_kit", "flux_render")):
        return
    try:
        reason, ctx = decide(json.loads(raw))
    except Exception:
        return
    if reason:
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
    elif ctx:
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx}}
    else:
        return
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
