import faulthandler, json, os, sys, time

faulthandler.dump_traceback_later(1200, exit=True)  # hard stop at 20 minutes
HOME = os.path.expanduser("~")
sys.path.insert(0, HOME + r"\Documents\flux_env\pkgs")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import torch
from diffusers import Flux3DistilledAutoBlocks

LOCAL = HOME + r"\Documents\models\flux-3-image-klein"
OUT = HOME + r"\Documents\flux_test"
os.makedirs(OUT, exist_ok=True)

PROMPT = ("Full-body three-quarter side view of a large realistic western dragon standing on all four legs in a neutral pose with legs planted apart, "
          "head raised and turned slightly toward the camera, mouth closed, wings folded neatly against its back, tail extended behind it, entire creature from snout to tail tip fully visible, nothing cropped. "
          "Dark brown and deep forest-green scales: dark green back, flanks and wing membranes, warm brown belly plates, chest and underside, "
          "overlapping keeled scales of varying size, heavy armoured brow ridges, a crown of dark horns, ember-amber eyes, thick muscular neck, powerful clawed feet, scarred and weathered hide with fine surface detail. "
          "Large but not huge, about the size of a big horse and a half, slightly larger than a two-headed demon. "
          "Hyper-realistic photographic quality, like a still from a practical creature-effects film shot on a full-frame camera: real reptile and crocodile skin with micro-detail, individual scale texture, skin pores and creases, wet specular highlights, scars, dirt and wear, natural muscle and bone structure. NOT cartoon, NOT stylised, NOT illustration, NOT painterly, no glossy toy or plastic look. Grim dark fantasy AAA game concept reference, soft studio lighting with a subtle rim light, plain dark charcoal background, centred, 4k character concept sheet.")

t0 = time.time()
blocks = Flux3DistilledAutoBlocks()
blocks.sub_blocks.pop("prompt_upsampler")
pipe = blocks.init_pipeline(LOCAL)
pipe.load_components(names=["text_encoder", "tokenizer", "transformer", "vae"], torch_dtype=torch.bfloat16)
pipe.to("cuda")
torch.cuda.synchronize()
print(f"loaded in {time.time() - t0:.1f}s", flush=True)

W, H = int(sys.argv[1]), int(sys.argv[2])
torch.cuda.reset_peak_memory_stats()
t1 = time.time()
image = pipe(prompt=PROMPT, width=W, height=H, generator=torch.Generator().manual_seed(42), output="images")[0]
torch.cuda.synchronize()
path = OUT + rf"\dragon_flux_klein_seed42_{W}x{H}.png"
image.save(path)
print(f"RESULT path={path} size={image.size} gen_s={time.time() - t1:.1f} peak_alloc_gb={torch.cuda.max_memory_allocated() / 2**30:.1f}", flush=True)

