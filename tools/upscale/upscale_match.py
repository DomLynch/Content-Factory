"""Real-ESRGAN x4 upscale, then restore tone and colour from the original (Lab mean/std match, per channel).
Usage: upscale_match.py <in.png> <out.png> [passes=1]   passes=2 turns 2048 into 8192 on the long side.
The raw upscale comes out about 10% darker than the source; the Lab match fixes that. Runs on the GPU for ~30 s per pass: one GPU job at a time."""
import os, subprocess, sys, tempfile

import cv2
import numpy as np

EXE = os.path.expanduser(r"~\Documents\pipeline_tools\realesrgan-full\app\realesrgan-ncnn-vulkan.exe")
src, dst = sys.argv[1], sys.argv[2]
passes = int(sys.argv[3]) if len(sys.argv) > 3 else 1
asset_id = sys.argv[4] if len(sys.argv) > 4 else None
import time
t0 = time.time()

cur = src
tmp = tempfile.mkdtemp(prefix="up_")
for i in range(passes):
    out = os.path.join(tmp, f"pass{i}.png")
    subprocess.run([EXE, "-i", cur, "-o", out, "-n", "realesrgan-x4plus", "-s", "4", "-f", "png"], check=True)
    cur = out
up = cv2.imread(cur, cv2.IMREAD_COLOR)
ref = cv2.imread(src, cv2.IMREAD_COLOR)
ref_big = cv2.resize(ref, (up.shape[1], up.shape[0]), interpolation=cv2.INTER_CUBIC)

up_lab = cv2.cvtColor(up, cv2.COLOR_BGR2LAB).astype(np.float32)
ref_lab = cv2.cvtColor(ref_big, cv2.COLOR_BGR2LAB).astype(np.float32)
for c in range(3):
    u_m, u_s = up_lab[..., c].mean(), up_lab[..., c].std() + 1e-6
    r_m, r_s = ref_lab[..., c].mean(), ref_lab[..., c].std()
    up_lab[..., c] = (up_lab[..., c] - u_m) * (r_s / u_s) + r_m
out_img = cv2.cvtColor(np.clip(up_lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
cv2.imwrite(dst, out_img)
print(f"wrote {dst} {out_img.shape[1]}x{out_img.shape[0]}")
if asset_id:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import cf_common
    cfg = cf_common.load_config()
    cf_common.write_receipt(cfg, asset_id, 'upscale', tool='upscale_match.py', model={'id': 'realesrgan-x4plus (ncnn-vulkan)'}, inputs=[src],
                            params={'passes': passes, 'colour_match': 'Lab mean/std'}, timing={'wall_s': round(time.time() - t0, 1)}, outputs=[dst])
