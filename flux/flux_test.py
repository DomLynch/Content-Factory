import faulthandler, json, os, shutil, sys, time

faulthandler.dump_traceback_later(600, exit=True)  # hard stop at 10 minutes
HOME = os.path.expanduser("~")
sys.path.insert(0, HOME + r"\Documents\flux_env\pkgs")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import torch
import diffusers
import transformers
from diffusers import ModularPipeline

LOCAL = HOME + r"\Documents\models\flux-3-image-klein"
OUT = HOME + r"\Documents\flux_test"
os.makedirs(OUT, exist_ok=True)
print("diffusers", diffusers.__version__, "transformers", transformers.__version__, "torch", torch.__version__, flush=True)

# point the modular index at the plain local folder (the pup entries keep their hub ids and are not loaded)
idx = LOCAL + r"\modular_model_index.json"
if not os.path.exists(idx + ".orig"):
    shutil.copyfile(idx, idx + ".orig")
j = json.load(open(idx + ".orig"))
for k in ("text_encoder", "tokenizer", "transformer", "vae"):
    j[k][2]["pretrained_model_name_or_path"] = LOCAL.replace("\\", "/")
json.dump(j, open(idx, "w"), indent=2)

PROMPT = ("Full-body front view of Vlad the Impaler as Count Dracula, 15th-century Wallachian voivode, standing straight facing the camera, "
          "arms relaxed at his sides, head to boots fully visible. Pale gaunt face, long black hair, thin moustache, faint red eyes, stern expression. "
          "Crimson damask long coat with dense gold embroidery and a high stiff collar, heavy black fur mantle over the shoulders, ornate leather belt "
          "with a double-headed eagle buckle, embroidered tabard with a castle and cross motif, white ruffled cuffs, rings, tall black leather boots with "
          "gilded knee plates, long dark cloak to the floor. Photorealistic, ultra-detailed fabric and metal, soft studio lighting, plain dark charcoal "
          "background, centred, symmetrical, 4k character concept sheet.")

t0 = time.time()
from diffusers import Flux3DistilledAutoBlocks
blocks = Flux3DistilledAutoBlocks()
blocks.sub_blocks.pop('prompt_upsampler')  # encode the prompt exactly as given (no upsampler model)
pipe = blocks.init_pipeline(LOCAL)
print("pipeline object:", type(pipe).__name__, round(time.time() - t0, 1), "s", flush=True)
pipe.load_components(names=["text_encoder", "tokenizer", "transformer", "vae"], torch_dtype=torch.bfloat16)
pipe.to("cuda")
torch.cuda.synchronize()
load_s = time.time() - t0
print(f"loaded in {load_s:.1f}s; GPU allocated {torch.cuda.memory_allocated() / 2**30:.1f} GB", flush=True)

torch.cuda.reset_peak_memory_stats()
t1 = time.time()
image = pipe(prompt=PROMPT, width=1024, height=1360, generator=torch.Generator().manual_seed(42), output="images")[0]
torch.cuda.synchronize()
gen_s = time.time() - t1
path = OUT + r"\dracula_flux_klein_seed42.png"
image.save(path)
print("SIZE", image.size, flush=True)
print(f"RESULT path={path} load_s={load_s:.1f} gen_s={gen_s:.1f} peak_alloc_gb={torch.cuda.max_memory_allocated() / 2**30:.1f} peak_reserved_gb={torch.cuda.max_memory_reserved() / 2**30:.1f}", flush=True)


