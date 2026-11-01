"""Point the two LLM2Vec adapters at a LOCAL Llama 3 8B Instruct mirror so the ARDY/Kimodo text encoder loads with no network and no Hugging Face token.
Standard library only. Usage: prepare_text_encoder.py <text_encoders_dir> <llama_mirror_dir> [--check]

LLM2Vec = Llama 3 8B Instruct + two small LoRA adapters (McGill-NLP, MIT). The adapters' adapter_config.json names `meta-llama/Meta-Llama-3-8B-Instruct`, which is a gated
Hugging Face repo; here that name is replaced by the local mirror directory (original saved once as adapter_config.json.orig). Nothing is downloaded.
Only the adapter folders under <text_encoders_dir>/McGill-NLP/ are touched; the mirror is only read.
Exit 0: ready (or --check passed). Exit 2: a problem, printed one per line (mirror incomplete, adapter missing, ...)."""
import json, os, shutil, sys

ADAPTERS = ("LLM2Vec-Meta-Llama-3-8B-Instruct-mntp", "LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised")
GATED_ID = "meta-llama/Meta-Llama-3-8B-Instruct"


def mirror_problems(mirror: str) -> list:
    out = []
    if not os.path.isdir(mirror):
        return [f"llama mirror folder not found: {mirror}"]
    for f in ("config.json", "LICENSE"):
        if not os.path.isfile(os.path.join(mirror, f)):
            out.append(f"mirror file missing: {f}")
    idx = os.path.join(mirror, "model.safetensors.index.json")
    if not os.path.isfile(idx):
        return out + ["mirror file missing: model.safetensors.index.json (download not finished?)"]
    try:
        shards = sorted(set(json.load(open(idx, encoding="utf-8"))["weight_map"].values()))
    except Exception as e:
        return out + [f"mirror index unreadable: {type(e).__name__}"]
    for s in shards:
        if not os.path.isfile(os.path.join(mirror, s)):
            out.append(f"mirror shard missing: {s}")
    return out


def adapter_problems(enc_dir: str) -> list:
    out = []
    for a in ADAPTERS:
        d = os.path.join(enc_dir, "McGill-NLP", a)
        for f in ("adapter_config.json", "adapter_model.safetensors"):
            if not os.path.isfile(os.path.join(d, f)):
                out.append(f"adapter file missing: McGill-NLP/{a}/{f}")
    d = os.path.join(enc_dir, "McGill-NLP", ADAPTERS[0])
    for f in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        if not os.path.isfile(os.path.join(d, f)):
            out.append(f"adapter file missing: McGill-NLP/{ADAPTERS[0]}/{f}")
    return out


def current_base(enc_dir: str, adapter: str) -> str:
    return json.load(open(os.path.join(enc_dir, "McGill-NLP", adapter, "adapter_config.json"), encoding="utf-8")).get("base_model_name_or_path", "")


def apply(enc_dir: str, mirror: str) -> list:
    """Rewrite base_model_name_or_path in both adapter configs. Returns the problems (empty = done)."""
    probs = mirror_problems(mirror) + adapter_problems(enc_dir)
    if probs:
        return probs
    target = os.path.abspath(mirror)
    for a in ADAPTERS:
        path = os.path.join(enc_dir, "McGill-NLP", a, "adapter_config.json")
        cfg = json.load(open(path, encoding="utf-8"))
        if os.path.abspath(cfg.get("base_model_name_or_path", "")) == target:
            continue
        if cfg.get("base_model_name_or_path") != GATED_ID:
            return [f"{a}: unexpected base_model_name_or_path {cfg.get('base_model_name_or_path')!r}, not touching it"]
        if not os.path.exists(path + ".orig"):
            shutil.copy2(path, path + ".orig")
        cfg["base_model_name_or_path"] = target
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, indent=2)
    return []


def check(enc_dir: str, mirror: str) -> list:
    probs = mirror_problems(mirror) + adapter_problems(enc_dir)
    if probs:
        return probs
    target = os.path.abspath(mirror)
    return [f"{a}: adapter_config base is {current_base(enc_dir, a)!r}, not the local mirror (run prepare_text_encoder.py)" for a in ADAPTERS
            if os.path.abspath(current_base(enc_dir, a)) != target]


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    enc, mirror = argv[1], argv[2]
    probs = check(enc, mirror) if "--check" in argv else apply(enc, mirror)
    for p in probs:
        print("PROBLEM", p)
    print("text encoder ready: " + mirror if not probs else "text encoder NOT ready")
    return 2 if probs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
