"""Tiled-vs-plain SDPA exactness on THIS GPU, bf16, at a TRELLIS-sized shape. GPU, takes seconds. Run with no other GPU job.
Usage: attn_exactness.py [L=20167] [H=4] [D=64]
Both bf16 results are compared with a float32 reference (smaller head count keeps the 32-bit score matrix in memory), and tiled is compared with plain."""
import os, sys, time

import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.expanduser(r"~\Documents\Trellis-3-312"))
from backends import attention_mps as am

L = int(sys.argv[1]) if len(sys.argv) > 1 else 20167
H = int(sys.argv[2]) if len(sys.argv) > 2 else 4
D = int(sys.argv[3]) if len(sys.argv) > 3 else 64
dev = "cuda"
g = torch.Generator(device="cpu").manual_seed(0)
q, k, v = (torch.randn(1, H, L, D, generator=g).to(dev) for _ in range(3))
qb, kb, vb = (t.to(torch.bfloat16) for t in (q, k, v))

t0 = time.time(); plain = F.scaled_dot_product_attention(qb, kb, vb).float(); torch.cuda.synchronize(); t_plain = time.time() - t0
t0 = time.time(); tiled = am.sdpa_tiled(qb, kb, vb, 2048, 2048).float(); torch.cuda.synchronize(); t_tiled = time.time() - t0
ref = F.scaled_dot_product_attention(q, k, v).float()

def st(a, b):
    d = (a - b).abs(); return f"max {float(d.max()):.2e} mean {float(d.mean()):.2e}"
print(f"shape B=1 H={H} L=S={L} D={D} bf16 on {torch.cuda.get_device_name(0)}")
print("tiled vs plain (bf16):  ", st(tiled, plain), f"  (tolerance 1e-3 max: {'PASS' if float((tiled - plain).abs().max()) <= 1e-3 else 'FAIL'})")
print("plain bf16 vs fp32 ref: ", st(plain, ref))
print("tiled bf16 vs fp32 ref: ", st(tiled, ref))
print(f"time plain {t_plain:.2f}s tiled {t_tiled:.2f}s, peak GPU {torch.cuda.max_memory_allocated()/2**30:.1f} GB")
