"""tex_inpaint.py <mesh.glb> <out.glb> [lum=8] [maxpx=500]
Demon 2 v18 cleanup row (asm_stage_a.py H1_INPAINT block), generalised to any single-mesh GLB: fill small isolated near-black blobs in the
base-colour texture (only texels covered by a face in UV space), TELEA inpaint; larger black regions (real dark cloth or hair) are left alone.
CPU only. Prints and writes <out>.inpaint.json with near_black_before_pct / near_black_after_pct. The source GLB is never modified."""
import json, sys
import cv2, numpy as np, trimesh
from PIL import Image

src, dst = sys.argv[1], sys.argv[2]
LUM = float(sys.argv[3]) if len(sys.argv) > 3 else 8
MAXPX = int(sys.argv[4]) if len(sys.argv) > 4 else 500
scene = trimesh.load(src)
g = list(scene.geometry.values())[0] if hasattr(scene, 'geometry') else scene
mat = g.visual.material
tex = mat.baseColorTexture.convert('RGB')
a = np.asarray(tex).copy()
H, W = a.shape[:2]
uv, faces = np.asarray(g.visual.uv, np.float64), np.asarray(g.faces)
m = np.zeros((H, W), np.uint8)
for tri in faces:
    p = np.stack([uv[tri][:, 0] * (W - 1), (1 - uv[tri][:, 1]) * (H - 1)], 1)
    cv2.fillConvexPoly(m, p.round().astype(np.int32), 255)
blk = ((a.mean(2) < LUM) & (m > 0)).astype(np.uint8)
n, lb, st, _ = cv2.connectedComponentsWithStats(blk)
small = [i for i in range(1, n) if st[i, 4] <= MAXPX]
sel = np.isin(lb, small).astype(np.uint8)
sel = cv2.dilate(sel, np.ones((3, 3), np.uint8), iterations=2)
fixed = cv2.inpaint(a, sel * 255, 3, cv2.INPAINT_TELEA)
after = ((fixed.mean(2) < LUM) & (m > 0)).sum()
stat = {'mesh': src, 'tex': [W, H], 'near_black_before_pct': round(100 * blk.sum() / (m > 0).sum(), 3),
        'near_black_after_pct': round(100 * after / (m > 0).sum(), 3), 'blobs_total': n - 1, 'blobs_filled': len(small),
        'max_blob_px': MAXPX, 'lum_threshold': LUM}
mat.baseColorTexture = Image.fromarray(fixed)
scene.export(dst)
json.dump(stat, open(dst + '.inpaint.json', 'w'), indent=1)
print(json.dumps(stat))
