"""Pure-PyTorch stand-in for the parts of NATTEN (SHI-Labs, MIT) that valeoai/NAF (Apache-2.0) calls: 2-D neighborhood attention.

Why: NAF's hubconf.py declares dependencies=['torch','natten'] and torch.hub refuses to load it without a module named natten; real natten is
CUDA-only (no ROCm build). This package is put FIRST on sys.path by tools/pixal3d_entry.py (or via PYTHONPATH) and is NOT the real library:
no fused kernels, no backend choice (the `backend` kwarg is accepted and ignored), no stride > 1, no causal masking, no relative position bias.
Same window rule and scaling as natten's na2d (see _window.py); it is slower than the CUDA kernels but runs on any torch device.
"""
from . import _na2d, functional
from ._na2d import na2d as _na2d_core

__version__ = "0.17.5+pixal3d.purepytorch.shim"
IS_SHIM = True
__all__ = ["na2d", "functional", "__version__", "IS_SHIM"]


def na2d(query, key, value, kernel_size, dilation=1, is_causal=False, scale=None, stride=1, **kwargs):
    """natten.na2d: query/key/value [B, H, W, heads, dim]; kwargs such as backend=, q_tile_shape= are accepted and ignored."""
    return _na2d_core(query, key, value, kernel_size, dilation=dilation, is_causal=is_causal, scale=scale, stride=stride)
