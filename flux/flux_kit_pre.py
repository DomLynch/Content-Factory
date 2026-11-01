"""Render ONE row from pre-encoded text embeds: flux_kit_pre.py <kit.json> <row_id>
Like flux_kit.py but the Qwen3-VL text encoder is never loaded: the pipeline is built without its text_encoder block and
prompt_embeds come from kits/embeds/<kit>/<row_id>.pt (written by flux_embeds.py). txt_ids are recomputed by the pipeline's own
RoPE block from the embeds and checked against the saved ones. Same seed / size / receipt / black-image guard / output path as
flux_kit.py. Receipt: tool 'flux_kit_pre.py', params gain 'embeds_sha256' (+ 'embeds_file').
Exit 2: stale or missing embeds (saved prompt sha256 != sha256 of the row's current final prompt), black image, row uses a reference.
Env: CF_DRY=1 (no torch import), CF_STEPS=<n>, CF_ATTN_BACKEND=<name>, CF_VAE_TILING=0|1, CF_VAE_SLICING=0|1 (unset = config
     models.flux), CF_OUT_DIR=<dir> (write the png there instead of outputs_dir/<kit>, so a test render does not overwrite the
     queue's file), CF_COMPARE=<png> (after saving, print max/mean abs pixel diff and whether the pixels are identical)."""
import faulthandler, hashlib, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flux_embeds
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import cf_common

TOOL = "flux_kit_pre.py"
cfg = cf_common.load_config()
LOC = cfg["local"]
if len(sys.argv) != 3:
    raise SystemExit("usage: flux_kit_pre.py <kit.json> <row_id>")
KIT_PATH, row_id = sys.argv[1], sys.argv[2]
kit = json.load(open(KIT_PATH, encoding="utf-8-sig"))
row = next((r for r in kit["rows"] if r.get("row_id", r["name"]) == row_id), None)
if row is None:
    raise SystemExit(f"row not found in {KIT_PATH}: {row_id}")
seed = kit.get("seed", cfg["defaults"]["seed"])
prompt, W, H, ref_path, use_ref = flux_embeds.final_prompt(kit, row, cfg)
asset_id = cf_common.slug_id(kit["kit"], row_id, prompt, seed)
out_dir = os.environ.get("CF_OUT_DIR") or os.path.join(LOC["outputs_dir"], kit["kit"])
path = os.path.join(out_dir, f"{row['name']}_{W}x{H}.png")
epath = flux_embeds.embeds_path(KIT_PATH, kit, row_id)
STEPS = int(os.environ["CF_STEPS"]) if os.environ.get("CF_STEPS") else None
ATTN_BACKEND = os.environ.get("CF_ATTN_BACKEND") or None
VAE_TILING = os.environ.get("CF_VAE_TILING") or None
VAE_SLICING = os.environ.get("CF_VAE_SLICING") or None
cur_sha = flux_embeds.prompt_sha256(prompt)


def refuse(msg):
    print(f"REFUSED {row_id}: {msg}", flush=True)
    sys.exit(2)


if use_ref:
    refuse("row uses a reference image; flux_kit_pre.py only supports use_ref false rows (use flux_kit.py)")
if os.environ.get("CF_DRY"):
    print(json.dumps({"asset_id": asset_id, "path": path, "W": W, "H": H, "embeds_file": epath, "embeds_exists": os.path.exists(epath),
                      "current_prompt_sha256": cur_sha, "prompt_head": prompt[:120],
                      "note": "freshness (saved sha vs current prompt) is checked at load time and needs torch"}, indent=1))
    sys.exit(0)

sys.path.insert(0, LOC["flux_pkgs_dir"])
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
faulthandler.dump_traceback_later(cfg["limits"]["watchdog_seconds"], exit=True)

import numpy as np
import torch

if not os.path.exists(epath):
    refuse(f"no embeds file {epath} (run flux_embeds.py first)")
saved = torch.load(epath, map_location="cpu", weights_only=False)
if saved.get("prompt_sha256") != cur_sha or flux_embeds.prompt_sha256(saved.get("prompt", "")) != cur_sha:
    refuse(f"stale embeds: saved prompt sha256 {str(saved.get('prompt_sha256'))[:12]} != current {cur_sha[:12]}; re-run flux_embeds.py")
emb = saved["prompt_embeds"]
embeds_sha = flux_embeds.tensor_sha256(emb)
if embeds_sha != saved.get("embeds_sha256"):
    refuse("embeds tensor does not match its stored embeds_sha256 (file corrupt)")

from diffusers import Flux3DistilledAutoBlocks
from diffusers.modular_pipelines.flux3.before_denoise import Flux3TextRoPEInputsStep

os.makedirs(out_dir, exist_ok=True)
cf_common.wait_for_free_ram(cfg, "flux render")
ram = cf_common.FreeRamWatch(); ram.start()
t0 = time.time()
fmodel = cfg["models"]["flux"]
params = {"seed": seed, "w": W, "h": H, "reference": None, "prompt": prompt, "kit": KIT_PATH, "row_id": row_id,
          "embeds_sha256": embeds_sha, "embeds_file": epath, "steps": STEPS or "default"}
model = {"id": fmodel["id"], "dir": LOC["flux_model_dir"]}
try:
    blocks = Flux3DistilledAutoBlocks()
    blocks.sub_blocks.pop("prompt_upsampler")
    blocks.sub_blocks.pop("text_encoder")   # prompt_embeds become a pipeline input; txt_ids come from the RoPE block
    pipe = blocks.init_pipeline(LOC["flux_model_dir"])
    pipe.load_components(names=["transformer", "vae"], torch_dtype=getattr(torch, fmodel["dtype"]))
    pipe.to("cuda")
    want_slicing = bool(fmodel.get("vae_slicing")) if VAE_SLICING is None else VAE_SLICING == "1"
    want_tiling = bool(fmodel.get("vae_tiling")) if VAE_TILING is None else VAE_TILING == "1"
    (pipe.vae.enable_slicing if want_slicing else pipe.vae.disable_slicing)()
    (pipe.vae.enable_tiling if want_tiling else pipe.vae.disable_tiling)()
    if ATTN_BACKEND:
        pipe.transformer.set_attention_backend(ATTN_BACKEND)
    pe = emb.to("cuda")
    if not torch.equal(Flux3TextRoPEInputsStep._prepare_text_ids(pe).cpu(), saved["txt_ids"]):
        refuse("txt_ids recomputed from the embeds differ from the saved txt_ids")
    t_load = time.time() - t0
    extra = {"num_inference_steps": STEPS} if STEPS else {}
    t1 = time.time()
    image = pipe(prompt_embeds=pe, width=W, height=H, generator=torch.Generator().manual_seed(seed), output="images", **extra)[0]
    torch.cuda.synchronize()
    gen_s = time.time() - t1
    arr = np.asarray(image.convert("RGB"))
    mean = float(arr.mean())
    mem = {"peak_gpu_alloc_gb": round(torch.cuda.max_memory_allocated() / 2**30, 1), "peak_gpu_reserved_gb": round(torch.cuda.max_memory_reserved() / 2**30, 1), "lowest_free_ram_gb": ram.stop()}
    timing = {"wall_s": round(time.time() - t0, 1), "load_s": round(t_load, 1), "generate_s": round(gen_s, 1)}
    if mean < 3:
        cf_common.write_receipt(cfg, asset_id, "flux", tool=TOOL, model=model, params=params, timing=timing, memory=mem, status="fail", error=f"black image mean={mean:.1f}")
        print(f"BLACK_IMAGE {row['name']} mean={mean:.1f} (not saved)", flush=True)
        sys.exit(2)
    image.save(path)
    psha = hashlib.sha256(arr.tobytes()).hexdigest()
    rp = cf_common.write_receipt(cfg, asset_id, "flux", tool=TOOL, model=model, params={**params, "mean_brightness": round(mean, 1), "pixel_sha256": psha},
                                 inputs=[], timing=timing, memory=mem, outputs=[path])
    print(f"RESULT {row['name']} path={path} gen_s={gen_s:.1f} wall_s={timing['wall_s']} peak_alloc_gb={mem['peak_gpu_alloc_gb']} mean={mean:.0f} pixel_sha256={psha[:12]} receipt={rp}", flush=True)
    cmp_path = os.environ.get("CF_COMPARE")
    if cmp_path:
        from PIL import Image
        other = np.asarray(Image.open(cmp_path).convert("RGB"))
        if other.shape != arr.shape:
            print(f"COMPARE shape differs: {other.shape} vs {arr.shape}", flush=True)
        else:
            d = np.abs(other.astype(np.int16) - arr.astype(np.int16))
            print(f"COMPARE vs {cmp_path}: identical={bool(d.max() == 0)} max_abs_diff={int(d.max())} mean_abs_diff={d.mean():.5f} differing_pixels={int((d.max(axis=2) > 0).sum())}", flush=True)
except SystemExit:
    raise
except Exception as e:
    cf_common.write_receipt(cfg, asset_id, "flux", tool=TOOL, model=model, params=params, timing={"wall_s": round(time.time() - t0, 1)},
                            memory={"lowest_free_ram_gb": ram.stop()}, status="fail", error=repr(e)[:500])
    raise
