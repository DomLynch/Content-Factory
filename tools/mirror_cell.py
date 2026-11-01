"""Make symmetric sheet cells by flipping their passed source cell: mirror_cell.py <kit.json> [row_id ...] [--force].

A kit row with `mirror_of: <source row_id>` (the data layer, source of truth) is a flip of its source, never a render (plugin 0.1.22: the Flux gate
refuses to render it). With no row ids every row that has mirror_of is made. The source png must exist at <outputs_dir>/<kit>/<name>_<W>x<H>.png
(the flux_kit.py convention) and the source row needs a QC verdict PASS in manifests/<kit>.json. The flip is written at the TARGET row's own
name and size (refused when the sizes differ, no silent resize); an existing target png is never overwritten without --force, so a re-run cannot
clobber a flip or a render. Receipt: receipts/<kit>-<row_id>-mirror/mirror-<utc>.json (stage mirror, so it is not a Flux roll). CPU only, PIL.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "factory", "lib"))
import cf_flux


def cell_path(cfg, kit, row):
    W, H = row.get("w", kit.get("w", cfg["defaults"]["w"])), row.get("h", kit.get("h", cfg["defaults"]["h"]))
    return os.path.join(cfg["local"]["outputs_dir"], kit["kit"], f"{row['name']}_{W}x{H}.png")


def mirror(cfg, kit, kit_path, rid, force=False, check_verdict=True):
    from PIL import Image, ImageOps
    rows = {r.get("row_id", r["name"]): r for r in kit["rows"]}
    dst_row = rows[rid]
    sid = dst_row.get("mirror_of")
    if not sid or sid not in rows:
        return f"{rid}: no mirror_of, or its source row is not in the kit"
    if check_verdict and cf_flux.verdict(kit["kit"], sid) != "PASS":
        return f"{rid}: source {sid} has no QC verdict PASS in manifests/{kit['kit']}.json (python tools/manifest.py <kit> --verdict {sid} PASS)"
    src, dst = cell_path(cfg, kit, rows[sid]), cell_path(cfg, kit, dst_row)
    if not os.path.isfile(src):
        return f"{rid}: source cell {sid} has no png at {src}: render and pass it first"
    if os.path.exists(dst) and not force:
        return f"{rid}: {dst} exists, not overwritten (use --force)"
    im = Image.open(src)
    want = (dst_row.get("w", kit.get("w")), dst_row.get("h", kit.get("h")))
    if want[0] and want[1] and im.size != tuple(want):
        return f"{rid}: source is {im.size[0]}x{im.size[1]}, target row is {want[0]}x{want[1]}; sizes must match"
    ImageOps.mirror(im).save(dst)
    rp = cf_common.write_receipt(cfg, f"{kit['kit']}-{rid}-mirror", "mirror", tool="mirror_cell.py", model={"id": "none"}, inputs=[src], outputs=[dst],
                                 params={"kit": kit_path, "row_id": rid, "mirror_of": sid})
    print(f"MIRROR {rid} <- {sid} path={dst} receipt={rp}")
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("kit")
    ap.add_argument("rows", nargs="*")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    cfg = cf_common.load_config()
    kit = json.load(open(a.kit, encoding="utf-8-sig"))
    todo = a.rows or [r.get("row_id", r["name"]) for r in kit["rows"] if r.get("mirror_of")]
    errs = [e for e in (mirror(cfg, kit, a.kit, rid, a.force) for rid in todo) if e]
    for e in errs:
        print("REFUSED " + e, file=sys.stderr)
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
