"""Pure-PyTorch 2-D neighborhood attention (non-causal, stride 1) with NATTEN's window rule; see _window.py for the rule.

Layout of the core functions: q, k, v are [B, H, W, N, D] (batch, rows, cols, heads, dim), the layout of natten.na2d. The K*K window is walked one
offset (ky, kx) at a time with an advanced-index gather, so the transient memory is one extra feature map per offset, never the K*K-times
expanded tensor; rows are processed in blocks sized from `max_block_elems` so 512x512 / 1024x1024 maps with 9x9 windows stay small.
The window axis is flattened row-major (ky * Kw + kx).
"""
from __future__ import annotations

import torch

from . import _window

MAX_BLOCK_ELEMS = 1 << 24   # elements of one [B, R, W, N, max(D, K*K)] block; about 64 MB in float32


def _prep(kernel_size, dilation, stride, is_causal, h, w):
    kh, kw = _window.as_pair(kernel_size, "kernel_size")
    dh, dw = _window.as_pair(dilation, "dilation")
    sh, sw = _window.as_pair(stride, "stride")
    if (sh, sw) != (1, 1):
        raise NotImplementedError("natten shim: stride != 1 is not implemented (NAF uses stride=1)")
    if is_causal not in (False, None) and not (isinstance(is_causal, (tuple, list)) and not any(is_causal)):
        raise NotImplementedError("natten shim: causal neighborhood attention is not implemented")
    rows = torch.tensor(_window.axis_table(h, kh, dh), dtype=torch.long)   # [H, Kh]
    cols = torch.tensor(_window.axis_table(w, kw, dw), dtype=torch.long)   # [W, Kw]
    return kh, kw, rows, cols


def _blocks(b, h, w, n, width, max_block_elems):
    rows_per_block = max(1, int(max_block_elems // max(1, b * w * n * width)))
    return [(y, min(y + rows_per_block, h)) for y in range(0, h, rows_per_block)]


def _qk_block(q, k, rows, cols, y0, y1, kh, kw):
    ri = rows[y0:y1].to(q.device)
    ci = cols.to(q.device)
    qb = q[:, y0:y1].float()
    out = []
    for ky in range(kh):
        a = ri[:, ky][:, None]
        for kx in range(kw):
            kk = k[:, a, ci[:, kx][None, :]].float()            # [B, R, W, N, D]
            out.append((qb * kk).sum(-1))                       # [B, R, W, N]
    return torch.stack(out, dim=-1)                             # [B, R, W, N, Kh*Kw]


def _av_block(attn, v, rows, cols, y0, y1, kh, kw):
    ri = rows[y0:y1].to(v.device)
    ci = cols.to(v.device)
    acc = None
    for ky in range(kh):
        a = ri[:, ky][:, None]
        for kx in range(kw):
            vv = v[:, a, ci[:, kx][None, :]].float()            # [B, R, W, N, Dv]
            term = attn[..., ky * kw + kx].unsqueeze(-1) * vv
            acc = term if acc is None else acc + term
    return acc


def _check(q, k, v=None):
    if q.ndim != 5 or k.shape[:3] != q.shape[:3] or k.shape[3] != q.shape[3] or k.shape[4] != q.shape[4]:
        raise ValueError(f"expected q,k of shape [B,H,W,N,D] with equal shapes, got {tuple(q.shape)} and {tuple(k.shape)}")
    if v is not None and v.shape[:4] != q.shape[:4]:
        raise ValueError(f"v must be [B,H,W,N,Dv] matching q, got {tuple(v.shape)}")


def qk(q, k, kernel_size, dilation=1, max_block_elems=MAX_BLOCK_ELEMS):
    """Raw (unscaled) attention logits: [B, H, W, N, Kh*Kw] in q's dtype."""
    _check(q, k)
    b, h, w, n, d = q.shape
    kh, kw, rows, cols = _prep(kernel_size, dilation, 1, False, h, w)
    parts = [_qk_block(q, k, rows, cols, y0, y1, kh, kw).to(q.dtype) for y0, y1 in _blocks(b, h, w, n, max(d, kh * kw), max_block_elems)]
    return torch.cat(parts, dim=1)


def av(attn, v, kernel_size, dilation=1, max_block_elems=MAX_BLOCK_ELEMS):
    """Weighted sum of the window values: attn [B,H,W,N,Kh*Kw], v [B,H,W,N,Dv] -> [B,H,W,N,Dv]."""
    b, h, w, n, dv = v.shape
    kh, kw, rows, cols = _prep(kernel_size, dilation, 1, False, h, w)
    if attn.shape != (b, h, w, n, kh * kw):
        raise ValueError(f"attn must be {(b, h, w, n, kh * kw)}, got {tuple(attn.shape)}")
    parts = [_av_block(attn[:, y0:y1].float(), v, rows, cols, y0, y1, kh, kw).to(v.dtype)
             for y0, y1 in _blocks(b, h, w, n, max(dv, kh * kw), max_block_elems)]
    return torch.cat(parts, dim=1)


def na2d(q, k, v, kernel_size, dilation=1, is_causal=False, scale=None, stride=1, max_block_elems=MAX_BLOCK_ELEMS):
    """Fused neighborhood attention: softmax(scale * q.k over the window) . v, with scale = D ** -0.5 when None (natten's default)."""
    _check(q, k, v)
    b, h, w, n, d = q.shape
    dv = v.shape[-1]
    kh, kw, rows, cols = _prep(kernel_size, dilation, stride, is_causal, h, w)
    s = d ** -0.5 if scale is None else scale
    parts = []
    for y0, y1 in _blocks(b, h, w, n, max(d, dv, kh * kw), max_block_elems):
        logits = _qk_block(q, k, rows, cols, y0, y1, kh, kw) * s
        parts.append(_av_block(logits.softmax(dim=-1), v, rows, cols, y0, y1, kh, kw).to(q.dtype))
    return torch.cat(parts, dim=1)
