# Detail normal map from the base colour (height = high-passed luminance).
# Replaces the hi->lo geometric bake, which came out inverted on TRELLIS's
# mixed-facing thin shells (wings, loincloth).
import sys, numpy as np, cv2
src, dst = sys.argv[1], sys.argv[2]
strength = float(sys.argv[3]) if len(sys.argv) > 3 else 2.5
img = cv2.imread(src).astype(np.float32) / 255
lum = img @ np.array([0.114, 0.587, 0.299], np.float32)
h = lum - cv2.GaussianBlur(lum, (0, 0), 12)          # remove large-scale shading
h = cv2.GaussianBlur(h, (0, 0), 0.8)
dx = cv2.Sobel(h, cv2.CV_32F, 1, 0, ksize=3)
dy = cv2.Sobel(h, cv2.CV_32F, 0, 1, ksize=3)
n = np.dstack([-dx * strength, dy * strength, np.ones_like(h)])  # glTF: +Y up in tangent space; image rows go down
n /= np.linalg.norm(n, axis=2, keepdims=True)
out = ((n * 0.5 + 0.5) * 255).astype(np.uint8)
cv2.imwrite(dst, out[..., ::-1])  # RGB -> BGR for cv2
print("normal z mean", float(n[..., 2].mean()))
