"""Proof of the cloth stage (plugin 0.1.18): the draped cloak on the T-posed body versus the baked one-piece figure, rendered with the SAME camera, angles and three-point light rig as the
A/B turntable (tools/ab/turntable_blender.py with config ab.render, nothing new), then a side-by-side contact sheet (left = simulated cloth, right = baked one-piece).
Usage: proof_render.py <asset_id> <cloth_glb> <body_glb> <baked_glb> [--label-cloth NAME] [--label-baked NAME]
  cloth_glb  the settled cloth part written by run_cloth.py; body_glb  the body GLB it is rendered with (the bound body); baked_glb  the one-piece baked figure.
Each Blender render takes the same guard as the sim (free RAM, exclusive lock file, no Flux load) and is CPU only. Output: <outputs_dir>/cloth_proof/<asset_id>/{cloth,baked}/turn_*.png and contact_sheet.png."""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(TOOLS, "ab"))
import cf_common, cloth_core, ab_core


def render(cfg, glb, extras, out_dir, label):
    loc, cl, ab = cfg["local"], cfg["cloth"], cfg["ab"]
    matched = ab_core.matched_render_config(ab)
    matched["angles_deg"] = cl["proof"]["angles_deg"]
    rcfg = {**matched, "glb": glb, "extra_glbs": extras, "out_dir": out_dir, "label": label, "seed": 0, "config_hash": ab_core.config_hash(matched), "glb_sha256": cf_common.sha256(glb)}
    os.makedirs(out_dir, exist_ok=True)
    cpath = os.path.join(out_dir, "render_config.json")
    json.dump(rcfg, open(cpath, "w", encoding="utf-8"), indent=2)
    script = os.path.join(cf_common.REPO, ab["render"]["script"])
    with cloth_core.BlenderLock(cloth_core.lock_path(loc), cl["guard"]["lock_wait_s"]):
        cloth_core.wait_for_headroom(loc, cl["guard"])
        with open(os.path.join(loc["logs_dir"], f"cloth_proof_{label}.txt"), "w", encoding="utf-8") as lf:
            rc = subprocess.run([loc["blender_exe"], "--background", "--python", script, "--", "--config", cpath], stdout=lf, stderr=subprocess.STDOUT, timeout=cl["proof"]["timeout_s"]).returncode
    man = os.path.join(out_dir, "turntable.json")
    if rc != 0 or not os.path.exists(man):
        sys.exit(f"render {label} failed: blender exit {rc}")
    return json.load(open(man, encoding="utf-8"))


def sheet(left, right, titles, out, thumb_w):
    from PIL import Image, ImageDraw
    rows = list(zip(left["frames"], right["frames"]))
    tiles = [[Image.open(p).convert("RGB") for p in r] for r in rows]
    tiles = [[im.resize((thumb_w, round(im.height * thumb_w / im.width))) for im in r] for r in tiles]
    h = [max(i.height for i in r) for r in tiles]
    img = Image.new("RGB", (2 * thumb_w + 30, sum(h) + 40 + 10 * len(h)), (20, 20, 20))
    d = ImageDraw.Draw(img)
    d.text((10, 8), titles[0], fill=(255, 255, 255)); d.text((thumb_w + 20, 8), titles[1], fill=(255, 255, 255))
    y = 28
    for r, hh in zip(tiles, h):
        for i, t in enumerate(r):
            img.paste(t, (10 + i * (thumb_w + 10), y))
        y += hh + 10
    img.save(out)
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 4:
        sys.exit(__doc__)
    asset_id, cloth_glb, body_glb, baked_glb = [os.path.abspath(a) if i else a for i, a in enumerate(args)]
    cfg = cf_common.load_config()
    base = os.path.join(cfg["local"]["outputs_dir"], "cloth_proof", asset_id)
    lc = render(cfg, body_glb, [cloth_glb], os.path.join(base, "cloth"), "cloth")
    lb = render(cfg, baked_glb, [], os.path.join(base, "baked"), "baked")
    out = sheet(lc, lb, ("simulated cloth cloak on the T-posed body", "baked one-piece figure"), os.path.join(base, "contact_sheet.png"), cfg["cloth"]["proof"]["thumb_w"])
    print("contact sheet", out)
