"""Runs INSIDE the Motion-312 venv (torch ROCm + ardy). Generates one motion with NVIDIA ARDY and writes the NPZ plus a motion JSON that retarget_blender.py reads.

Usage: ardy_generate.py --text T --model core40 --duration 4 --seed 0 --out-dir D --checkpoints-dir C --ardy-repo R
         [--text-encoders-dir E] [--text-encoder-device cpu|cuda] [--device cpu|gpu] [--no-postprocess] [--diffusion-steps N]

It runs ARDY's own scripts/generate.py unmodified (runpy). Only the text encoder loading is steered, with environment variables ARDY already reads:
  TEXT_ENCODER_MODE=local   never the localhost text-encoder service
  TEXT_ENCODERS_DIR         a folder holding McGill-NLP/<LLM2Vec adapters>; their adapter_config.json points at the LOCAL Llama 3 mirror (prepare_text_encoder.py)
  HF_HUB_OFFLINE=1          no network, no token: nothing here reads or sends a Hugging Face token
  TEXT_ENCODER_DEVICE       cpu keeps the encoder on the CPU (ARDY reads it at load time, but then moves the encoder with .to(cuda): patched below so cpu stays cpu)
Two guards on the encoder (both asserted, never silent; the code is in encoder_patch.py, shared with kimodo_generate.py):
  - LLM2Vec only applies the Llama 3 chat template when config._name_or_path is exactly meta-llama/Meta-Llama-3-8B-Instruct; a local mirror path would silently change
    every embedding, so that name is enforced after loading.
  - embeddings are moved to the model device after a CPU encode.
"""
import argparse, json, os, runpy, sys, time

LLAMA_ID = "meta-llama/Meta-Llama-3-8B-Instruct"


def args():
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--model", default="core40")
    p.add_argument("--duration", type=float, default=4.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--checkpoints-dir", required=True)
    p.add_argument("--ardy-repo", required=True)
    p.add_argument("--text-encoders-dir", default="")
    p.add_argument("--text-encoder-device", default="")
    p.add_argument("--device", default="gpu", choices=["gpu", "cpu"])
    p.add_argument("--llama-id", default=LLAMA_ID, help="config motion.text_encoder_name: the exact name LLM2Vec needs for the Llama 3 chat template")
    p.add_argument("--no-postprocess", action="store_true")
    p.add_argument("--diffusion-steps", type=int, default=0)
    return p.parse_args()


def steer_environment(a):
    os.environ["TEXT_ENCODER_MODE"] = "local"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    if a.text_encoders_dir:
        os.environ["TEXT_ENCODERS_DIR"] = a.text_encoders_dir
    if a.text_encoder_device:
        os.environ["TEXT_ENCODER_DEVICE"] = a.text_encoder_device
    if a.device == "cpu":
        os.environ["HIP_VISIBLE_DEVICES"] = "-1"
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    os.environ["CHECKPOINTS_DIR"] = a.checkpoints_dir


def patch_encoder(a):
    """The two encoder guards (chat-template name, cpu stays cpu) live in encoder_patch.py, shared with kimodo_generate.py."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import encoder_patch
    encoder_patch.patch("ardy", a.llama_id, a.text_encoder_device)


def export_json(npz_path, json_path, a, postprocess=False):
    import numpy as np
    from ardy.skeleton import build_skeleton
    z = np.load(npz_path, allow_pickle=True)
    glob = np.asarray(z["global_rot_mats"], dtype=np.float64)   # [T, J, 3, 3]
    root = np.asarray(z["root_positions"], dtype=np.float64)    # [T, 3]
    J = glob.shape[1]
    skel = build_skeleton(J)
    names = list(skel.bone_order_names)
    rest = skel.neutral_joints.detach().cpu().numpy().astype(np.float64)
    assert len(names) == J == rest.shape[0], (len(names), J, rest.shape)
    doc = {"schema": 1, "source": "ardy", "model": a.model, "text": a.text, "seed": a.seed, "fps": float(z["fps"]), "frames": int(glob.shape[0]),
           "skeleton": skel.name, "joints": names, "parents": [int(x) for x in skel.joint_parents.tolist()],
           "rest_positions": np.round(rest, 6).tolist(), "global_rot": np.round(glob.reshape(glob.shape[0], J, 9), 6).tolist(),
           "root_positions": np.round(root, 6).tolist(), "postprocess": bool(postprocess), "axes": "y-up, +z forward, metres (glTF convention)"}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return doc["frames"], doc["skeleton"], doc["fps"]


def main():
    a = args()
    steer_environment(a)
    sys.path.insert(0, a.ardy_repo)
    os.makedirs(a.out_dir, exist_ok=True)
    patch_encoder(a)
    stem = os.path.join(a.out_dir, "ardy_motion")
    argv = ["generate.py", a.text, "--model", a.model, "--duration", str(a.duration), "--seed", str(a.seed), "--output", stem, "--checkpoints_dir", a.checkpoints_dir]
    import importlib.util
    have_mc = importlib.util.find_spec("motion_correction") is not None   # the optional C++ foot-skate clean-up
    postprocess = have_mc and not a.no_postprocess
    if not postprocess:
        argv.append("--no-postprocess")
        print(f"POSTPROCESS OFF ({'extension motion_correction not built' if not have_mc else 'asked for'}): foot skating is not cleaned up", flush=True)
    if a.diffusion_steps:
        argv += ["--diffusion_steps", str(a.diffusion_steps)]
    sys.argv = argv
    t0 = time.time()
    runpy.run_path(os.path.join(a.ardy_repo, "scripts", "generate.py"), run_name="__main__")
    frames, skel, fps = export_json(stem + ".npz", os.path.join(a.out_dir, "motion.json"), a, postprocess)
    print(f"ARDY OK frames={frames} skeleton={skel} fps={fps} wall_s={time.time() - t0:.1f}", flush=True)


if __name__ == "__main__":
    main()
