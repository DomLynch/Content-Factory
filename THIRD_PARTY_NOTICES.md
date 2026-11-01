# Third-party notices (plugin 0.1.8, `motion_generate`)

Nothing below is redistributed in this repository: no weights, no model code and no licence text of the third parties are copied here. The Content-Factory only
calls these projects from local folders on the machine (`config/config.local.json`). The notices are kept here because the licences ask for them when the
software or its outputs are used and shown.

Licensed by NVIDIA Corporation under the NVIDIA Open Model License

Built with Meta Llama 3

## NVIDIA ARDY (code and weights)
| Part | Source | Licence |
|---|---|---|
| ARDY code | github.com/nv-tlabs/ardy, commit `e706e27acc88070918308a10922b8f7174d0825e` | Apache License 2.0 (`LICENSE`, sha256 `5523be8aaec6cb600dc81db7983ddfb145a142b6ac6e64c4c96861d3f716c2d2`), Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES |
| ARDY weights `ARDY-Core-RP-20FPS-Horizon40` | huggingface.co/nvidia/ARDY-Core-RP-20FPS-Horizon40, revision `fe1f897459605c98d5418d036bfcfe73bc244fd8` | NVIDIA Open Model Agreement / NVIDIA Open Model License (the repo's `LICENSE`, sha256 `11ccdcb47db87ac30b63765b5e5154403026c4fbb52c19919d99d912ec454e19`). Model card: ready for commercial or non-commercial use. |

Terms of the NVIDIA Open Model License that matter here (read from the weights' own `LICENSE` file, 2026-11-01): the licence is revocable and terminates if You bypass a
guardrail or start patent or copyright litigation over the model (2.1); use must follow NVIDIA's Trustworthy AI terms (2.3); NVIDIA claims no ownership of outputs and
You are responsible for them (2.4); a recipient of the Model must get a copy of the Agreement and the "Licensed by NVIDIA Corporation under the NVIDIA Open Model
License" notice in a Notice text file (3.1, which is why the line stands at the top of this file). The model was trained on the Bones Rigplay 1 dataset; the model card
asks that prompts describe actions in neutral terms. Re-read the licence text itself before shipping anything built on these weights.

## NVIDIA Kimodo (cloned, weights downloaded, path not run in 0.1.8)
| Part | Source | Licence |
|---|---|---|
| Kimodo code | github.com/nv-tlabs/kimodo, commit `e1c219c99b4b94ba48be1fb6a1cbc64ecee4c954` | Apache License 2.0 (`LICENSE`, sha256 `987057aca9b5c6f338cadfcc2666d74a093c43e6fd02173c5d9ed63ec66cc53d`) |
| Kimodo weights `Kimodo-SOMA-RP-v1.2` (SOMA 77 joints, the README's recommended full Bones Rigplay 1 model) | huggingface.co/nvidia/Kimodo-SOMA-RP-v1.2 | NVIDIA Open Model License (same notice as above) |

## Text encoder: LLM2Vec on Llama 3
ARDY and Kimodo condition on text with LLM2Vec, which is Meta Llama 3 8B Instruct plus two LoRA adapters:
- Meta Llama 3 8B Instruct weights from the ungated mirror `unsloth/llama-3-8b-Instruct`, kept in `Documents\models\llama-3-8b-instruct-mirror` with its own `LICENSE`
  (Meta Llama 3 Community License Agreement, release date April 18, 2024; the file's sha256 is `6bcca341999ab474e2973116128177b77f97cd64028eeace224035358ce6474f`).
  The Llama 3 licence asks that a product or service that uses the Llama Materials prominently displays "Built with Meta Llama 3" on a related website, user interface,
  blogpost, about page or product documentation, and that use follows Meta's Acceptable Use Policy (https://llama.meta.com/llama3/use-policy). The notice is repeated in
  each `motion` receipt and in the `motion_generate` tool result.
- LLM2Vec adapters `McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp` and `...-mntp-supervised`: MIT licence (Copyright (c) 2024 McGill NLP; ARDY's vendored copy of the
  LLM2Vec code in `ardy/model/llm2vec` is MIT AND Apache-2.0 and carries the McGill copyright header).

The gated Hugging Face repo `meta-llama/Meta-Llama-3-8B-Instruct` is not used: the adapters' `adapter_config.json` is pointed at the local mirror folder
(`tools/motion/prepare_text_encoder.py`), the original kept as `adapter_config.json.orig`. If the owner's access request to the gated repo is granted, nothing
here changes; the mirror serves the same weights.

## Build-time and Python dependencies
The Motion-312 venv installs ARDY's pinned Python dependencies from PyPI (torch ROCm copied from the Trellis venv, transformers 5.8.1, peft, hydra-core and others),
each under its own licence. ARDY's optional C++ motion-correction extension fetches pybind11, Eigen and (on Arm) sse2neon with CMake FetchContent when built; their
licences (BSD-style, MPL-2.0, MIT) apply to that build only. See ARDY's `ATTRIBUTIONS.MD` for the vendored notices (LLM2Vec MIT, Unitree mujoco BSD-3-Clause).
