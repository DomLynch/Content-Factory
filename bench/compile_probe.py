"""torch.compile probe for the Flux transformer (plugin 0.1.23): compile_probe.py <kit.json> <row_id> --mode eager|compiled --out result.json

ONE cell per process (a second image in a process comes out black on this box), so bench/compile_probe.ps1 runs it twice, eager then compiled, and compares.
Reports: compiled or fell back, seconds per step (median of the steps after the first; the first step of the compiled run includes the compile), the first
step, total generate time, and the sha256 of the PNG. Step times are the intervals between scheduler.step calls (works the same compiled or not).
Hard stop: 300 s watchdog (faulthandler exit) inside, the runner adds a process kill. NO package is installed by this script; a missing one is reported in `error`.
CF_DRY=1 prints the plan and exits before torch is imported. Expected on Windows ROCm: no Triton, so inductor cannot compile and the run is reported as fell_back.
"""
import faulthandler, hashlib, json, os, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(REPO, "plugins", "factory", "lib"))
import cf_common
import cf_flux

args = [a for a in sys.argv[1:]]
if "--mode" not in args or "--out" not in args or len(args) < 6:
    sys.exit(__doc__)
mode, out = args[args.index("--mode") + 1], args[args.index("--out") + 1]
kit_path, row_id = args[0], args[1]
cfg = cf_common.load_config()
LOC = cfg["local"]
kit = json.load(open(kit_path, encoding="utf-8-sig"))
row = next(r for r in kit["rows"] if r.get("row_id", r["name"]) == row_id)
prompt, seed = cf_flux.row_prompt_seed(kit, row, cfg["defaults"]["seed"])
W, H = row.get("w", kit.get("w", cfg["defaults"]["w"])), row.get("h", kit.get("h", cfg["defaults"]["h"]))
ref_path = row.get("ref", kit.get("ref"))
use_ref = bool(ref_path) and row.get("use_ref", True)
png = os.path.join(LOC["outputs_dir"], "_speed_tests", f"probe_{mode}_{row['name']}_{W}x{H}.png")
if os.environ.get("CF_DRY"):
    print(json.dumps({"mode": mode, "row": row_id, "w": W, "h": H, "reference": use_ref, "png": png, "result_json": out}, indent=1))
    sys.exit(0)
if mode not in ("eager", "compiled"):
    sys.exit("--mode eager|compiled")
faulthandler.dump_traceback_later(300, exit=True)
sys.path.insert(0, LOC["flux_pkgs_dir"])
os.environ["HF_HUB_OFFLINE"] = "1"
os.makedirs(os.path.dirname(png), exist_ok=True)

res = {"mode": mode, "row_id": row_id, "w": W, "h": H, "reference": use_ref, "compiled": False, "fell_back": False, "error": None}
try:
    import torch
    from diffusers import Flux3DistilledAutoBlocks
    t0 = time.time()
    blocks = Flux3DistilledAutoBlocks()
    blocks.sub_blocks.pop("prompt_upsampler")
    pipe = blocks.init_pipeline(LOC["flux_model_dir"])
    pipe.load_components(names=["text_encoder", "tokenizer", "transformer", "vae"], torch_dtype=getattr(torch, cfg["models"]["flux"]["dtype"]))
    pipe.to("cuda")
    if cfg["models"]["flux"].get("vae_slicing"):
        pipe.vae.enable_slicing()
    if cfg["models"]["flux"].get("vae_tiling"):
        pipe.vae.enable_tiling()
    if mode == "compiled":
        try:
            pipe.transformer.compile()   # in place: the pipeline keeps the same module object
        except Exception as e:
            res.update(fell_back=True, error="compile() raised: " + repr(e)[:400])
    stamps = []
    sched = getattr(pipe, "scheduler", None)
    if sched is not None:
        orig_step = sched.step

        def timed_step(*a, **k):
            torch.cuda.synchronize()
            stamps.append(time.time())
            return orig_step(*a, **k)
        sched.step = timed_step
    extra = {}
    if use_ref:
        from PIL import Image
        extra["image"] = [Image.open(ref_path).convert("RGB")]
    t1 = time.time()
    try:
        image = pipe(prompt=prompt, width=W, height=H, generator=torch.Generator().manual_seed(seed), output="images", **extra)[0]
        torch.cuda.synchronize()
    except Exception as e:
        res.update(fell_back=True, error="render raised: " + repr(e)[:400])
        raise
    gen_s = time.time() - t1
    image.save(png)
    gaps = [b - a for a, b in zip([t1] + stamps[:-1], stamps)]
    res.update(gen_s=round(gen_s, 2), steps=len(stamps), png=png, sha256=hashlib.sha256(open(png, "rb").read()).hexdigest())
    if gaps:
        res["first_step_s"] = round(gaps[0], 2)
        rest = gaps[1:] or gaps
        res["s_per_step_median"] = round(statistics.median(rest), 3)
        res["compile_s_estimate"] = round(max(0.0, gaps[0] - statistics.median(rest)), 2) if mode == "compiled" else 0.0
    if mode == "compiled":
        try:
            graphs = torch._dynamo.utils.counters["stats"]["unique_graphs"]
        except Exception:
            graphs = 0
        res["unique_graphs"] = graphs
        res["compiled"] = graphs > 0 and not res["fell_back"]
        res["fell_back"] = not res["compiled"]
except SystemExit:
    raise
except Exception as e:
    res["error"] = res["error"] or repr(e)[:500]
    res["fell_back"] = mode == "compiled"
json.dump(res, open(out, "w"), indent=1)
print(json.dumps(res, indent=1))
sys.exit(0 if res.get("sha256") else 3)
