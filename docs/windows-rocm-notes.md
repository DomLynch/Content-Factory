# Windows ROCm notes (ASUS ROG Flow Z13, Ryzen AI Max+ 395, Radeon 8060S / gfx1151)

Recorded 2026-10-31 from the first run on the laptop. Fork at 06f1518, Python 3.11.9,
torch 2.11.0+rocm7.13.0 from https://repo.amd.com/rocm/whl/gfx1151/ (Windows cp311 wheel),
`ROCM_PORTS=0 bash setup_rocm.sh` (no native-port wheels).

## Background remover fails on the GPU (do not chase this)

Running the in-pipeline BiRefNet on the GPU fails inside `BiRefNet.__call__`:

```
MIOpen(HIP): Error [Compile] 'hiprtcCompileProgram(...)' MIOpenBatchNormFwdInferSpatial.cpp: HIPRTC_ERROR_COMPILATION (6)
C:\Users\...\comgr-...\include\miopen_type_traits.hpp:151:10: fatal error: 'type_traits' file not found
1 error generated when compiling for gfx1151.
MIOpen Error: ... hipoc_program.cpp:299: Code object build failed. Source: MIOpenBatchNormFwdInferSpatial.cpp
RuntimeError: miopenStatusUnknownError
```

MIOpen runtime-compiles its batch-norm kernel and the HIP runtime compiler on Windows cannot find
the C++ standard headers. That is an AMD packaging problem. We do not install ROCm devel headers
or change the system for it.

Standing workaround: cut the background out on the CPU first, then generate with the remover off.

```
python tools/cutout_cpu.py photo.png photo_cut.png     # BiRefNet (MIT) on the CPU, ~100 s
TRELLIS3_REMBG=none python generate.py photo_cut.png --preset draft --device rocm
```

`cutout_cpu.py` warns when the subject image is under 1024 px (project input gate).

## Other facts from the first runs

- The BiRefNet remote code needs `einops` (`pip install einops` in the venv).
- `TRELLIS3_REMBG=ZhengPeng7/BiRefNet` needs fork commit 06f1518 or newer.
- IPv6 is broken on this network: Hugging Face downloads must force IPv4.
- GPU memory after the 32 GB GPU / 32 GB RAM split: torch reports 43.9 GB and held 42 GB
  (30.8 GB dedicated + ~13 GB shared spill) in an allocation probe (`tools/probe_gpu_mem.py`).
- Draft preset, `assets/shoe_front.png`: total 244.5 s (coords 92 s, shape 46 s, tex 26 s, decode 10 s),
  1,937,178 triangles. Output comes out lying on its side (needs an alignment step before rig binding).

## Benchmark, 2026-10-31 (venv A: Python 3.11, torch 2.11.0+rocm7.13.0, conv_none; GPU 32 GB split)

`generate.py assets/shoe_front.png --preset master --device rocm --single-process --profile-memory`
(TRELLIS3_REMBG=none, no --attn-tile). Total 4,699 s = sampling 3,465 s + decode ~50 s + CPU bake/export 1,182 s.
Peaks: GPU reserved 42.9 GB (shape_hr), GPU allocated 30.7 GB (shape_up), process RSS 19.3 GB (load).
Stage seconds: load 339, coords 156, shape_lr 104, shape_up 19, shape_hr 2,202 (~220 s/step, the limiter),
tex 644, decode_shape 28, decode_tex 20, export (CPU bake) 1,182. Output: 999,346 faces, 4096 base colour + normal.
Visual: shape good; colour dark/grainy (sample input is itself a grainy red render; judge on a real photo).
For non-final profile runs use `--preset master --texture-size 2048 --bake-faces 500000`; hero is for finals.

Venv B (Python 3.12.10, torch 2.13.0+rocm10.0.0, flex_gemm native): draft sampling 108-115 s vs 229 s (about 2x).
Open items: (1) `ATTN_IMPL=aule` fails: `AssertionError assert q.dim() == 4` in aule/triton_flash.py:533 via
t3_sdpa.py:164 (sdpa_aule passes 3-D tensors); not used. (2) Decode/export on B fails with
`fatal error: 'stdlib.h' file not found` (hipcc JIT; needs MSVC/Windows SDK headers; VS 2022 Build Tools installed).
(3) `import o_voxel` failed with `ModuleNotFoundError: No module named 'plyfile'`; `pip install plyfile` fixes the import.


## Update
- Native path (venv B) export FAILURE IS RESOLVED: with VS 2022 Build Tools (VCTools workload) installed, draft decode/export works: draft total 174.1 s vs 244.5 s on venv A. Reduced master on the clean 1076 px robot (2048 tex, 500k faces): total 412.8 s, GPU bake 98 s vs 1,182 s on the CPU bake.
- Audit finding to verify: the attention auto-tiler default (6 GB) may have slowed shape_hr on venv A; benchmarks with --attn-auto-gb 24/16 queued.
