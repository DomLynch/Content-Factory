"""Assemble the vampire from separate TRELLIS parts (stage A, ported from the Demon 2 build's work/asm_stage_a.py).
CPU only: numpy, scipy, trimesh, OpenCV and Pillow, no Blender and no GPU (the Demon script does not use bpy either). Run with the
TRELLIS venv python (config.local.json "python"). Peak RAM is a few GB for 500k-face parts; it waits for start_min_free_ram_gb first.

INPUTS (all from <outputs_dir>/3d/, outputs_dir from config/config.local.json; override with flags)
  body  : vampire-v1-body/mesh.glb  a FULL body INCLUDING its own head, from the body_underlayer_front cell.
  head  : vampire-v1-head/mesh.glb  the head part from the head_front cell (its own small neck collar at the bottom is fine).
  hands : optional, any number (normally two): --hands left.glb right.glb, or "hands" in tools/vampire_asm.json.

HOW TO CUT THE HEAD OFF THE BODY (the one manual judgement; measure it, do not guess it)
  The cut is a horizontal plane at a height FRACTION of the body mesh: frac = (y - y_min) / (y_max - y_min), y is up.
  Faces whose centroid is at or above the plane are dropped, so the body's own head disappears and a neck hole is left.
  1. Run  vampire_asm.py --measure  : it prints the width of the central column per height slice between search_lo and
     search_hi (default 0.80-0.93) and the narrowest slice. The neck is the narrowest slice that has a clearly wider head
     above it (min_head_to_neck_ratio 1.3). In "auto" mode that slice is the cut. If no such slice exists (high collar,
     cape, hair over the neck) it falls back to neck.fixed_frac (0.86, mid neck of an 8-head figure) and says so.
  2. Look at the profile. If the cut is wrong, pass  --cut-frac 0.87  (any value; it forces a fixed cut) and re-run --measure.
     Cut BELOW the chin and ABOVE the shoulder line: too high leaves chin or beard on the stump and the head part clips it,
     too low cuts into the collar or cape and the cap fan covers a wide hole. A hole wider than cap.max_radius_frac of the
     height (0.12) is not capped on purpose (it is a sleeve or hem, not the neck).

RECIPE (each step is a block below, same order as the Demon script)
  1 load parts (texture + UV kept: each part keeps the generator's own UVs and baked colour, no reprojection)
  2 cut the body's head at the neck plane
  3 clean each part: weld by position (UV seams split vertices, so connectivity is computed on position-welded ids), drop
    floating pieces under N faces (counted over ALL faces so slivers do not shatter the surface), drop needle faces (aspect)
  4 face-loss gate: faces removed by cleanup / faces after the cut, summed over parts; fail above face_loss_budget_pct (5)
  5 inpaint isolated near-black blobs (black specks) in each albedo (large black areas such as a black coat stay)
  6 cap the neck hole: fan from the loop centre pushed down by a dome, so nothing is see-through
  7 place the head: scale so its cranium width equals the width of the body's own removed head, put its neck axis on the
    cap axis, sink its bottom below the cap
  8 export one GLB, one node and one material per part, welded normals; report JSON + receipt

USAGE (from the repo root; <py> = the "python" entry of config/config.local.json)
  <py> tools/vampire_asm.py --dry
  <py> tools/vampire_asm.py --measure
  <py> tools/vampire_asm.py
  <py> tools/vampire_asm.py --body X.glb --head Y.glb --hands L.glb R.glb --out Z.glb --cut-frac 0.87 --head-scale 0.95
Output: <outputs_dir>/3d/vampire-v1/assembled.glb, assemble.report.json beside it, receipts/vampire-v1/assemble-<ts>.json.
Exit codes: 0 ok, 2 face-loss gate or other failure (receipt status "fail"), 1 usage/missing input.
"""
import argparse, importlib.util, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

TOOL = "vampire_asm.py"
HERE = os.path.dirname(os.path.abspath(__file__))
np = trimesh = cv2 = Image = None


def jd(o):
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(type(o))


def load_heavy():
    global np, trimesh, cv2, Image
    import numpy as np_, trimesh as tm, cv2 as cv, PIL.Image as pim
    np, trimesh, cv2, Image = np_, tm, cv, pim


def rd(x, n=4):
    return round(float(x), n)


# ---------------------------------------------------------------- loading
def load_part(path):
    """Largest geometry of the GLB with node transforms applied. Returns dict(v, f, uv, tex, note)."""
    s = trimesh.load(path)
    if isinstance(s, trimesh.Trimesh):
        items = [(s, np.eye(4))]
    else:
        items = []
        for node in s.graph.nodes_geometry:
            T, gname = s.graph[node]
            items.append((s.geometry[gname], T))
    if not items:
        raise SystemExit("no mesh in " + path)
    items.sort(key=lambda it: -len(it[0].faces))
    g, T = items[0]
    v = trimesh.transformations.transform_points(np.asarray(g.vertices, np.float64), T)
    f = np.asarray(g.faces, np.int64)
    vis, uv, tex = g.visual, None, None
    if getattr(vis, "uv", None) is not None:
        uv = np.asarray(vis.uv, np.float64)
    mat = getattr(vis, "material", None)
    img = None
    if mat is not None:
        img = getattr(mat, "baseColorTexture", None)
        if img is None:
            img = getattr(mat, "image", None)
    if img is not None:
        tex = img.convert("RGB")
    if uv is None or len(uv) != len(v):
        uv, tex = np.zeros((len(v), 2)), None
    return {"v": v, "f": f, "uv": uv, "tex": tex, "geometries": len(items)}


def weld_ids(v, dec):
    _, wid = np.unique(np.round(v * 10 ** dec).astype(np.int64), axis=0, return_inverse=True)
    return wid.reshape(-1)


def compact(v, f, uv, keep):
    f2 = f[keep]
    used = np.unique(f2.ravel())
    rm = -np.ones(len(v), np.int64)
    rm[used] = np.arange(len(used))
    return v[used], rm[f2], uv[used]


# ---------------------------------------------------------------- neck
def robust_width(v, upper_frac, axis=0):
    y = v[:, 1]
    sel = v[y >= y.min() + (1 - upper_frac) * (y.max() - y.min())]
    return float(np.percentile(sel[:, axis], 98) - np.percentile(sel[:, axis], 2))


def find_neck(v, N):
    """Narrowest slice of the central column in [search_lo, search_hi] (fraction of mesh height) with a wider head above it."""
    y0, y1 = float(v[:, 1].min()), float(v[:, 1].max())
    H = y1 - y0
    fr = (v[:, 1] - y0) / H
    cx = float(np.median(v[:, 0]))
    col = np.abs(v[:, 0] - cx) < N["column_half_frac"] * H
    lo, hi, nb = N["search_lo"], N["search_hi"], int(N["bins"])
    ed = np.linspace(lo, hi, nb + 1)
    w = np.full(nb, np.nan)
    for i in range(nb):
        m = col & (fr >= ed[i]) & (fr < ed[i + 1])
        if m.sum() >= 20:
            x = v[m, 0]
            w[i] = np.percentile(x, 98) - np.percentile(x, 2)
    sm = w.copy()
    for i in range(nb):
        win = w[max(0, i - 1): i + 2]
        win = win[~np.isnan(win)]
        sm[i] = win.mean() if len(win) else np.nan
    prof = [[rd(0.5 * (ed[i] + ed[i + 1]), 4), None if np.isnan(sm[i]) else rd(sm[i] / H, 4)] for i in range(nb)]
    info = {"y_min": rd(y0), "y_max": rd(y1), "height": rd(H), "axis_x": rd(cx), "profile_frac_vs_width_over_height": prof,
            "method": "fixed_fallback", "frac": None}
    if not np.all(np.isnan(sm)):
        k = int(np.nanargmin(sm))
        above = col & (fr >= hi)
        head_w = float(np.percentile(v[above, 0], 98) - np.percentile(v[above, 0], 2)) if above.sum() >= 20 else 0.0
        info.update(narrowest_frac=rd(0.5 * (ed[k] + ed[k + 1])), narrowest_width=rd(sm[k]), head_width_above=rd(head_w))
        if sm[k] > 0 and head_w / sm[k] >= N["min_head_to_neck_ratio"] and 0 < k < nb - 1:
            info.update(method="auto", frac=0.5 * (ed[k] + ed[k + 1]))
    return info


# ---------------------------------------------------------------- cleanup
def clean_part(v, f, uv, min_comp, aspect, dec):
    """Port of clean(): weld by position, drop needle faces and tiny floating components. Returns v, f, uv, stats."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    n = len(f)
    tri = v[f]
    e = np.stack([np.linalg.norm(tri[:, 0] - tri[:, 1], axis=1), np.linalg.norm(tri[:, 1] - tri[:, 2], axis=1),
                  np.linalg.norm(tri[:, 2] - tri[:, 0], axis=1)], 1)
    L = e.max(1)
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    asp = L / np.maximum(2 * area / np.maximum(L, 1e-12), 1e-12)
    del tri, e
    needle = asp >= aspect
    wid = weld_ids(v, dec)
    merged = int(len(v) - (wid.max() + 1))
    adj = trimesh.graph.face_adjacency(wid[f])           # components over ALL faces, so removing needles does not shatter the surface
    nc, lab = connected_components(coo_matrix((np.ones(len(adj)), (adj[:, 0], adj[:, 1])), shape=(n, n)), directed=False)
    sz = np.bincount(lab, minlength=nc)
    small = sz[lab] < min_comp
    keep = ~needle & ~small
    v2, f2, uv2 = compact(v, f, uv, keep)
    st = {"faces_before": n, "faces_after": int(len(f2)), "weld_merged_vertices": merged, "unique_positions": int(wid.max() + 1),
          "min_component_faces_used": int(min_comp), "needle_aspect": aspect,
          "dropped_needle_faces": int(needle.sum()), "dropped_floating_pieces": int((sz < min_comp).sum()),
          "dropped_floating_faces": int(small.sum()), "removed_faces": int((~keep).sum())}
    return v2, f2, uv2, st


# ---------------------------------------------------------------- black specks
def inpaint_specks(tex, uv, f, P):
    """Port of the H1_INPAINT block: near-black blobs of at most max_blob_px (scaled to the texture size) inside UV-covered texels are inpainted."""
    a = np.asarray(tex).copy()
    H_, W_ = a.shape[:2]
    cov = np.zeros((H_, W_), np.uint8)
    tp = np.stack([uv[f][:, :, 0] * (W_ - 1), (1 - uv[f][:, :, 1]) * (H_ - 1)], 2).round().astype(np.int32)
    for i in range(0, len(tp), 50000):
        cv2.fillPoly(cov, list(tp[i:i + 50000]), 255)
    covb = cov > 0
    s = a[..., 0].astype(np.uint16) + a[..., 1] + a[..., 2]
    blk = ((s < 3 * P["lum"]) & covb).astype(np.uint8)
    max_px = int(round(P["max_blob_px_at_2048"] * (W_ * H_) / float(P["reference_px"]) ** 2))
    nc, lb, st, _ = cv2.connectedComponentsWithStats(blk)
    small = np.zeros(nc, bool)
    small[1:] = st[1:, 4] <= max_px
    out = {"texture": [int(W_), int(H_)], "max_blob_px_used": max_px, "blobs_total": int(nc - 1), "blobs_inpainted": int(small.sum()),
           "specks_px": int(st[1:, 4][small[1:]].sum()), "near_black_before_pct": rd(100.0 * blk.sum() / max(covb.sum(), 1), 3)}
    if not small.any():
        out["near_black_after_pct"] = out["near_black_before_pct"]
        return tex, out, None
    sel = small[lb].astype(np.uint8)
    sel = cv2.dilate(sel, np.ones((3, 3), np.uint8), iterations=int(P["dilate_iterations"]))
    fixed = cv2.inpaint(a, sel * 255, int(P["telea_radius"]), cv2.INPAINT_TELEA)
    sf = fixed[..., 0].astype(np.uint16) + fixed[..., 1] + fixed[..., 2]
    out["near_black_after_pct"] = rd(100.0 * ((sf < 3 * P["lum"]) & covb).sum() / max(covb.sum(), 1), 3)
    return Image.fromarray(fixed), out, a


# ---------------------------------------------------------------- neck cap
def cap_neck(v, f, uv, y_cut, H, body_cx, C, dec):
    """Port of cap_open_boundary(): fan-triangulate the open boundary loops of the neck cut. Only boundary edges within band_frac*H
    below the cut AND within max_radius_frac*H of the neck axis count, so sleeve cuffs and hems are never capped."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    ymin = y_cut - C["band_frac"] * H
    rmax = C["max_radius_frac"] * H
    ring = v[(v[:, 1] >= ymin) & (v[:, 1] < y_cut) & (np.abs(v[:, 0] - body_cx) < rmax)]
    if len(ring) < 8:
        return v, f, uv, [], None
    ax = np.array([np.median(ring[:, 0]), np.median(ring[:, 2])])
    wid = weld_ids(v, dec)
    nw = wid.max() + 1
    rep = np.zeros(nw, np.int64)
    rep[wid] = np.arange(len(v))
    fw = wid[f]
    ed = np.sort(np.concatenate([fw[:, [0, 1]], fw[:, [1, 2]], fw[:, [2, 0]]]), axis=1)
    uq, cnt = np.unique(ed, axis=0, return_counts=True)
    bnd = uq[cnt == 1]
    pa_, pb_ = v[rep[bnd[:, 0]]], v[rep[bnd[:, 1]]]
    ok = np.ones(len(bnd), bool)
    for p in (pa_, pb_):
        ok &= (p[:, 1] > ymin) & (np.hypot(p[:, 0] - ax[0], p[:, 2] - ax[1]) <= rmax)
    bnd = bnd[ok]
    if len(bnd) == 0:
        return v, f, uv, [], ax
    ids = np.unique(bnd.ravel())
    loc = -np.ones(nw, np.int64)
    loc[ids] = np.arange(len(ids))
    g = coo_matrix((np.ones(len(bnd)), (loc[bnd[:, 0]], loc[bnd[:, 1]])), shape=(len(ids), len(ids)))
    nc, lab = connected_components(g, directed=False)
    lab_e = lab[loc[bnd[:, 0]]]
    nv, nuv, nf, loops = [], [], [], []
    for c in range(nc):
        members = ids[lab == c]
        if len(members) < C["min_loop_vertices"]:
            continue
        be = bnd[lab_e == c]
        pos = v[rep[members]]
        ctr = pos.mean(0)
        ctr[1] -= C["dome_frac"] * H
        ci = len(v) + len(nv)
        nv.append(ctr[None])
        nuv.append(uv[rep[members]].mean(0)[None])
        a_, b_ = rep[be[:, 0]], rep[be[:, 1]]
        n_up = np.cross(v[b_] - v[a_], ctr[None] - v[a_])[:, 1]
        cc = np.full(len(be), ci)
        tris = np.where((n_up > 0)[:, None], np.stack([a_, b_, cc], 1), np.stack([b_, a_, cc], 1))
        nf.append(tris)
        ar = 0.5 * np.linalg.norm(np.cross(v[b_] - v[a_], ctr[None] - v[a_]), axis=1).sum()
        loops.append({"boundary_vertices": int(len(members)), "faces_added": int(len(tris)),
                      "diameter_x": rd(np.ptp(pos[:, 0])), "diameter_z": rd(np.ptp(pos[:, 2])),
                      "diameter_over_height": rd(max(np.ptp(pos[:, 0]), np.ptp(pos[:, 2])) / H, 4),
                      "centre": [rd(x) for x in ctr], "dome_depth": rd(C["dome_frac"] * H), "fan_area": rd(ar, 5)})
    if not nv:
        return v, f, uv, [], ax
    return np.concatenate([v] + nv), np.concatenate([f] + nf), np.concatenate([uv] + nuv), loops, ax


# ---------------------------------------------------------------- normals, export
def welded_normals(v, f, dec):
    wid = weld_ids(v, dec)
    tri = v[f]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])        # area weighted
    acc = np.zeros((wid.max() + 1, 3))
    for k in range(3):
        np.add.at(acc, wid[f[:, k]], fn)
    n = acc[wid]
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.maximum(ln, 1e-12)


def part_mesh(p, M, dec):
    m = trimesh.Trimesh(vertices=p["v"], faces=p["f"], process=False)
    if p["tex"] is not None:
        mat = trimesh.visual.material.PBRMaterial(name=p["name"], baseColorTexture=p["tex"], metallicFactor=M["metallic"], roughnessFactor=M["roughness"])
        m.visual = trimesh.visual.TextureVisuals(uv=p["uv"], material=mat)
    m.vertex_normals = welded_normals(p["v"], p["f"], dec)
    return m


# ---------------------------------------------------------------- config / paths
def side_of(path, idx):
    n = os.path.basename(path).lower() + os.path.basename(os.path.dirname(path)).lower()
    if "left" in n or "_l." in n or "-l." in n:
        return "left"
    if "right" in n or "_r." in n or "-r." in n:
        return "right"
    return "left" if idx == 0 else "right"


def main():
    ap = argparse.ArgumentParser(description="Assemble the vampire from separate body / head / hand GLBs (see module docstring).")
    ap.add_argument("--config", default=os.path.join(HERE, "vampire_asm.json"))
    ap.add_argument("--asset-id")
    ap.add_argument("--body")
    ap.add_argument("--head")
    ap.add_argument("--hands", nargs="*")
    ap.add_argument("--out")
    ap.add_argument("--cut-frac", type=float, help="force a fixed neck cut at this fraction of body height")
    ap.add_argument("--head-scale", type=float, help="force the head scale instead of matching cranium width")
    ap.add_argument("--measure", action="store_true", help="load body+head, print the neck profile, the suggested cut and head scale, write nothing")
    ap.add_argument("--dry", action="store_true", help="print what would be done and exit; loads no meshes")
    a = ap.parse_args()

    S = json.load(open(a.config, encoding="utf-8-sig"))
    cfg = cf_common.load_config()
    outputs = cfg["local"]["outputs_dir"]
    asset = a.asset_id or S["asset_id"]
    d3 = os.path.join(outputs, "3d")
    glb = S["part_glb_name"]
    body_p = os.path.abspath(a.body) if a.body else os.path.join(d3, S["body_asset"], glb)
    head_p = os.path.abspath(a.head) if a.head else os.path.join(d3, S["head_asset"], glb)
    hands_in = a.hands if a.hands is not None else S.get("hands", [])
    hands_p = [h if os.path.isfile(h) else os.path.join(d3, h, glb) for h in hands_in]
    out_dir = os.path.join(d3, asset)
    out_p = os.path.abspath(a.out) if a.out else os.path.join(out_dir, S["out_name"])
    rep_p = os.path.join(os.path.dirname(out_p), S["report_name"])
    dbg = os.path.join(os.path.dirname(out_p), S["debug_dir_name"])
    N, CL, IP, CP, HD, HP, MT = S["neck"], S["cleanup"], S["inpaint"], S["cap"], S["head"], S["hands_place"], S["material"]
    if a.cut_frac is not None:
        N = dict(N, mode="fixed", fixed_frac=a.cut_frac)
    if a.head_scale is not None:
        HD = dict(HD, match="fixed", fixed_scale=a.head_scale)
    dec = CL["weld_decimals"]

    if a.dry:
        print("DRY RUN: nothing is loaded or written")
        print("asset_id      :", asset)
        for k, p in [("body", body_p), ("head", head_p)] + [("hand%d" % i, p) for i, p in enumerate(hands_p)]:
            print("%-14s: %s  [%s]" % (k, p, ("%.1f MB" % (os.path.getsize(p) / 1e6)) if os.path.isfile(p) else "MISSING"))
        print("output glb    :", out_p)
        print("report json   :", rep_p)
        print("receipt       : receipts/%s/assemble-<ts>.json (stage assemble, tool %s)" % (asset, TOOL))
        print("modules       :", {m: importlib.util.find_spec(m) is not None for m in ("numpy", "scipy", "trimesh", "cv2", "PIL")}, "(run with the TRELLIS venv python if any is False)")
        print("steps         : 1 load  2 cut body head (%s%s)  3 weld+drop floating(min %s faces or %.2f%% of part)+needles(aspect %s)  4 face-loss gate <= %s%%"
              % (N["mode"], " fixed %.3f" % N["fixed_frac"] if N["mode"] == "fixed" else " search %.2f-%.2f, fallback %.3f" % (N["search_lo"], N["search_hi"], N["fixed_frac"]),
                 CL["min_component_faces"], 100 * CL["min_component_frac"], CL["needle_aspect"], CL["face_loss_budget_pct"]))
        print("                5 inpaint black specks (lum<%s, blob<=%s px @2048) in %s  6 cap neck hole (dome %.3f*H)  7 head: %s, sink %.3f*H  8 export + report + receipt"
              % (IP["lum"], IP["max_blob_px_at_2048"], IP["parts"] if IP["enabled"] else "nothing (disabled)", CP["dome_frac"],
                 "scale %.3f" % HD["fixed_scale"] if HD["match"] == "fixed" else "match cranium width", HD["sink_frac"]))
        return 0

    for p in [body_p, head_p] + hands_p:
        if not os.path.isfile(p):
            print("missing input:", p)
            return 1
    load_heavy()
    cf_common.wait_for_free_ram(cfg, TOOL)
    ramw = cf_common.FreeRamWatch()
    ramw.start()
    t0 = time.time()
    report = {"asset_id": asset, "tool": TOOL, "status": "fail", "inputs": {"body": body_p, "head": head_p, "hands": hands_p}, "params": S}
    try:
        report.update(run(a, S, N, CL, IP, CP, HD, HP, MT, dec, body_p, head_p, hands_p, out_p, dbg))
    except SystemExit:
        raise
    except Exception as ex:
        report["error"] = "%s: %s" % (type(ex).__name__, ex)
        report["status"] = "fail"
    report["seconds"] = rd(time.time() - t0, 1)
    if a.measure:
        print(json.dumps({k: v for k, v in report.items() if k in ("neck", "head_measure", "error")}, indent=1, default=jd))
        ramw.stop()
        return 0 if "error" not in report else 2
    os.makedirs(os.path.dirname(rep_p), exist_ok=True)
    with open(rep_p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=1, default=jd)
    low = ramw.stop()
    ok = report["status"] == "ok"
    summ = {k: report.get(k) for k in ("cut", "weld_count", "dropped_floating_pieces", "dropped_needle_faces", "face_loss_pct", "face_loss_budget_pct",
                                      "black_specks_inpainted", "neck_cap_hole", "head_placement", "warnings")}
    rp = cf_common.write_receipt(cfg, asset, "assemble", tool=TOOL, model="none (CPU geometry: numpy/scipy/trimesh/opencv)",
                                 inputs=[body_p, head_p] + hands_p, params=summ, timing={"total_s": report["seconds"]},
                                 memory={"lowest_free_ram_gb": low}, outputs=[out_p, rep_p] if ok else [rep_p],
                                 status="ok" if ok else "fail", error=report.get("error", ""))
    print(json.dumps(summ, indent=1, default=jd))
    print("report :", rep_p)
    print("receipt:", rp)
    if ok:
        print("assembled:", out_p)
    else:
        print("FAIL:", report.get("error"))
    return 0 if ok else 2


def run(a, S, N, CL, IP, CP, HD, HP, MT, dec, body_p, head_p, hands_p, out_p, dbg):
    warn = []
    R = {"warnings": warn}
    P = {"body": load_part(body_p), "head": load_part(head_p)}
    for i, hp in enumerate(hands_p):
        P["hand_" + side_of(hp, i)] = load_part(hp)
    for k, p in P.items():
        p["name"] = k
        p["kind"] = "hands" if k.startswith("hand") else k
        if p["geometries"] > 1:
            warn.append("%s GLB holds %d geometries: only the largest is used" % (k, p["geometries"]))
        if p["tex"] is None:
            warn.append("%s has no base colour texture: no specks inpainted, exported without a material" % k)
    vb, fb, uvb = P["body"]["v"], P["body"]["f"], P["body"]["uv"]
    y0, y1 = float(vb[:, 1].min()), float(vb[:, 1].max())
    H = y1 - y0
    R["parts_loaded"] = {k: {"vertices": int(len(p["v"])), "faces": int(len(p["f"])), "texture": list(p["tex"].size) if p["tex"] else None} for k, p in P.items()}

    # 2 neck plane
    neck = find_neck(vb, N)
    if N["mode"] == "fixed":
        frac, method = N["fixed_frac"], "fixed"
    elif neck["method"] == "auto":
        frac, method = neck["frac"] + N["cut_offset_frac"], "auto"
    else:
        frac, method = N["fixed_frac"], "fixed_fallback"
        warn.append("no clear neck minimum found in the search band: fell back to fixed_frac %.3f, inspect the result" % frac)
    y_cut = y0 + frac * H
    neck.update(chosen_frac=rd(frac), chosen_method=method, y_cut=rd(y_cut))
    R["neck"] = neck
    cen = vb[fb].mean(1)
    removed = cen[:, 1] >= y_cut
    if removed.sum() < 100:
        raise ValueError("fewer than 100 body faces above the cut (frac %.3f): the cut is above the head" % frac)
    rv = vb[np.unique(fb[removed].ravel())]
    body_head = {"width": robust_width(rv, HD["upper_frac"]), "depth": robust_width(rv, HD["upper_frac"], 2), "height_above_cut": float(rv[:, 1].max() - y_cut),
                 "faces": int(removed.sum())}
    hw = robust_width(P["head"]["v"], HD["upper_frac"])
    R["head_measure"] = {"body_head_removed": {k: rd(v) for k, v in body_head.items()}, "head_part_cranium_width": rd(hw),
                         "suggested_scale": rd(body_head["width"] / hw), "head_part_height": rd(np.ptp(P["head"]["v"][:, 1]))}
    if a.measure:
        return R
    body_cx = float(np.median(vb[:, 0]))

    # hands: anchors on the original body (T-pose assumption), optional cut of the body's own hands
    keep_b = ~removed
    anchors = {}
    for k in [k for k in P if k.startswith("hand_")]:
        sg = 1.0 if k.endswith("left") else -1.0
        xe = float((sg * vb[:, 0]).max())
        tip = vb[sg * vb[:, 0] >= xe - 0.01 * H].mean(0)
        anchors[k] = (sg, xe, tip)
        if HP["cut_body_hands"]:
            keep_b &= ~(sg * cen[:, 0] > xe - HP["length_frac"] * H)
    vb2, fb2, uvb2 = compact(vb, fb, uvb, keep_b)
    R["cut"] = {"frac": rd(frac), "method": method, "y_cut": rd(y_cut), "faces_before": int(len(fb)), "faces_after_cut": int(len(fb2)),
                "head_faces_removed": int(removed.sum()), "body_height": rd(H)}

    # 3 clean
    P["body"].update(v=vb2, f=fb2, uv=uvb2)
    stats = {}
    for k, p in P.items():
        mc = max(int(CL["min_component_faces"][p["kind"]]), int(round(CL["min_component_frac"] * len(p["f"]))))
        p["v"], p["f"], p["uv"], stats[k] = clean_part(p["v"], p["f"], p["uv"], mc, CL["needle_aspect"][p["kind"]], dec)
        stats[k]["loss_pct"] = rd(100.0 * stats[k]["removed_faces"] / max(stats[k]["faces_before"], 1), 2)
        print("clean %-10s %7d -> %7d faces (-%.2f%%), weld merged %d, floating pieces %d, needles %d"
              % (k, stats[k]["faces_before"], stats[k]["faces_after"], stats[k]["loss_pct"], stats[k]["weld_merged_vertices"], stats[k]["dropped_floating_pieces"], stats[k]["dropped_needle_faces"]), flush=True)
    tb = sum(s["faces_before"] for s in stats.values())
    tr = sum(s["removed_faces"] for s in stats.values())
    loss = 100.0 * tr / max(tb, 1)
    R.update(per_part_cleanup=stats, weld_count=sum(s["weld_merged_vertices"] for s in stats.values()),
             dropped_floating_pieces=sum(s["dropped_floating_pieces"] for s in stats.values()),
             dropped_needle_faces=sum(s["dropped_needle_faces"] for s in stats.values()),
             face_loss_pct=rd(loss, 3), face_loss_budget_pct=CL["face_loss_budget_pct"])
    over = [k for k, s in stats.items() if s["loss_pct"] > CL["face_loss_budget_pct"]]
    if loss > CL["face_loss_budget_pct"] or (CL["gate_per_part"] and over):
        R["error"] = "face loss %.2f%% over the %.1f%% budget (parts over: %s)" % (loss, CL["face_loss_budget_pct"], over)
        return R
    if over:
        warn.append("parts over the budget on their own (total is within it, as in the Demon build): %s" % over)

    # 5 black specks
    R["black_specks"] = {}
    os.makedirs(dbg, exist_ok=True)
    for k, p in P.items():
        if IP["enabled"] and p["kind"] in IP["parts"] and p["tex"] is not None:
            p["tex"], st, before = inpaint_specks(p["tex"], p["uv"], p["f"], IP)
            R["black_specks"][k] = st
            if before is not None and IP["save_debug_images"]:
                Image.fromarray(before).save(os.path.join(dbg, k + "_albedo_before.png"))
                p["tex"].save(os.path.join(dbg, k + "_albedo_after.png"))
            print("specks %-10s blobs %d inpainted %d (%d px), near-black %.3f%% -> %.3f%%" % (k, st["blobs_total"], st["blobs_inpainted"], st["specks_px"], st["near_black_before_pct"], st["near_black_after_pct"]), flush=True)
    R["black_specks_inpainted"] = sum(s["blobs_inpainted"] for s in R["black_specks"].values())

    # 6 cap
    b = P["body"]
    b["v"], b["f"], b["uv"], loops, ax = cap_neck(b["v"], b["f"], b["uv"], y_cut, H, body_cx, CP, dec)
    R["neck_cap_hole"] = {"loops": loops, "largest": max(loops, key=lambda l: l["boundary_vertices"]) if loops else None, "axis_xz": None if ax is None else [rd(ax[0]), rd(ax[1])]}
    if not loops:
        msg = "no open neck boundary found near the cut (frac %.3f): nothing capped" % frac
        if CP["required"]:
            R["error"] = msg
            return R
        warn.append(msg)
    if ax is None:
        ax = np.array([body_cx, float(np.median(b["v"][:, 2]))])
    cap_y = float(np.mean([l["centre"][1] for l in loops])) if loops else y_cut

    # 7 head placement
    h = P["head"]
    if HD["collar_trim_frac"] > 0:
        thr = h["v"][:, 1].min() + HD["collar_trim_frac"] * np.ptp(h["v"][:, 1])
        h["v"], h["f"], h["uv"] = compact(h["v"], h["f"], h["uv"], h["v"][h["f"]].mean(1)[:, 1] >= thr)
    hw = robust_width(h["v"], HD["upper_frac"])
    sc = HD["fixed_scale"] if HD["match"] == "fixed" else body_head["width"] / hw
    if not HD["scale_min"] <= sc <= HD["scale_max"]:
        warn.append("head scale %.3f outside [%.2f, %.2f], clamped: check the cut height and the head cell" % (sc, HD["scale_min"], HD["scale_max"]))
        sc = min(max(sc, HD["scale_min"]), HD["scale_max"])
    hv = h["v"] * sc
    low = hv[:, 1] < hv[:, 1].min() + 0.05 * np.ptp(hv[:, 1])
    hax = np.array([np.median(hv[low, 0]), np.median(hv[low, 2])])
    t = np.array([ax[0] - hax[0] + HD["dx_frac"] * H, (y_cut - HD["sink_frac"] * H) - hv[:, 1].min(), ax[1] - hax[1] + HD["dz_frac"] * H])
    h["v"] = hv + t
    M4 = np.eye(4)
    M4[:3, :3] *= sc
    M4[:3, 3] = t
    R["head_placement"] = {"scale": rd(sc, 5), "translation": [rd(x, 5) for x in t], "matrix_4x4_row_major": [[rd(x, 6) for x in r] for r in M4],
                           "applied_to": "head part vertices as loaded (glTF Y-up), after collar_trim", "match": HD["match"],
                           "head_part_cranium_width": rd(hw), "body_head_width": rd(body_head["width"]), "head_bottom_y": rd(h["v"][:, 1].min()),
                           "cap_y": rd(cap_y), "sink_below_cap": rd(cap_y - h["v"][:, 1].min()), "head_top_y": rd(h["v"][:, 1].max()),
                           "body_original_top_y": rd(y1), "neck_axis_xz": [rd(ax[0]), rd(ax[1])]}
    if cap_y - h["v"][:, 1].min() <= 0:
        warn.append("head bottom is not below the cap: the head floats, raise sink_frac")

    # hands
    R["hands_placement"] = {}
    for k in [k for k in P if k.startswith("hand_")]:
        p = P[k]
        sg, xe, tip = anchors[k]
        rot = trimesh.transformations.euler_matrix(*np.radians(HP["rotate_deg"][k[5:]]), "sxyz")[:3, :3]
        cen_ = p["v"].mean(0)
        hv = (p["v"] - cen_) @ rot.T
        s_ = HP["length_frac"] * H / float(np.ptp(hv, axis=0).max())
        hv = hv * s_
        target = tip.copy()
        target[0] -= sg * HP["inward_frac"] * HP["length_frac"] * H
        bb = 0.5 * (hv.min(0) + hv.max(0))
        p["v"] = hv - bb + target
        R["hands_placement"][k] = {"scale": rd(s_, 5), "rotate_deg": HP["rotate_deg"][k[5:]], "target_centre": [rd(x) for x in target], "body_tip": [rd(x) for x in tip],
                                   "note": "T-pose and +X = character left assumed; verify by eye"}

    # 8 export
    sc_ = trimesh.Scene()
    for k, p in P.items():
        sc_.add_geometry(part_mesh(p, MT, dec), node_name=k, geom_name=k)
    try:
        data = sc_.export(file_type="glb", include_normals=True)
    except TypeError:
        data = sc_.export(file_type="glb")
    os.makedirs(os.path.dirname(out_p), exist_ok=True)
    with open(out_p, "wb") as fh:
        fh.write(data)
    R["output"] = {"path": out_p, "bytes": len(data), "nodes": list(P), "faces": {k: int(len(p["f"])) for k, p in P.items()}}
    R["status"] = "ok"
    return R


if __name__ == "__main__":
    sys.exit(main())
