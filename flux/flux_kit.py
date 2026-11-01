"""Render ONE row of a kit file (data rows, not literals): flux_kit.py <kit.json> <row name>.
One row per process: a second image in the same process comes out black on this machine."""
import faulthandler, json, os, sys, time

faulthandler.dump_traceback_later(1500, exit=True)
HOME = os.path.expanduser("~")
sys.path.insert(0, HOME + r"\Documents\flux_env\pkgs")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import numpy as np
import torch
from diffusers import Flux3DistilledAutoBlocks

kit = json.load(open(sys.argv[1], encoding="utf-8-sig"))
row = next(r for r in kit["rows"] if r["name"] == sys.argv[2])
prompt = " ".join([row["prompt"], kit["subject"] if row.get("use_subject", True) else "", kit["style"]]).strip()
W, H = row.get("w", kit["w"]), row.get("h", kit["h"])
out_dir = HOME + rf"\Documents\kits_out\{kit['kit']}"
os.makedirs(out_dir, exist_ok=True)

blocks = Flux3DistilledAutoBlocks()
blocks.sub_blocks.pop("prompt_upsampler")
pipe = blocks.init_pipeline(HOME + r"\Documents\models\flux-3-image-klein")
pipe.load_components(names=["text_encoder", "tokenizer", "transformer", "vae"], torch_dtype=torch.bfloat16)
pipe.to("cuda")
for fn in ("enable_slicing", "enable_tiling"):
    if hasattr(pipe.vae, fn):
        getattr(pipe.vae, fn)()
t1 = time.time()
extra = {}
if kit.get("ref") and row.get("use_ref", True):
    # identity lock: the model takes up to 10 reference images (image-conditioned workflow)
    from PIL import Image
    extra["image"] = [Image.open(kit["ref"]).convert("RGB")]
    prompt = kit["ref_prefix"] + " " + prompt
image = pipe(prompt=prompt, width=W, height=H, generator=torch.Generator().manual_seed(kit.get("seed", 42)), output="images", **extra)[0]
torch.cuda.synchronize()
mean = float(np.asarray(image.convert("RGB")).mean())
path = out_dir + rf"\{row['name']}_{W}x{H}.png"
if mean < 3:
    print(f"BLACK_IMAGE {row['name']} mean={mean:.1f} (not saved)", flush=True)
    sys.exit(2)
image.save(path)
print(f"RESULT {row['name']} path={path} gen_s={time.time() - t1:.1f} peak_alloc_gb={torch.cuda.max_memory_allocated() / 2**30:.1f} mean={mean:.0f}", flush=True)
