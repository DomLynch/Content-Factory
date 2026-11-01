"""Diff the saved shape/texture features of two TRELLIS runs. Usage: latent_diff.py <a.latents.pt> <b.latents.pt>"""
import sys

import torch


def feats(path):
    s = torch.load(path, map_location="cpu", weights_only=False)["state"]
    return {k: v for k, v in s.items() if torch.is_tensor(v) and v.is_floating_point() and k.endswith("feats")}


a, b = feats(sys.argv[1]), feats(sys.argv[2])
for k in sorted(a):
    if k in b and a[k].shape == b[k].shape:
        d = (a[k].float() - b[k].float()).abs()
        print(f"{k:22s} max {float(d.max()):9.4f} mean {float(d.mean()):9.5f} (ref mean abs {float(a[k].float().abs().mean()):.3f}, {100*float(d.mean())/float(a[k].float().abs().mean()):.2f}%)")
