# Pull TRELLIS base-colour / metal-rough maps out of the GLB and inpaint the black
# pinholes the voxel bake leaves (texels with no voxel hit come out ~black).
import sys, numpy as np, cv2, trimesh
src, outdir = sys.argv[1], sys.argv[2]
prefix = sys.argv[3] if len(sys.argv) > 3 else "asset"
s = trimesh.load(src); g = list(s.geometry.values())[0]; m = g.visual.material
base = np.array(m.baseColorTexture.convert("RGB"))
mr = np.array(m.metallicRoughnessTexture.convert("RGB"))
v = base.max(2)
holes = (v < 14).astype(np.uint8)
# only small pinholes; big dark regions are real (gaps between UV islands are handled by dilation already)
n, lab, stats, _ = cv2.connectedComponentsWithStats(holes, 8)
small = np.isin(lab, [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] < 400]).astype(np.uint8)
small = cv2.dilate(small, np.ones((3, 3), np.uint8))
print("pinhole texels:", int(small.sum()), "of", small.size)
fixed = cv2.inpaint(base[..., ::-1], small, 4, cv2.INPAINT_TELEA)
cv2.imwrite(f"{outdir}/{prefix}_basecolor.png", fixed)
print("TRELLIS metal mean", mr[..., 2].mean() / 255, "rough mean", mr[..., 1].mean() / 255, "(metal ~1 everywhere: replaced)")
# own metal/rough from colour: gold/brass trim = metal, skin/horn/cloth = dielectric
hsv = cv2.cvtColor(fixed, cv2.COLOR_BGR2HSV).astype(np.float32)
h, s_, v_ = hsv[..., 0] * 2, hsv[..., 1] / 255, hsv[..., 2] / 255
ss = lambda a, b, x: np.clip((x - a) / (b - a), 0, 1) ** 2 * (3 - 2 * np.clip((x - a) / (b - a), 0, 1))
gold = ss(0.22, 0.4, s_) * ss(0.30, 0.5, v_) * ss(18, 30, h) * (1 - ss(55, 70, h))
gold = cv2.GaussianBlur(gold, (0, 0), 1.2)
red = ss(0.35, 0.6, s_) * ((h < 15) | (h > 340)).astype(np.float32)  # membranes / wet flesh
metal = np.clip(gold * 1.0, 0, 1)
detail = cv2.Laplacian(cv2.GaussianBlur(v_, (0, 0), 1.0), cv2.CV_32F)
rough = 0.68 - 0.30 * gold - 0.12 * red + np.clip(np.abs(detail) * 2.0, 0, 0.12)
rough = np.clip(rough, 0.22, 0.9)
out = np.zeros_like(fixed)  # BGR -> B=metal, G=rough, R=0 (glTF packing in RGB: G rough, B metal)
out[..., 0] = (metal * 255).astype(np.uint8); out[..., 1] = (rough * 255).astype(np.uint8)
cv2.imwrite(f"{outdir}/{prefix}_mr.png", out)
print("own metal mean", metal.mean(), "rough mean", rough.mean())

