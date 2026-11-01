"""Textures stage: fix the base colour of a TRELLIS mesh GLB (inpaint voxel-bake pinholes), build own metal/roughness and a detail normal, write a before/after compare image and one receipt.
Usage: run_textures.py <asset_id> <mesh.glb> [--out-name STEM]
Settings come from config/config.json ("textures") and config.local.json. Outputs go to <outputs_dir>/3d/<asset_id>/textures/.
CPU only and light: the GLB is never modified. The logic lives in tools/texture/fix_textures.py and detail_normal.py (run as subprocesses)."""
import argparse, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

TEX_DIR = os.path.join(cf_common.REPO, "tools", "texture")


def run(cmd, timeout):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{os.path.basename(cmd[1])} failed ({r.returncode}): {r.stderr[-800:]}")
    return r.stdout


def extract_and_centres(glb, orig_path, crops, crop_px):
    """Save the GLB's base colour and find each crop's UV centre: the window position holding most UV points of the vertices inside a height band (fractions of the tallest axis)."""
    import cv2, numpy as np, trimesh
    g = list(trimesh.load(glb).geometry.values())[0]
    g.visual.material.baseColorTexture.convert("RGB").save(orig_path)
    v, uv = np.asarray(g.vertices), np.asarray(g.visual.uv)
    ax = int(np.argmax(np.ptp(v, axis=0)))
    t = (v[:, ax] - v[:, ax].min()) / np.ptp(v[:, ax])
    out = {}
    for name, (lo, hi) in crops.items():
        sel = (t >= lo) & (t <= hi)
        hh, ww = g.visual.material.baseColorTexture.size[::-1]
        dens = np.zeros((hh, ww), np.float32)
        x = np.clip((uv[sel, 0] * ww).astype(int), 0, ww - 1); y = np.clip(((1 - uv[sel, 1]) * hh).astype(int), 0, hh - 1)  # glTF v runs top-down
        np.add.at(dens, (y, x), 1)
        dens = cv2.boxFilter(dens, -1, (crop_px, crop_px), normalize=False)
        cy, cx = np.unravel_index(int(np.argmax(dens)), dens.shape)
        out[name] = (cx / ww, cy / hh)
    return out


def make_compare(before_path, after_path, centres, cc, dst):
    import cv2, numpy as np
    from PIL import Image
    before = np.array(Image.open(before_path).convert("RGB"))
    after = cv2.imread(after_path)[..., ::-1]
    h, w = before.shape[:2]
    half = cc["crop_px"] // 2
    rows = []
    for name, (u, v) in centres.items():
        x0 = int(min(max(u * w - half, 0), w - cc["crop_px"])); y0 = int(min(max(v * h - half, 0), h - cc["crop_px"]))
        pair = []
        for label, img in (("before", before), ("after", after)):
            c = np.ascontiguousarray(img[y0:y0 + cc["crop_px"], x0:x0 + cc["crop_px"]])
            c = cv2.resize(c, None, fx=cc["zoom"], fy=cc["zoom"], interpolation=cv2.INTER_NEAREST)
            cv2.putText(c, f"{name} {label}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            pair.append(c)
        rows.append(np.hstack(pair))
    cv2.imwrite(dst, np.vstack(rows)[..., ::-1])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("asset_id")
    ap.add_argument("glb")
    ap.add_argument("--out-name", default=None, help="output file stem (default: asset id)")
    a = ap.parse_args()
    cfg = cf_common.load_config()
    tc, loc = cfg["textures"], cfg["local"]
    stem = a.out_name or a.asset_id
    out = os.path.join(loc["outputs_dir"], "3d", a.asset_id, tc["subdir"])
    os.makedirs(out, exist_ok=True)
    watch = cf_common.FreeRamWatch()
    free = watch._free_gb()
    if free < tc["min_free_ram_gb"]:
        raise SystemExit(f"free RAM {free:.1f} GB is below {tc['min_free_ram_gb']} GB: not starting")
    watch.start()
    py, t0 = loc["python"], time.time()
    status, err, stats = "ok", "", {}
    outputs = []
    try:
        log = run([py, "-I", os.path.join(TEX_DIR, "fix_textures.py"), a.glb, out, stem, str(tc["hole_max_value"]), str(tc["hole_max_area"])], tc["timeout_s"])
        print(log, end="")
        m = re.search(r"pinhole texels: (\d+) of (\d+)", log)
        m2 = re.search(r"pinhole texels after: (\d+)", log)
        stats = {"pinhole_texels_before": int(m.group(1)), "texels_total": int(m.group(2)), "pinhole_texels_still_dark_after": int(m2.group(1))}
        base, mr, nrm = (os.path.join(out, f"{stem}{s}.png") for s in ("_basecolor", "_mr", "_normal"))
        print(run([py, "-I", os.path.join(TEX_DIR, "detail_normal.py"), base, nrm, str(tc["normal_strength"])], tc["timeout_s"]), end="")
        # the "before" image is the GLB's own base colour, extracted once for the compare
        orig = os.path.join(out, f"{stem}_basecolor_orig.png")
        centres = extract_and_centres(a.glb, orig, tc["compare"]["crops"], tc["compare"]["crop_px"])
        cmp_path = os.path.join(out, "compare.png")
        make_compare(orig, base, centres, tc["compare"], cmp_path)
        outputs = [base, mr, nrm, orig, cmp_path]
    except Exception as e:  # noqa: BLE001 - receipt records any failure
        status, err = "failed", str(e)[:1500]
        print("FAILED:", err)
    low = watch.stop()
    rp = cf_common.write_receipt(cfg, a.asset_id, "textures", tool="tools/run_textures.py", model="none (cv2 inpaint, own metal/rough, detail normal)",
                                 inputs=[a.glb], params={"out_name": stem, **{k: tc[k] for k in ("hole_max_value", "hole_max_area", "normal_strength")}, "compare": tc["compare"], **stats},
                                 timing={"total_s": round(time.time() - t0, 1)}, memory={"lowest_free_ram_gb": low}, outputs=outputs, status=status, error=err)
    print("receipt:", rp)
    sys.exit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
