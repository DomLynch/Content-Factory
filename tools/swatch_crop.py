"""swatch_crop.py [kit.json] [--force] [--dry-run] [--only ROW ...] [--out-dir DIR] [--manifest FILE]
Material swatches as CROPS of approved cells: every kit row with
  "swatch_of": {"source_row": R, "box": [x0, y0, x1, y1] (fractions of the source image), "size": [w, h]}
is cut from the source png at native resolution (Pillow; LANCZOS only when the crop must be resized to "size").
Writes <outputs_dir>/<kit>/<name>_<W>x<H>.png (flux_kit.py naming, so the queue sees it), refuses to overwrite without --force,
requires the source png to exist (verdicts are checked by the reviewer, not here), writes a receipt (stage swatch_crop, input and
output sha256) and an entry in manifests/<kit>_swatches.json. CPU only, no GPU, no upscaler."""
import datetime, json, os, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))


def opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


def main():
    args = sys.argv[1:]
    force, dry = "--force" in args, "--dry-run" in args
    args = [a for a in args if a not in ("--force", "--dry-run")]
    only = []
    while "--only" in args:
        only.append(opt(args, "--only"))
    out_dir_opt, manifest_opt = opt(args, "--out-dir"), opt(args, "--manifest")
    kit_path = args[0] if args else os.path.join(REPO, "kits", "vampire_gpt_sheet_kit.json")
    kit = json.load(open(kit_path, encoding="utf-8-sig"))
    import cf_common
    cfg = cf_common.load_config()
    out_dir = out_dir_opt or os.path.join(cfg["local"]["outputs_dir"], kit["kit"])
    manifest = manifest_opt or os.path.join(REPO, "manifests", kit["kit"] + "_swatches.json")
    rows = {r["name"]: r for r in kit["rows"]}
    data = json.load(open(manifest, encoding="utf-8-sig")) if os.path.exists(manifest) else {"swatches": []}
    from PIL import Image
    made, refused = 0, 0
    for r in kit["rows"]:
        so = r.get("swatch_of")
        if not so or (only and r["name"] not in only):
            continue
        s = rows.get(so["source_row"])
        if not s:
            print("SKIP", r["name"], "unknown source row", so["source_row"]); refused += 1; continue
        sw, sh = s.get("w", kit["w"]), s.get("h", kit["h"])
        src = os.path.join(out_dir, f"{s['name']}_{sw}x{sh}.png")
        W, H = r.get("w", kit["w"]), r.get("h", kit["h"])
        target = os.path.join(out_dir, f"{r['name']}_{W}x{H}.png")
        if not os.path.exists(src):
            print("SKIP", r["name"], "source png missing:", src); refused += 1; continue
        if os.path.exists(target) and not force:
            print("REFUSE", r["name"], "png exists (use --force):", target); refused += 1; continue
        im = Image.open(src).convert("RGB")
        x0, y0, x1, y1 = so["box"]
        px = [round(x0 * im.width), round(y0 * im.height), round(x1 * im.width), round(y1 * im.height)]
        size = so.get("size") or [W, H]
        if dry:
            print("WOULD CROP", r["name"], "<-", s["name"], px, f"{px[2]-px[0]}x{px[3]-px[1]} native ->", size); continue
        t0 = time.time()
        crop = im.crop(px)
        if list(crop.size) != list(size):
            crop = crop.resize(tuple(size), Image.LANCZOS)
        os.makedirs(out_dir, exist_ok=True)
        crop.save(target)
        asset_id = cf_common.slug_id(kit["kit"], r["row_id"], "swatch_of " + json.dumps(so, sort_keys=True), r.get("seed", kit["seed"]))
        receipt = cf_common.write_receipt(cfg, asset_id, "swatch_crop", tool="swatch_crop.py", model="Pillow crop (LANCZOS only if resized)",
                                          inputs=[src], outputs=[target], timing={"wall_s": round(time.time() - t0, 2)},
                                          params={"row": r["name"], "source_row": so["source_row"], "box": so["box"], "crop_px": px, "size": size})
        entry = {"row": r["name"], "source_row": so["source_row"], "source_sha256": cf_common.sha256(src), "box": so["box"], "crop_px": px,
                 "native_px": [px[2] - px[0], px[3] - px[1]], "size": size, "sha256": cf_common.sha256(target), "receipt": os.path.relpath(receipt, REPO),
                 "created": datetime.datetime.now().isoformat(timespec="seconds")}
        data["swatches"] = [e for e in data["swatches"] if e["row"] != r["name"]] + [entry]
        print("MADE", target, f"{px[2]-px[0]}x{px[3]-px[1]} native from {s['name']}"); made += 1
    if made:
        os.makedirs(os.path.dirname(manifest), exist_ok=True)
        json.dump(data, open(manifest, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(made, "swatch crop(s) made,", refused, "refused/skipped")
    sys.exit(1 if refused else 0)


if __name__ == "__main__":
    main()
