"""Faster Flux 3 klein cell renderer: flux_kit_fast.py <kit.json> <row_id> [<row_id> ...]
Same prompts, seed, output names, receipts and black-image guard as flux_kit.py, plus:
  1. the reference is downscaled before conditioning ONLY when CF_REF_LONG is set (px long side, sides snapped to
     multiples of 16 so the pipeline adds no reflect padding; 640 drops reference tokens from ~6000 to ~1200).
     Unset or 0 = the full-size reference, exactly as flux_kit.py.
  2. steps = the pipeline default (16 for klein distilled). CF_STEPS may LOWER it for experiments only.
  3. the model is loaded once and many rows are rendered in one process. Per row: a fresh CPU torch.Generator,
     freshly drawn latents, scheduler step state reset, gc + cuda sync + empty_cache. If a row still comes out black
     it is retried once in-process; if that fails too (or a row raises) every remaining row, and that row, is
     rendered in a fresh child process (the old one-row-per-process behaviour), so a bad process never poisons a batch.
Env: CF_DRY=1 dry run (no torch import), CF_REF_LONG=<px> (unset/0 = full reference), CF_STEPS=<n>,
     CF_FAST_CHILD=1 (internal: do not fall back again), CF_INPROC=0 (always one process per row).
Measured-fix switches, all OFF by default (unset = previous behaviour):
     CF_ATTN_BACKEND=<name>  transformer.set_attention_backend(name); the default in this diffusers is "native" (torch SDPA auto-dispatch).
                             Valid: native, _native_flash, _native_efficient, _native_math, _native_cudnn, flex, flash, sage, xformers, ...
     CF_VAE_TILING=0|1, CF_VAE_SLICING=0|1  override config vae_tiling / vae_slicing (unset = config value)
     CF_NAN_DIAG=1           isnan on the final latents and on the decoded tensor; printed in RESULT and written to receipt params
     CF_CACHE_REF=1          encode the reference once per process (the pipeline's own vae_encoder block) and pass image_cond_latents
                             so the preprocess + VAE-encode stage is skipped on every row (pixel_sha256 is in the receipt)."""
import faulthandler, gc, hashlib, json, os, subprocess, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import cf_common

TOOL = "flux_kit_fast.py"
cfg = cf_common.load_config()
LOC = cfg["local"]
WATCHDOG = cfg["limits"]["watchdog_seconds"]
sys.path.insert(0, LOC["flux_pkgs_dir"])
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

if len(sys.argv) < 3:
    raise SystemExit("usage: flux_kit_fast.py <kit.json> <row_id> [<row_id> ...]")
KIT_PATH = sys.argv[1]
kit = json.load(open(KIT_PATH, encoding="utf-8-sig"))
REF_LONG = int(os.environ.get("CF_REF_LONG") or 0)   # 0 = full-size reference; only an explicit CF_REF_LONG downscales
ATTN_BACKEND = os.environ.get("CF_ATTN_BACKEND") or None
VAE_TILING = os.environ.get("CF_VAE_TILING") or None      # "0" / "1" / None (= config)
VAE_SLICING = os.environ.get("CF_VAE_SLICING") or None
NAN_DIAG = os.environ.get("CF_NAN_DIAG") == "1"
CACHE_REF = os.environ.get("CF_CACHE_REF") == "1"
SWITCHES = {"attn_backend": ATTN_BACKEND, "vae_tiling": VAE_TILING, "vae_slicing": VAE_SLICING, "nan_diag": NAN_DIAG, "cache_ref": CACHE_REF}
STEPS = int(os.environ["CF_STEPS"]) if os.environ.get("CF_STEPS") else None
IS_CHILD = bool(os.environ.get("CF_FAST_CHILD"))
seed = kit.get("seed", cfg["defaults"]["seed"])
out_dir = os.path.join(LOC["outputs_dir"], kit["kit"])
model = {"id": cfg["models"]["flux"]["id"], "dir": LOC["flux_model_dir"]}


def plan(row_id):
    """Everything about one row that does not need the GPU (identical logic to flux_kit.py)."""
    row = next((r for r in kit["rows"] if r.get("row_id", r["name"]) == row_id), None)
    if row is None:
        raise SystemExit(f"row not found in {KIT_PATH}: {row_id}")
    prompt = " ".join([row["prompt"], kit["subject"] if row.get("use_subject", True) else "", kit["style"]]).strip()
    W, H = row.get("w", kit.get("w", cfg["defaults"]["w"])), row.get("h", kit.get("h", cfg["defaults"]["h"]))
    ref_path = row.get("ref", kit.get("ref"))
    use_ref = bool(ref_path) and row.get("use_ref", True)
    if use_ref:
        prompt = row.get("ref_prefix", kit["ref_prefix"]) + " " + prompt
    return {"row": row, "row_id": row_id, "prompt": prompt, "W": W, "H": H, "ref_path": ref_path, "use_ref": use_ref,
            "asset_id": cf_common.slug_id(kit["kit"], row_id, prompt, seed),
            "path": os.path.join(out_dir, f"{row['name']}_{W}x{H}.png")}


plans = [plan(r) for r in sys.argv[2:]]   # validates every id before anything is loaded
if os.environ.get("CF_DRY"):
    print(json.dumps([{"asset_id": p["asset_id"], "path": p["path"], "W": p["W"], "H": p["H"], "ref": p["use_ref"],
                       "ref_long": (REF_LONG or None) if p["use_ref"] else None, "steps": STEPS or "default", "switches": SWITCHES,
                       "prompt_head": p["prompt"][:120]} for p in plans], indent=1))
    sys.exit(0)
os.makedirs(out_dir, exist_ok=True)


def snap16(v):
    return max(16, int(round(v / 16.0)) * 16)


_ref_cache = {}


def load_ref(path):
    """Reference as an RGB PIL image, downscaled so the long side is REF_LONG (never upscaled), sides multiples of 16."""
    if path in _ref_cache:
        return _ref_cache[path]
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if REF_LONG and max(im.size) > REF_LONG:
        s = REF_LONG / max(im.size)
        im = im.resize((snap16(im.size[0] * s), snap16(im.size[1] * s)), Image.Resampling.LANCZOS)
    _ref_cache[path] = im
    return im


def run_child(row_id):
    env = dict(os.environ, CF_FAST_CHILD="1")
    print(f"FALLBACK_CHILD {row_id}: fresh process", flush=True)
    return subprocess.run([sys.executable, os.path.abspath(__file__), KIT_PATH, row_id], env=env).returncode


import numpy as np
import torch
from diffusers import Flux3DistilledAutoBlocks

cf_common.wait_for_free_ram(cfg, "flux render")
ram = cf_common.FreeRamWatch(); ram.start()
t_proc = time.time()
faulthandler.dump_traceback_later(WATCHDOG, exit=True)
fmodel = cfg["models"]["flux"]
blocks = Flux3DistilledAutoBlocks()
blocks.sub_blocks.pop("prompt_upsampler")
pipe = blocks.init_pipeline(LOC["flux_model_dir"])
pipe.load_components(names=["text_encoder", "tokenizer", "transformer", "vae"], torch_dtype=getattr(torch, fmodel["dtype"]))
pipe.to("cuda")   # everything resident on the GPU: there is no CPU offload of the text encoder in this path
want_slicing = bool(fmodel.get("vae_slicing")) if VAE_SLICING is None else VAE_SLICING == "1"
want_tiling = bool(fmodel.get("vae_tiling")) if VAE_TILING is None else VAE_TILING == "1"
(pipe.vae.enable_slicing if want_slicing else pipe.vae.disable_slicing)()
(pipe.vae.enable_tiling if want_tiling else pipe.vae.disable_tiling)()
if ATTN_BACKEND:
    pipe.transformer.set_attention_backend(ATTN_BACKEND)
from diffusers.models.attention_dispatch import _AttentionBackendRegistry
_procs = {str(getattr(m.processor, "_attention_backend", None)) for m in pipe.transformer.modules()
          if hasattr(m, "processor") and m.processor is not None}
print(f"attention backend: registry_active={_AttentionBackendRegistry.get_active_backend()[0]} processor_overrides={sorted(_procs)} "
      f"(requested={ATTN_BACKEND or 'none -> default'}); vae slicing={getattr(pipe.vae, 'use_slicing', None)} "
      f"tiling={getattr(pipe.vae, 'use_tiling', None)}", flush=True)
torch.cuda.synchronize()
LOAD_S = time.time() - t_proc
print(f"loaded in {LOAD_S:.1f}s", flush=True)


_dec_nan = []
if NAN_DIAG:
    _orig_decode = pipe.vae.decode

    def _decode_nan_probe(*a, **k):
        out = _orig_decode(*a, **k)
        t = out[0] if isinstance(out, (tuple, list)) else getattr(out, "sample", out)
        _dec_nan.append(torch.isnan(t).any())
        return out
    pipe.vae.decode = _decode_nan_probe

_cond_cache = {}


def cached_ref_cond(path):
    """CF_CACHE_REF: run the pipeline's own preprocess + VAE-encode block once per reference; returns image_cond_latents (list) or None."""
    if path in _cond_cache:
        return _cond_cache[path]
    try:
        from diffusers.modular_pipelines import PipelineState
        st = PipelineState()
        st.set("image", [load_ref(path)])
        with torch.no_grad():
            _, st = pipe._blocks.sub_blocks["vae_encoder"](pipe, st)
        lat = st.get("image_cond_latents")
        if not lat:
            raise RuntimeError("vae_encoder block produced no image_cond_latents")
        _cond_cache[path] = [t.detach().clone() for t in lat]
        print(f"CACHE_REF encoded {os.path.basename(path)} -> {[tuple(t.shape) for t in lat]}", flush=True)
    except Exception as e:
        print(f"CACHE_REF failed ({e!r}); this reference is encoded by the pipeline as usual", flush=True)
        _cond_cache[path] = None
    return _cond_cache[path]


def fresh_state():
    """Drop anything a previous row could leave behind."""
    sch = getattr(pipe, "scheduler", None)
    if sch is not None:
        for a in ("_step_index", "_begin_index"):
            if hasattr(sch, a):
                setattr(sch, a, None)
    gc.collect()
    torch.cuda.synchronize()
    torch.cuda.empty_cache()


def fresh_latents(W, H, gen):
    """Initial noise drawn in float32 on CPU from this row's own generator (the same stream the pipeline would draw),
    shape (1, C, H/16, W/16) as Flux3PrepareLatentsStep expects. Returns None if the pipeline geometry is unknown,
    in which case the pipeline draws from the same generator itself."""
    try:
        c = int(pipe.num_channels_latents)
        f = int(pipe.vae_scale_factor) * 2
        return torch.randn((1, c, H // f, W // f), generator=gen, dtype=torch.float32)
    except Exception as e:
        print(f"note: own latents unavailable ({e!r}); pipeline draws them", flush=True)
        return None


def render(p):
    """Returns (image, gen_s, ref_info, diag). Fresh generator + latents every call."""
    fresh_state()
    gen = torch.Generator().manual_seed(seed)
    extra, ref_info = {}, None
    if p["use_ref"]:
        im = load_ref(p["ref_path"])
        ref_info = list(im.size)
        cond = cached_ref_cond(p["ref_path"]) if CACHE_REF else None
        if cond is not None:
            extra["image_cond_latents"] = cond   # no `image`: the auto vae_encoder block is skipped
        else:
            extra["image"] = [im]
    lat = fresh_latents(p["W"], p["H"], gen)
    if lat is not None:
        extra["latents"] = lat
    if STEPS:
        extra["num_inference_steps"] = STEPS
    torch.cuda.reset_peak_memory_stats()
    _dec_nan.clear()
    t1 = time.time()
    diag = None
    with torch.no_grad():
        if NAN_DIAG:
            res = pipe(prompt=p["prompt"], width=p["W"], height=p["H"], generator=gen, output=["images", "latents"], **extra)
            image = res["images"][0]
            diag = {"latents_nan": bool(torch.isnan(res["latents"]).any().item()),
                    "decoded_nan": bool(any(x.item() for x in _dec_nan)) if _dec_nan else None}
        else:
            image = pipe(prompt=p["prompt"], width=p["W"], height=p["H"], generator=gen, output="images", **extra)[0]
    torch.cuda.synchronize()
    return image, time.time() - t1, ref_info, diag


def fail(p, wall, error):
    cf_common.write_receipt(cfg, p["asset_id"], "flux", tool=TOOL, model=model, params=params_of(p), timing={"wall_s": round(wall, 1)},
                            memory={"lowest_free_ram_gb": ram.lowest}, status="fail", error=error[:500])


def params_of(p, **more):
    return {"seed": seed, "w": p["W"], "h": p["H"], "reference": p["ref_path"] if p["use_ref"] else None, "prompt": p["prompt"],
            "kit": KIT_PATH, "row_id": p["row_id"], "ref_long": (REF_LONG or None) if p["use_ref"] else None, "steps": STEPS or "default", "switches": SWITCHES, **more}


rc = 0
degraded = IS_CHILD or os.environ.get("CF_INPROC") == "0"   # degraded: no more in-process rows
for n, p in enumerate(plans):
    if degraded and not IS_CHILD:
        rc = max(rc, 2 if run_child(p["row_id"]) else 0)
        continue
    faulthandler.cancel_dump_traceback_later()
    faulthandler.dump_traceback_later(WATCHDOG, exit=True)
    t0 = time.time()
    try:
        image, gen_s, ref_info, diag = render(p)
        mean = float(np.asarray(image.convert("RGB")).mean())
        retried = False
        diag_s = f" nan={diag}" if diag else ""
        if mean < 3:
            print(f"BLACK_IMAGE {p['row']['name']} mean={mean:.1f}: retrying once in-process with fresh state", flush=True)
            retried = True
            del image
            image, gen_s, ref_info, diag = render(p)
            mean = float(np.asarray(image.convert("RGB")).mean())
            diag_s = f" nan={diag}" if diag else ""
        mem = {"peak_gpu_alloc_gb": round(torch.cuda.max_memory_allocated() / 2**30, 1),
               "peak_gpu_reserved_gb": round(torch.cuda.max_memory_reserved() / 2**30, 1), "lowest_free_ram_gb": ram.lowest}
        # load_s is charged to the first row only; later rows reuse the loaded model
        timing = {"wall_s": round(time.time() - t0 + (LOAD_S if n == 0 else 0), 1), "load_s": round(LOAD_S if n == 0 else 0.0, 1),
                  "generate_s": round(gen_s, 1), "row_index_in_process": n}
        if mean < 3:
            cf_common.write_receipt(cfg, p["asset_id"], "flux", tool=TOOL, model=model, params=params_of(p, retried=True, nan_diag=diag), timing=timing,
                                    memory=mem, status="fail", error=f"black image mean={mean:.1f} (also after in-process retry)")
            print(f"BLACK_IMAGE {p['row']['name']} mean={mean:.1f} (not saved)", flush=True)
            if IS_CHILD:
                rc = 2
            else:
                degraded = True   # this process cannot be trusted: redo this row and the rest in fresh processes
                print("process marked untrusted: this row and the rest go to fresh child processes", flush=True)
                rc = max(rc, 2 if run_child(p["row_id"]) else 0)
            continue
        image.save(p["path"])
        rp = cf_common.write_receipt(cfg, p["asset_id"], "flux", tool=TOOL, model=model,
                                     params=params_of(p, mean_brightness=round(mean, 1), ref_size=ref_info, retried=retried, nan_diag=diag,
                                                  pixel_sha256=hashlib.sha256(np.asarray(image.convert("RGB")).tobytes()).hexdigest()),
                                     inputs=[p["ref_path"]] if p["use_ref"] else [], timing=timing, memory=mem, outputs=[p["path"]])
        print(f"RESULT {p['row']['name']} path={p['path']} gen_s={gen_s:.1f} wall_s={timing['wall_s']} peak_alloc_gb={mem['peak_gpu_alloc_gb']} "
              f"mean={mean:.0f}{diag_s} receipt={rp}", flush=True)
        del image
    except SystemExit:
        raise
    except Exception as e:
        fail(p, time.time() - t0, repr(e))
        print(f"ERROR {p['row']['name']}: {e!r}", flush=True)
        if IS_CHILD:
            raise
        degraded = True
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
        rc = max(rc, 2 if run_child(p["row_id"]) else 0)

ram.stop()
faulthandler.cancel_dump_traceback_later()
sys.exit(rc)
