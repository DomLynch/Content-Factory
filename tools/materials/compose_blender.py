"""Side-by-side stills (baked texture LEFT, scanned material RIGHT), inside headless Blender (no render, numpy and the image API only; plugin 0.1.17).
  blender.exe --background --python tools/materials/compose_blender.py -- <baked_dir> <scanned_dir> <out_dir>
For every turn_*.png present in BOTH folders writes <out_dir>/side_by_side_<frame>.png, 8 px neutral-grey gutter between the two. Nothing is resized: both come from the same
camera and the same rig at the same resolution, so a size mismatch is an error. Prints COMPOSE> lines and writes <out_dir>/side_by_side.json."""
import glob
import json
import os
import sys

import bpy
import numpy as np

GUTTER = 8


def load(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    return px.reshape(h, w, 4)


def main():
    a, b, out = sys.argv[sys.argv.index("--") + 1:][:3]
    os.makedirs(out, exist_ok=True)
    done = []
    for pa in sorted(glob.glob(os.path.join(a, "turn_*.png"))):
        name = os.path.basename(pa)
        pb = os.path.join(b, name)
        if not os.path.exists(pb):
            continue
        A, B = load(pa), load(pb)
        if A.shape != B.shape:
            raise SystemExit(f"COMPOSE> size mismatch for {name}: {A.shape} vs {B.shape}")
        gap = np.full((A.shape[0], GUTTER, 4), 0.2, dtype=np.float32)
        gap[..., 3] = 1.0
        row = np.concatenate([A, gap, B], axis=1)
        img = bpy.data.images.new("sbs_" + name, row.shape[1], row.shape[0], alpha=True)
        img.pixels.foreach_set(row.ravel())
        img.filepath_raw = os.path.join(out, "side_by_side_" + name)
        img.file_format = "PNG"
        img.save()
        done.append(img.filepath_raw)
        print("COMPOSE>", img.filepath_raw, flush=True)
    json.dump({"status": "ok" if done else "empty", "left": a, "right": b, "files": done}, open(os.path.join(out, "side_by_side.json"), "w", encoding="utf-8"), indent=2)
    if not done:
        raise SystemExit("COMPOSE> no common turn_*.png")


main()
