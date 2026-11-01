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
os.makedirs(os.path.dirname(mpath), exist_ok=True)
with open(mpath, "w", encoding="utf-8", newline="\n") as f:
    json.dump({"schema": 1, "kit": kit["kit"], "rows": rows}, f, indent=1)
print(mpath, {k: bool(v["pick"]) for k, v in rows.items()})
