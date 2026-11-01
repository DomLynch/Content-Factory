"""Runs INSIDE the ARDY or Kimodo venv (needs torch and the package). The two guards on the LLM2Vec text encoder that both generators share (plugin 0.1.12).

ARDY (`ardy.model.llm2vec`) and Kimodo (`kimodo.model.llm2vec`) vendor the same LLM2Vec loader: base = <TEXT_ENCODERS_DIR>/McGill-NLP/LLM2Vec-...-mntp (its adapter_config.json
names the Llama 3 8B Instruct base, rewritten to the local mirror by prepare_text_encoder.py), peft = <TEXT_ENCODERS_DIR>/McGill-NLP/LLM2Vec-...-mntp-supervised.

patch(package, llama_id, encoder_device) applies, never silently:
  - LLM2Vec only applies the Llama 3 chat template when config._name_or_path is exactly `llama_id` (config motion.text_encoder_name, meta-llama/Meta-Llama-3-8B-Instruct);
    a local mirror path would silently change every embedding, so the name is enforced after loading and asserted.
  - TEXT_ENCODER_DEVICE=cpu: the package moves the encoder with .to(cuda) after loading; that is turned back into cpu, and the embeddings are moved to cuda after a CPU encode.
"""
from __future__ import annotations

import importlib
import os


def patch(package: str, llama_id: str, encoder_device: str = "") -> None:
    import torch
    l2v = importlib.import_module(f"{package}.model.llm2vec.llm2vec")
    wrap = importlib.import_module(f"{package}.model.llm2vec.llm2vec_wrapper")
    orig_from = l2v.LLM2Vec.from_pretrained.__func__

    def from_pretrained(cls, *a, **k):
        m = orig_from(cls, *a, **k)
        if m.model.config._name_or_path != llama_id:
            print(f"ENCODER NOTE: config._name_or_path was {m.model.config._name_or_path!r}, set to {llama_id} so the chat template applies", flush=True)
            m.model.config._name_or_path = llama_id
        assert m.model.config._name_or_path == llama_id
        return m
    l2v.LLM2Vec.from_pretrained = classmethod(from_pretrained)

    pinned = encoder_device or os.environ.get("TEXT_ENCODER_DEVICE", "")
    orig_to, orig_call = wrap.LLM2VecEncoder.to, wrap.LLM2VecEncoder.__call__

    def to(self, device=None, *rest, **kw):
        if pinned == "cpu" and device is not None and str(device) != "cpu":
            print(f"ENCODER NOTE: TEXT_ENCODER_DEVICE=cpu, keeping the encoder on the CPU (asked for {device})", flush=True)
            device = "cpu"
        return orig_to(self, device, *rest, **kw)

    def call(self, text):
        feat, lengths = orig_call(self, text)
        if pinned == "cpu" and torch.cuda.is_available():
            feat = feat.to("cuda")
        return feat, lengths
    wrap.LLM2VecEncoder.to, wrap.LLM2VecEncoder.__call__ = to, call
