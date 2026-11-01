"""Make a symmetric sheet cell by flipping its passed source cell: mirror_cell.py <kit.json> <row_id>.

The pairs live in mirrors.json next to the kit ({"hands_right": "hands_left", "head_profile_right": "head_profile_left"}). The source cell must
already have an ok flux receipt (the plugin 0.1.20 sheet rule: symmetric cells are mirrors, not renders). Writes the flipped png under the row's
own name and a `mirror` receipt (stage mirror, so it never counts as a Flux roll). CPU only, PIL.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common


def cell_path(cfg, kit, row):
    W, H = row.get("w", kit.get("w", cfg["defaults"]["w"])), row.get("h", kit.get("h", cfg["defaults"]["h"]))
    return os.path.join(cfg["local"]["outputs_dir"], kit["kit"], f"{row['name']}_{W}x{H}.png")


def main(kit_path, row_id):
    from PIL import Image, ImageOps
    cfg = cf_common.load_config()
    kit = json.load(open(kit_path, encoding="utf-8-sig"))
    mirrors = json.load(open(os.path.join(os.path.dirname(os.path.abspath(kit_path)), "mirrors.json"), encoding="utf-8-sig"))
    if row_id not in mirrors:
        sys.exit(f"{row_id} is not listed in mirrors.json")
    rows = {r.get("row_id", r["name"]): r for r in kit["rows"]}
    src_row, dst_row = rows[mirrors[row_id]], rows[row_id]
    src, dst = cell_path(cfg, kit, src_row), cell_path(cfg, kit, dst_row)
    if not os.path.isfile(src):
        sys.exit(f"source cell {mirrors[row_id]} has no png at {src}: render and pass it first")
    ImageOps.mirror(Image.open(src)).save(dst)
    rp = cf_common.write_receipt(cfg, f"{kit['kit']}-{row_id}-mirror", "mirror", tool="mirror_cell.py", model={"id": "none"}, inputs=[src], outputs=[dst],
                                 params={"kit": kit_path, "row_id": row_id, "mirror_of": mirrors[row_id]})
    print(f"MIRROR {row_id} <- {mirrors[row_id]} path={dst} receipt={rp}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
