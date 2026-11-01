# Benchmarks (ROG Flow Z13, Ryzen AI Max+ 395, gfx1151, 32 GB GPU / 32 GB RAM)

Robot sample, reduced master (`--preset master --texture-size 2048 --bake-faces 500000`), 2026-11-01.

| Row | Venv | Attention budget | shape_hr (s) | Total (s) | Result |
|---|---|---|---|---|---|
| earlier default run | native (py3.12, rocm10) | default | 42.5 | 412.8 | ok |
| attn 24 | native | `--attn-auto-gb 24` | 73.4 | 409.7 | ok |
| attn 16 | native | `--attn-auto-gb 16` | 78.0 | 423.0 | ok |
| default (rerun) | native | default | n/a | n/a | killed by RAM guard at model load (4 GB limit) |
| attn 24 | fallback (py3.11) | `--attn-auto-gb 24` | n/a | n/a | killed by RAM guard at model load |
| attn 24 + tokens 98304 | native | `--attn-auto-gb 24 --max-tokens 98304` | n/a | n/a | killed by RAM guard at model load |

Reference: master shoe on the fallback venv: shape_hr 2,202 s (about 220 s per step), total 4,699 s.

Reading: on the native venv a larger attention budget did not help (42 to 78 s). The roughly 220 s per step tiling penalty was a fallback-venv problem, so the fallback is fallback-only and its remaining rows are not run (Strategy ruling). The one row still worth running is native, reduced master, `--max-tokens 98304`, guard at 2 GB, for quality and peak memory.

Flux klein notes: 2048x1536 untiled 251 s, peak 36.2 GB, free RAM hit 0.0 GB. Tiled peak 24.8 GB, free RAM still fell to 0.1 GB (its 21,693 s time was sleep, not compute). Default for sheets: 1.5 MP. Kit renders at 1024-1664 px: 50-120 s each warm plus about 35 s model load; reference-edited full-body views about 5 min.
