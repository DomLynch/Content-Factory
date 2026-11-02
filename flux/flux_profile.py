"""Profiling harness for the Flux 3 klein cell renderer: flux_profile.py <kit.json> <row_id_no_ref> <row_id_with_ref>
Builds the pipeline once exactly like flux_kit_fast.py, renders the two rows, and prints ONE JSON line per row:
model load s, text-encoder s, transformer s total and per step, VAE decode s (and VAE encode s for the reference),
dtypes, steps actually run, SDPA flags + backend probes + observed SDPA calls, torch.compile / triton availability,
peak allocated GPU GB. Lines are also appended to C:\\Users\\dom12\\Documents\\flux_env\\flux_profile_out.jsonl.
Profile images go to ...\\flux_env\\profile_imgs\\ (never the kit output folder). Timers are cuda.synchronize()-bracketed
forward hooks (text_encoder, transformer) and monkeypatches (vae.decode, vae.encode).
Env: CF_DRY=1 dry check (no torch import), CF_REF_LONG=<px> (unset/0 = full reference, as flux_kit_fast.py), CF_STEPS=<n>.
Measured-fix switches (same names/semantics as flux_kit_fast.py; unset = previous behaviour):
  CF_ATTN_BACKEND=<name>, CF_VAE_TILING=0|1, CF_VAE_SLICING=0|1, CF_CACHE_REF=1 (reference encoded before the timed region and
  reported as cache_ref_build_s / cache_ref_cached; later rows reuse it), CF_NAN_DIAG=0 (the NaN probe is ON here by default:
  latents_nan / decoded_nan in every line). Every line carries "pixel_sha256" so a switch run can be compared with the baseline run.
  The profile image name gets a suffix naming the active switches so a baseline image is never overwritten."""
import faulthandler, gc, hashlib, json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import cf_common

cfg = cf_common.load_config()
LOC = cfg["local"]
WATCHDOG = cfg["limits"]["watchdog_seconds"]
sys.path.insert(0, LOC["flux_pkgs_dir"])
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

if len(sys.argv) != 4:
    raise SystemExit("usage: flux_profile.py <kit.json> <row_id_no_ref> <row_id_with_ref>")
KIT_PATH = sys.argv[1]
kit = json.load(open(KIT_PATH, encoding="utf-8-sig"))
REF_LONG = int(os.environ.get("CF_REF_LONG") or 0)   # 0 = full-size reference
ATTN_BACKEND = os.environ.get("CF_ATTN_BACKEND") or None
VAE_TILING = os.environ.get("CF_VAE_TILING") or None
VAE_SLICING = os.environ.get("CF_VAE_SLICING") or None
NAN_DIAG = os.environ.get("CF_NAN_DIAG", "1") != "0"
CACHE_REF = os.environ.get("CF_CACHE_REF") == "1"
SWITCHES = {"attn_backend": ATTN_BACKEND, "vae_tiling": VAE_TILING, "vae_slicing": VAE_SLICING, "nan_diag": NAN_DIAG, "cache_ref": CACHE_REF}
TAG = "".join([f"_attn-{ATTN_BACKEND.strip('_')}" if ATTN_BACKEND else "", f"_tile{VAE_TILING}" if VAE_TILING else "",
               f"_slice{VAE_SLICING}" if VAE_SLICING else "", "_cacheref" if CACHE_REF else ""])
STEPS = int(os.environ["CF_STEPS"]) if os.environ.get("CF_STEPS") else None
seed = kit.get("seed", cfg["defaults"]["seed"])
ENV_DIR = os.path.join(os.path.expanduser("~"), "Documents", "flux_env")
OUT_JSONL = os.path.join(ENV_DIR, "flux_profile_out.jsonl")
IMG_DIR = os.path.join(ENV_DIR, "profile_imgs")


def plan(row_id):
    """Same logic as flux_kit_fast.py plan()."""
    row = next((r for r in kit["rows"] if r.get("row_id", r["name"]) == row_id), None)
    if row is None:
        raise SystemExit(f"row not found in {KIT_PATH}: {row_id}")
    prompt = " ".join([row["prompt"], kit["subject"] if row.get("use_subject", True) else "", kit["style"]]).strip()
    W, H = row.get("w", kit.get("w", cfg["defaults"]["w"])), row.get("h", kit.get("h", cfg["defaults"]["h"]))
    ref_path = row.get("ref", kit.get("ref"))
    use_ref = bool(ref_path) and row.get("use_ref", True)
    if use_ref:
        prompt = row.get("ref_prefix", kit["ref_prefix"]) + " " + prompt
    return {"row": row, "row_id": row_id, "prompt": prompt, "W": W, "H": H, "ref_path": ref_path, "use_ref": use_ref}


plans = [plan(sys.argv[2]), plan(sys.argv[3])]
if plans[0]["use_ref"]:
    print(f"warning: first row {sys.argv[2]} has a reference (expected a no-ref row)", file=sys.stderr)
if not plans[1]["use_ref"]:
    print(f"warning: second row {sys.argv[3]} has no reference (expected a with-ref row)", file=sys.stderr)
if os.environ.get("CF_DRY"):
    print(json.dumps([{"row_id": p["row_id"], "W": p["W"], "H": p["H"], "ref": p["use_ref"], "ref_long": (REF_LONG or None) if p["use_ref"] else None,
                       "steps": STEPS or "default", "switches": SWITCHES, "out_jsonl": OUT_JSONL, "img_dir": IMG_DIR, "prompt_head": p["prompt"][:100]}
                      for p in plans], indent=1))
    sys.exit(0)
os.makedirs(IMG_DIR, exist_ok=True)


def snap16(v):
    return max(16, int(round(v / 16.0)) * 16)


def load_ref(path):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if REF_LONG and max(im.size) > REF_LONG:
        s = REF_LONG / max(im.size)
        im = im.resize((snap16(im.size[0] * s), snap16(im.size[1] * s)), Image.Resampling.LANCZOS)
    return im


import importlib.util
import numpy as np
import torch
import torch.nn.functional as F
from diffusers import Flux3DistilledAutoBlocks

faulthandler.dump_traceback_later(WATCHDOG, exit=True)
cf_common.wait_for_free_ram(cfg, "flux profile")
fmodel = cfg["models"]["flux"]

# ---- build (identical to flux_kit_fast.py) ----
t_proc = time.time()
blocks = Flux3DistilledAutoBlocks()
blocks.sub_blocks.pop("prompt_upsampler")
pipe = blocks.init_pipeline(LOC["flux_model_dir"])
pipe.load_components(names=["text_encoder", "tokenizer", "transformer", "vae"], torch_dtype=getattr(torch, fmodel["dtype"]))
pipe.to("cuda")
want_slicing = bool(fmodel.get("vae_slicing")) if VAE_SLICING is None else VAE_SLICING == "1"
want_tiling = bool(fmodel.get("vae_tiling")) if VAE_TILING is None else VAE_TILING == "1"
(pipe.vae.enable_slicing if want_slicing else pipe.vae.disable_slicing)()
(pipe.vae.enable_tiling if want_tiling else pipe.vae.disable_tiling)()
if ATTN_BACKEND:
    pipe.transformer.set_attention_backend(ATTN_BACKEND)
from diffusers.models.attention_dispatch import _AttentionBackendRegistry
_procs = {str(getattr(m.processor, "_attention_backend", None)) for m in pipe.transformer.modules()
          if hasattr(m, "processor") and m.processor is not None}
ATTN_ACTIVE = {"registry_active": str(_AttentionBackendRegistry.get_active_backend()[0]), "processor_overrides": sorted(_procs),
               "requested": ATTN_BACKEND or "none (default)"}
print(f"attention backend: {ATTN_ACTIVE}", file=sys.stderr, flush=True)
torch.cuda.synchronize()
LOAD_S = time.time() - t_proc
print(f"loaded in {LOAD_S:.1f}s", file=sys.stderr, flush=True)

# ---- timers ----
T = {"text_encoder": [], "transformer": [], "vae_decode": [], "vae_encode": []}


def _sync_time():
    torch.cuda.synchronize()
    return time.perf_counter()


def hook_module(mod, key):
    state = {}

    def pre(m, a, k=None):
        state["t"] = _sync_time()

    mod.register_forward_pre_hook(pre)
    mod.register_forward_hook(lambda m, a, o: T[key].append(_sync_time() - state["t"]))


def patch_method(obj, name, key):
    orig = getattr(obj, name)

    def wrapped(*a, **k):
        t = _sync_time()
        try:
            return orig(*a, **k)
        finally:
            T[key].append(_sync_time() - t)
    setattr(obj, name, wrapped)


hook_module(pipe.text_encoder, "text_encoder")
hook_module(pipe.transformer, "transformer")
patch_method(pipe.vae, "decode", "vae_decode")
_dec_nan = []
if NAN_DIAG:
    _timed_decode = pipe.vae.decode

    def _decode_nan_probe(*a, **k):
        out = _timed_decode(*a, **k)
        t = out[0] if isinstance(out, (tuple, list)) else getattr(out, "sample", out)
        _dec_nan.append(torch.isnan(t).any())
        return out
    pipe.vae.decode = _decode_nan_probe
if hasattr(pipe.vae, "encode"):
    patch_method(pipe.vae, "encode", "vae_encode")

# ---- observed SDPA calls (wrap functional SDPA; counts only, no sync) ----
SDPA = {"calls": 0, "shapes": {}, "dtypes": set(), "masked": 0}
_orig_sdpa = F.scaled_dot_product_attention


def _sdpa(q, k, v, *a, **kw):
    SDPA["calls"] += 1
    key = f"q{tuple(q.shape)} k{tuple(k.shape)}"
    SDPA["shapes"][key] = SDPA["shapes"].get(key, 0) + 1
    SDPA["dtypes"].add(str(q.dtype).replace("torch.", ""))
    if (a and a[0] is not None) or kw.get("attn_mask") is not None:
        SDPA["masked"] += 1
    return _orig_sdpa(q, k, v, *a, **kw)


F.scaled_dot_product_attention = _sdpa
torch.nn.functional.scaled_dot_product_attention = _sdpa


def dtype_of(m):
    try:
        return str(next(m.parameters()).dtype).replace("torch.", "")
    except Exception as e:
        return f"unknown({e!r})"


def sdpa_probe(p):
    """Try each backend on a q/k/v of the real transformer geometry (heads/head_dim from config, seq = latent tokens + 512 text + ref tokens,
    capped so the probe is cheap). Uses the original SDPA so probe calls are not counted."""
    from torch.nn.attention import sdpa_kernel, SDPBackend
    cfgt = pipe.transformer.config
    heads = getattr(cfgt, "num_attention_heads", 24)
    hd = getattr(cfgt, "attention_head_dim", 128)
    seq = (p["H"] // 16) * (p["W"] // 16) + 512
    seq = min(seq, 4096)
    q = torch.randn(1, heads, seq, hd, device="cuda", dtype=pipe.transformer.dtype)
    res = {"shape": [1, heads, seq, hd], "dtype": str(q.dtype).replace("torch.", "")}
    for name in ("FLASH_ATTENTION", "EFFICIENT_ATTENTION", "MATH"):
        be = getattr(SDPBackend, name, None)
        if be is None:
            res[name] = "enum_missing"
            continue
        try:
            with sdpa_kernel(be):
                o = _orig_sdpa(q, q, q)
            torch.cuda.synchronize()
            res[name] = "ok" if torch.isfinite(o).all().item() else "nonfinite"
        except Exception as e:
            res[name] = "FAIL: " + str(e).strip().splitlines()[0][:160]
    del q
    return res


def env_info():
    b = torch.backends.cuda
    info = {"torch": torch.__version__, "hip": getattr(torch.version, "hip", None), "cuda_ver": torch.version.cuda,
            "device": torch.cuda.get_device_name(0),
            "sdpa_flags": {"flash": b.flash_sdp_enabled(), "mem_efficient": b.mem_efficient_sdp_enabled(), "math": b.math_sdp_enabled()},
            "triton_present": importlib.util.find_spec("triton") is not None,
            "torch_compile_available": hasattr(torch, "compile")}
    try:
        import triton
        info["triton_version"] = triton.__version__
    except Exception as e:
        info["triton_version"] = f"import failed: {e!r}"[:120]
    try:
        import torch._dynamo
        info["torch_dynamo_importable"] = True
    except Exception as e:
        info["torch_dynamo_importable"] = f"no: {e!r}"[:120]
    try:
        import torch._inductor
        info["torch_inductor_importable"] = True
    except Exception as e:
        info["torch_inductor_importable"] = f"no: {e!r}"[:120]
    try:
        procs = pipe.transformer.attn_processors
        info["attn_processors"] = sorted({type(v).__name__ for v in procs.values()})
    except Exception as e:
        info["attn_processors"] = f"unavailable: {e!r}"[:120]
    return info


ENV = env_info()
PROBES = {}
_cond_cache = {}


def cached_ref_cond(path):
    """CF_CACHE_REF: the pipeline's own preprocess + VAE-encode block, run once per reference. Returns (latents list or None, build_s)."""
    if path in _cond_cache:
        return _cond_cache[path], 0.0
    t = _sync_time()
    try:
        from diffusers.modular_pipelines import PipelineState
        st = PipelineState()
        st.set("image", [load_ref(path)])
        with torch.no_grad():
            _, st = pipe._blocks.sub_blocks["vae_encoder"](pipe, st)
        lat = st.get("image_cond_latents")
        if not lat:
            raise RuntimeError("vae_encoder block produced no image_cond_latents")
        _cond_cache[path] = [x.detach().clone() for x in lat]
    except Exception as e:
        print(f"CACHE_REF failed ({e!r}); pipeline encodes as usual", file=sys.stderr, flush=True)
        _cond_cache[path] = None
    return _cond_cache[path], _sync_time() - t


def render(p, first):
    gc.collect(); torch.cuda.synchronize(); torch.cuda.empty_cache()
    for v in T.values():
        v.clear()
    SDPA.update(calls=0, shapes={}, dtypes=set(), masked=0)
    sch = getattr(pipe, "scheduler", None)
    if sch is not None:
        for a in ("_step_index", "_begin_index"):
            if hasattr(sch, a):
                setattr(sch, a, None)
    gen = torch.Generator().manual_seed(seed)
    extra = {}
    cond, cache_build_s = None, 0.0
    if p["use_ref"]:
        if CACHE_REF:
            cond, cache_build_s = cached_ref_cond(p["ref_path"])
            for v in T.values():
                v.clear()   # the encode of the cache build is reported as cache_ref_build_s, not as pipeline time
        if cond is not None:
            extra["image_cond_latents"] = cond
        else:
            extra["image"] = [load_ref(p["ref_path"])]
    try:
        c, f = int(pipe.num_channels_latents), int(pipe.vae_scale_factor) * 2
        extra["latents"] = torch.randn((1, c, p["H"] // f, p["W"] // f), generator=gen, dtype=torch.float32)
    except Exception:
        pass
    if STEPS:
        extra["num_inference_steps"] = STEPS
    torch.cuda.reset_peak_memory_stats()
    _dec_nan.clear()
    nan_diag = {}
    t0 = _sync_time()
    with torch.no_grad():
        if NAN_DIAG:
            res = pipe(prompt=p["prompt"], width=p["W"], height=p["H"], generator=gen, output=["images", "latents"], **extra)
            image = res["images"][0]
            nan_diag = {"latents_nan": bool(torch.isnan(res["latents"]).any().item()),
                        "decoded_nan": bool(any(x.item() for x in _dec_nan)) if _dec_nan else None}
        else:
            image = pipe(prompt=p["prompt"], width=p["W"], height=p["H"], generator=gen, output="images", **extra)[0]
    total = _sync_time() - t0
    tf = T["transformer"]
    # the guidance-distilled model runs one transformer forward per step; if the pipeline batched/doubled this would differ
    line = {
        "row_id": p["row_id"], "ref": p["use_ref"], "W": p["W"], "H": p["H"], "ref_long": (REF_LONG or None) if p["use_ref"] else None,
        "switches": SWITCHES, "attn_backend_active": ATTN_ACTIVE, "cache_ref_cached": cond is not None,
        "cache_ref_build_s": round(cache_build_s, 3), **nan_diag,
        "model_load_s": round(LOAD_S, 2),
        "text_encoder_s": round(sum(T["text_encoder"]), 3), "text_encoder_calls": len(T["text_encoder"]),
        "transformer_s_total": round(sum(tf), 3), "transformer_calls": len(tf),
        "transformer_s_per_step": round(sum(tf) / len(tf), 3) if tf else None,
        "transformer_call_s": [round(x, 3) for x in tf],
        "vae_decode_s": round(sum(T["vae_decode"]), 3), "vae_decode_calls": len(T["vae_decode"]),
        "vae_encode_s": round(sum(T["vae_encode"]), 3), "vae_encode_calls": len(T["vae_encode"]),
        "pipeline_total_s": round(total, 3),
        "other_s": round(total - sum(T["text_encoder"]) - sum(tf) - sum(T["vae_decode"]) - sum(T["vae_encode"]), 3),
        "steps_run": len(tf), "steps_requested": STEPS or "default",
        "dtype": {"transformer": dtype_of(pipe.transformer), "text_encoder": dtype_of(pipe.text_encoder), "vae": dtype_of(pipe.vae)},
        "vae_slicing": bool(getattr(pipe.vae, "use_slicing", False)), "vae_tiling": bool(getattr(pipe.vae, "use_tiling", False)),
        "sdpa_observed": {"calls": SDPA["calls"], "masked_calls": SDPA["masked"], "q_dtypes": sorted(SDPA["dtypes"]),
                          "shapes": dict(sorted(SDPA["shapes"].items(), key=lambda kv: -kv[1])[:6])},
        "peak_alloc_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
        "peak_reserved_gb": round(torch.cuda.max_memory_reserved() / 2**30, 2),
        "image_mean": round(float(np.asarray(image.convert("RGB")).mean()), 1),
        "pixel_sha256": hashlib.sha256(np.asarray(image.convert("RGB")).tobytes()).hexdigest(),
    }
    path = os.path.join(IMG_DIR, f"profile_{p['row_id']}_{p['W']}x{p['H']}{TAG}.png")
    image.save(path)
    line["profile_image"] = path
    if first:
        line["env"] = ENV
        line["sdpa_backend_probe"] = sdpa_probe(p)
        torch.cuda.reset_peak_memory_stats()
    return line


with open(OUT_JSONL, "a", encoding="utf-8") as fh:
    for i, p in enumerate(plans):
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(WATCHDOG, exit=True)
        try:
            line = render(p, i == 0)
        except Exception as e:
            line = {"row_id": p["row_id"], "error": repr(e)[:500]}
        s = json.dumps(line)
        print(s, flush=True)
        fh.write(s + "\n"); fh.flush()
faulthandler.cancel_dump_traceback_later()
