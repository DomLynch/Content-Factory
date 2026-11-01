"""Run Pixal3D (TencentARC, MIT) single-view inference WITHOUT editing the upstream clone. Started by tools/run_pixal3d.py under the Pixal3D venv.

Usage: pixal3d_entry.py <image> <output.glb> --repo DIR --model-dir DIR --dino-dir DIR --moge-dir DIR --birefnet-dir DIR [--seed N] [--resolution N] [--low-vram]

What it changes at run time, in memory only (the upstream files stay as cloned, so `git status` in the clone stays clean):
  - the DINOv3 and MoGe-2 weights are read from local directories (no network, no token);
  - the background-removal model in the weights' pipeline.json is briaai/RMBG-2.0, whose licence (bria-rmbg-2.0) is non-commercial: pipeline.json is rewritten once to the MIT BiRefNet
    (ZhengPeng7/BiRefNet, local dir), the original kept as pipeline.json.orig;
  - ATTN_BACKEND is sdpa unless the caller set it (flash-attn is only taken when asked);
  - NAF (valeoai/NAF, loaded by torch.hub in _load_naf) needs the CUDA-only `natten`: tools/pixal3d_shims/natten (pure PyTorch, same window rule) is put FIRST on sys.path
    (--shim-dir, default next to this script; PYTHONPATH=<that dir> does the same for other launchers), and TORCH_HOME is set to --torch-home so the hub cache is in one stable folder;
  - --no-naf: the ROUGH fallback. `use_naf_upsample=False` cannot be used (the shipped flow models expect 2*1024 projection channels = lr ++ hr, so 1024 would crash their proj_linear),
    so _load_naf is replaced by a plain bicubic upsample of the DINOv3 feature map: same channel count, no hub download, no natten. Quality is lower: label it ROUGH / NAF-off.
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


DEFAULT_SHIM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pixal3d_shims")
NAF_HUB_DIR = "valeoai_NAF_main"
NAF_CHECKPOINT = "naf_release.pth"


def naf_cache_report(torch_home: str) -> str:
    """One line saying whether the torch.hub cache already holds NAF's code and checkpoint (what Models Dev pre-fetches); nothing is downloaded here."""
    if not torch_home:
        return "NAF cache: TORCH_HOME not set by the entry (torch default applies)"
    code = os.path.isfile(os.path.join(torch_home, "hub", NAF_HUB_DIR, "hubconf.py"))
    ckpt = os.path.isfile(os.path.join(torch_home, "hub", "checkpoints", NAF_CHECKPOINT))
    return f"NAF cache in {torch_home}: code {'present' if code else 'MISSING (torch.hub will download it)'}, {NAF_CHECKPOINT} {'present' if ckpt else 'MISSING (torch.hub will download it)'}"


def install_plain_upsample(module) -> None:
    """Replace DinoV3ProjFeatureExtractor._load_naf (in the given image_conditioned_proj module) by a bicubic upsample with NAF's call signature and output shape."""
    import torch
    import torch.nn.functional as F

    class PlainUpsample(torch.nn.Module):
        def forward(self, image, features, output_size, *args, **kwargs):
            out = F.interpolate(features.float(), size=tuple(output_size), mode="bicubic", align_corners=False)
            return out.to(features.dtype)

    def _load_naf(self):
        if self.naf_model is None:
            self.naf_model = PlainUpsample().eval()

    module.DinoV3ProjFeatureExtractor._load_naf = _load_naf


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
    ap.add_argument("--shim-dir", default=DEFAULT_SHIM_DIR, help="directory holding the pure-PyTorch natten package, put first on sys.path ('' = do not)")
    ap.add_argument("--torch-home", default="", help="TORCH_HOME for the torch.hub cache of valeoai/NAF and naf_release.pth ('' = leave unset)")
    ap.add_argument("--no-naf", action="store_true", help="ROUGH fallback: bicubic feature upsample instead of NAF")
    a = ap.parse_args()

    os.environ.setdefault("ATTN_BACKEND", "sdpa")
    os.environ.setdefault("SPARSE_ATTN_BACKEND", "sdpa")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    if a.torch_home:
        os.makedirs(a.torch_home, exist_ok=True)
        os.environ["TORCH_HOME"] = a.torch_home
    sys.path.insert(0, a.repo)
    if a.shim_dir and not a.no_naf:
        sys.path.insert(0, a.shim_dir)   # first of all: `import natten` must find the shim, never a stray build
    os.chdir(a.repo)
    print(naf_cache_report(a.torch_home), flush=True)
    print("rembg model:", ensure_licence_clean_rembg(a.model_dir, a.birefnet_dir), flush=True)

    import inference as P   # upstream inference.py (its __main__ block does not run on import)

    for conf in P.IMAGE_COND_CONFIGS.values():
        conf["model_name"] = a.dino_dir
    real_moge = P.load_moge_model
    if a.no_naf:
        from pixal3d.trainers.flow_matching.mixins import image_conditioned_proj as ICP
        install_plain_upsample(ICP)
        print("NAF OFF: bicubic feature upsample (ROUGH / NAF-off)", flush=True)
    else:
        import natten
        print("natten:", natten.__version__, getattr(natten, "__file__", ""), flush=True)
    P.load_moge_model = lambda *args, **kw: real_moge(*args[:1], model_name=a.moge_dir)

    P.run_inference(image_path=a.image, output_path=a.output, seed=a.seed, manual_fov=-1.0, model_path=a.model_dir,
                    low_vram=a.low_vram, resolution=a.resolution)


if __name__ == "__main__":
    main()
