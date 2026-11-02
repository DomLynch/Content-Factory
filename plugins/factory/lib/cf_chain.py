r"""The build order as a chain of gates (plugin 0.1.24; standard library only). Each stage refuses to run without the previous stage's PASS evidence on disk.

Why: the vampire that failed skipped stages (a fused mesh from one cell, no sheet, no cut-outs). Before this module the plugin gated single facts (token floor, qc before
stills, read-back, parts at publish) and DESCRIBED the order. Now:

  1 sheet       manifests/<kit>.json: EVERY cell of the kit has a QC verdict PASS, or WEAK with a note (rows with mirror_of count through their source); none REJECT or
                unreviewed; and a consistency pass is recorded (manifest key `consistency`, written by `manifest.py --consistency <review.json>`).
  2 approval    receipts/<character_id>/approval-*.json (stage `approval`, status ok, params.approved_by): Dom's yes on the contact sheet, written by the FACTORY with
                tools/record_approval.py after Dom said it in chat. It cannot be written while the sheet is incomplete.
  3 cutout      per part: receipts/<part asset>/cutout-*.json status ok whose output png passes the input gate (subject fill >= gates.min_subject_frac, head parts
                >= gates.min_head_px, portrait/square). Required when run_3d.py is given --cutout-png (the Factory's own cut-out stage); a plain run_3d.py makes its cut-out
                itself and needs only 1 and 2.
  4 part_mesh   run_3d.py / trellis_run: needs 1 and 2 (and 3 with --cutout-png) for the character the asset belongs to.

NOT enforced yet (the stage tool does not exist or does not record the evidence): cleanup face-loss cap (asm_stage_a port), capped assembly, the landmark rig check against
psyche-106-v3, the v1 contact sheet. `status()` lists them as pending so a dry run is honest about what is not gated.

An asset belongs to a character when characters/*.json has a character_id that is the asset id or a prefix of it (`<character_id>-<part>`). A character spec without
`sheet_kit` is reported as NOT EVALUATED (never silently satisfied), and an asset that matches no character spec is outside the chain (ad-hoc assets, benchmarks).
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import cf_gates
import cf_guard

PENDING = ("cleanup (face-loss cap)", "capped assembly", "bind landmark rig check (psyche-106-v3)", "v1 contact sheet to Dom",
           "projection texture pass after Trellis (backs and sides)", "head v2 pass after the body mesh",
           "FBX export from the Blender master (implementation path: Demon pipeline work/export_fbx_v7.py, see docs/vampire-v2-rules.md)")
# The fixed stage order (Dom 2026-11-02). The Factory may not reorder it; the first arrow is enforced below (body mesh waits for the head mesh), the rest are listed as pending above.
STAGE_ORDER = ("head mesh", "body cells", "body mesh", "head v2", "gear", "assembly", "exports (GLB + FBX)")


def spec_for_asset(asset_id: str) -> tuple[dict, str] | None:
    """(character spec, part name) for an asset id, or None when no character spec claims it. Longest character_id wins."""
    best = None
    for f in glob.glob(str(cf_guard.HOME / "characters" / "*.json")):
        r = cf_gates._read(f)
        cid = (r or {}).get("character_id")
        for pre in [x for x in ((r or {}).get("asset_prefix"), cid) if x]:   # assets are named <prefix>-<part>; the prefix may differ from character_id
            if (asset_id == pre or asset_id.startswith(pre + "-")) and (best is None or len(pre) > best[2]):
                best = (r, asset_id[len(pre) + 1:] or "body", len(pre))
    return None if best is None else (best[0], best[1])


def _manifest(kit: str) -> dict | None:
    return cf_gates._read(cf_guard.HOME / "manifests" / f"{kit}.json")


def _kit_rows(kit: str) -> list[dict] | None:
    for cand in (cf_guard.HOME / "kits" / f"{kit}_kit.json", cf_guard.HOME / "kits" / f"{kit}.json"):
        r = cf_gates._read(cand)
        if r and r.get("rows"):
            return r["rows"]
    return None


def sheet_reasons(kit: str) -> list[str]:
    m = _manifest(kit)
    rows = _kit_rows(kit)
    if m is None or rows is None:
        return [f"CHAIN (1 sheet): no manifests/{kit}.json or kits/{kit}_kit.json: the sheet has not been built and reviewed. Render the sheet, review every cell, "
                f"record verdicts with tools/manifest.py --verdict / --import-review."]
    bad = []
    by_id = {r.get("row_id", r.get("name")): r for r in rows}
    for rid, r in by_id.items():
        src = r.get("mirror_of")
        e = (m.get("rows") or {}).get(src or rid) or {}
        v = str(e.get("verdict") or "").upper()
        if v == "PASS":
            continue
        if v == "WEAK":
            if not str(e.get("verdict_note") or "").strip():
                bad.append(f"{rid}: WEAK without a note")
            continue
        bad.append(f"{rid}: {v or 'no verdict'}" + (f" (mirror of {src})" if src else ""))
    tol = float(cf_gates.gates_cfg().get("yaw_tol_deg", 15))
    for rid, r in by_id.items():   # QC fails a head cell whose yaw does not match its row (vampire v2): a row with yaw_deg needs the measured yaw in the manifest
        if r.get("yaw_deg") is None or any(x.startswith(rid + ":") for x in bad):
            continue
        got = ((m.get("rows") or {}).get(rid) or {}).get("yaw_measured")
        if got is None:
            bad.append(f"{rid}: yaw not measured (row wants {r['yaw_deg']} deg; manifest.py --yaw {rid} DEG)")
        elif abs(float(got) - float(r["yaw_deg"])) > tol:
            bad.append(f"{rid}: yaw {got} deg, row wants {r['yaw_deg']} (+-{tol:g})")
    out = []
    if bad:
        out.append(f"CHAIN (1 sheet): the sheet is not complete: {len(bad)} of {len(by_id)} cells block it ({'; '.join(bad[:8])}{' ...' if len(bad) > 8 else ''}). "
                   "Every cell needs PASS, or WEAK with a note.")
    if not (m.get("consistency") or {}).get("file"):
        out.append("CHAIN (1 sheet): no consistency pass recorded in the manifest (python tools/manifest.py <kit> --consistency <review.json>).")
    return out


def approval_reasons(char_id: str, kit: str) -> list[str]:
    for f in sorted(glob.glob(str(cf_guard.HOME / "receipts" / char_id / "approval-*.json")), reverse=True):
        r = cf_gates._read(f)
        p = (r or {}).get("params") or {}
        if r and r.get("status") == "ok" and p.get("approved_by") and p.get("kit") == kit:
            return []
    return [f"CHAIN (2 approval): no approval receipt for '{char_id}' (kit {kit}). Dom's yes on the contact sheet must be recorded first: "
            "the Factory runs `python tools/record_approval.py <kit> --by Dom --note \"...\"` after he says it in chat."]


def cutout_reasons(asset_id: str) -> list[str]:
    for f in sorted(glob.glob(str(cf_guard.HOME / "receipts" / asset_id / "cutout-*.json")), reverse=True):
        r = cf_gates._read(f)
        if not r or r.get("status") != "ok":
            continue
        outs = [o.get("path") for o in r.get("outputs") or [] if str(o.get("path", "")).lower().endswith(".png")]
        if not outs:
            continue
        why = cf_gates.input_reason(outs[0])
        return [f"CHAIN (3 cutout): the cut-out of '{asset_id}' fails the input gate: {why}"] if why else []
    return [f"CHAIN (3 cutout): no ok cutout receipt for '{asset_id}'. Cut the part out (one part per frame, from a PASS cell) and keep its receipt before making the mesh."]


def part_mesh_reasons(asset_id: str, with_cutout_png: bool = False) -> list[str]:
    """Reasons a part mesh (run_3d.py / trellis_run) may not start; [] when allowed or when the asset is outside the chain."""
    found = spec_for_asset(asset_id)
    if not found:
        return []
    spec, _part = found
    kit = spec.get("sheet_kit")
    if not kit:
        return [f"CHAIN: characters spec '{spec['character_id']}' has no `sheet_kit`, so the build order cannot be checked. Add the sheet kit name to the spec."]
    out = sheet_reasons(kit)
    if not out:   # approval is only meaningful once the sheet is complete
        out += approval_reasons(spec["character_id"], kit)
    if with_cutout_png:
        out += cutout_reasons(asset_id)
    out += order_reasons(asset_id, spec, _part)
    return out


def order_reasons(asset_id: str, spec: dict, part: str) -> list[str]:
    """Fixed stage order, first arrow: the body mesh waits for the head mesh (a trellis receipt with status ok) when the spec lists a head part."""
    try:
        names = [p.get("name") for p in spec.get("parts", []) if p.get("name")]
        head = next((n for n in names if n.startswith("head")), None)
        if not head or not part.startswith("body") or part == head:
            return []
        hid = asset_id[: len(asset_id) - len(part)] + head
        for f in glob.glob(str(cf_guard.HOME / "receipts" / hid / "trellis-*.json")):
            if (cf_gates._read(f) or {}).get("status") == "ok":
                return []
        return [f"CHAIN (order): the body mesh '{asset_id}' waits for the head mesh '{hid}' (head mesh -> body cells -> body mesh -> head v2 -> gear -> assembly -> exports). Make the head mesh first; the Factory may not reorder this."]
    except Exception:
        return []


def status(char_id: str) -> list[tuple[str, bool, str]]:
    """(gate, satisfied, detail) for a dry run: which gates of the chain are already satisfied for this character."""
    found = spec_for_asset(char_id)
    if not found:
        return [("chain", False, f"no characters/*.json with character_id '{char_id}'")]
    spec = found[0]
    kit = spec.get("sheet_kit")
    if not kit:
        return [("chain", False, "NOT EVALUATED: the spec has no sheet_kit")]
    s = sheet_reasons(kit)
    rows = [("1 sheet complete + consistency", not s, "; ".join(s))]
    a = approval_reasons(spec["character_id"], kit)
    rows.append(("2 Dom's yes on the contact sheet", not a, "; ".join(a)))
    for p in spec.get("parts", []):
        n = p.get("name")
        if n:
            c = cutout_reasons(f"{spec.get('asset_prefix') or spec['character_id']}-{n}")
            rows.append((f"3 cutout: {n}", not c, "; ".join(c)))
    rows += [(f"pending (not gated yet): {x}", False, "the stage tool does not record this evidence yet") for x in PENDING]
    return rows
