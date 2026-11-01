"""Runs INSIDE the Kimodo-312 venv (torch + kimodo, transformers==5.1.0: NOT the Motion-312 venv, ARDY needs 5.8.1). Generates one motion with NVIDIA Kimodo (SOMA, 77 joints)
and writes the NPZ plus the same motion JSON that retarget_blender.py reads for ARDY (schema 1).   DRAFT: written from the Kimodo code, never run.

Usage: kimodo_generate.py --text T --model Kimodo-SOMA-RP-v1.2 --duration 4 --seed 0 --out-dir D --checkpoints-dir C --kimodo-repo R
         [--constraints constraints.json] [--text-encoders-dir E] [--text-encoder-device cpu|cuda] [--device cpu|gpu] [--fps 30]
         [--diffusion-steps 100] [--llama-id meta-llama/Meta-Llama-3-8B-Instruct] [--no-postprocess]

It runs Kimodo's own `kimodo.scripts.generate` unmodified (runpy). Only the loading is steered, with environment variables Kimodo already reads:
  CHECKPOINT_DIR            the folder that holds Kimodo-SOMA-RP-v1.2/ (config.yaml, model.safetensors, stats/); Kimodo downloads from Hugging Face when it is missing,
                            so run_motion.py refuses before this script starts if the files are absent or the wrong size
  TEXT_ENCODER_MODE=local   never the localhost text-encoder service
  TEXT_ENCODERS_DIR         a folder holding McGill-NLP/<LLM2Vec adapters> (their adapter_config.json points at the LOCAL Llama 3 mirror: prepare_text_encoder.py)
  HF_HUB_OFFLINE=1          no network, no token
  TEXT_ENCODER_DEVICE       cpu keeps the encoder on the CPU (encoder_patch.py keeps cpu from being moved back to cuda)
Kimodo splits the prompt at periods and generates one segment per sentence with transitions (each sentence gets --duration seconds). Constraints: a Kimodo constraints.json
(root2d, fullbody, left-hand, right-hand, left-foot, right-foot, end-effector; frame_indices 0-based, y-up metres, relative to the canonical origin), validated by run_motion.py.
The output is somaskel77 (the model converts its 30-joint working skeleton to 77 before saving). Kimodo's post-process (foot-skate clean-up) needs the C++ `motion_correction`
extension; when it is not importable it is switched off and recorded in motion.json (`postprocess`).
"""
import argparse, json, os, runpy, sys, time

LLAMA_ID = "meta-llama/Meta-Llama-3-8B-Instruct"


def args():
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--model", default="Kimodo-SOMA-RP-v1.2")
    p.add_argument("--duration", type=float, default=4.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--checkpoints-dir", required=True)
    p.add_argument("--kimodo-repo", required=True)
    p.add_argument("--constraints", default="")
    p.add_argument("--text-encoders-dir", default="")
    p.add_argument("--text-encoder-device", default="")
    p.add_argument("--device", default="gpu", choices=["gpu", "cpu"])
    p.add_argument("--fps", type=float, default=30.0, help="the model's fps (config.yaml motion_rep.fps); Kimodo's NPZ does not carry it")
    p.add_argument("--diffusion-steps", type=int, default=0)
    p.add_argument("--llama-id", default=LLAMA_ID, help="config motion.text_encoder_name")
    p.add_argument("--no-postprocess", action="store_true")
    return p.parse_args()


def steer_environment(a):
    os.environ["TEXT_ENCODER_MODE"] = "local"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["CHECKPOINT_DIR"] = a.checkpoints_dir
    if a.text_encoders_dir:
        os.environ["TEXT_ENCODERS_DIR"] = a.text_encoders_dir
    if a.text_encoder_device:
        os.environ["TEXT_ENCODER_DEVICE"] = a.text_encoder_device
    if a.device == "cpu":
        os.environ["HIP_VISIBLE_DEVICES"] = "-1"
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"


def export_json(npz_path, json_path, a, postprocess=False):
    import numpy as np
    from kimodo.skeleton import build_skeleton
    z = np.load(npz_path, allow_pickle=False)
    glob = np.asarray(z["global_rot_mats"], dtype=np.float64)   # [T, J, 3, 3]
    root = np.asarray(z["root_positions"], dtype=np.float64)    # [T, 3]
    J = glob.shape[1]
    skel = build_skeleton(J)
    names = list(skel.bone_order_names)
    rest = skel.neutral_joints.detach().cpu().numpy().astype(np.float64)
    assert len(names) == J == rest.shape[0], (len(names), J, rest.shape)
    doc = {"schema": 1, "source": "kimodo", "model": a.model, "text": a.text, "seed": a.seed, "fps": float(a.fps), "frames": int(glob.shape[0]),
           "skeleton": skel.name, "joints": names, "parents": [int(x) for x in skel.joint_parents.tolist()],
           "rest_positions": np.round(rest, 6).tolist(), "global_rot": np.round(glob.reshape(glob.shape[0], J, 9), 6).tolist(),
           "root_positions": np.round(root, 6).tolist(), "postprocess": bool(postprocess), "constraints": os.path.basename(a.constraints) if a.constraints else "",
           "axes": "y-up, +z forward, metres (glTF convention)"}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return doc["frames"], doc["skeleton"], doc["fps"]


def main():
    a = args()
    steer_environment(a)
    sys.path.insert(0, a.kimodo_repo)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(a.out_dir, exist_ok=True)
    import encoder_patch
    encoder_patch.patch("kimodo", a.llama_id, a.text_encoder_device)
    stem = os.path.join(a.out_dir, "kimodo_motion")
    argv = ["generate.py", a.text, "--model", a.model, "--duration", str(a.duration), "--output", stem, "--seed", str(a.seed)]
    if a.constraints:
        argv += ["--constraints", a.constraints]
    if a.diffusion_steps:
        argv += ["--diffusion_steps", str(a.diffusion_steps)]
    import importlib.util
    have_mc = importlib.util.find_spec("motion_correction") is not None   # the optional C++ foot-skate clean-up
    postprocess = have_mc and not a.no_postprocess
    if not postprocess:
        argv.append("--no-postprocess")
        print(f"POSTPROCESS OFF ({'extension motion_correction not built' if not have_mc else 'asked for'}): foot skating is not cleaned up", flush=True)
    sys.argv = argv
    t0 = time.time()
    runpy.run_module("kimodo.scripts.generate", run_name="__main__", alter_sys=True)
    frames, skel, fps = export_json(stem + ".npz", os.path.join(a.out_dir, "motion.json"), a, postprocess)
    print(f"KIMODO OK frames={frames} skeleton={skel} fps={fps} wall_s={time.time() - t0:.1f}", flush=True)


if __name__ == "__main__":
    main()
