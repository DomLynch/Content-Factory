"""Repair the pure-black decode block (fully black 32 px cells, seen as a 256x1024 rectangle) that tiled Flux decodes leave on some 1024x1536 renders.
Usage: fix_black_block.py <image.png> [--mirror CX]   Saves the untouched original to <folder>/orig/ first.
Default: fill the cells with the neighbouring background. --mirror CX: rebuild the cells by mirroring the image about column CX (for symmetric subjects).
Prints the number of black cells found; 0 = nothing to do."""
import os, shutil, sys

import numpy as np
from PIL import Image

CELL = 32
path = sys.argv[1]
mirror = int(sys.argv[sys.argv.index("--mirror") + 1]) if "--mirror" in sys.argv else None
a = np.asarray(Image.open(path).convert("RGB")).copy()
H, W, _ = a.shape
mask = np.zeros((H // CELL, W // CELL), bool)
for i in range(mask.shape[0]):
    for j in range(mask.shape[1]):
        mask[i, j] = a[i * CELL:(i + 1) * CELL, j * CELL:(j + 1) * CELL].max() < 4
n = int(mask.sum())
print(f"{os.path.basename(path)}: {n} fully black {CELL}px cells")
if n <= 40:
    sys.exit(0)
os.makedirs(os.path.join(os.path.dirname(path), "orig"), exist_ok=True)
shutil.copy(path, os.path.join(os.path.dirname(path), "orig", os.path.basename(path)))
for i, j in zip(*np.where(mask)):
    y0, x0 = i * CELL, j * CELL
    if mirror is not None:
        for x in range(x0, x0 + CELL):
            xs = 2 * mirror - x
            if 0 <= xs < W and not mask[i, xs // CELL]:
                a[y0:y0 + CELL, x] = a[y0:y0 + CELL, xs]
    else:
        jl = j
        while jl > 0 and mask[i, jl - 1]:
            jl -= 1
        src = a[y0:y0 + CELL, max(0, jl * CELL - CELL):jl * CELL].astype(float)
        col = src.mean(axis=1, keepdims=True) if src.size else np.full((CELL, 1, 3), 90.0)
        a[y0:y0 + CELL, x0:x0 + CELL] = np.repeat(col, CELL, axis=1).astype(np.uint8)
Image.fromarray(a).save(path)
print("repaired", "(mirror about %d)" % mirror if mirror is not None else "(background fill)")
