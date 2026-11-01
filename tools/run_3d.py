"""Cut-out then TRELLIS for one picked image, one receipt per stage.
Usage: run_3d.py <asset_id> <picked_image.png> [--preset NAME] [--from-latents PATH --out-name STEM]
Settings come from config/config.json ("trellis") and config.local.json; nothing here is a literal path or flag.
--from-latents re-decodes and re-bakes saved latents (no sampling, no cut-out): the hero/master tier on the same asset id.
Must be started with nothing else on the GPU."""
import argparse, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

ap = argparse.ArgumentParser()
ap.add_argument("asset_id")
ap.add_argument("image")
ap.add_argument("--preset", default="master_reduced")
ap.add_argument("--from-latents", default=None)
ap.add_argument("--out-name", default="mesh")
a = ap.parse_args()

cfg = cf_common.load_config()
loc, tr = cfg["local"], cfg["trellis"]
preset = tr["presets"][a.preset]
repo_dir = loc["trellis_repo_dir"]
work = os.path.join(loc["outputs_dir"], "3d", a.asset_id)
os.makedirs(work, exist_ok=True)

env = dict(os.environ, **tr["env"])
cut = os.path.join(work, "cutout.png")

if not a.from_latents:
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
ram = cf_common.FreeRamWatch(); ram.start()
out_prefix = os.path.join(work, a.out_name)
cmd = [loc["trellis_python"], "generate.py", cut, "--preset", preset["preset"], "--texture-size", str(preset["texture_size"]),
       "--bake-faces", str(preset["bake_faces"]), "--device", "rocm", "--single-process", "--profile-memory", "--output", out_prefix] + preset.get("extra", [])
if a.from_latents:
    cmd += ["--from-latents", a.from_latents, "--normal-map"]
t0 = time.time()
log = os.path.join(loc["logs_dir"], f"trellis_{a.asset_id}_{a.out_name}.txt")
with open(log, "w", encoding="utf-8") as lf:
    r = subprocess.run(cmd, cwd=repo_dir, env=env, stdout=lf, stderr=subprocess.STDOUT)
outs = [p for p in (out_prefix + ".glb", out_prefix + ".receipt.json") if os.path.exists(p)]
cf_common.write_receipt(cfg, a.asset_id, "trellis", tool="generate.py", model={"id": "TRELLIS.2-4B", "repo": repo_dir},
                        inputs=[cut] + ([a.from_latents] if a.from_latents else []),
                        params={"preset": a.preset, **preset, "env": tr["env"], "log": log, "from_latents": a.from_latents, "out_name": a.out_name},
                        timing={"wall_s": round(time.time() - t0, 1)}, memory={"lowest_free_ram_gb": ram.stop()},
                        outputs=outs, status="ok" if r.returncode == 0 and any(p.endswith(".glb") for p in outs) else "fail",
                        error="" if r.returncode == 0 else f"exit {r.returncode}, see log")
print("trellis exit", r.returncode, "outputs", outs)
