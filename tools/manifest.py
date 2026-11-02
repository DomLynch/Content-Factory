"""Build or update manifests/<kit>.json: per kit row the candidate outputs (sha256) and the pick.
Usage: manifest.py <kit.json> [--pick row_id ...] [--by NAME] [--note TEXT]
Picks are data, never a renamed file. Re-running keeps earlier picks and adds new candidates."""
import argparse, datetime, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

ap = argparse.ArgumentParser()
ap.add_argument("kit")
ap.add_argument("--pick", nargs="*", default=[])
ap.add_argument("--by", default="")
ap.add_argument("--note", default="")
ap.add_argument("--consistency", metavar="REVIEW.json", help="record the consistency pass of the sheet (plugin 0.1.24 chain gate 1): the review file, its sha256, its counts")
ap.add_argument("--yaw", nargs=2, action="append", default=[], metavar=("ROW_ID", "DEG"), help="record the measured head yaw of a cell (chain gate 1 compares it with the row's yaw_deg)")
ap.add_argument("--score", type=int, default=None, help="QC score 0-10 recorded with --verdict")
ap.add_argument("--prompt-fix", default="", help="QC prompt_fix recorded with --verdict")
ap.add_argument("--import-review", action="append", default=[], metavar="REVIEW.json",
                help="import the verdicts of a review file (cells[] with cell or row, score, verdict, notes, prompt_fix or fix); repeatable, a later file wins")
ap.add_argument("--verdict", nargs=2, action="append", default=[], metavar=("ROW_ID", "PASS|WEAK|REJECT"),
                help="record the QC verdict of a row (plugin 0.1.22: reference-first needs PASS on the identity cell); repeatable")
ap.add_argument("--check", nargs=3, action="append", default=[], metavar=("ROW_ID", "NAME", "true|false"), help="record one STRUCTURAL check of a cell (names: config qc.cell_checks)")
ap.add_argument("--cosmetic", nargs=2, action="append", default=[], metavar=("ROW_ID", "FLAG"), help="record a cosmetic flag (eye colour, skin tint, costume drift, background texture, hair length): WEAK, never FAIL")
ap.add_argument("--judge", action="append", default=[], metavar="ROW_ID", help="derive the verdict (qc_pass_pct policy): any structural check false = REJECT, else cosmetic flags = WEAK, else PASS")
ap.add_argument("--accept-as-is", action="append", default=[], metavar="ROW_ID", help="after a FAIL and one re-roll (two FAILs judged), accept the cell as WEAK with the flag")
a = ap.parse_args()

cfg = cf_common.load_config()
kit = json.load(open(a.kit, encoding="utf-8-sig"))
mpath = os.path.join(cf_common.REPO, "manifests", f"{kit['kit']}.json")
old = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else {"rows": {}}
now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
rows = old["rows"]
for r in kit["rows"]:
    rid = r.get("row_id", r["name"])
    entry = rows.setdefault(rid, {"candidates": [], "pick": None, "picked_by": None, "picked_at": None, "note": ""})
    known = {c["sha256"] for c in entry["candidates"]}
    for p in glob.glob(os.path.join(cfg["local"]["outputs_dir"], kit["kit"], f"{r['name']}_*.png")):
        h = cf_common.sha256(p)
        if h not in known:
            entry["candidates"].append({"sha256": h, "path": p})
    if rid in a.pick and entry["candidates"]:
        entry["pick"] = entry["candidates"][-1]["sha256"]
        entry["picked_by"], entry["picked_at"], entry["note"] = a.by, now, a.note
def set_verdict(vrow, v, note="", score=None, fix=""):
    if vrow not in rows or str(v).upper() not in ("PASS", "WEAK", "REJECT"):
        sys.exit(f"--verdict {vrow} {v}: unknown row or verdict (PASS, WEAK, REJECT)")
    rows[vrow]["verdict"], rows[vrow]["verdict_note"], rows[vrow]["verdict_at"] = str(v).upper(), note, now
    if score is not None:
        rows[vrow]["score"] = score
    if fix:
        rows[vrow]["prompt_fix"] = fix

for rev in a.import_review:
    for c in json.load(open(rev, encoding="utf-8-sig")).get("cells", []):
        rid = c.get("cell") or c.get("row")
        if rid in rows and c.get("verdict"):   # a reviewed cell that is not in this kit is skipped, not an error
            set_verdict(rid, c["verdict"], c.get("notes") or "", c.get("score"), c.get("prompt_fix") or c.get("fix") or "")
for vrow, v in a.verdict:
    set_verdict(vrow, v, a.note, a.score, a.prompt_fix)
for vrow, deg in a.yaw:   # measured head yaw (plugin 0.1.26): the chain compares it with the row's yaw_deg
    if vrow not in rows:
        sys.exit(f"--yaw {vrow}: unknown row")
    rows[vrow]["yaw_measured"] = float(deg)
GROUP = {r.get("row_id", r["name"]): (r.get("group") or r.get("part") or "") for r in kit["rows"]}
for vrow, name, val in a.check:
    if vrow not in rows or val.lower() not in ("true", "false"):
        sys.exit(f"--check {vrow} {name} {val}: unknown row or value (true|false)")
    rows[vrow].setdefault("checks", {})[name] = val.lower() == "true"
for vrow, flag in a.cosmetic:
    if vrow not in rows:
        sys.exit(f"--cosmetic {vrow}: unknown row")
    rows[vrow].setdefault("cosmetic", [])
    if flag not in rows[vrow]["cosmetic"]:
        rows[vrow]["cosmetic"].append(flag)
for vrow in a.judge:
    if vrow not in rows:
        sys.exit(f"--judge {vrow}: unknown row")
    g = GROUP.get(vrow, "")
    g = "hands" if g in ("hands_feet", "hand", "feet") else g
    need = cfg.get("qc", {}).get("cell_checks", {}).get(g, [])
    have = rows[vrow].get("checks", {})
    missing = [c for c in need if c not in have]
    if missing:
        sys.exit(f"--judge {vrow}: structural checks not recorded yet: {missing} (--check {vrow} NAME true|false)")
    failed = [c for c, ok in have.items() if not ok]
    if failed:
        rows[vrow]["fail_count"] = rows[vrow].get("fail_count", 0) + 1
        set_verdict(vrow, "REJECT", "structural FAIL: " + ", ".join(failed), a.score, a.prompt_fix)
    elif rows[vrow].get("cosmetic"):
        set_verdict(vrow, "WEAK", "cosmetic: " + ", ".join(rows[vrow]["cosmetic"]), a.score, a.prompt_fix)
    else:
        set_verdict(vrow, "PASS", a.note, a.score, a.prompt_fix)
for vrow in a.accept_as_is:
    if vrow not in rows or rows[vrow].get("fail_count", 0) < 2:
        sys.exit(f"--accept-as-is {vrow}: needs two judged FAILs (one re-roll) first")
    set_verdict(vrow, "WEAK", "accepted as-is after FAIL and one re-roll: " + ", ".join(c for c, ok in rows[vrow].get("checks", {}).items() if not ok), a.score, a.prompt_fix)
consistency = old.get("consistency")
if a.consistency:
    rv = json.load(open(a.consistency, encoding="utf-8-sig"))
    consistency = {"file": os.path.abspath(a.consistency), "sha256": cf_common.sha256(a.consistency), "recorded_utc": now, "counts": rv.get("counts"), "reviewer": rv.get("reviewer")}
os.makedirs(os.path.dirname(mpath), exist_ok=True)
with open(mpath, "w", encoding="utf-8", newline="\n") as f:
    json.dump({"schema": 1, "kit": kit["kit"], "rows": rows, **({"consistency": consistency} if consistency else {})}, f, indent=1)
print(mpath, {k: bool(v["pick"]) for k, v in rows.items()})
