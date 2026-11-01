"""Run Pixal3D (TencentARC, MIT) single-view inference WITHOUT editing the upstream clone. Started by tools/run_pixal3d.py under the Pixal3D venv.

Usage: pixal3d_entry.py <image> <output.glb> --repo DIR --model-dir DIR --dino-dir DIR --moge-dir DIR --birefnet-dir DIR [--seed N] [--resolution N] [--low-vram]

What it changes at run time, in memory only (the upstream files stay as cloned, so `git status` in the clone stays clean):
  - the DINOv3 and MoGe-2 weights are read from local directories (no network, no token);
  - the background-removal model in the weights' pipeline.json is briaai/RMBG-2.0, whose licence (bria-rmbg-2.0) is non-commercial: pipeline.json is rewritten once to the MIT BiRefNet
    (ZhengPeng7/BiRefNet, local dir), the original kept as pipeline.json.orig;
  - ATTN_BACKEND is sdpa unless the caller set it (flash-attn and natten are CUDA-only; the upstream code does not import natten at all and takes flash-attn only when asked).
"""
import argparse
import json
import os
import shutil
import sys


def ensure_licence_clean_rembg(model_dir: str, birefnet_dir: str) -> str:
    """Rewrite pipeline.json from RMBG-2.0 (non-commercial) to the local MIT BiRefNet; returns the model name now in force."""
    p = os.path.join(model_dir, "pipeline.json")
    d = json.load(open(p, encoding="utf-8"))
    rb = d["args"]["rembg_model"]
    if "RMBG" in str(rb["args"].get("model_name", "")):
        shutil.copyfile(p, p + ".orig")
        rb["name"] = "BiRefNet"
        rb["args"]["model_name"] = birefnet_dir
        json.dump(d, open(p, "w", encoding="utf-8"), indent=2)
    return rb["args"]["model_name"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("output")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--dino-dir", required=True)
    ap.add_argument("--moge-dir", required=True)
    ap.add_argument("--birefnet-dir", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--resolution", type=int, default=-1)
    ap.add_argument("--low-vram", action="store_true")
    a = ap.parse_args()

    os.environ.setdefault("ATTN_BACKEND", "sdpa")
    os.environ.setdefault("SPARSE_ATTN_BACKEND", "sdpa")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    sys.path.insert(0, a.repo)
    os.chdir(a.repo)
    print("rembg model:", ensure_licence_clean_rembg(a.model_dir, a.birefnet_dir), flush=True)

    import inference as P   # upstream inference.py (its __main__ block does not run on import)

    for conf in P.IMAGE_COND_CONFIGS.values():
        conf["model_name"] = a.dino_dir
    real_moge = P.load_moge_model
    P.load_moge_model = lambda *args, **kw: real_moge(*args[:1], model_name=a.moge_dir)

    P.run_inference(image_path=a.image, output_path=a.output, seed=a.seed, manual_fov=-1.0, model_path=a.model_dir,
                    low_vram=a.low_vram, resolution=a.resolution)


if __name__ == "__main__":
    main()
