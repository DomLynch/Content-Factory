"""Render ONE row of a kit file: flux_kit.py <kit.json> <row_id>. All paths and limits come from config/; writes a receipt.
One row per process: a second image in the same process comes out black on this machine."""
import faulthandler, json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import cf_common
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "factory", "lib"))
import cf_flux  # quality policy, plugin 0.1.6
import cf_guard

cfg = cf_common.load_config()
LOC = cfg["local"]
faulthandler.dump_traceback_later(cfg["limits"]["watchdog_seconds"], exit=True)
sys.path.insert(0, LOC["flux_pkgs_dir"])
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

kit = json.load(open(sys.argv[1], encoding="utf-8-sig"))
row = next(r for r in kit["rows"] if r.get("row_id", r["name"]) == sys.argv[2])
row_id = row.get("row_id", row["name"])
seed = kit.get("seed", cfg["defaults"]["seed"])
prompt = " ".join([row["prompt"], kit["subject"] if row.get("use_subject", True) else "", kit["style"]]).strip()
W, H = row.get("w", kit.get("w", cfg["defaults"]["w"])), row.get("h", kit.get("h", cfg["defaults"]["h"]))
ref_path = row.get("ref", kit.get("ref"))   # a row may use its own identity reference
use_ref = bool(ref_path) and row.get("use_ref", True)
if use_ref:
    prompt = row.get("ref_prefix", kit["ref_prefix"]) + " " + prompt
asset_id = cf_common.slug_id(kit["kit"], row_id, prompt, seed)
spec = cf_flux.run_spec(kit, row)
refusals = cf_flux.policy_reasons(spec)
if refusals and os.environ.get("FACTORY_RAW"):
    cf_guard.raw_override("FACTORY_RAW=" + os.environ["FACTORY_RAW"], "flux")   # logged exception
elif refusals:
    print("\n".join(refusals), flush=True)
    sys.exit(2)
out_dir = os.path.join(LOC["outputs_dir"], kit["kit"])
path = os.path.join(out_dir, f"{row['name']}_{W}x{H}.png")
if os.environ.get("CF_DRY"):
    print(json.dumps({"asset_id": asset_id, "path": path, "W": W, "H": H, "ref": use_ref, "prompt_head": prompt[:120]}, indent=1))
    sys.exit(0)
os.makedirs(out_dir, exist_ok=True)

import numpy as np
import torch
from diffusers import Flux3DistilledAutoBlocks

cf_common.wait_for_free_ram(cfg, "flux render")
ram = cf_common.FreeRamWatch(); ram.start()
t0 = time.time()
params = {"seed": seed, "w": W, "h": H, "reference": ref_path if use_ref else None, "prompt": prompt, "kit": sys.argv[1], "row_id": row_id}
model = {"id": cfg["models"]["flux"]["id"], "dir": LOC["flux_model_dir"]}
params.update(cf_flux.receipt_fields(spec, ref_path if use_ref else None, os.environ.get("CF_ATTN_BACKEND") or cf_flux.flux_cfg()["attention_backend"], "child", False,
                                      cfg["models"]["flux"].get("vae_tiling"), cfg["models"]["flux"].get("vae_slicing")))
try:
    blocks = Flux3DistilledAutoBlocks()
    blocks.sub_blocks.pop("prompt_upsampler")
    pipe = blocks.init_pipeline(LOC["flux_model_dir"])
    pipe.load_components(names=["text_encoder", "tokenizer", "transformer", "vae"], torch_dtype=getattr(torch, cfg["models"]["flux"]["dtype"]))
    pipe.to("cuda")
    if cfg["models"]["flux"].get("vae_slicing"):
        pipe.vae.enable_slicing()
    if cfg["models"]["flux"].get("vae_tiling"):
        pipe.vae.enable_tiling()
    t_load = time.time() - t0
    extra = {}
    if use_ref:
        from PIL import Image
        extra["image"] = [Image.open(ref_path).convert("RGB")]
    t1 = time.time()
    image = pipe(prompt=prompt, width=W, height=H, generator=torch.Generator().manual_seed(seed), output="images", **extra)[0]
    torch.cuda.synchronize()
    gen_s = time.time() - t1
    mean = float(np.asarray(image.convert("RGB")).mean())
    mem = {"peak_gpu_alloc_gb": round(torch.cuda.max_memory_allocated() / 2**30, 1), "peak_gpu_reserved_gb": round(torch.cuda.max_memory_reserved() / 2**30, 1), "lowest_free_ram_gb": ram.stop()}
    timing = {"wall_s": round(time.time() - t0, 1), "load_s": round(t_load, 1), "generate_s": round(gen_s, 1)}
    if mean < 3:
        cf_common.write_receipt(cfg, asset_id, "flux", tool="flux_kit.py", model=model, params=params, timing=timing, memory=mem, status="fail", error=f"black image mean={mean:.1f}")
        print(f"BLACK_IMAGE {row['name']} mean={mean:.1f} (not saved)", flush=True)
        sys.exit(2)
    image.save(path)
    rp = cf_common.write_receipt(cfg, asset_id, "flux", tool="flux_kit.py", model=model, params={**params, "mean_brightness": round(mean, 1)},
                                 inputs=[ref_path] if use_ref else [], timing=timing, memory=mem, outputs=[path])
    print(f"RESULT {row['name']} path={path} gen_s={gen_s:.1f} peak_alloc_gb={mem['peak_gpu_alloc_gb']} mean={mean:.0f} receipt={rp}", flush=True)
except SystemExit:
    raise
except Exception as e:
    cf_common.write_receipt(cfg, asset_id, "flux", tool="flux_kit.py", model=model, params=params, timing={"wall_s": round(time.time() - t0, 1)},
                            memory={"lowest_free_ram_gb": ram.stop()}, status="fail", error=repr(e)[:500])
    raise
