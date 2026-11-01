#!/usr/bin/env python3
"""PreToolUse hook: no stills until the mesh has passed QC (0.1.3).

Deny the factory `stills` MCP tool unless the newest receipts/<asset_id>/qc-*.json for that asset says params.overall == "pass" (written by tools/run_qc.py,
MCP tool `qc`) AND was made for the very GLB being rendered (sha256 of the receipt's first input equals the sha256 of tool_input.glb, when that file is readable).
Override: set FACTORY_RAW=<reason> in the environment of the Claude Code session (the stills tool schema has no raw_reason field, so an env var is the only
escape). The reason is appended to the same override log as hooks/plugin_guard.py (config plugin_guard.override_log). Any error allows the call (fail open).
Output is the standard PreToolUse deny JSON.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cf_guard  # noqa: E402

MCP_STILLS = "mcp__plugin_factory_factory__stills"


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def latest_qc(asset_id: str) -> Path | None:
    d = cf_guard.HOME / "receipts" / asset_id
    files = sorted(d.glob("qc-*.json"), key=lambda p: (p.name, p.stat().st_mtime)) if d.is_dir() else []   # names carry the UTC stamp
    return files[-1] if files else None


def decide(payload: dict) -> str:
    """'' to allow, else the refusal reason."""
    if payload.get("tool_name") != MCP_STILLS:
        return ""
    inp = payload.get("tool_input") or {}
    asset = str(inp.get("asset_id") or "")
    raw = os.environ.get("FACTORY_RAW", "").strip()
    if raw:
        try:
            log = cf_guard.guard_path("override_log")
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "reason": raw, "scripts": ["stills (qc_gate)"], "asset_id": asset}) + "\n")
        except Exception:
            pass
        return ""
    if not asset or "/" in asset or "\\" in asset or asset.startswith("."):
        return ""   # not a usable asset id: let the tool refuse it itself
    how = "Run the factory `qc` tool (asset_id, glb, concept) on this mesh, fix what it flags, and retry. Logged exception: FACTORY_RAW=<reason> in the session environment."
    rec = latest_qc(asset)
    if rec is None:
        return f"No QC receipt for asset {asset} (receipts/{asset}/qc-*.json): stills are rendered only from a mesh that passed QC. {how}"
    r = cf_guard.read_json(rec)
    overall = (r.get("params") or {}).get("overall")
    if r.get("status") != "ok" or overall != "pass":
        failed = ", ".join((r.get("params") or {}).get("failed", [])) or r.get("error", "") or "see the receipt"
        return f"The latest QC for {asset} is not a pass ({rec.name}: overall={overall}, status={r.get('status')}; failed: {failed}). {how}"
    glb = inp.get("glb")
    ins = r.get("inputs") or []
    if glb and ins and os.path.isfile(glb) and sha256(glb) != ins[0].get("sha256"):
        return f"The latest passing QC for {asset} ({rec.name}) was made for a different file than {glb} (sha256 differs). {how}"
    return ""


def main() -> None:
    raw = sys.stdin.read() or "{}"
    if "stills" not in raw:
        return   # fast exit
    try:
        reason = decide(json.loads(raw))
    except Exception:
        return
    if reason:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
