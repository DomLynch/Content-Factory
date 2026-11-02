"""Cut-out then TRELLIS for one picked image, one receipt per stage.
Usage: run_3d.py <asset_id> <picked_image.png> [--preset NAME] [--seed N] [--cutout-png PNG [--cutout-receipt R]] [--from-latents PATH --out-name STEM]
Settings come from config/config.json ("trellis") and config.local.json; nothing here is a literal path or flag.
--cutout-png uses an already cut-out RGBA png as work/<asset>/cutout.png and skips the cut-out (the A/B gives both engines the same cut-out).
--from-latents re-decodes and re-bakes saved latents (no sampling, no cut-out): the hero/master tier on the same asset id.
Must be started with nothing else on the GPU."""
import argparse, os, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

ap = argparse.ArgumentParser()
ap.add_argument("asset_id")
ap.add_argument("image")
ap.add_argument("--preset", default="master_reduced")
ap.add_argument("--from-latents", default=None)
ap.add_argument("--out-name", default="mesh")
ap.add_argument("--cutout-png", default=None)   # A/B fairness: an ALREADY cut-out RGBA png (the Factory's one cutout stage): copied to work/<asset>/cutout.png, the cutout stage is skipped
ap.add_argument("--cutout-receipt", default=None)   # with --cutout-png: path of the cutout receipt that made that png, recorded in the trellis receipt
ap.add_argument("--seed", type=int, default=None)   # A/B harness: same seed as the Pixal3D half; omitted = generate.py default (42)
a = ap.parse_args()

cfg = cf_common.load_config()
cf_common.plugin_gate("chain", a.asset_id, bool(a.cutout_png))   # build order: sheet complete, Dom's yes, cut-out receipt (plugin 0.1.24)
cf_common.plugin_gate("token", a.asset_id, a.preset, bool(a.from_latents))
cf_common.keep_awake()   # token floor, also outside Claude Code (plugin 0.1.23)
loc, tr = cfg["local"], cfg["trellis"]
preset = tr["presets"][a.preset]
repo_dir = loc["trellis_repo_dir"]
work = os.path.join(loc["outputs_dir"], "3d", a.asset_id)
os.makedirs(work, exist_ok=True)

env = dict(os.environ, **tr["env"])
cut = os.path.join(work, "cutout.png")

if a.cutout_png:
    if a.from_latents:
        sys.exit("--cutout-png and --from-latents do not combine")
    if not os.path.isfile(a.cutout_png):
        sys.exit(f"--cutout-png not found: {a.cutout_png}")
    shutil.copyfile(a.cutout_png, cut)   # no cutout stage and no cutout receipt of ours: the Factory's receipt is referenced below
elif not a.from_latents:
    # stage: cutout (CPU, about 105 s)
    ram = cf_common.FreeRamWatch(); ram.start()
    t0 = time.time()
    cut_env = {k: v for k, v in env.items() if k != "TRELLIS3_REMBG"}  # cutout_cpu.py reads TRELLIS3_REMBG as its model id; "none" is only for the generate stage
    r = subprocess.run([loc["cutout_python"], os.path.join(loc["cutout_repo_dir"], "tools", "cutout_cpu.py"), a.image, cut], cwd=loc["cutout_repo_dir"], env=cut_env, capture_output=True, text=True)  # BiRefNet needs einops: only the py3.11 venv has it
    cf_common.write_receipt(cfg, a.asset_id, "cutout", tool="cutout_cpu.py", model={"id": tr["cutout_model"]}, inputs=[a.image],
                            params={"device": "cpu"}, timing={"wall_s": round(time.time() - t0, 1)}, memory={"lowest_free_ram_gb": ram.stop()},
                            outputs=[cut], status="ok" if r.returncode == 0 and os.path.exists(cut) else "fail", error=r.stderr[-400:] if r.returncode else "")
    if r.returncode or not os.path.exists(cut):
        sys.exit("cutout failed")
elif not os.path.exists(cut):
    sys.exit("--from-latents needs the asset's existing cutout.png")

# stage: trellis
cf_common.wait_for_free_ram(cfg, "trellis")
ram = cf_common.FreeRamWatch(); ram.start()
out_prefix = os.path.join(work, a.out_name)
cmd = [loc["trellis_python"], "generate.py", cut, "--preset", preset["preset"], "--texture-size", str(preset["texture_size"]),
       "--bake-faces", str(preset["bake_faces"]), "--device", "rocm", "--single-process", "--profile-memory", "--output", out_prefix] + preset.get("extra", [])
if a.seed is not None:
    cmd += ["--seed", str(a.seed)]
if a.from_latents:
    cmd += ["--from-latents", a.from_latents, "--normal-map"]
t0 = time.time()
log = os.path.join(loc["logs_dir"], f"trellis_{a.asset_id}_{a.out_name}.txt")
with open(log, "w", encoding="utf-8") as lf:
    r = subprocess.run(cmd, cwd=repo_dir, env=env, stdout=lf, stderr=subprocess.STDOUT)
outs = [p for p in (out_prefix + ".glb", out_prefix + ".receipt.json") if os.path.exists(p)]
cf_common.write_receipt(cfg, a.asset_id, "trellis", tool="generate.py", model={"id": "TRELLIS.2-4B", "repo": repo_dir},
                        inputs=[cut] + ([a.from_latents] if a.from_latents else []),
                        params={"preset": a.preset, **preset, "env": tr["env"], "log": log, "from_latents": a.from_latents, "out_name": a.out_name, "seed": a.seed,
                                **({"cutout_png": a.cutout_png, "cutout_png_sha256": cf_common.sha256(a.cutout_png), "cutout_receipt": a.cutout_receipt} if a.cutout_png else {})},
                        timing={"wall_s": round(time.time() - t0, 1)}, memory={"lowest_free_ram_gb": ram.stop()},
                        outputs=outs, status="ok" if r.returncode == 0 and any(p.endswith(".glb") for p in outs) else "fail",
                        error="" if r.returncode == 0 else f"exit {r.returncode}, see log")
print("trellis exit", r.returncode, "outputs", outs)
