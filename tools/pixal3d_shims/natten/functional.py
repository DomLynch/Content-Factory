"""Legacy natten.functional API (natten 0.14 to 0.17): na2d_qk / na2d_av on the [B, heads, H, W, D] layout.

NAF's attentions.py does `from natten.functional import na2d_av, na2d_qk` first and takes this legacy path when it works (it also supports
return_weights); only if that import fails does it fall back to the recent `natten.na2d(..., backend="cutlass-fna")`. The shim provides both.
Legacy semantics: na2d_qk returns UNSCALED logits [B, heads, H, W, Kh*Kw] (the caller scales and softmaxes), na2d_av takes the weights.
"""
from __future__ import annotations

from . import _na2d


def na2d_qk(query, key, kernel_size, dilation=1, rpb=None, **kwargs):
    if rpb is not None:
        raise NotImplementedError("natten shim: relative position bias is not implemented (NAF does not use it)")
    q, k = (x.permute(0, 2, 3, 1, 4) for x in (query, key))     # [B,N,H,W,D] -> [B,H,W,N,D]
    return _na2d.qk(q, k, kernel_size, dilation).permute(0, 3, 1, 2, 4)


def na2d_av(attn, value, kernel_size, dilation=1, **kwargs):
    a = attn.permute(0, 2, 3, 1, 4)
    v = value.permute(0, 2, 3, 1, 4)
    return _na2d.av(a, v, kernel_size, dilation).permute(0, 3, 1, 2, 4)
