#!/usr/bin/env python3
"""PreToolUse(Bash) hook: no literals in a run. Deny a Bash command that runs generate.py or flux_kit.py with inline numeric values.

Owner rule (LESSONS.md, 2026-11-01): prompts, sizes, seeds, budgets and paths live in a kit row, a preset or config, never on the command line.
Denied: a numeric value after a flag (`--texture-size 2048`, `--attn-auto-gb=28`, `-n 5`), and for flux_kit.py any numeric positional beyond `<kit.json> <row_id>`.
Allowed: `--preset NAME`, `--device rocm`, kit/row names, `--help`, and anything that is not a direct generate.py / flux_kit.py invocation
(tools/run_3d.py reads the numbers from config/config.json trellis.presets, so use `run_3d.py --preset NAME`).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cf_cmd  # noqa: E402

NUM = re.compile(r"^-?\d+(\.\d+)?$")


def literals(script: str, args: list[str]) -> list[str]:
    bad = []
    for i, a in enumerate(args):
        if a.startswith("-") and not NUM.match(a):
            if "=" in a and NUM.match(a.split("=", 1)[1]):
                bad.append(a)
            elif i + 1 < len(args) and NUM.match(args[i + 1]):
                bad.append(f"{a} {args[i + 1]}")
        elif NUM.match(a) and script == "flux_kit.py" and (i == 0 or not args[i - 1].startswith("-")):
            bad.append(a)
    return bad


def decide(command: str) -> str:
    """'' to allow, else the refusal reason."""
    if "--help" in (command or ""):
        return ""
    for script, args in cf_cmd.script_calls(command, ("generate.py", "flux_kit.py")):
        bad = literals(script, args)
        if bad:
            fix = ("`python tools/run_3d.py <asset_id> <image> --preset NAME` (presets: config/config.json -> trellis.presets), or add a preset there"
                   if script == "generate.py" else "a row in the kit file (size w/h, seed, prompt) and `flux_kit.py <kit.json> <row_id>`")
            return (f"No literals in a run: {script} was given inline numbers ({', '.join(bad)}). Put them in a preset or a kit row instead: {fix}. "
                    "Rule: LESSONS.md 2026-11-01 'Do not put literals in a run'.")
    return ""


def main() -> None:
    raw = sys.stdin.read() or "{}"
    if "generate.py" not in raw and "flux_kit" not in raw:
        return
    try:
        payload = json.loads(raw)
        if payload.get("tool_name") != "Bash":
            return
        reason = decide((payload.get("tool_input") or {}).get("command", ""))
    except Exception:
        return
    if reason:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
