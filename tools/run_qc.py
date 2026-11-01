"""QC stage: deterministic geometry / texture / thin-structure checks on a textured mesh GLB, one receipt (stage 'qc').
Usage: run_qc.py <asset_id> <mesh.glb> [--concept concept.png] [--budget-tris N]
Writes receipts/<asset_id>/qc-<utcstamp>.json (cf_common.write_receipt). params.overall is "pass" or "fail"; params.checks has one row per metric with value, limit and pass.
Thresholds come from config/config.json ("qc"), loose to start. CPU only, no model, no renderer: numpy + trimesh (load) + scipy (shells); cv2 is used for polygon
fill when importable, PIL otherwise. The GLB and the concept image are only read.
Metrics
  geometry : non-manifold edges (>2 faces), boundary edges (1 face), loose shells, triangles vs budget, faceting_score = mean angle (deg) between the normals of
             neighbouring faces. Vertices are welded by position first, so UV seams and split normals do not count as holes.
  texture  : percent near-black texels inside UV islands (and the largest connected black patch), UV coverage fraction, per-triangle UV stretch p99
             (sigma_max / sigma_min of the 3D->UV Jacobian), mean luminance / saturation of covered texels against the concept subject (alpha > 0.5),
             'linear-not-encoded' when the base colour is darker or greyer than the concept by more than the configured ratio.
  thin     : front orthographic silhouette of the mesh (glTF +Y up, looking along Z) against the concept alpha matte: coverage ratio and IoU.
"""
import argparse, json, os, sys, time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common


# ---- loading ----------------------------------------------------------------------------------------------------------------------------------------
def load_parts(glb):
    """[(vertices Nx3, faces Mx3, uv Nx2 or None, base colour RGB uint8 array or None)] in scene space (node transforms applied)."""
    import trimesh
    sc = trimesh.load(glb, process=False)
    geoms = sc.dump() if isinstance(sc, trimesh.Scene) else [sc]
    parts = []
    for g in geoms:
        if len(g.faces) == 0:
            continue
        uv = tex = None
        vis = g.visual
        if getattr(vis, "uv", None) is not None:
            uv = np.asarray(vis.uv, dtype=np.float64)
            img = getattr(getattr(vis, "material", None), "baseColorTexture", None)
            if img is not None:
                tex = np.asarray(img.convert("RGB"))
        parts.append((np.asarray(g.vertices, dtype=np.float64), np.asarray(g.faces, dtype=np.int64), uv, tex))
    if not parts:
        raise RuntimeError("no triangles in " + glb)
    return parts


# ---- rasterising ------------------------------------------------------------------------------------------------------------------------------------
def fill_triangles(tri_xy, w, h):
    """uint8 mask (h, w), 1 where a triangle (pixel coordinates, array Mx3x2) covers the pixel."""
    mask = np.zeros((h, w), np.uint8)
    try:
        import cv2
        shift = 4
        pts = np.round(tri_xy * (1 << shift)).astype(np.int32)
        step = 200000
        for i in range(0, len(pts), step):
            cv2.fillPoly(mask, list(pts[i:i + step]), 1, lineType=cv2.LINE_8, shift=shift)
    except ImportError:
        from PIL import Image, ImageDraw
        im = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(im)
        for t in tri_xy:
            d.polygon([tuple(p) for p in t], fill=1)
        mask = np.asarray(im)
    return mask


# ---- geometry ---------------------------------------------------------------------------------------------------------------------------------------
def geometry_metrics(parts):
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    verts, faces, off = [], [], 0
    for v, f, _, _ in parts:
        verts.append(v)
        faces.append(f + off)
        off += len(v)
    V, F = np.vstack(verts), np.vstack(faces)
    diag = float(np.linalg.norm(V.max(0) - V.min(0))) or 1.0
    q = np.round(V / (diag * 1e-6)).astype(np.int64)
    _, inv = np.unique(q, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    W = np.zeros((inv.max() + 1, 3))
    W[inv] = V
    F = inv[F]
    keep = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])
    degenerate = int((~keep).sum())
    F = F[keep]
    n = len(W)
    # edges
    e = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    e.sort(axis=1)
    key = e[:, 0] * n + e[:, 1]
    fid = np.tile(np.arange(len(F)), 3)
    order = np.argsort(key, kind="stable")
    ks, fs = key[order], fid[order]
    uk, start, cnt = np.unique(ks, return_index=True, return_counts=True)
    non_manifold = int((cnt > 2).sum())
    boundary = int((cnt == 1).sum())
    # faceting: angle between the two faces of every manifold edge
    two = start[cnt == 2]
    a, b = fs[two], fs[two + 1]
    p0, p1, p2 = W[F[:, 0]], W[F[:, 1]], W[F[:, 2]]
    nrm = np.cross(p1 - p0, p2 - p0)
    ln = np.linalg.norm(nrm, axis=1)
    ok = ln > 0
    nrm[ok] /= ln[ok, None]
    good = ok[a] & ok[b]
    ang = np.degrees(np.arccos(np.clip((nrm[a] * nrm[b]).sum(1)[good], -1.0, 1.0)))
    faceting = float(ang.mean()) if len(ang) else 0.0
    # loose shells (connected components over the vertices that faces use)
    r = np.concatenate([F[:, 0], F[:, 1]])
    c = np.concatenate([F[:, 1], F[:, 2]])
    g = coo_matrix((np.ones(len(r), np.int8), (r, c)), shape=(n, n))
    ncomp, lab = connected_components(g, directed=False)
    used = np.unique(F)
    shells = int(len(np.unique(lab[used])))
    tri_per_shell = np.bincount(lab[F[:, 0]], minlength=ncomp)
    tri_per_shell = tri_per_shell[tri_per_shell > 0]
    return {"triangles": int(sum(len(f) for _, f, _, _ in parts)), "degenerate_triangles": degenerate, "non_manifold_edges": non_manifold, "boundary_edges": boundary,
            "non_manifold_edge_pct": round(100.0 * non_manifold / max(len(uk), 1), 4), "boundary_edge_pct": round(100.0 * boundary / max(len(uk), 1), 4), "edges": int(len(uk)), "loose_shells": shells,
            "largest_shell_tri_pct": round(100.0 * float(tri_per_shell.max()) / max(len(F), 1), 3), "faceting_score": round(faceting, 4),
            "bbox_min": [round(float(x), 5) for x in V.min(0)], "bbox_max": [round(float(x), 5) for x in V.max(0)]}


# ---- texture ----------------------------------------------------------------------------------------------------------------------------------------
def lum_sat(rgb):
    """Mean Rec.601 luma (0..1) and mean HSV saturation (0..1) of an (N, 3) uint8 array, in the stored (sRGB-encoded) values."""
    x = rgb.astype(np.float32) / 255.0
    mx, mn = x.max(1), x.min(1)
    luma = 0.299 * x[:, 0] + 0.587 * x[:, 1] + 0.114 * x[:, 2]
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    return float(luma.mean()), float(sat.mean())


def uv_stretch(P, Q):
    """(ratios, n_uv_degenerate, n_counted): per-triangle sigma_max / sigma_min of the 3D -> UV Jacobian (>= 1) over triangles that have 3D area and UV area;
    triangles with zero UV area (collapsed in the atlas, so no texture) are counted separately, because their ratio is infinite."""
    e1, e2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    l1 = np.linalg.norm(e1, axis=1)
    ok = l1 > 1e-12
    e1, e2, l1, Q = e1[ok], e2[ok], l1[ok], Q[ok]
    x2 = (e1 * e2).sum(1) / l1
    y2 = np.linalg.norm(e2 - (x2 / l1)[:, None] * e1, axis=1)
    ok = y2 > 1e-12
    l1, x2, y2, Q = l1[ok], x2[ok], y2[ok], Q[ok]
    d1, d2 = Q[:, 1] - Q[:, 0], Q[:, 2] - Q[:, 0]
    # J = [d1 d2] * inv([[l1, x2], [0, y2]])
    j00, j10 = d1[:, 0] / l1, d1[:, 1] / l1
    j01, j11 = (d2[:, 0] - x2 * j00) / y2, (d2[:, 1] - x2 * j10) / y2
    s = j00 ** 2 + j01 ** 2 + j10 ** 2 + j11 ** 2
    det = j00 * j11 - j01 * j10
    d = np.sqrt(np.maximum(s * s - 4 * det * det, 0))
    lo = s - d
    good = (np.abs(det) > 1e-12 * np.maximum(s, 1e-30)) & (lo > 0)
    return np.sqrt((s[good] + d[good]) / lo[good]), int((~good).sum()), len(good)


def subject_pixels(concept):
    from PIL import Image
    im = Image.open(concept)
    if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
        rgba = np.asarray(im.convert("RGBA"))
        return rgba[..., :3], rgba[..., 3] > 127
    rgb = np.asarray(im.convert("RGB")).astype(np.int16)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    return rgb.astype(np.uint8), np.abs(rgb - bg).max(2) > 24   # no alpha: whatever differs from the border colour is the subject


def texture_metrics(parts, concept, q):
    from scipy import ndimage
    cov_px = dark_px = total_px = 0
    sums = np.zeros(3)
    dark_blob = 0
    stretch, degen, counted = [], 0, 0
    n_tex = 0
    for v, f, uv, tex in parts:
        if uv is None or tex is None:
            continue
        n_tex += 1
        th, tw = tex.shape[:2]
        s = min(1.0, q["max_raster_px"] / max(th, tw))
        h, w = max(1, int(th * s)), max(1, int(tw * s))
        if (h, w) != (th, tw):
            from PIL import Image
            tex = np.asarray(Image.fromarray(tex).resize((w, h), Image.BOX))
        tri = np.stack([uv[f][..., 0] * w, (1.0 - uv[f][..., 1]) * h], axis=-1)   # trimesh uv has v up; image rows run down
        mask = fill_triangles(tri, w, h).astype(bool)
        px = tex[mask]
        cov_px += int(mask.sum())
        total_px += w * h
        dark = mask & (tex.max(2) <= q["near_black_value"])
        dark_px += int(dark.sum())
        lab, nb = ndimage.label(dark)
        if nb:
            dark_blob = max(dark_blob, int(np.bincount(lab.ravel())[1:].max()))
        sums += np.array([px.shape[0], *[0, 0]])
        l, sa = lum_sat(px) if len(px) else (0.0, 0.0)
        sums[1] += l * len(px)
        sums[2] += sa * len(px)
        r, dg, nc = uv_stretch(v[f], uv[f])
        stretch.append(r)
        degen += dg
        counted += nc
    if not n_tex:
        return {"skipped": "no base colour texture / UVs"}
    n = max(sums[0], 1)
    out = {"uv_coverage": round(cov_px / max(total_px, 1), 4), "near_black_pct": round(100.0 * dark_px / max(cov_px, 1), 3),
           "largest_black_patch_pct": round(100.0 * dark_blob / max(cov_px, 1), 3), "uv_stretch_p99": round(float(np.percentile(np.concatenate(stretch), 99)), 3),
           "uv_degenerate_pct": round(100.0 * degen / max(counted, 1), 3),
           "mean_luminance": round(float(sums[1] / n), 4), "mean_saturation": round(float(sums[2] / n), 4)}
    if concept:
        rgb, m = subject_pixels(concept)
        cl, cs = lum_sat(rgb[m])
        out.update(concept_luminance=round(cl, 4), concept_saturation=round(cs, 4), luminance_ratio=round(out["mean_luminance"] / max(cl, 1e-6), 4),
                   saturation_ratio=round(out["mean_saturation"] / max(cs, 1e-6), 4))
        out["colour_flag"] = ("linear-not-encoded" if out["luminance_ratio"] < q["min_luminance_ratio"] or out["saturation_ratio"] < q["min_saturation_ratio"] else "ok")
    return out


# ---- thin structures --------------------------------------------------------------------------------------------------------------------------------
def silhouette_metrics(parts, concept, q):
    from PIL import Image
    V = np.vstack([v for v, _, _, _ in parts])
    off, F = 0, []
    for v, f, _, _ in parts:
        F.append(f + off)
        off += len(v)
    F = np.vstack(F)
    x, y = V[:, 0], V[:, 1]   # glTF: +Y up, front view looks along Z
    span_y, span_x = float(np.ptp(y)), float(np.ptp(x))
    H = int(q["raster_px"])
    sc = (H - 2) / max(span_y, 1e-9)
    W = max(2, int(span_x * sc) + 2)
    pts = np.stack([(x - x.min()) * sc + 1, (y.max() - y) * sc + 1], axis=-1)
    mm = fill_triangles(pts[F], W, H).astype(bool)
    _, cm = subject_pixels(concept)
    ys, xs = np.where(cm)
    if len(ys) == 0:
        return {"skipped": "concept has no subject pixels"}
    cm = cm[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    ys, xs = np.where(mm)
    mm = mm[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    # coverage of each silhouette as area / (bbox height)^2 so the two are comparable at any resolution or aspect
    mesh_cov = float(mm.sum()) / mm.shape[0] ** 2
    con_cov = float(cm.sum()) / cm.shape[0] ** 2
    g = int(q["iou_px"])
    a = np.asarray(Image.fromarray(mm.astype(np.uint8) * 255).resize((g, g), Image.BILINEAR)) > 127
    b = np.asarray(Image.fromarray(cm.astype(np.uint8) * 255).resize((g, g), Image.BILINEAR)) > 127
    iou = float((a & b).sum()) / max(float((a | b).sum()), 1.0)
    return {"mesh_coverage": round(mesh_cov, 4), "concept_coverage": round(con_cov, 4), "coverage_ratio": round(mesh_cov / max(con_cov, 1e-9), 4),
            "mesh_aspect_w_over_h": round(mm.shape[1] / mm.shape[0], 4), "concept_aspect_w_over_h": round(cm.shape[1] / cm.shape[0], 4), "iou": round(iou, 4)}


# ---- verdict ----------------------------------------------------------------------------------------------------------------------------------------
def verdict(m, q, budget):
    """[{metric, value, op, limit, pass}] for every metric that has a value; metrics without one (no concept, no texture, no budget) are listed as skipped."""
    rows, skipped = [], []

    def add(name, value, op, limit):
        if value is None or limit is None:
            skipped.append(name)
            return
        rows.append({"metric": name, "value": value, "op": op, "limit": limit, "pass": bool(value <= limit if op == "<=" else value >= limit)})

    g, t, s = m["geometry"], m["texture"], m["thin"]
    qg, qt, qs = q["geometry"], q["texture"], q["thin"]
    add("non_manifold_edge_pct", g["non_manifold_edge_pct"], "<=", qg["max_non_manifold_edge_pct"])
    add("boundary_edge_pct", g["boundary_edge_pct"], "<=", qg["max_boundary_edge_pct"])
    add("loose_shells", g["loose_shells"], "<=", qg["max_loose_shells"])
    add("triangles", g["triangles"], "<=", None if budget is None else int(budget * qg["budget_tolerance"]))
    add("faceting_score", g["faceting_score"], "<=", qg["max_faceting_deg"])
    tv = (lambda k: t.get(k)) if "skipped" not in t else (lambda k: None)
    add("near_black_pct", tv("near_black_pct"), "<=", qt["max_near_black_pct"])
    add("largest_black_patch_pct", tv("largest_black_patch_pct"), "<=", qt["max_black_patch_pct"])
    add("uv_coverage", tv("uv_coverage"), ">=", qt["min_uv_coverage"])
    add("uv_stretch_p99", tv("uv_stretch_p99"), "<=", qt["max_uv_stretch_p99"])
    add("uv_degenerate_pct", tv("uv_degenerate_pct"), "<=", qt["max_uv_degenerate_pct"])
    add("luminance_ratio", tv("luminance_ratio"), ">=", qt["min_luminance_ratio"])
    add("saturation_ratio", tv("saturation_ratio"), ">=", qt["min_saturation_ratio"])
    sv = (lambda k: s.get(k)) if "skipped" not in s else (lambda k: None)
    add("silhouette_coverage_ratio", sv("coverage_ratio"), ">=", qs["min_coverage_ratio"])
    add("silhouette_iou", sv("iou"), ">=", qs["min_iou"])
    return rows, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("asset_id")
    ap.add_argument("glb")
    ap.add_argument("--concept", default="")
    ap.add_argument("--budget-tris", type=int, default=None)
    a = ap.parse_args()
    cfg = cf_common.load_config()
    q = cfg["qc"]
    budget = a.budget_tris if a.budget_tris is not None else q["geometry"].get("budget_tris")
    t0 = time.time()
    ram = cf_common.FreeRamWatch()
    ram.start()
    status, err, params = "ok", "", {"concept": a.concept, "budget_tris": budget, "thresholds": q}
    try:
        if not os.path.isfile(a.glb):
            raise FileNotFoundError("glb not found: " + a.glb)
        if a.concept and not os.path.isfile(a.concept):
            raise FileNotFoundError("concept not found: " + a.concept)
        parts = load_parts(a.glb)
        m = {"geometry": geometry_metrics(parts), "texture": texture_metrics(parts, a.concept, q["texture"]),
             "thin": silhouette_metrics(parts, a.concept, q["thin"]) if a.concept else {"skipped": "no concept image"}}
        rows, skipped = verdict(m, q, budget)
        failed = [r["metric"] for r in rows if not r["pass"]]
        params.update(metrics=m, checks=rows, skipped=skipped, failed=failed, overall="fail" if failed else "pass")
        if "colour_flag" in m["texture"]:
            params["colour_flag"] = m["texture"]["colour_flag"]
    except Exception as e:
        status, err = "fail", f"{type(e).__name__}: {e}"
        params["overall"] = "fail"
    path = cf_common.write_receipt(cfg, a.asset_id, "qc", tool="run_qc.py", model={"id": "none (deterministic numpy checks)"}, inputs=[p for p in (a.glb, a.concept) if p],
                                   params=params, timing={"wall_s": round(time.time() - t0, 1)}, memory={"lowest_free_ram_gb": ram.stop()}, status=status, error=err)
    print(json.dumps({"receipt": path, "status": status, "overall": params["overall"], "failed": params.get("failed", []), "error": err,
                      "metrics": params.get("metrics")}, indent=1))
    sys.exit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
