"""Pure-Python (no torch) window rule of NATTEN 2-D neighborhood attention. Imported by the shim and by the tests.

NATTEN rule (Hassani et al., Neighborhood Attention Transformer, CVPR 2023; natten csrc `get_window_start`): a query at position i on an axis of
length L with odd kernel size K (radius r = K // 2) attends K positions. The window is centred on i, and where it would cross the border it is
SHIFTED to stay inside (so every query always sees exactly K keys; nothing is zero padded):

    start = max(i - r, 0) + (i + r >= L) * (L - i - r - 1)

With dilation d the axis is split into d interleaved sub-sequences (positions with the same i % d); a query is windowed inside its own
sub-sequence, in sub-sequence coordinates, and the key position is mapped back with `g + d * (start + k)`, g = i % d.
NATTEN requires every sub-sequence to hold at least K positions (L >= K * d); the shim raises ValueError otherwise, as NATTEN does.
"""
from __future__ import annotations


def window_start(i: int, length: int, kernel_size: int) -> int:
    """First key position of the window of query i on an axis of `length` (no dilation)."""
    if kernel_size % 2 != 1 or kernel_size < 1:
        raise ValueError(f"kernel_size must be a positive odd number, got {kernel_size}")
    if length < kernel_size:
        raise ValueError(f"axis length {length} is smaller than kernel_size {kernel_size}")
    if not 0 <= i < length:
        raise ValueError(f"position {i} outside 0..{length - 1}")
    r = kernel_size // 2
    return max(i - r, 0) + (1 if i + r >= length else 0) * (length - i - r - 1)


def window_indices(i: int, length: int, kernel_size: int, dilation: int = 1) -> list[int]:
    """The K key positions (absolute, ascending) that query i attends to on one axis."""
    if dilation < 1:
        raise ValueError(f"dilation must be >= 1, got {dilation}")
    if length < kernel_size * dilation:
        raise ValueError(f"axis length {length} is smaller than kernel_size*dilation = {kernel_size * dilation}")
    g, j = i % dilation, i // dilation
    sub_len = (length - g + dilation - 1) // dilation
    s = window_start(j, sub_len, kernel_size)
    return [g + dilation * (s + k) for k in range(kernel_size)]


def axis_table(length: int, kernel_size: int, dilation: int = 1) -> list[list[int]]:
    """window_indices for every position of the axis: a length x K table."""
    return [window_indices(i, length, kernel_size, dilation) for i in range(length)]


def as_pair(value, name: str) -> tuple[int, int]:
    """int or 2-sequence -> (a, b) ints."""
    if isinstance(value, int):
        return value, value
    value = tuple(value)
    if len(value) != 2 or not all(isinstance(v, int) for v in value):
        raise ValueError(f"{name} must be an int or a pair of ints, got {value!r}")
    return value
