"""mirror_rows.py [kit.json] [--out-dir DIR] [--manifest FILE] [--dry-run]
Makes every kit row that has "mirror_of" by flipping the source row's png horizontally: only if the source png exists and the
mirror's png does not. Appends one entry per created file to the review manifest (default manifests/<kit>_review/mirrors.json)
with a sha256 prefix. Never overwrites a png and never renders (the Flux queue skips mirror_of rows)."""
import datetime, glob, hashlib, json, os, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    if dry:
        args.remove("--dry-run")
    out_dir_opt, manifest_opt = opt(args, "--out-dir"), opt(args, "--manifest")
    kit_path = args[0] if args else os.path.join(REPO, "kits", "vampire_gpt_sheet_kit.json")
    kit = json.load(open(kit_path, encoding="utf-8-sig"))
    if out_dir_opt:
        out_dir = out_dir_opt
    else:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import cf_common
        out_dir = os.path.join(cf_common.load_config()["local"]["outputs_dir"], kit["kit"])
    manifest = manifest_opt or os.path.join(REPO, "manifests", kit["kit"] + "_review", "mirrors.json")
    rows = {r["name"]: r for r in kit["rows"]}
    data = json.load(open(manifest, encoding="utf-8-sig")) if os.path.exists(manifest) else {"mirrors": []}
    made = []
    for r in kit["rows"]:
        src_name = r.get("mirror_of")
        if not src_name:
            continue
        if src_name not in rows:
            print("SKIP", r["name"], "mirror_of unknown row", src_name); continue
        src = sorted(glob.glob(os.path.join(out_dir, src_name + "_*.png")))
        src = [s for s in src if os.path.basename(s)[len(src_name) + 1:][:1].isdigit()]   # name_WxH.png, not name_qc1.png or other rows
        w, h = r.get("w", kit["w"]), r.get("h", kit["h"])
        target = os.path.join(out_dir, f"{r['name']}_{w}x{h}.png")
        if glob.glob(os.path.join(out_dir, r["name"] + "_*x*.png")):
            print("SKIP", r["name"], "png already exists"); continue
        if not src:
            print("SKIP", r["name"], "source png missing:", src_name); continue
        if dry:
            print("WOULD MAKE", target, "from", src[0]); continue
        from PIL import Image, ImageOps
        ImageOps.mirror(Image.open(src[0]).convert("RGB")).save(target)
        sha = hashlib.sha256(open(target, "rb").read()).hexdigest()[:16]
        data["mirrors"].append({"row": r["name"], "source_row": src_name, "method": "horizontal flip",
                                "reason": "symmetric anatomy; guarantees left/right agreement (kit mirror_of)",
                                "created": datetime.datetime.now().isoformat(timespec="seconds"), "sha256": sha})
        made.append(target); print("MADE", target, sha)
    if made:
        os.makedirs(os.path.dirname(manifest), exist_ok=True)
        with open(manifest, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
    print(len(made), "mirror png(s) made")


if __name__ == "__main__":
    main()
