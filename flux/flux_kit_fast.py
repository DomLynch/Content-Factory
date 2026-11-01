"""Faster Flux 3 klein cell renderer: flux_kit_fast.py <kit.json> <row_id> [<row_id> ...]
Same prompts, seed, output names, receipts and black-image guard as flux_kit.py, plus:
  1. the reference is downscaled before conditioning (CF_REF_LONG, default 640 px long side, sides snapped to
     multiples of 16 so the pipeline adds no reflect padding). Reference tokens drop from ~6000 to ~1200.
  2. steps = the pipeline default (16 for klein distilled). CF_STEPS may LOWER it for experiments only.
  3. the model is loaded once and many rows are rendered in one process. Per row: a fresh CPU torch.Generator,
     freshly drawn latents, scheduler step state reset, gc + cuda sync + empty_cache. If a row still comes out black
     it is retried once in-process; if that fails too (or a row raises) every remaining row, and that row, is
     rendered in a fresh child process (the old one-row-per-process behaviour), so a bad process never poisons a batch.
Env: CF_DRY=1 dry run (no torch import), CF_REF_LONG=<px> (0 = use the full reference as flux_kit.py does),
     CF_STEPS=<n>, CF_FAST_CHILD=1 (internal: do not fall back again), CF_INPROC=0 (always one process per row)."""
import faulthandler, gc, json, os, subprocess, sys, time

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
REF_LONG = int(os.environ.get("CF_REF_LONG", "640"))
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
                       "ref_long": REF_LONG if p["use_ref"] else None, "steps": STEPS or "default",
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
if fmodel.get("vae_slicing"):
    pipe.vae.enable_slicing()
if fmodel.get("vae_tiling"):
    pipe.vae.enable_tiling()
torch.cuda.synchronize()
LOAD_S = time.time() - t_proc
print(f"loaded in {LOAD_S:.1f}s", flush=True)


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
    """Returns (image, gen_s, ref_info). Fresh generator + latents every call."""
    fresh_state()
    gen = torch.Generator().manual_seed(seed)
    extra, ref_info = {}, None
    if p["use_ref"]:
        im = load_ref(p["ref_path"])
        extra["image"] = [im]
        ref_info = list(im.size)
    lat = fresh_latents(p["W"], p["H"], gen)
    if lat is not None:
        extra["latents"] = lat
    if STEPS:
        extra["num_inference_steps"] = STEPS
    torch.cuda.reset_peak_memory_stats()
    t1 = time.time()
    with torch.no_grad():
        image = pipe(prompt=p["prompt"], width=p["W"], height=p["H"], generator=gen, output="images", **extra)[0]
    torch.cuda.synchronize()
    return image, time.time() - t1, ref_info


def fail(p, wall, error):
    cf_common.write_receipt(cfg, p["asset_id"], "flux", tool=TOOL, model=model, params=params_of(p), timing={"wall_s": round(wall, 1)},
                            memory={"lowest_free_ram_gb": ram.lowest}, status="fail", error=error[:500])


def params_of(p, **more):
    return {"seed": seed, "w": p["W"], "h": p["H"], "reference": p["ref_path"] if p["use_ref"] else None, "prompt": p["prompt"],
            "kit": KIT_PATH, "row_id": p["row_id"], "ref_long": REF_LONG if p["use_ref"] else None, "steps": STEPS or "default", **more}


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
        image, gen_s, ref_info = render(p)
        mean = float(np.asarray(image.convert("RGB")).mean())
        retried = False
        if mean < 3:
            print(f"BLACK_IMAGE {p['row']['name']} mean={mean:.1f}: retrying once in-process with fresh state", flush=True)
            retried = True
            del image
            image, gen_s, ref_info = render(p)
            mean = float(np.asarray(image.convert("RGB")).mean())
        mem = {"peak_gpu_alloc_gb": round(torch.cuda.max_memory_allocated() / 2**30, 1),
               "peak_gpu_reserved_gb": round(torch.cuda.max_memory_reserved() / 2**30, 1), "lowest_free_ram_gb": ram.lowest}
        # load_s is charged to the first row only; later rows reuse the loaded model
        timing = {"wall_s": round(time.time() - t0 + (LOAD_S if n == 0 else 0), 1), "load_s": round(LOAD_S if n == 0 else 0.0, 1),
                  "generate_s": round(gen_s, 1), "row_index_in_process": n}
        if mean < 3:
            cf_common.write_receipt(cfg, p["asset_id"], "flux", tool=TOOL, model=model, params=params_of(p, retried=True), timing=timing,
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
                                     params=params_of(p, mean_brightness=round(mean, 1), ref_size=ref_info, retried=retried),
                                     inputs=[p["ref_path"]] if p["use_ref"] else [], timing=timing, memory=mem, outputs=[p["path"]])
        print(f"RESULT {p['row']['name']} path={p['path']} gen_s={gen_s:.1f} wall_s={timing['wall_s']} peak_alloc_gb={mem['peak_gpu_alloc_gb']} "
              f"mean={mean:.0f} receipt={rp}", flush=True)
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
