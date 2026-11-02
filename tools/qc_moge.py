"""MoGe-2 extra input for the qc stage: faceting and normal/depth agreement of a render against its concept image. Written 2026-11-01, plugin 0.1.13.

STATUS: the pure scoring functions below are unit-tested (stdlib only, plain Python lists, no numpy). The model call in `infer_view` / `main` is UNRUN: it is written against the
MoGe README / `moge.model.v2.MoGeModel` API and has never been executed here (no torch, no weights load, no GPU was touched when it was written).

Usage (under the Pixal3D venv python, which has torch + moge; run_qc.py starts it, you rarely call it by hand):
  qc_moge.py --concept concept.png --render render.png --model <dir with model.pt or model.pt> [--device cpu] [--out result.json]
Prints one JSON object (and writes --out if given): {"status": "ok", "normal_source": ..., "faceting": {...}, "agreement": {...}} or exits non-zero with the error on stderr.
Local weights only: HF_HUB_OFFLINE=1 is set, no network, no token. The base checkpoint Ruicheng/moge-2-vitl predicts points, depth, intrinsics and mask but NOT normals (only the
`-normal` variants do), so normals are derived from the predicted point map (cross product of central differences, oriented towards the camera) unless the model returns `normal`.

Definitions (all on one n x n grid per image: the square around the subject mask's bounding box is cropped and nearest-neighbour resampled to n x n, so framing and resolution cancel out while the aspect ratio is kept;
`score_px` n defaults to 96; angles in degrees; "valid" = predicted-valid AND inside the subject (alpha > 0.5 when the image has alpha), eroded by `erode` pixels):
  neighbour angle     angle between the unit normals of two 4-neighbour pixels that are both valid (horizontal and vertical pairs).
  faceting_p99_deg    99th percentile of the neighbour angles. A smooth surface has small angles everywhere; a faceted / low-poly render is piecewise flat with sharp creases,
                      so its upper tail is large while the concept's is not.   0 = perfectly smooth.
  mean_neighbor_deg   mean of the neighbour angles (less sensitive to a few creases, reported for reference).
  crease_fraction     share of neighbour pairs whose angle exceeds `crease_deg` (default 15).
  faceting_delta_deg  render faceting_p99_deg minus concept faceting_p99_deg. Positive = the render is more faceted than the concept. THE number a threshold applies to.
  normal_mean_deg     mean angle between render and concept normals over the pixels valid in both (after the crop/resample alignment). 0 = identical orientation field.
  normal_p90_deg      90th percentile of the same angles.
  depth_rel_err       mean |render_depth / median(render_depth) - concept_depth / median(concept_depth)| over the same pixels: relative depth error with each depth map
                      normalised by its own median, so the unknown scale of a single-view prediction cancels. 0 = same relative depth.
  mask_iou            intersection over union of the two valid masks after alignment (1 = same silhouette). Low values mean the two images are not the same view and the
                      agreement numbers above are not comparable.
Agreement assumes the render shows the same view as the concept (front view of the subject); for any other view read the faceting numbers only.
"""
import argparse
import json
import math
import os
import sys

# ---- pure functions (plain lists) --------------------------------------------------------------------------------------------------------------------


def percentile(values, p):
    """Linear-interpolated percentile (0..100) of a list; None when empty."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo = int(math.floor(k))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _unit(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return None if n < 1e-12 or n != n else (v[0] / n, v[1] / n, v[2] / n)


def angle_deg(a, b):
    """Angle in degrees between two unit 3-vectors."""
    d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    return math.degrees(math.acos(max(-1.0, min(1.0, d))))


def mask_bbox(mask):
    """(y0, y1, x0, x1) inclusive bounds of the True cells, or None."""
    ys = [y for y, row in enumerate(mask) if any(row)]
    if not ys:
        return None
    xs = [x for row in mask for x, v in enumerate(row) if v]
    return ys[0], ys[-1], min(xs), max(xs)


def square_box(box):
    """The smallest square box (may reach outside the image) with the same centre as box: cropping to it keeps the aspect ratio of the subject."""
    y0, y1, x0, x1 = box
    s = max(y1 - y0, x1 - x0) + 1
    ny0, nx0 = y0 - (s - (y1 - y0 + 1)) // 2, x0 - (s - (x1 - x0 + 1)) // 2
    return ny0, ny0 + s - 1, nx0, nx0 + s - 1


def crop_resample(grid, box, n, fill=None):
    """Crop a 2D grid (list of rows) to box (inclusive, may reach outside the grid: those cells read `fill`) and resample it to n x n by nearest neighbour."""
    y0, y1, x0, x1 = box
    h, w = y1 - y0 + 1, x1 - x0 + 1
    gh, gw = len(grid), len(grid[0])

    def at(y, x):
        return grid[y][x] if 0 <= y < gh and 0 <= x < gw else fill
    return [[at(y0 + min(h - 1, int((j + 0.5) * h / n)), x0 + min(w - 1, int((i + 0.5) * w / n))) for i in range(n)] for j in range(n)]


def erode(mask, k=1):
    """Binary erosion with the 4-neighbourhood, k times; out-of-image counts as empty."""
    h, w = len(mask), len(mask[0]) if mask else 0
    cur = [list(map(bool, r)) for r in mask]
    for _ in range(max(0, int(k))):
        cur = [[cur[y][x] and y > 0 and y < h - 1 and x > 0 and x < w - 1 and cur[y - 1][x] and cur[y + 1][x] and cur[y][x - 1] and cur[y][x + 1]
                for x in range(w)] for y in range(h)]
    return cur


def normals_from_points(points, mask):
    """Unit normals (H x W of 3-tuples or None) from a point map (H x W x 3, OpenCV camera: +x right, +y down, +z forward): cross product of the central differences of the
    points, flipped to face the camera (z <= 0). A pixel is None unless it and its four neighbours are valid."""
    h, w = len(points), len(points[0])
    out = [[None] * w for _ in range(h)]
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if not (mask[y][x] and mask[y][x - 1] and mask[y][x + 1] and mask[y - 1][x] and mask[y + 1][x]):
                continue
            a, b, c, d = points[y][x - 1], points[y][x + 1], points[y - 1][x], points[y + 1][x]
            u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            v = (d[0] - c[0], d[1] - c[1], d[2] - c[2])
            n = _unit((u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]))
            if n is None:
                continue
            out[y][x] = n if n[2] <= 0 else (-n[0], -n[1], -n[2])
    return out


def unit_normals(normals, mask):
    """Normalise a predicted normal map (H x W x 3) and drop invalid pixels (None)."""
    out = []
    for y, row in enumerate(normals):
        r = []
        for x, v in enumerate(row):
            n = _unit(v) if mask[y][x] else None
            r.append(n)
        out.append(r)
    return out


def neighbor_angles(normals, valid):
    """Angles (deg) of all horizontal and vertical neighbour pairs where both pixels are valid and have a normal."""
    h, w = len(normals), len(normals[0])
    angles = []
    for y in range(h):
        for x in range(w):
            a = normals[y][x] if valid[y][x] else None
            if a is None:
                continue
            if x + 1 < w and valid[y][x + 1] and normals[y][x + 1] is not None:
                angles.append(angle_deg(a, normals[y][x + 1]))
            if y + 1 < h and valid[y + 1][x] and normals[y + 1][x] is not None:
                angles.append(angle_deg(a, normals[y + 1][x]))
    return angles


def faceting_stats(normals, mask, erode_px=1, crease_deg=15.0, min_pairs=50):
    """{'faceting_p99_deg', 'mean_neighbor_deg', 'crease_fraction', 'pairs'} for one aligned view, or {'skipped': reason} with too few pairs to say anything."""
    ang = neighbor_angles(normals, erode(mask, erode_px))
    if len(ang) < min_pairs:
        return {"skipped": f"only {len(ang)} valid neighbour pairs (< {min_pairs})", "pairs": len(ang)}
    return {"faceting_p99_deg": round(percentile(ang, 99), 3), "mean_neighbor_deg": round(sum(ang) / len(ang), 3),
            "crease_fraction": round(sum(1 for a in ang if a > crease_deg) / len(ang), 4), "pairs": len(ang)}


def agreement_stats(n_render, d_render, m_render, n_concept, d_concept, m_concept, erode_px=1, min_pixels=50):
    """Render against concept on the same grid: normal_mean_deg, normal_p90_deg, depth_rel_err, mask_iou, pixels; or {'skipped': reason, 'mask_iou': ...}."""
    er, ec = erode(m_render, erode_px), erode(m_concept, erode_px)
    h, w = len(er), len(er[0])
    union = sum(1 for y in range(h) for x in range(w) if m_render[y][x] or m_concept[y][x])
    inter_m = sum(1 for y in range(h) for x in range(w) if m_render[y][x] and m_concept[y][x])
    iou = round(inter_m / union, 4) if union else 0.0
    both = [(y, x) for y in range(h) for x in range(w) if er[y][x] and ec[y][x] and n_render[y][x] is not None and n_concept[y][x] is not None]
    if len(both) < min_pixels:
        return {"skipped": f"only {len(both)} pixels valid in both views (< {min_pixels})", "mask_iou": iou, "pixels": len(both)}
    ang = [angle_deg(n_render[y][x], n_concept[y][x]) for y, x in both]
    mr = percentile([d_render[y][x] for y, x in both], 50)
    mc = percentile([d_concept[y][x] for y, x in both], 50)
    if not mr or not mc or mr <= 0 or mc <= 0:
        rel = None
    else:
        rel = sum(abs(d_render[y][x] / mr - d_concept[y][x] / mc) for y, x in both) / len(both)
    return {"normal_mean_deg": round(sum(ang) / len(ang), 3), "normal_p90_deg": round(percentile(ang, 90), 3),
            "depth_rel_err": None if rel is None else round(rel, 4), "mask_iou": iou, "pixels": len(both)}


def align_view(view, n):
    """view = {'mask': HxW bool, 'depth': HxW float, 'points': HxWx3 or None, 'normal': HxWx3 or None} -> the same maps cropped to the mask bbox and resampled to n x n
    (None if the mask is empty). Normals are derived from the points after resampling when the model gave none."""
    box = mask_bbox(view["mask"])
    if box is None:
        return None
    box = square_box(box)
    m = crop_resample(view["mask"], box, n, False)
    out = {"mask": m, "depth": crop_resample(view["depth"], box, n, 0.0)}
    if view.get("normal") is not None:
        out["normals"] = unit_normals(crop_resample(view["normal"], box, n, (0.0, 0.0, 0.0)), m)
        out["normal_source"] = "model"
    elif view.get("points") is not None:
        out["normals"] = normals_from_points(crop_resample(view["points"], box, n, (0.0, 0.0, 0.0)), m)
        out["normal_source"] = "derived from points"
    else:
        raise ValueError("view has neither normal nor points")
    return out


def score_views(concept, render, n=96, erode_px=1, crease_deg=15.0):
    """The full moge block from two views (see align_view): {'status': 'ok', 'faceting': {...}, 'agreement': {...}} with faceting_delta_deg when both sides have a score."""
    c, r = align_view(concept, n), align_view(render, n)
    if c is None or r is None:
        return {"status": "unavailable", "reason": "no valid MoGe pixels in the " + ("concept" if c is None else "render")}
    fc = faceting_stats(c["normals"], c["mask"], erode_px, crease_deg)
    fr = faceting_stats(r["normals"], r["mask"], erode_px, crease_deg)
    delta = round(fr["faceting_p99_deg"] - fc["faceting_p99_deg"], 3) if "faceting_p99_deg" in fc and "faceting_p99_deg" in fr else None
    ag = agreement_stats(r["normals"], r["depth"], r["mask"], c["normals"], c["depth"], c["mask"], erode_px)
    return {"status": "ok", "normal_source": r["normal_source"], "score_px": n, "erode_px": erode_px, "crease_deg": crease_deg,
            "faceting": {"concept": fc, "render": fr, "faceting_delta_deg": delta}, "agreement": ag}


# ---- model call (UNRUN) ------------------------------------------------------------------------------------------------------------------------------


def infer_view(model, path, device, maps_px, resolution_level, fp16):
    """Run MoGe-2 on one image file and return a view dict of plain lists (stride-downsampled to at most maps_px on the long side). UNRUN."""
    import numpy as np
    import torch
    from PIL import Image
    im = Image.open(path)
    alpha = np.asarray(im.convert("RGBA"))[..., 3] > 127 if (im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info) else None
    rgba = im.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (127, 127, 127, 255))
    rgb = np.asarray(Image.alpha_composite(bg, rgba).convert("RGB"), dtype=np.float32) / 255.0   # cut-outs go on mid grey: the RGB under alpha 0 is arbitrary
    tensor = torch.from_numpy(rgb).to(device).permute(2, 0, 1)
    with torch.inference_mode():
        out = model.infer(tensor, resolution_level=resolution_level, use_fp16=fp16)
    get = lambda k: None if out.get(k) is None else out[k].detach().float().cpu().numpy()
    points, depth, mask, normal = get("points"), get("depth"), get("mask"), get("normal")
    valid = (mask > 0.5) & np.isfinite(depth) & np.isfinite(points).all(-1)
    if alpha is not None:
        valid &= alpha
    s = max(1, int(math.ceil(max(valid.shape) / float(maps_px))))
    sub = lambda a: None if a is None else a[::s, ::s]
    view = {"mask": sub(valid).tolist(), "depth": np.nan_to_num(sub(depth)).tolist(), "points": np.nan_to_num(sub(points)).tolist(),
            "normal": None if normal is None else np.nan_to_num(sub(normal)).tolist()}
    return view


def resolve_checkpoint(model):
    """A directory holding model.pt, or the .pt file itself -> the file path (local only)."""
    p = os.path.join(model, "model.pt") if os.path.isdir(model) else model
    if not os.path.isfile(p):
        raise FileNotFoundError("MoGe-2 checkpoint not found: " + p)
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--concept", required=True)
    ap.add_argument("--render", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--maps-px", type=int, default=256)
    ap.add_argument("--score-px", type=int, default=96)
    ap.add_argument("--erode-px", type=int, default=1)
    ap.add_argument("--crease-deg", type=float, default=15.0)
    ap.add_argument("--resolution-level", type=int, default=9)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    ckpt = resolve_checkpoint(a.model)
    import torch
    from moge.model.v2 import MoGeModel   # pip install git+https://github.com/microsoft/MoGe.git in the Pixal3D venv; not installed by this repo
    model = MoGeModel.from_pretrained(ckpt).to(a.device).eval()
    fp16 = a.device != "cpu"
    views = [infer_view(model, p, a.device, a.maps_px, a.resolution_level, fp16) for p in (a.concept, a.render)]
    res = score_views(views[0], views[1], a.score_px, a.erode_px, a.crease_deg)
    res["model"] = {"id": "Ruicheng/moge-2-vitl (MIT)", "checkpoint": ckpt, "device": a.device, "resolution_level": a.resolution_level, "torch": torch.__version__}
    text = json.dumps(res)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:   # the parent treats any non-zero exit as 'moge unavailable'
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
