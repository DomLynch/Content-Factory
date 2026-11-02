"""Pre-encode Flux 3 klein prompts: flux_embeds.py <kit.json> [row_id ...]   (no row ids = every row with use_ref false)
Loads ONLY the Qwen3-VL text encoder + tokenizer (no transformer, no VAE), encodes each requested row's final prompt with the
pipeline's own Flux3DistilledTextEncoderStep code path (_get_qwen3_vl_prompt_embeds, same defaults: pad_multiple from the block,
max_sequence_length 8192, layers 4..32), and writes kits/embeds/<kit name>/<row_id>.pt next to the kit with:
  prompt_embeds (cpu, bfloat16), txt_ids, prompt (exact string), prompt_sha256, embeds_sha256, encoder settings.
Scope of this first version: rows with use_ref false only (gear and swatch rows). Rows that use a reference image are skipped
with a SKIP line (the reference image also goes through the VAE encoder, which this tool does not do).
The text encoder runs on cuda in the checkpoint dtype exactly as in flux_kit.py, so the embeds are what flux_kit.py computes.
Env: CF_DRY=1 prints the plan (final prompt head + sha256, target file) without importing torch.
final_prompt() is the single place that builds the prompt text; flux_kit_pre.py imports it for its stale-embeds check."""
import hashlib, json, os, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
import cf_common


def final_prompt(kit, row, cfg):
    """Same logic as flux_kit.py / flux_kit_fast.py plan(): returns (prompt, W, H, ref_path, use_ref)."""
    prompt = " ".join([row["prompt"], kit["subject"] if row.get("use_subject", True) else "", kit["style"]]).strip()
    W, H = row.get("w", kit.get("w", cfg["defaults"]["w"])), row.get("h", kit.get("h", cfg["defaults"]["h"]))
    ref_path = row.get("ref", kit.get("ref"))
    use_ref = bool(ref_path) and row.get("use_ref", True)
    if use_ref:
        prompt = row.get("ref_prefix", kit["ref_prefix"]) + " " + prompt
    return prompt, W, H, ref_path, use_ref


def prompt_sha256(prompt):
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def embeds_path(kit_path, kit, row_id):
    return os.path.join(os.path.dirname(os.path.abspath(kit_path)), "embeds", kit["kit"], f"{row_id}.pt")


def tensor_sha256(t):
    import torch
    t = t.detach().cpu().contiguous()
    if t.dtype == torch.bfloat16:
        return hashlib.sha256(t.view(torch.int16).numpy().tobytes()).hexdigest()
    return hashlib.sha256(t.numpy().tobytes()).hexdigest()


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: flux_embeds.py <kit.json> [row_id ...]")
    cfg = cf_common.load_config()
    LOC = cfg["local"]
    kit_path = sys.argv[1]
    kit = json.load(open(kit_path, encoding="utf-8-sig"))
    by_id = {r.get("row_id", r["name"]): r for r in kit["rows"]}
    ids = sys.argv[2:] or [i for i, r in by_id.items() if not final_prompt(kit, r, cfg)[4]]
    jobs = []
    for i in ids:
        if i not in by_id:
            raise SystemExit(f"row not found in {kit_path}: {i}")
        prompt, W, H, ref_path, use_ref = final_prompt(kit, by_id[i], cfg)
        if use_ref:
            print(f"SKIP {i}: use_ref is true (reference rows are not supported by flux_embeds.py yet)", flush=True)
            continue
        jobs.append({"row_id": i, "prompt": prompt, "sha": prompt_sha256(prompt), "path": embeds_path(kit_path, kit, i)})
    if os.environ.get("CF_DRY"):
        print(json.dumps([{"row_id": j["row_id"], "path": j["path"], "prompt_sha256": j["sha"], "prompt_head": j["prompt"][:120]} for j in jobs], indent=1))
        return 0
    if not jobs:
        print("nothing to encode", flush=True)
        return 0

    sys.path.insert(0, LOC["flux_pkgs_dir"])
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    import faulthandler, time
    import torch
    from diffusers import Flux3DistilledAutoBlocks
    from diffusers.modular_pipelines.flux3.encoders import Flux3DistilledTextEncoderStep
    from diffusers.modular_pipelines.flux3.before_denoise import Flux3TextRoPEInputsStep

    cf_common.wait_for_free_ram(cfg, "flux text encode")
    faulthandler.dump_traceback_later(cfg["limits"]["watchdog_seconds"], exit=True)
    t0 = time.time()
    blocks = Flux3DistilledAutoBlocks()
    blocks.sub_blocks.pop("prompt_upsampler")
    enc_block = blocks.sub_blocks["text_encoder"]   # same block class flux_kit.py runs; supplies pad_multiple
    pipe = blocks.init_pipeline(LOC["flux_model_dir"])
    pipe.load_components(names=["text_encoder", "tokenizer"], torch_dtype=getattr(torch, cfg["models"]["flux"]["dtype"]))
    pipe.text_encoder.to("cuda")
    torch.cuda.synchronize()
    print(f"text encoder loaded in {time.time() - t0:.1f}s", flush=True)

    MAXLEN, LAYERS = 8192, (4, 8, 12, 16, 20, 24, 28, 32)   # the block's input defaults (flux_kit.py passes neither)
    for j in jobs:
        t1 = time.time()
        with torch.no_grad():
            emb = Flux3DistilledTextEncoderStep._get_qwen3_vl_prompt_embeds(
                text_encoder=pipe.text_encoder, tokenizer=pipe.tokenizer, prompt=j["prompt"], device=torch.device("cuda"),
                max_sequence_length=MAXLEN, pad_multiple=enc_block.pad_multiple, hidden_states_layers=LAYERS)
        emb = emb.detach().cpu().contiguous()
        txt_ids = Flux3TextRoPEInputsStep._prepare_text_ids(emb)
        esha = tensor_sha256(emb)
        os.makedirs(os.path.dirname(j["path"]), exist_ok=True)
        torch.save({"kit": kit["kit"], "row_id": j["row_id"], "prompt": j["prompt"], "prompt_sha256": j["sha"], "prompt_embeds": emb,
                    "txt_ids": txt_ids, "embeds_sha256": esha, "dtype": str(emb.dtype), "pad_multiple": enc_block.pad_multiple,
                    "max_sequence_length": MAXLEN, "text_encoder_out_layers": list(LAYERS), "model": cfg["models"]["flux"]["id"]}, j["path"])
        print(f"EMBEDS {j['row_id']} shape={tuple(emb.shape)} dtype={emb.dtype} embeds_sha256={esha[:12]} {time.time() - t1:.1f}s -> {j['path']}", flush=True)
    faulthandler.cancel_dump_traceback_later()
    return 0


if __name__ == "__main__":
    sys.exit(main())
