r"""Reference-stream cache for diffusers' Flux3Transformer2DModel (plugin 0.1.6, part B). Zero pixel change by construction.

What it caches. In Flux3Transformer2DModel.forward the reference image goes through its own per-mode blocks (`self.mode_blocks["image_cond"]`, step 5 of forward) BEFORE it ever
meets the image tokens. Those blocks see only the reference latents, the reference's modulation (zero timestep embedding + the target-timestep embedding) and its rotary
embedding, so for one reference they give the same tensor on every call that shares the target timestep, in every cell of a queue that runs in one process. The patch runs the chain once
per distinct input and returns the very same tensor afterwards. Nothing is approximated: the cache key is a blake2b hash of the exact input bytes (reference tokens, modulation, rotary),
so a different reference, timestep, dtype or shape can never hit, and a hit returns the stored output of the identical computation.

How it hooks in. `install(transformer)` swaps `transformer.mode_blocks["image_cond"]` (an iterated ModuleList) for a wrapper whose iteration yields one cached chain module; forward() itself is
untouched, so nothing of diffusers is copied. Skipped when gradients are enabled (inference only). `uninstall()` puts the original list back. Apply only when config flux.cache_ref_conds is on:
`install_if_enabled(transformer, cfg)`.

NOT done here (awaiting a GPU window and a measurement): skipping the reference's queries and feed-forward in the LAST joint block, whose reference output is discarded. That changes matmul and
attention shapes, which can change low bits on this stack, so it must not ride under a "bit-identical" switch until the Factory's pixel diff on head_front proves it; it would need its own key.

Verification belongs to the Factory: same seed, cache off vs on, pixel diff on head_front must be 0. The unit tests (plugins/factory/tests/test_flux.py) cover the cache logic with fake tensors; the
torch path is exercised only when torch is installed, and the real model has NOT been run by this module's author.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict

STATS = {"hits": 0, "misses": 0}


def _bytes(t) -> bytes:
    x = t.detach().contiguous().cpu()
    try:
        return x.numpy().tobytes()
    except TypeError:   # bfloat16 has no numpy dtype: same bytes as int16
        import torch
        return x.view(torch.int16).numpy().tobytes()


def fingerprint(*parts) -> str:
    """blake2b over the exact bytes, dtype and shape of every tensor (tuples are walked), plus plain values."""
    h = hashlib.blake2b(digest_size=20)
    stack = list(parts)
    while stack:
        p = stack.pop(0)
        if isinstance(p, (tuple, list)):
            stack[0:0] = list(p)
        elif p is None or isinstance(p, (int, float, str, bool)):
            h.update(repr(p).encode())
        else:
            h.update(f"{getattr(p, 'dtype', '')}|{tuple(getattr(p, 'shape', ()))}|{getattr(p, 'device', '')}".encode())
            h.update(_bytes(p))
    return h.hexdigest()


class RefStreamCache:
    """LRU of {fingerprint: output}. `run(blocks, x, mod, rot)` is the cached chain."""

    def __init__(self, max_entries: int = 64):
        self.max_entries = max_entries
        self.store: OrderedDict[str, object] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def run(self, blocks, x, mod, rot):
        key = fingerprint(x, mod, rot)
        if key in self.store:
            self.store.move_to_end(key)
            self.hits += 1
            STATS["hits"] += 1
            return self.store[key]
        out = x
        for b in blocks:
            out = b(out, mod, rot)
        self.misses += 1
        STATS["misses"] += 1
        self.store[key] = out
        while len(self.store) > self.max_entries:
            self.store.popitem(last=False)
        return out

    def clear(self) -> None:
        self.store.clear()


def install(transformer, max_entries: int = 64) -> RefStreamCache:
    """Wrap transformer.mode_blocks['image_cond']; returns the cache (cache.hits / cache.misses for the receipt)."""
    import torch

    cache = RefStreamCache(max_entries)
    orig = transformer.mode_blocks["image_cond"]

    class Chain(torch.nn.Module):
        def __init__(self, blocks):
            super().__init__()
            self.blocks = blocks

        def forward(self, x, mod, rot):
            if torch.is_grad_enabled():
                out = x
                for b in self.blocks:
                    out = b(out, mod, rot)
                return out
            return cache.run(self.blocks, x, mod, rot)

    class Wrapper(torch.nn.Module):
        def __init__(self, blocks):
            super().__init__()
            self.orig = blocks
            self.chain = Chain(blocks)

        def __iter__(self):
            return iter([self.chain])

        def __len__(self):
            return 1

    transformer.mode_blocks["image_cond"] = Wrapper(orig)
    transformer._ref_cache_original = orig
    transformer._ref_cache = cache
    return cache


def uninstall(transformer) -> None:
    orig = getattr(transformer, "_ref_cache_original", None)
    if orig is not None:
        transformer.mode_blocks["image_cond"] = orig
        transformer._ref_cache_original = None
        transformer._ref_cache = None


def install_if_enabled(transformer, cfg: dict):
    """Install only when config flux.cache_ref_conds is on; returns the cache or None."""
    if (cfg.get("flux") or {}).get("cache_ref_conds"):
        return install(transformer)
    return None
