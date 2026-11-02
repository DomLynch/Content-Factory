"""Pixal3D multi-view input (plugin 0.1.18): join the character sheet's views of one subject into ONE shape.

Capability we lacked: four-view input and pixel back-projection (joining the sheet's views into one shape). Pixal3D's multi-view pipeline (upstream
inference_mv.py, weights ckpts/*_mv, config pipeline_mv.json) takes V views with GIVEN cameras and averages their DINOv3 features in one 3D grid.

This module is CPU-only (PIL + standard library; no torch, no model, no GPU). It
  1. maps sheet cells to orbit angles (CELLS below),
  2. validates every view (alpha channel, same size, the 1024 px / subject-fraction input gate, width fit) and REFUSES with numbers,
  3. writes the upstream input directory: viewNN_azimAAA.png (square RGBA, subject re-framed to the same height) + transforms.json (exactly the keys inference_mv.load_views
     reads: camera_angle_x, mesh_scale, frames[file_path, name, transform_matrix]) + mv_manifest.json (hashes, angles, assumption flags; NOT read by upstream),
  4. checks the _mv weights are on disk (weights_reason) and runs the upstream function inside the Pixal3D venv (run_entry, called from tools/pixal3d_entry.py --mv-dir).

Cell to angle (azimuth: 0 = front, camera at (0,-d,0); 90 = camera at (+d,0,0) = the subject's LEFT side, subject faces image LEFT; 180 = back; 270 = the subject's RIGHT side,
subject faces image RIGHT; same formula as upstream assets/mv_images/example/transforms.json, verified by test against a copy of it):
  front cells                    0
  profile cells                  90 if the subject faces image-left, 270 if image-right (FACING below: decided by eye 2026-11-01, override with "facing" in the mapping file)
  three_quarter cells            45 / 315 (nominal: a Flux three-quarter is not measured; upstream accepts ANY c2w matrix, the features are averaged)
  back cells                     180
Assumptions are never silent: every one is a flag in mv_manifest.json and in the receipt (params.assumptions).
Mirroring a profile to make the missing opposite side is OFF by default (upstream takes any number of views V >= 1, "--num_views"), opt-in only (--mirror-profile), flagged.

CLI (assemble only, nothing heavy):  pixal3d_mv.py assemble <views folder | mapping.json | inline json> <out_dir> [--mirror-profile] [--no-three-quarter] [--check]
"""
from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# ---- camera rig: the upstream example rig (assets/mv_images/example/transforms.json): 20 degree horizontal fov, distance 3.1192, elevation 0, mesh_scale 1.0 ---------------------
CAMERA_ANGLE_X = 0.3490658503988659
DISTANCE = 3.1192049980163574
MESH_SCALE = 1.0
VIEW_SIZE = 1024            # inference_mv.load_views image_sizes=(512, 1024): nothing above 1024 is ever read
SUBJECT_FRAC = 0.85         # subject height as a fraction of the frame; upstream example views are 0.83 to 0.89
MAX_WIDTH_FRAC = 0.98       # a pose wider than this at that scale is refused (it would be cropped)
ALPHA_THRESHOLD = 16        # same subject test as lib/cf_gates.subject_box

# cell -> role. A cell is `<set>_<...>`; the set (head or body) is the prefix before the first underscore.
CELLS = {
    "head_front": "front", "head_profile": "profile", "head_three_quarter": "three_quarter", "head_back": "back",
    "body_underlayer_front": "front", "body_underlayer_left": "profile", "body_underlayer_right": "profile",
    "body_underlayer_three_quarter": "three_quarter", "body_underlayer_back": "back",
}
# which way the subject faces in the picture, looked at by eye on kits_out/vampire_gpt_sheet (2026-11-01): head_profile and head_three_quarter face image-left,
# body_underlayer_left faces image-left, body_underlayer_right faces image-right (but it reads as a three-quarter, not a true profile: flagged below).
FACING = {"head_profile": "left", "head_three_quarter": "left", "body_underlayer_left": "left", "body_underlayer_right": "right"}
NOTES = {"body_underlayer_right": "the right cell of the sheet is turned about three-quarters, not a true 270 degree profile (seen by eye): angle is nominal"}

# real sizes on disk (Models Dev, sha256-verified 2026-11-01): ss 5359822584, shape 512 / 1024 5546764048, tex 5546960656 bytes; json 503 / 535 bytes.
# the _mv weights: path relative to the MV model dir (config pixal3d.mv_model_dir, default work_dir/Pixal3D-run: the patched offline pipeline_mv.json plus a ckpts link to models/Pixal3D/ckpts) -> minimum size in bytes (about 99% of the single-view twin, same architecture; the task figures are 5.36 / 5.55 / 5.55 / 5.55 GB)
GB = 1e9
MV_FILES = {
    "pipeline_mv.json": 1000,
    "ckpts/ss_flow_img_dit_1_3B_64_bf16_mv.json": 100,
    "ckpts/ss_flow_img_dit_1_3B_64_bf16_mv.safetensors": 5.30 * GB,
    "ckpts/slat_flow_img2shape_dit_1_3B_512_bf16_mv.json": 100,
    "ckpts/slat_flow_img2shape_dit_1_3B_512_bf16_mv.safetensors": 5.49 * GB,
    "ckpts/slat_flow_img2shape_dit_1_3B_1024_bf16_mv.json": 100,
    "ckpts/slat_flow_img2shape_dit_1_3B_1024_bf16_mv.safetensors": 5.49 * GB,
    "ckpts/slat_flow_imgshape2tex_dit_1_3B_1024_bf16_mv.json": 100,
    "ckpts/slat_flow_imgshape2tex_dit_1_3B_1024_bf16_mv.safetensors": 5.49 * GB,
}


def _apply_config() -> None:
    """Numbers live in config/config.json pixal3d.multiview (the values above are the defaults and what the offline tests pin)."""
    global CAMERA_ANGLE_X, DISTANCE, MESH_SCALE, VIEW_SIZE, SUBJECT_FRAC, MAX_WIDTH_FRAC, FACING
    try:
        with open(os.path.join(REPO, "config", "config.json"), encoding="utf-8-sig") as f:
            mv = (json.load(f).get("pixal3d") or {}).get("multiview") or {}
    except Exception:
        return
    CAMERA_ANGLE_X = float(mv.get("camera_angle_x", CAMERA_ANGLE_X))
    DISTANCE = float(mv.get("distance", DISTANCE))
    MESH_SCALE = float(mv.get("mesh_scale", MESH_SCALE))
    VIEW_SIZE = int(mv.get("view_size", VIEW_SIZE))
    SUBJECT_FRAC = float(mv.get("subject_frac", SUBJECT_FRAC))
    MAX_WIDTH_FRAC = float(mv.get("max_width_frac", MAX_WIDTH_FRAC))
    FACING = {**FACING, **(mv.get("facing") or {})}


_apply_config()


class MVError(Exception):
    """A refusal: the message carries the numbers that failed. Nothing has been started."""


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---- weights ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def mv_model_dir(cfg: dict) -> str:
    """The folder passed to upstream as --model_path for multi-view: config.local pixal3d_mv_model_dir, else config pixal3d.mv_model_dir, else work_dir/Pixal3D-run."""
    loc, px = cfg.get("local") or {}, cfg.get("pixal3d") or {}
    return loc.get("pixal3d_mv_model_dir") or px.get("mv_model_dir") or os.path.join(loc.get("work_dir", ""), "Pixal3D-run")


def weights_missing(model_dir: str) -> list[dict]:
    out = []
    scale = float(os.environ.get("FACTORY_TEST_MV_WEIGHT_SCALE", "1"))   # test hook only (offline tests use tiny stand-in files), like FACTORY_FAKE_FREE_RAM_GB
    for rel, need in ((r, n * scale) for r, n in MV_FILES.items()):
        p = os.path.join(model_dir, *rel.split("/"))
        have = os.path.getsize(p) if os.path.isfile(p) else None
        if have is None or have < need:
            out.append({"file": rel, "path": p, "have_bytes": have, "need_at_least_bytes": int(need)})
    return out


def weights_reason(model_dir: str) -> str:
    """'' when every multi-view weight file is present and full size, else the refusal naming each file."""
    miss = weights_missing(model_dir)
    if not miss:
        return ""
    lines = [f"{m['file']} ({'absent' if m['have_bytes'] is None else str(m['have_bytes']) + ' bytes'}, need at least {m['need_at_least_bytes']} bytes = {m['need_at_least_bytes'] / GB:.2f} GB)" if m["file"].endswith(".safetensors")
             else f"{m['file']} ({'absent' if m['have_bytes'] is None else str(m['have_bytes']) + ' bytes'})" for m in miss]
    return (f"MULTI-VIEW WEIGHTS missing in {model_dir}: " + "; ".join(lines) + ". They come from TencentARC/Pixal3D (ckpts/*_mv and pipeline_mv.json); Models Dev fetches them, this tool never downloads.")


# ---- the views ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
def parse_spec(spec) -> dict:
    """Normalise a views argument into {"views": {cell: path}, "facing": {}, "mirror_profile": None, "three_quarter": None}.
    spec: a dict cell->path, a dict with a "views" key, a folder, a mapping .json file, or an inline json string."""
    if isinstance(spec, str):
        s = spec.strip()
        if s.startswith("{"):
            spec = json.loads(s)
        elif os.path.isdir(s):
            found = {}
            files = [f for f in glob.glob(os.path.join(s, "*")) if f.lower().endswith((".png", ".webp"))]
            for cell in CELLS:
                # the exact cell name, or the cell name followed by _ - or . (head_front_1024x1024.png, head_front.png)
                hits = [f for f in files if os.path.basename(f).startswith(cell) and os.path.basename(f)[len(cell)] in "_-."]
                if len(hits) > 1:
                    raise MVError(f"folder {s}: {len(hits)} files match cell {cell}: {sorted(os.path.basename(h) for h in hits)}; name one per cell or pass a mapping file")
                if hits:
                    found[cell] = hits[0]
            if not found:
                raise MVError(f"folder {s}: no file starts with any cell name {sorted(CELLS)}")
            spec = {"views": found}
        elif os.path.isfile(s):
            with open(s, encoding="utf-8-sig") as f:
                spec = json.load(f)
        else:
            raise MVError(f"views {s!r} is not a folder, a mapping file or inline json")
    if not isinstance(spec, dict):
        raise MVError("views must be a dict cell -> png path, a folder or a mapping json")
    views = spec["views"] if isinstance(spec.get("views"), dict) else spec
    meta = {k: spec.get(k) for k in ("facing", "mirror_profile", "three_quarter")} if "views" in spec else {}
    return {"views": {str(k): str(v) for k, v in views.items()}, "facing": dict(meta.get("facing") or {}),
            "mirror_profile": meta.get("mirror_profile"), "three_quarter": meta.get("three_quarter")}


def plan(cells: list[str], facing: dict | None = None, three_quarter: bool = True) -> list[dict]:
    """Ordered view plan [{cell, role, angle_deg, flags}] (frame 0 = front). Raises MVError on a mix of sets, no front, unknown cell or unknown facing."""
    facing = {**FACING, **(facing or {})}
    unknown = [c for c in cells if c not in CELLS]
    if unknown:
        raise MVError(f"unknown cell(s) {unknown}; known: {sorted(CELLS)}")
    sets = sorted({c.split("_")[0] for c in cells})
    if len(sets) > 1:
        raise MVError(f"one run joins ONE subject part, not {sets} (head cells with head cells, body cells with body cells)")
    rows = []
    for c in cells:
        role = CELLS[c]
        if role == "three_quarter" and not three_quarter:
            continue
        flags = []
        if role == "front":
            ang = 0
        elif role == "back":
            ang = 180
        else:
            side = facing.get(c)
            if side not in ("left", "right"):
                raise MVError(f"cell {c} ({role}): which way does the subject face in the picture? give facing {c}: left|right (no default is set)")
            if role == "profile":
                ang = 90 if side == "left" else 270
                flags.append("profile_facing_" + side + "_by_eye")
            else:
                ang = 45 if side == "left" else 315
                flags.append("three_quarter_nominal_angle")
        if c in NOTES:
            flags.append(NOTES[c])
        rows.append({"cell": c, "role": role, "angle_deg": ang, "flags": flags})
    if not any(r["role"] == "front" for r in rows):
        raise MVError("a front cell is required: frame 0 is the main view and must be the canonical front view (upstream check_main_view)")
    if len(rows) < 2:
        raise MVError("multi-view needs at least two views (front plus one more)")
    angles = [r["angle_deg"] for r in rows]
    if len(set(angles)) != len(angles):
        raise MVError(f"two cells share an azimuth: {[(r['cell'], r['angle_deg']) for r in rows]}")
    rows.sort(key=lambda r: r["angle_deg"])
    return rows


def transform_matrix(angle_deg: float, distance: float = DISTANCE) -> list[list[float]]:
    """Camera-to-world, Z-up world, camera looks along its -Z with +Y up, at elevation 0. angle 0 = (0,-d,0) front; same values as the upstream example for 0/90/180/270."""
    t = math.radians(angle_deg)
    c, s = math.cos(t), math.sin(t)
    m = [[c, 0.0, s, distance * s], [s, 0.0, -c, -distance * c], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
    return [[0.0 if abs(v) < 1e-12 else v for v in row] for row in m]


def canonical_front(distance: float) -> list[list[float]]:
    """What upstream check_main_view compares frame 0 with (ProjGridMV front_view_transform_matrix with [1,3] = -distance)."""
    return [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, -1.0, -distance], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def check_transforms(meta: dict, atol: float = 1e-4) -> list[str]:
    """Problems upstream load_views / check_main_view would hit, as strings ([] = fine). Mirrors the loader: frames, per-frame or top-level camera_angle_x, 4x4 matrices."""
    bad = []
    frames = meta.get("frames")
    if not isinstance(frames, list) or not frames:
        return ["no frames"]
    for i, fr in enumerate(frames):
        if "file_path" not in fr:
            bad.append(f"frame {i}: no file_path")
        if "camera_angle_x" not in fr and "camera_angle_x" not in meta:
            bad.append(f"frame {i}: no camera_angle_x (frame or top level)")
        m = fr.get("transform_matrix")
        if not (isinstance(m, list) and len(m) == 4 and all(isinstance(r, list) and len(r) == 4 for r in m)):
            bad.append(f"frame {i}: transform_matrix is not 4x4")
    if not bad:
        d0 = math.sqrt(sum(frames[0]["transform_matrix"][r][3] ** 2 for r in range(3)))
        err = max(abs(frames[0]["transform_matrix"][r][c] - canonical_front(d0)[r][c]) for r in range(4) for c in range(4))
        if err > atol:
            bad.append(f"frame 0 is not the canonical front view (max deviation {err:.3e}, tolerance {atol})")
    return bad


# ---- validation and assembly ----------------------------------------------------------------------------------------------------------------------------------
def _gates():
    sys.path.insert(0, os.path.join(REPO, "plugins", "factory", "lib"))
    import cf_gates
    import cf_guard
    return cf_gates, cf_guard


def inspect_view(cell: str, path: str, gates: bool = True) -> dict:
    """Open one cut-out and check it. Returns facts; raises MVError with numbers."""
    from PIL import Image
    if not os.path.isfile(path):
        raise MVError(f"view {cell}: file not found: {path}")
    with Image.open(path) as im:
        mode, size = im.mode, im.size
        if "A" not in im.getbands():
            raise MVError(f"view {cell}: {os.path.basename(path)} is {mode} {size[0]}x{size[1]} with NO alpha channel: run the cut-out stage first (multi-view takes the Factory's cut-out RGBA, never a matted-on-the-fly background)")
        alpha = im.getchannel("A")
    lo, hi = alpha.getextrema()
    if lo == 255:
        raise MVError(f"view {cell}: {os.path.basename(path)} has an alpha channel but it is fully opaque (alpha min {lo}, max {hi}): that is not a cut-out")
    box = alpha.point(lambda v: 255 if v > ALPHA_THRESHOLD else 0).getbbox()
    if not box:
        raise MVError(f"view {cell}: {os.path.basename(path)} alpha is empty (max {hi}): nothing to join")
    if gates:
        cf_gates, cf_guard = _gates()
        why = cf_guard.small_image_reason(path) or cf_gates.input_reason(path)
        if why:
            raise MVError(f"view {cell}: {why}")
    return {"size": list(size), "mode": mode, "alpha_min": lo, "alpha_max": hi, "subject_box": list(box)}


def validate(spec, mirror_profile: bool | None = None, three_quarter: bool | None = None, gates: bool = True) -> dict:
    """Everything that can be refused before anything is written. Returns the plan with facts per view."""
    p = parse_spec(spec)
    mirror = bool(p["mirror_profile"] if mirror_profile is None and p["mirror_profile"] is not None else mirror_profile)
    tq = True if three_quarter is None and p["three_quarter"] is None else bool(p["three_quarter"] if three_quarter is None else three_quarter)
    rows = plan(list(p["views"]), p["facing"], tq)
    for r in rows:
        r["source"] = p["views"][r["cell"]]
        r["facts"] = inspect_view(r["cell"], r["source"], gates)
    sizes = {tuple(r["facts"]["size"]) for r in rows}
    if len(sizes) > 1:
        raise MVError("views differ in size: " + ", ".join(f"{r['cell']} {r['facts']['size'][0]}x{r['facts']['size'][1]}" for r in rows) + ". Every view of one run must come from the same cut-out pipeline at one size.")
    for r in rows:
        x0, y0, x1, y1 = r["facts"]["subject_box"]
        bw, bh = x1 - x0, y1 - y0
        scale = SUBJECT_FRAC * VIEW_SIZE / bh
        r["scale"] = scale
        wfrac = bw * scale / VIEW_SIZE
        if wfrac > MAX_WIDTH_FRAC:
            raise MVError(f"view {r['cell']}: subject {bw}x{bh} px is too wide to frame at {SUBJECT_FRAC:.0%} height: it would fill {wfrac:.0%} of the frame width (limit {MAX_WIDTH_FRAC:.0%})")
    profiles = [r for r in rows if r["role"] == "profile"]
    r_assume = []
    if mirror and len(profiles) == 1:
        src = profiles[0]
        r_assume.append({"flag": "mirrored_profile", "detail": f"the {src['angle_deg']} degree view of {src['cell']} was flipped left-right to stand in for the {360 - src['angle_deg']} degree view that the sheet does not have; ears, hair parting, braids, asymmetric features are copied to the other side"})
    elif mirror:
        r_assume.append({"flag": "mirror_not_needed", "detail": f"mirror requested with {len(profiles)} profile views: nothing mirrored"})
    for r in rows:
        for f in r["flags"]:
            r_assume.append({"flag": f, "cell": r["cell"], "angle_deg": r["angle_deg"]})
    return {"rows": rows, "mirror": mirror and len(profiles) == 1, "assumptions": r_assume, "three_quarter": tq}


def frame_view(src_path: str, facts: dict, scale: float, size: int = VIEW_SIZE):
    """Re-frame one cut-out onto a transparent square: subject box scaled so its height is SUBJECT_FRAC of the frame, box centre on the frame centre."""
    from PIL import Image
    with Image.open(src_path) as im:
        rgba = im.convert("RGBA")
    x0, y0, x1, y1 = facts["subject_box"]
    crop = rgba.crop((x0, y0, x1, y1))
    nw, nh = max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))
    crop = crop.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(crop, ((size - nw) // 2, (size - nh) // 2))
    return canvas


def assemble(spec, out_dir: str, mirror_profile: bool | None = None, three_quarter: bool | None = None, gates: bool = True) -> dict:
    """Validate, then write out_dir/{viewNN_azimAAA.png, transforms.json, mv_manifest.json}. Returns the manifest (also written)."""
    from PIL import ImageOps
    v = validate(spec, mirror_profile, three_quarter, gates)
    os.makedirs(out_dir, exist_ok=True)
    items = []   # (angle, cell, image, derived, row)
    for r in v["rows"]:
        items.append((r["angle_deg"], r["cell"], frame_view(r["source"], r["facts"], r["scale"]), None, r))
    if v["mirror"]:
        src = next(r for r in v["rows"] if r["role"] == "profile")
        img = ImageOps.mirror(next(i[2] for i in items if i[1] == src["cell"]))
        items.append((360 - src["angle_deg"], src["cell"] + "__mirrored", img, src["cell"], src))
    items.sort(key=lambda t: t[0])
    frames, views = [], []
    for i, (ang, cell, img, derived, r) in enumerate(items):
        fn = f"view{i:02d}_azim{ang:03d}.png"
        img.save(os.path.join(out_dir, fn))
        frames.append({"file_path": fn, "name": f"azim{ang:03d}", "transform_matrix": transform_matrix(ang)})
        views.append({"cell": cell, "role": r["role"], "angle_deg": ang, "source": r["source"], "source_sha256": sha256(r["source"]), "source_size": r["facts"]["size"],
                      "subject_box": r["facts"]["subject_box"], "scale": round(r["scale"], 5), "mirrored_from": derived, "file": fn, "file_sha256": sha256(os.path.join(out_dir, fn))})
    meta = {"camera_angle_x": CAMERA_ANGLE_X, "mesh_scale": MESH_SCALE, "frames": frames}
    problems = check_transforms(meta)
    if problems:
        raise MVError("transforms.json would not load upstream: " + "; ".join(problems))
    tpath = os.path.join(out_dir, "transforms.json")
    with open(tpath, "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, indent=2)
    manifest = {"schema": 1, "mode": "multiview", "dir": out_dir, "set": views[0]["cell"].split("_")[0], "views": views, "angles_deg": [x["angle_deg"] for x in views],
                "camera": {"fov_deg": math.degrees(CAMERA_ANGLE_X), "camera_angle_x": CAMERA_ANGLE_X, "distance": DISTANCE, "elevation_deg": 0, "mesh_scale": MESH_SCALE},
                "framing": {"view_size": VIEW_SIZE, "subject_frac": SUBJECT_FRAC}, "assumptions": v["assumptions"], "mirror_profile": v["mirror"],
                "three_quarter_included": any(x["cell"].endswith("three_quarter") for x in views), "transforms_sha256": sha256(tpath), "front": os.path.join(out_dir, views[0]["file"])}
    with open(os.path.join(out_dir, "mv_manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def receipt_params(mv: dict, model_dir: str = "") -> dict:
    """The multiview fields of a pixal3d receipt (run_pixal3d.py merges them into params)."""
    return {"mode": "multiview", "pipeline": "PIXAL3D", "mv_dir": mv["dir"], "num_views": len(mv["views"]), "angles_deg": mv["angles_deg"],
            "views": [{k: x[k] for k in ("cell", "angle_deg", "source", "source_sha256", "file", "file_sha256", "mirrored_from")} for x in mv["views"]],
            "mirror_profile": mv["mirror_profile"], "three_quarter_included": mv["three_quarter_included"], "assumptions": mv["assumptions"],
            "camera": mv["camera"], "transforms_sha256": mv["transforms_sha256"], "weights_config": "pipeline_mv.json", "cameras": "given, not estimated (MoGe unused)",
            "rembg_mv": "not used: every view carries the cut-out alpha"}


def prepare(model_dir: str, views, out_dir: str, mirror_profile: bool | None = None, three_quarter: bool | None = None) -> dict:
    """run_pixal3d.py hook: weights first (cheap), then validate + assemble. Raises MVError."""
    why = weights_reason(model_dir)
    if why:
        raise MVError(why)
    return assemble(views, out_dir, mirror_profile, three_quarter)


# ---- inside the Pixal3D venv (called by pixal3d_entry.py --mv-dir) --------------------------------------------------------------------------------------------
def rewrite_mv_pipeline(model_dir: str, birefnet_dir: str) -> str:
    """pipeline_mv.json names briaai/RMBG-2.0 (non-commercial) like pipeline.json: rewrite once to the local MIT BiRefNet, keep pipeline_mv.json.orig. Returns the model name in force."""
    p = os.path.join(model_dir, "pipeline_mv.json")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    rb = d["args"]["rembg_model"]
    if "RMBG" in str(rb["args"].get("model_name", "")):
        import shutil
        shutil.copyfile(p, p + ".orig")
        rb["name"] = "BiRefNet"
        rb["args"]["model_name"] = birefnet_dir
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    return rb["args"]["model_name"]


def install_plain_upsample_mv(module) -> None:
    """--no-naf for multi-view: bicubic stand-in for NAF on the base extractor (the MV extractor inherits _load_naf). Upstream calls naf_model(image, lr_features, naf_target_size) with an
    INT size, so the size may be an int (square) or a pair."""
    import torch
    import torch.nn.functional as F

    class PlainUpsample(torch.nn.Module):
        def forward(self, image, features, output_size, *args, **kwargs):
            size = (int(output_size), int(output_size)) if isinstance(output_size, int) else tuple(int(v) for v in output_size)
            return F.interpolate(features.float(), size=size, mode="bicubic", align_corners=False).to(features.dtype)

    def _load_naf(self):
        if self.naf_model is None:
            self.naf_model = PlainUpsample().eval()

    module.DinoV3ProjFeatureExtractor._load_naf = _load_naf


def export_cfg() -> dict:
    """config pixal3d.export {module, path}: an optional module whose install() replaces upstream's cumesh GLB export (default empty = upstream as is)."""
    try:
        with open(os.path.join(REPO, "config", "config.json"), encoding="utf-8-sig") as f:
            return (json.load(f).get("pixal3d") or {}).get("export") or {}
    except Exception:
        return {}


def install_export(M=None, cfg: dict | None = None) -> str:
    """Import config pixal3d.export.module (its folder, export.path, first on sys.path) and call install() BEFORE the upstream call, so o_voxel.postprocess.to_glb is replaced.
    inference_mv.py's to_glb call is the same cumesh call as single view, which crashes on this ROCm build (io.hip:134): the CPU exporter from the export-fix PR
    (tools/pixal3d_glb_cpu.py, branch factory/pixal-export-0.1.16) is what this hook expects. That PR is NOT a dependency of this one: with module empty nothing is imported.
    Returns the module name installed ('' = none)."""
    cfg = export_cfg() if cfg is None else cfg
    name = (cfg.get("module") or "").strip()
    if not name:
        return ""
    if cfg.get("path"):
        sys.path.insert(0, cfg["path"] if os.path.isabs(cfg["path"]) else os.path.join(REPO, cfg["path"]))
    import importlib
    importlib.import_module(name).install()
    print("GLB export module installed:", name, flush=True)
    return name


def run_entry(a) -> None:
    """Called from pixal3d_entry.main() when --mv-dir is given, after sys.path (repo, natten shim) and env (sdpa, offline, TORCH_HOME) are set and cwd is the repo.
    Same local-model overrides as the single-view path: local DINOv3, BiRefNet in pipeline_mv.json; MoGe is not used (cameras are given); NAF shim or --no-naf."""
    print("rembg model (pipeline_mv.json):", rewrite_mv_pipeline(a.model_dir, a.birefnet_dir), flush=True)
    import inference_mv as M   # upstream inference_mv.py (its __main__ block does not run on import)
    install_export(M)

    for conf in M.IMAGE_COND_CONFIGS.values():
        conf["model_name"] = a.dino_dir
    if a.no_naf:
        from pixal3d.trainers.flow_matching.mixins import image_conditioned_proj as ICP
        install_plain_upsample_mv(ICP)
        print("NAF OFF: bicubic feature upsample (ROUGH / NAF-off)", flush=True)
    else:
        import natten
        print("natten:", natten.__version__, getattr(natten, "__file__", ""), flush=True)
    print("MULTIVIEW: views_dir", a.mv_dir, "(cameras given, MoGe not loaded)", flush=True)
    M.run_inference(views_dir=a.mv_dir, output_path=a.output, seed=a.seed, model_path=a.model_dir, low_vram=a.low_vram, resolution=a.resolution)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="assemble a Pixal3D multi-view input directory from sheet cut-outs (CPU only)")
    ap.add_argument("cmd", choices=("assemble",))
    ap.add_argument("views", help="folder, mapping .json or inline json")
    ap.add_argument("out_dir")
    ap.add_argument("--mirror-profile", action="store_true", default=None)
    ap.add_argument("--no-three-quarter", action="store_true")
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    a = ap.parse_args(argv)
    try:
        tq = False if a.no_three_quarter else None
        if a.check:
            v = validate(a.views, a.mirror_profile, tq)
            print(json.dumps({"ok": True, "views": [(r["cell"], r["angle_deg"]) for r in v["rows"]], "assumptions": v["assumptions"]}, indent=1))
        else:
            m = assemble(a.views, a.out_dir, a.mirror_profile, tq)
            print(json.dumps({"ok": True, "dir": m["dir"], "angles_deg": m["angles_deg"], "assumptions": m["assumptions"]}, indent=1))
    except MVError as e:
        print("REFUSED:", e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
