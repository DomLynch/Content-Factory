"""Build index/assets.json: one row per asset with the latest receipt per stage. Use --check to verify it is current."""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

# Folder names inside the repo (not machine paths); overridable via config.json "index".
DEFAULTS = {"receipts_dir": "receipts", "assets_dir": "assets", "out_file": "index/assets.json", "asset_file": "asset.json"}


def read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build(cfg, repo):
    o = {**DEFAULTS, **cfg.get("index", {})}
    stages = cfg["stages"]
    rdir, adir = os.path.join(repo, o["receipts_dir"]), os.path.join(repo, o["assets_dir"])
    ids = set(os.listdir(rdir)) if os.path.isdir(rdir) else set()
    if os.path.isdir(adir):
        ids |= {d for d in os.listdir(adir) if os.path.isfile(os.path.join(adir, d, o["asset_file"]))}
    rows = []
    for aid in sorted(ids):
        meta = read_json(os.path.join(adir, aid, o["asset_file"])) or {}
        row = {"asset_id": aid, "kind": meta.get("kind", ""), "stages": {}, "stage": "", "status": ""}
        d = os.path.join(rdir, aid)
        names = sorted(n for n in os.listdir(d) if n.endswith(".json")) if os.path.isdir(d) else []
        for n in names:  # file name = <stage>-<utc>.json, so a later name is a later receipt
            stage = n.rsplit("-", 1)[0]
            if stage not in stages:
                continue
            rec = read_json(os.path.join(d, n)) or {}
            cur = row["stages"].get(stage)
            if cur is None or n > cur["file"]:
                row["stages"][stage] = {"file": n, "status": rec.get("status", "unknown"), "created_utc": rec.get("created_utc", "")}
        done = [s for s in stages if s in row["stages"]]
        if done:
            row["stage"], row["status"] = done[-1], row["stages"][done[-1]]["status"]
        rows.append(row)
    return {"schema": 1, "stages": stages, "assets": rows}, o


def render(doc):
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 if the index file is out of date")
    ap.add_argument("--repo", default=cf_common.REPO, help="repo root (default: this repo)")
    a = ap.parse_args()
    cfg = cf_common._read(os.path.join(a.repo, "config", "config.json"))
    doc, o = build(cfg, a.repo)
    text, out = render(doc), os.path.join(a.repo, o["out_file"])
    if a.check:
        try:
            with open(out, encoding="utf-8", newline="") as f:
                cur = f.read()
        except OSError:
            cur = None
        if cur != text:
            print(f"{o['out_file']} is out of date: run tools/index.py")
            sys.exit(1)
        print("index is up to date")
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"wrote {o['out_file']}: {len(doc['assets'])} assets")


if __name__ == "__main__":
    main()
