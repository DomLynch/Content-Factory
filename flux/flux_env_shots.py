import faulthandler, os, sys, time

faulthandler.dump_traceback_later(2400, exit=True)
HOME = os.path.expanduser("~")
sys.path.insert(0, HOME + r"\Documents\flux_env\pkgs")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import torch
from diffusers import Flux3DistilledAutoBlocks

LOCAL = HOME + r"\Documents\models\flux-3-image-klein"
OUT = HOME + r"\Documents\flux_test"
W, H = int(sys.argv[1]), int(sys.argv[2])

STYLE = ("Photorealistic grim dark fantasy AAA game environment concept art in the spirit of Dark Souls and Elden Ring: restrained palette, "
         "volumetric fog, cold ash-grey light with small warm ember accents, believable weathered stone, wet surfaces, fine material detail, "
         "cinematic composition, ultra detailed, sharp, no characters, no text, not cartoon, not stylised, not cheesy, no neon.")
SHOTS = {
    "hades_arena": ("Elevated three-quarter view looking down on a broken stone battle arena in the realm of Hades, like a tabletop turn-based tactics board: "
                    "a clear walkable floor of cracked flagstones with low ruined walls, fallen pillars and rubble as cover, a chasm edge and a distant black river, "
                    "open lanes for movement, readable terrain for a strategy game. "),
    "hades_gate": ("Wide view of a colossal weathered gate of the underworld carved from black basalt, a long stone causeway leading to it across a dark river of slow mist, "
                   "dead gnarled trees, drifting ash, enormous scale, faint ember glow behind the gate. "),
    "hades_hall": ("Interior of a vast ruined cathedral-like hall in the underworld, tall broken ribbed arches, toppled statues, shallow black water on the floor reflecting "
                   "a few braziers, shafts of pale light through gaps in the ceiling, dust and ash in the air. "),
}
names = sys.argv[3:] or list(SHOTS)

t0 = time.time()
blocks = Flux3DistilledAutoBlocks()
blocks.sub_blocks.pop("prompt_upsampler")
pipe = blocks.init_pipeline(LOCAL)
pipe.load_components(names=["text_encoder", "tokenizer", "transformer", "vae"], torch_dtype=torch.bfloat16)
pipe.to("cuda")
for fn in ("enable_slicing", "enable_tiling"):
    if hasattr(pipe.vae, fn):
        getattr(pipe.vae, fn)()
torch.cuda.synchronize()
print(f"loaded in {time.time() - t0:.1f}s", flush=True)

for n in names:
    torch.cuda.reset_peak_memory_stats()
    t1 = time.time()
    image = pipe(prompt=SHOTS[n] + STYLE, width=W, height=H, generator=torch.Generator().manual_seed(7), output="images")[0]
    torch.cuda.synchronize()
    path = OUT + rf"\env_{n}_{W}x{H}.png"
    image.save(path)
    print(f"RESULT {n} path={path} gen_s={time.time() - t1:.1f} peak_alloc_gb={torch.cuda.max_memory_allocated() / 2**30:.1f}", flush=True)
