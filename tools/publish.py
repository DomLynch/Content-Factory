"""Copy a finished asset into a local clone of the library repo (opentrident-steam) and update site/data/unity.json.
Never pushes or opens a PR: it edits the clone only and prints the git commands to run. Spec: publish/<asset_id>.json."""
import argparse, json, os, re, shutil, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

# Library-relative layout (see docs/library-format.md); overridable via config.json "publish".
DEFAULTS = {"spec_dir": "publish", "models_dir": "models/unity", "thumbs_dir": "thumbs", "stills_dir": "stills",
            "data_file": "site/data/unity.json", "template_id": "demon2-106", "variant": "standard", "branch_prefix": "publish/",
            "site_dir": "site", "max_git_bytes": 50_000_000, "default_receipt": "https://github.com/DomLynch/Content-Factory/tree/main/receipts"}


def find_v(text):
    m = re.search(r"\?v=(\d+)", text or "")
    return int(m.group(1)) if m else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("asset_id")
    ap.add_argument("--library", required=True, help="local clone of the library repo")
    ap.add_argument("--dry-run", action="store_true", help="print planned changes only")
    ap.add_argument("--repo", default=cf_common.REPO, help="Content-Factory root holding publish/ specs (default: this repo)")
    a = ap.parse_args()
    cfg = cf_common.load_config()
    o = {**DEFAULTS, **cfg.get("publish", {})}
    t0 = time.time()
    spec_path = os.path.join(a.repo, o["spec_dir"], a.asset_id + ".json")
    if not os.path.exists(spec_path):
        sys.exit(f"missing spec {spec_path}")
    spec = cf_common._read(spec_path)
    miss = [k for k in ("id", "label", "group", "rig", "source", "glb", "thumb", "stills") if k not in spec]
    if miss:
        sys.exit(f"spec missing keys: {miss}")
    if not a.dry_run:
        cf_common.plugin_gate("publish", spec)   # parts, lighting, read-back and pipeline gates, also outside Claude Code (plugin 0.1.23)
    res = lambda p: p if os.path.isabs(p) else os.path.join(a.repo, p)
    srcs = [res(spec["glb"]), res(spec["thumb"])] + [res(s) for s in spec["stills"]]
    for s in srcs:
        if not os.path.isfile(s):
            sys.exit(f"missing file {s}")
    lib = os.path.abspath(a.library)
    data_path = os.path.join(lib, *o["data_file"].split("/"))
    if not os.path.isfile(data_path):
        sys.exit(f"not a library clone (no {o['data_file']}): {lib}")
    with open(data_path, encoding="utf-8-sig") as f:
        data = json.load(f)
    bodies = data.setdefault("bodies", [])
    by_id = {b.get("id"): b for b in bodies}
    old = by_id.get(spec["id"])
    template = by_id.get(o["template_id"])
    v = max(find_v(old.get("file")), find_v(old.get("thumb"))) + 1 if old else 1
    mdir = o["models_dir"]
    glb_rel = f"{mdir}/{spec['id']}.glb"
    thumb_rel = f"{mdir}/{o['thumbs_dir']}/{spec['id']}.png"
    still_rels = [f"{mdir}/{spec['id']}/{o['stills_dir']}/{os.path.basename(s)}" for s in spec["stills"]]
    names = {"head": "Head crop", **spec.get("still_names", {})}
    stills = [{"name": names.get(os.path.splitext(os.path.basename(s))[0], os.path.splitext(os.path.basename(s))[0].title()),
               "file": f"{r}?v={v}"} for s, r in zip(spec["stills"], still_rels)]
    row = {"id": spec["id"], "label": spec["label"], "group": spec["group"], "file": f"{glb_rel}?v={v}", "rigged": True,
           "rig": spec["rig"], "bytes": os.path.getsize(srcs[0]), "source": spec["source"],
           "thumb": f"{thumb_rel}?v={v}", "variant": o["variant"], "stills": stills,
           "receipt": spec.get("receipt", o["default_receipt"])}
    if spec.get("pipeline"):  # 0.1.4 pipeline gate: the library row says which pipeline made the asset
        row["pipeline"] = spec["pipeline"]
    if spec.get("clips"):
        row["clips"] = spec["clips"]
    if template:  # follow the template row's key order
        order = list(template.keys())
        row = {**{k: row[k] for k in order if k in row}, **{k: row[k] for k in row if k not in order}}
    copies = list(zip(srcs, [glb_rel, thumb_rel] + still_rels))
    big = os.path.getsize(srcs[0]) > o["max_git_bytes"]
    if big:  # too big for git: row is still written, binary is not copied/committed
        import hashlib
        h = hashlib.sha256()
        with open(srcs[0], "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        print(f"WARNING: GLB is {os.path.getsize(srcs[0])/1e6:.1f} MB (> {o['max_git_bytes']/1e6:.0f} MB): do NOT commit it. "
              f"Send path + sha256 to the library owner (tools/beast/MODELS.md):\n  {srcs[0]}\n  sha256 {h.hexdigest()}")
        copies = copies[1:]
    print(f"library: {lib}")
    print(f"{'update' if old else 'add'} row {spec['id']} (?v={v}) in {o['data_file']}"
          + ("" if template else f" [template row {o['template_id']} not found: using documented shape]"))
    for s, d in copies:
        print(f"  copy {s} -> {d}")
    print("  row: " + json.dumps(row, ensure_ascii=False))
    br = f"{o['branch_prefix']}{spec['id']}-v{v}"
    git = [f'git -C "{lib}" checkout -b {br}',
           f'git -C "{lib}" add ' + o["site_dir"] + "/" + mdir + " " + o["data_file"],
           f'git -C "{lib}" commit -m "Add/update {spec["id"]} (?v={v})"',
           f'git -C "{lib}" push -u origin {br}',
           "then open the PR yourself (publish is by PR only)"]
    if a.dry_run:
        print("dry run: nothing written")
    else:
        for s, d in copies:
            dst = os.path.join(lib, o["site_dir"], *d.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(s, dst)
        if old:
            bodies[bodies.index(old)] = row
        else:
            bodies.append(row)
        with open(data_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        rp = cf_common.write_receipt(cfg, a.asset_id, "publish", tool="publish.py", model={}, inputs=[spec_path] + srcs,
                                     params={"library": lib, "row_id": spec["id"], "version": v},
                                     timing={"wall_s": round(time.time() - t0, 1)},
                                     outputs=[os.path.join(lib, o["site_dir"], *d.split("/")) for _, d in copies])
        print(f"receipt: {rp}")
    print("\nNext (run yourself; this tool never pushes):")
    for g in git:
        print("  " + g)


if __name__ == "__main__":
    main()
