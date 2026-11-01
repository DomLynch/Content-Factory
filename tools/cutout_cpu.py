"""Cut the background out of a photo on the CPU (BiRefNet, MIT) and save an RGBA PNG.

The GPU path of the in-pipeline background remover fails on Windows ROCm (MIOpen cannot
runtime-compile its batch-norm kernel: <type_traits> not found). Run this first, then
generate with TRELLIS3_REMBG=none on the RGBA file.

    python tools/cutout_cpu.py photo.png [out.png]
"""
import os
import sys

import torch
from PIL import Image
from torchvision import transforms
from transformers import AutoModelForImageSegmentation

MODEL = os.environ.get("TRELLIS3_REMBG", "ZhengPeng7/BiRefNet")
GATE_PX = 1024


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + "_cut.png"
    image = Image.open(src).convert("RGB")
    print(f"input {src}: {image.size[0]}x{image.size[1]}")
    if max(image.size) < GATE_PX:
        print(f"WARNING: subject image is under {GATE_PX} px; the project input gate wants >= {GATE_PX} px")

    model = AutoModelForImageSegmentation.from_pretrained(MODEL, trust_remote_code=True).eval()
    prep = transforms.Compose([
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    with torch.no_grad():
        pred = model(prep(image).unsqueeze(0))[-1].sigmoid()[0].squeeze()
    mask = transforms.ToPILImage()(pred.float()).resize(image.size)
    image.putalpha(mask)
    image.save(dst)
    covered = sum(1 for a in mask.getdata() if a > 127) / (image.size[0] * image.size[1])
    print(f"saved {dst}  (foreground {covered:.0%} of the frame)")


if __name__ == "__main__":
    main()
