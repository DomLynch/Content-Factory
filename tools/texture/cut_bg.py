# Background cut for the front picture with public BiRefNet (ZhengPeng7/BiRefNet).
import sys, torch
sys.path.insert(0, "TRELLIS.2")
from PIL import Image
from trellis2.pipelines.rembg.BiRefNet import BiRefNet

src, dst = sys.argv[1], sys.argv[2]
device = sys.argv[3] if len(sys.argv) > 3 else "cpu"  # GPU rembg fails on this Windows/ROCm laptop; cpu works (about 105 s)
m = BiRefNet("ZhengPeng7/BiRefNet").to(torch.device(device))
im = m(Image.open(src).convert("RGB"))
im.save(dst)
a = im.split()[-1]
print("alpha bbox", a.getbbox(), "size", im.size)

