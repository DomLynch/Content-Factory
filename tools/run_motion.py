"""Generate one motion clip from text with NVIDIA ARDY (default) or NVIDIA Kimodo and retarget it onto the psyche-106 rig (plugin 0.1.8, Kimodo backend 0.1.12, stage `motion`).
Usage: run_motion.py <asset_id> "<text>" [--backend ardy|kimodo] [--constraints FILE.json] [--duration S] [--seed N] [--clip-id ID] [--in-place] [--install] [--device gpu|cpu] [--encoder-device cuda|cpu]
Backends: ardy = ARDY Core (27 joints, 20 fps) in the Motion-312 venv, text only. kimodo = Kimodo-SOMA-RP-v1.2 (SOMA 77 joints, 30 fps) in its OWN Kimodo-312 venv (config.local kimodo_python,
default <work_dir>/Kimodo-312; transformers 5.1.0 against ARDY's 5.8.1; never created here), text plus optional sparse constraints (root2d path, fullbody keyframes, hand/foot/end-effector
constraints: a Kimodo constraints.json, validated before anything starts). Same retarget step, same receipt shape (params.backend), same gates. DRAFT: neither backend has run on a GPU.
Steps: (1) tools/motion/prepare_text_encoder.py logic points the LLM2Vec adapters at the LOCAL Llama 3 mirror (no network, no token);
(2) Motion-312 python runs tools/motion/ardy_generate.py (ARDY scripts/generate.py unmodified) -> ardy_motion.npz + motion.json;
(3) headless Blender runs tools/motion/retarget_blender.py -> <clip_id>.glb in the Mixamo clip format (mixamorig:* nodes, no skin, animation `clip`);
(4) the clip is checked, optionally copied (--install, never overwriting) into mixamo_clips_dir, and one receipt is written.
The clip is found by the stills stage by NAME: set config stills.clips.<idle|walk|attack> to the clip id (the file is <mixamo_clips_dir>/<clip id>.glb).
Settings come from config/config.json ("motion") and config.local.json (motion_python, ardy_repo_dir, ardy_checkpoints_dir, kimodo_python, kimodo_repo_dir, kimodo_checkpoints_dir, text_encoders_dir, llama_mirror_dir, blender_exe, rig_glb).
Nothing is downloaded here. The GPU is used only by step (2) with --device gpu; the MCP tool starts this under the GPU lock and RAM gate."""
import argparse, hashlib, json, math, os, re, shutil, struct, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "motion"))
import cf_common
import prepare_text_encoder as pte

CLIP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def parse(argv):
    p = argparse.ArgumentParser(prog="run_motion.py", description="ARDY text-to-motion retargeted onto psyche-106")
    p.add_argument("asset_id")
    p.add_argument("text")
    p.add_argument("--duration", type=float, default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--clip-id", default="")
    p.add_argument("--in-place", action="store_true", help="drop the horizontal root travel (stills of idle or walk-in-place)")
    p.add_argument("--install", action="store_true", help="also copy the clip into mixamo_clips_dir (never overwrites)")
    p.add_argument("--device", default="gpu", choices=["gpu", "cpu"])
    p.add_argument("--encoder-device", default="", choices=["", "cuda", "cpu"])
    p.add_argument("--backend", default="ardy")
    p.add_argument("--constraints", default=None, help="kimodo only: path to a Kimodo constraints.json (a list of constraint objects)")
    return p.parse_args(argv)


def backend_spec(m, loc, backend):
    """Everything that differs between the two generators. Paths come from config.local.json; the Kimodo ones default under work_dir (venv Kimodo-312) when the key is absent."""
    if backend == "kimodo":
        wd, nt = loc.get("work_dir", ""), os.name == "nt"

        def pick(key, *rel):
            return loc.get(key) or (os.path.join(wd, *rel) if wd else "")
        return {"name": "kimodo", "label": "Kimodo", "python": pick("kimodo_python", m["kimodo_venv_name"], "Scripts" if nt else "bin", "python.exe" if nt else "python"),
                "repo": pick("kimodo_repo_dir", "kimodo"), "ckpt": pick("kimodo_checkpoints_dir", "models", "kimodo"), "model": m["kimodo_model"], "model_dir": m["kimodo_model_dir"],
                "skeleton": m["kimodo_expected_skeleton"], "prefix": m["kimodo_clip_prefix"], "guard": m["kimodo_guard_match"], "script": "kimodo_generate.py", "npz": "kimodo_motion.npz",
                "weights": ("config.yaml", "model.safetensors", "LICENSE"), "fps": m["kimodo_fps"]}
    return {"name": "ardy", "label": "ARDY", "python": loc.get("motion_python", ""), "repo": loc.get("ardy_repo_dir", ""), "ckpt": loc.get("ardy_checkpoints_dir", ""),
            "model": m["ardy_model"], "model_dir": m["ardy_model_dir"], "skeleton": m["ardy_expected_skeleton"], "prefix": m["clip_prefix"], "guard": m["guard_match"],
            "script": "ardy_generate.py", "npz": "ardy_motion.npz", "weights": ("config.yaml", "denoiser.safetensors", "tokenizer.safetensors", "LICENSE"), "fps": 20}


def sentence_count(text):
    """Kimodo splits the prompt at periods and generates one segment per sentence."""
    return len([t for t in text.split(".") if t.strip()])


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _shape_ok(x, shape):
    """x is a nested list of finite numbers with exactly this shape (None = any length at that level)."""
    if not shape:
        return _is_num(x)
    return isinstance(x, list) and (shape[0] is None or len(x) == shape[0]) and all(_shape_ok(v, shape[1:]) for v in x)


def constraint_problems(raw, m, n_frames):
    """Problems with a Kimodo constraints list (the format of kimodo docs/source/user_guide/constraints.md). [] = fine. Pure checks, no torch."""
    c, out = m["constraints"], []
    if not isinstance(raw, list) or not raw:
        return ["constraints must be a non-empty JSON list of constraint objects"]
    if len(raw) > c["max_sets"]:
        out.append(f"{len(raw)} constraint sets, the limit is {c['max_sets']} (config motion.constraints.max_sets)")
    for i, el in enumerate(raw[:c["max_sets"]]):
        tag = f"constraint {i}"
        if not isinstance(el, dict):
            out.append(f"{tag} is not an object")
            continue
        t = el.get("type")
        if t not in c["types"]:
            out.append(f"{tag}: type {t!r} is not one of {', '.join(c['types'])}")
            continue
        fi = el.get("frame_indices")
        if not (isinstance(fi, list) and fi and all(isinstance(f, int) and not isinstance(f, bool) for f in fi)):
            out.append(f"{tag} ({t}): frame_indices must be a non-empty list of integers")
            continue
        if len(fi) > c["max_frames_per_set"] or len(set(fi)) != len(fi):
            out.append(f"{tag} ({t}): frame_indices must be unique and at most {c['max_frames_per_set']} (config motion.constraints.max_frames_per_set)")
        if min(fi) < 0 or max(fi) >= n_frames:
            out.append(f"{tag} ({t}): frame_indices must be 0 to {n_frames - 1} (this clip has {n_frames} frames)")
        T = len(fi)
        if t == "root2d":
            if not _shape_ok(el.get("smooth_root_2d"), (T, 2)):
                out.append(f"{tag} (root2d): smooth_root_2d must be {T} pairs [x, z] of finite numbers (metres, relative to the start)")
            if "global_root_heading" in el and not _shape_ok(el["global_root_heading"], (T, 2)):
                out.append(f"{tag} (root2d): global_root_heading must be {T} pairs [cos, sin]")
            continue
        if not _shape_ok(el.get("root_positions"), (T, 3)):
            out.append(f"{tag} ({t}): root_positions must be {T} triples [x, y, z] of finite numbers (y = absolute hip height in metres)")
        rot = el.get("local_joints_rot")
        if not (isinstance(rot, list) and len(rot) == T and all(isinstance(r, list) and len(r) in c["joint_counts"] for r in rot) and _shape_ok(rot, (T, None, 3))):
            out.append(f"{tag} ({t}): local_joints_rot must be {T} x ({' or '.join(str(j) for j in c['joint_counts'])}) joints x 3 axis-angle numbers")
        if "smooth_root_2d" in el and not (_shape_ok(el["smooth_root_2d"], (T, 2)) or _shape_ok(el["smooth_root_2d"], (T, 3))):
            out.append(f"{tag} ({t}): smooth_root_2d must be {T} pairs [x, z]")
        if t == "end-effector":
            jn = el.get("joint_names")
            if not (isinstance(jn, list) and jn and all(isinstance(j, str) and j for j in jn)):
                out.append(f"{tag} (end-effector): joint_names must be a non-empty list of names (e.g. left_hand, right_foot)")
    return out


def read_constraints(spec, m):
    """(list or None, problems). spec is a path to a constraints.json, an already-parsed list, or None."""
    if spec in (None, "", []):
        return None, []
    lim = m["constraints"]["max_json_bytes"]
    if isinstance(spec, list):
        raw = spec
    else:
        path = str(spec)
        if not os.path.isfile(path):
            return None, [f"constraints file not found: {path}"]
        if os.path.getsize(path) > lim:
            return None, [f"constraints file is {os.path.getsize(path)} bytes, the limit is {lim} (config motion.constraints.max_json_bytes)"]
        try:
            raw = json.load(open(path, encoding="utf-8-sig"))
        except Exception as e:
            return None, [f"constraints file is not JSON: {type(e).__name__}: {e}"]
    if len(json.dumps(raw)) > lim:
        return None, [f"constraints are over {lim} bytes of JSON"]
    return raw, []


def default_clip_id(m, text, seed, backend="ardy"):
    return (m["kimodo_clip_prefix"] if backend == "kimodo" else m["clip_prefix"]) + re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32].strip("-") + "-" + hashlib.sha1(f"{text}|{seed}".encode("utf-8")).hexdigest()[:6]


def problems(cfg, a, free_ram_gb=None):
    """Every reason the run cannot start, as a list of strings. Pure checks: nothing is started, written or downloaded."""
    m, loc = cfg["motion"], cfg["local"]
    out = []
    backend = a.backend
    if backend not in m["backends_enabled"]:
        out.append(f"backend {backend!r} is not enabled (config motion.backends_enabled is {m['backends_enabled']})")
        backend = m["backend"]
    sp = backend_spec(m, loc, backend)
    text = a.text.strip()
    if not text:
        out.append("text is empty")
    if len(text) > m["text_max_chars"]:
        out.append(f"text is {len(text)} characters, the limit is {m['text_max_chars']} (config motion.text_max_chars)")
    dur = a.duration if a.duration is not None else m["default_duration_s"]
    dur_ok = math.isfinite(dur) and 1.0 <= dur <= m["max_duration_s"]
    if not dur_ok:
        out.append(f"duration {dur} s is outside 1 to {m['max_duration_s']} s (the model is trained on windows of at most 10 s)")
    if backend == "kimodo" and dur_ok and sentence_count(text) * dur > m["max_duration_s"]:
        out.append(f"Kimodo makes one {dur} s segment per sentence: {sentence_count(text)} sentences give {sentence_count(text) * dur} s, over {m['max_duration_s']} s (one sentence per clip)")
    if a.clip_id and not CLIP_ID.match(a.clip_id):
        out.append(f"clip id {a.clip_id!r} is not a plain file name (letters, digits, . _ -)")
    spec = getattr(a, "constraints", None)
    if spec not in (None, "", []):
        if backend != "kimodo":
            out.append("constraints are only supported by backend 'kimodo'; ARDY takes text only")
        else:
            raw, cp = read_constraints(spec, m)
            out += cp
            if raw is not None:
                if sentence_count(text) > 1:
                    out.append("constraints need a one-sentence text (Kimodo splits at periods into separate segments, so frame indices would be ambiguous)")
                out += constraint_problems(raw, m, int((dur if dur_ok else m["default_duration_s"]) * sp["fps"]))
    for k in ("blender_exe", "rig_glb", "text_encoders_dir", "llama_mirror_dir"):
        if k not in loc:
            out.append(f"config.local.json has no {k} (see config.local.example.json)")
        elif not os.path.exists(loc[k]):
            out.append(f"{k} not found: {loc[k]}")
    if backend == "ardy":
        for k in ("motion_python", "ardy_repo_dir", "ardy_checkpoints_dir"):
            if k not in loc:
                out.append(f"config.local.json has no {k} (see config.local.example.json)")
            elif not os.path.exists(loc[k]):
                out.append(f"{k} not found: {loc[k]}")
        if "ardy_checkpoints_dir" in loc:
            d = os.path.join(loc["ardy_checkpoints_dir"], m["ardy_model_dir"])
            for f in ("config.yaml", "denoiser.safetensors", "tokenizer.safetensors"):
                if not os.path.isfile(os.path.join(d, f)):
                    out.append(f"ARDY weights file missing: {os.path.join(d, f)}")
    else:
        if not sp["python"]:
            out.append("config.local.json has no kimodo_python and no work_dir to default it under (see config.local.example.json)")
        elif not os.path.isfile(sp["python"]):
            out.append(f"Kimodo venv python not found: {sp['python']} (the {m['kimodo_venv_name']} venv needs kimodo's own pins, transformers==5.1.0 against ARDY's 5.8.1; this plugin never creates it)")
        if not sp["repo"] or not os.path.isfile(os.path.join(sp["repo"], "kimodo", "scripts", "generate.py")):
            out.append(f"kimodo_repo_dir has no kimodo/scripts/generate.py: {sp['repo'] or '(not set)'}")
        d = os.path.join(sp["ckpt"], sp["model_dir"]) if sp["ckpt"] else ""
        for f in sp["weights"]:
            if not d or not os.path.isfile(os.path.join(d, f)):
                out.append(f"Kimodo weights file missing: {os.path.join(d, f)}")
        wf = os.path.join(d, "model.safetensors")
        if d and os.path.isfile(wf) and os.path.getsize(wf) != m["kimodo_weights_bytes"]:
            out.append(f"Kimodo weights {wf} are {os.path.getsize(wf)} bytes, expected {m['kimodo_weights_bytes']} (download unfinished or wrong file)")
        if d and not os.path.isdir(os.path.join(d, "stats", "motion")):
            out.append(f"Kimodo weights folder has no stats/motion: {os.path.join(d, 'stats', 'motion')}")
    if "text_encoders_dir" in loc and "llama_mirror_dir" in loc and os.path.isdir(loc["llama_mirror_dir"]) and os.path.isdir(loc["text_encoders_dir"]):
        out += pte.mirror_problems(loc["llama_mirror_dir"], m.get("llama_shard_bytes")) + pte.adapter_problems(loc["text_encoders_dir"])
    enc = a.encoder_device or m["text_encoder_device"]
    if enc == "cpu" and free_ram_gb is not None and free_ram_gb < m["min_free_ram_gb_encoder_cpu"]:
        out.append(f"the text encoder on the CPU needs about {m['min_free_ram_gb_encoder_cpu']} GB free RAM (16 GB of bf16 weights) and {free_ram_gb} GB is free")
    if a.install and "mixamo_clips_dir" in loc:
        cid = a.clip_id or default_clip_id(m, text, a.seed if a.seed is not None else m["default_seed"], backend)
        dst = os.path.join(loc["mixamo_clips_dir"], cid + m["clip_ext"])
        if os.path.exists(dst):
            out.append(f"--install would overwrite an existing clip: {dst}")
    elif a.install:
        out.append("config.local.json has no mixamo_clips_dir to install into")
    return out


def glb_json(path):
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"glTF":
            raise ValueError("not a GLB file")
        n, kind = struct.unpack("<II", f.read(8))
        if kind != 0x4E4F534A:
            raise ValueError("first GLB chunk is not JSON")
        return json.loads(f.read(n))


def check_clip(path, m, frames):
    """Problems with an exported clip against the Mixamo clip format run_stills.py reads. [] = fine."""
    try:
        j = glb_json(path)
    except Exception as e:
        return [f"clip unreadable: {type(e).__name__}: {e}"]
    out = []
    pre = m["bone_prefix"]
    nodes = {n.get("name", "") for n in j.get("nodes", [])}
    if not any(n.startswith(pre) for n in nodes):
        out.append("clip has no mixamorig:* nodes")
    if (pre + "Hips") not in nodes:
        out.append("clip has no mixamorig:Hips node")
    if j.get("skins") or j.get("meshes"):
        out.append("clip carries a skin or mesh (a Mixamo clip has bone-named nodes only)")
    anims = j.get("animations", [])
    if len(anims) != 1:
        out.append(f"clip has {len(anims)} animations, expected exactly one")
    else:
        if anims[0].get("name") != m["clip_action_name"]:
            out.append(f"animation is named {anims[0].get('name')!r}, expected {m['clip_action_name']!r}")
        if len(anims[0].get("channels", [])) < 2 * len(m["retarget"]["required_bones"]):
            out.append(f"animation has only {len(anims[0].get('channels', []))} channels")
        acc = j.get("accessors", [])
        counts = {acc[s["input"]]["count"] for s in anims[0].get("samplers", [])}
        if counts and max(counts) < max(2, frames // 2):
            out.append(f"animation has {max(counts)} keys for {frames} generated frames")
    return out


def finite_motion(path):
    j = json.load(open(path, encoding="utf-8"))
    flat = [x for fr in j["global_rot"] for jt in fr for x in jt] + [x for r in j["root_positions"] for x in r]
    return j, all(math.isfinite(x) for x in flat)


def run(cmd, log, timeout, env=None, cwd=None):
    try:
        with open(log, "w", encoding="utf-8") as lf:
            return subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout, env=env, cwd=cwd).returncode
    except subprocess.TimeoutExpired:
        return "timeout"


def main(argv=None):
    a = parse(sys.argv[1:] if argv is None else argv)
    cfg = cf_common.load_config()
    m, loc = cfg["motion"], cfg["local"]
    text = a.text.strip()
    seed = a.seed if a.seed is not None else m["default_seed"]
    dur = a.duration if a.duration is not None else m["default_duration_s"]
    backend = a.backend if a.backend in m["backends_enabled"] else m["backend"]   # problems() reports an unknown backend; the receipt below records what was asked
    sp = backend_spec(m, loc, backend)
    clip_id = a.clip_id or default_clip_id(m, text, seed, backend)
    enc_dev = a.encoder_device or m["text_encoder_device"]
    work = os.path.join(loc["outputs_dir"], m["subdir"], a.asset_id, clip_id)
    clip = os.path.join(work, clip_id + m["clip_ext"])
    logs = loc["logs_dir"]
    gen_log, ret_log = os.path.join(logs, f"motion_gen_{a.asset_id}.txt"), os.path.join(logs, f"motion_retarget_{a.asset_id}.txt")
    model_dir = os.path.join(sp["ckpt"], sp["model_dir"])
    con_path = os.path.join(work, "constraints.json")
    constraints = getattr(a, "constraints", None) or None
    inputs = [os.path.join(model_dir, f) for f in sp["weights"]] + [loc.get("rig_glb", "")]
    base_params = {"text": text, "seed": seed, "duration_s": dur, "backend": a.backend, "model": sp["model"], "text_encoder_device": enc_dev, "device": a.device,
                   "clip_id": clip_id, "in_place": a.in_place, "gen_log": gen_log, "retarget_log": ret_log, "blender": loc.get("blender_exe", ""),
                   "constraints": bool(constraints), "text_encoder_name": m["text_encoder_name"]}

    def receipt(status, error="", timing=None, mem=None, outputs=(), extra=None):
        mirror_lic = os.path.join(loc.get("llama_mirror_dir", ""), "LICENSE")
        kimodo = backend == "kimodo"
        notes = {"weights_licence": ("NVIDIA Open Model License (nvidia/%s LICENSE, hashed in inputs)" % m["kimodo_model_dir"]) if kimodo
                 else "NVIDIA Open Model Agreement (nvidia/ARDY-Core-RP-20FPS-Horizon40 LICENSE, hashed in inputs)",
                 "code_licence": "Apache-2.0 (nv-tlabs/kimodo)" if kimodo else "Apache-2.0 (nv-tlabs/ardy)",
                 "encoder_licence": "Meta Llama 3 Community License (mirror LICENSE sha256 below); LLM2Vec adapters MIT",
                 "llama_mirror_licence_sha256": cf_common.sha256(mirror_lic) if os.path.exists(mirror_lic) else "",
                 "notice": "Licensed by NVIDIA Corporation under the NVIDIA Open Model License. Built with Meta Llama 3. See THIRD_PARTY_NOTICES.md."}
        return cf_common.write_receipt(cfg, a.asset_id, "motion", tool=f"run_motion.py ({sp['label']} + Blender retarget)",
                                       model={"id": "nvidia/" + sp["model_dir"], "text_encoder": "LLM2Vec-Meta-Llama-3-8B-Instruct (local mirror)"},
                                       inputs=inputs + ([con_path] if constraints and os.path.exists(con_path) else []),
                                       params={**base_params, **notes, **(extra or {})}, timing=timing or {}, memory=mem or {}, outputs=list(outputs), status=status, error=error)

    probs = problems(cfg, a, cf_common.FreeRamWatch()._free_gb() if os.name == "nt" else None)
    if probs:
        receipt("fail", "; ".join(probs))
        sys.exit("cannot generate motion:\n  " + "\n  ".join(probs))
    probs = pte.apply(loc["text_encoders_dir"], loc["llama_mirror_dir"], m["llama_shard_bytes"])
    if probs:
        receipt("fail", "; ".join(probs))
        sys.exit("text encoder not ready:\n  " + "\n  ".join(probs))
    os.makedirs(work, exist_ok=True)
    os.makedirs(logs, exist_ok=True)
    for p in (clip, os.path.join(work, "motion.json"), os.path.join(work, sp["npz"]), os.path.join(work, "retarget_result.json"), con_path):
        if os.path.exists(p):
            os.remove(p)
    if constraints:   # problems() validated it: keep a normalised copy beside the clip, hashed into the receipt
        raw, _ = read_constraints(constraints, m)
        with open(con_path, "w", encoding="utf-8") as f:
            json.dump(raw, f)

    ram = cf_common.FreeRamWatch(); ram.start()
    t0 = time.time()
    gen = [sp["python"], "-u", os.path.join(HERE, "motion", sp["script"]), "--text", text, "--model", sp["model"], "--duration", str(dur), "--seed", str(seed),
           "--out-dir", work, "--checkpoints-dir", sp["ckpt"], "--ardy-repo" if backend == "ardy" else "--kimodo-repo", sp["repo"], "--text-encoders-dir", loc["text_encoders_dir"],
           "--text-encoder-device", enc_dev, "--device", a.device, "--llama-id", m["text_encoder_name"]]
    if backend == "kimodo":
        gen += ["--fps", str(sp["fps"]), "--diffusion-steps", str(m["kimodo_diffusion_steps"])] + (["--constraints", con_path] if constraints else [])
    env = dict(os.environ, PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        env.pop(k, None)   # the generation never needs, reads or sends a token
    rc = run(gen, gen_log, m["timeout_s"], env=env)
    motion_json = os.path.join(work, "motion.json")
    if rc != 0 or not os.path.exists(motion_json):
        receipt("fail", f"{sp['label']} generation exit {rc}, see log", timing={"wall_s": round(time.time() - t0, 1)}, mem={"lowest_free_ram_gb": ram.stop()})
        sys.exit(f"{sp['label']} generation failed (exit {rc}); log: {gen_log}")
    doc, finite = finite_motion(motion_json)
    bad = []
    if doc["skeleton"] != sp["skeleton"]:
        bad.append(f"skeleton {doc['skeleton']} is not {sp['skeleton']}")
    if not finite or doc["frames"] < 2:
        bad.append("generated motion has NaN or too few frames")
    if bad:
        receipt("fail", "; ".join(bad), timing={"wall_s": round(time.time() - t0, 1)}, mem={"lowest_free_ram_gb": ram.stop()}, outputs=[motion_json])
        sys.exit(f"bad {sp['label']} output: " + "; ".join(bad))
    t_gen = time.time() - t0

    argf, resf = os.path.join(work, "_retarget_args.json"), os.path.join(work, "retarget_result.json")
    with open(argf, "w", encoding="utf-8") as f:
        json.dump({"motion": motion_json, "rig_glb": loc["rig_glb"], "out_glb": clip, "result": resf, "cfg": m, "in_place": a.in_place}, f)
    rc = run([loc["blender_exe"], "--background", "--python", os.path.join(HERE, "motion", "retarget_blender.py"), "--", argf], ret_log, m["retarget_timeout_s"])
    res = json.load(open(resf, encoding="utf-8")) if os.path.exists(resf) else {}
    cprobs = check_clip(clip, m, doc["frames"]) if os.path.exists(clip) else ["no clip written"]
    ok = rc == 0 and not cprobs
    outs = [p for p in (clip, motion_json, os.path.join(work, sp["npz"])) if os.path.exists(p)]
    installed = ""
    if ok and a.install:
        installed = os.path.join(loc["mixamo_clips_dir"], clip_id + m["clip_ext"])
        if os.path.exists(installed):
            ok, cprobs = False, [f"refusing to overwrite {installed}"]
        else:
            shutil.copyfile(clip, installed)
    status = "ok" if ok else "fail"
    receipt(status, "" if ok else (f"retarget exit {rc}; " if rc != 0 else "") + "; ".join(cprobs),
            timing={"wall_s": round(time.time() - t0, 1), "generate_s": round(t_gen, 1), "retarget_s": round(time.time() - t0 - t_gen, 1)}, mem={"lowest_free_ram_gb": ram.stop()},
            outputs=outs, extra={"frames": doc["frames"], "fps": doc["fps"], "skeleton": doc["skeleton"], "retarget": res, "installed_clip": installed,
                                 "postprocess": doc.get("postprocess"),
                                 "stills_use": f"set config stills.clips.<idle|walk|attack> to \"{clip_id}\" (clip file {clip_id}{m['clip_ext']} in mixamo_clips_dir)"})
    print("motion", status.upper() if not ok else "ok", "->", clip)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
