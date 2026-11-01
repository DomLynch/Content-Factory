"""One picked, cut-out image to a textured GLB with Pixal3D, one receipt (stage "pixal3d", same shape as the trellis receipt).
Usage: run_pixal3d.py <asset_id> <cutout.png> [--preset NAME] [--seed N]
Settings come from config/config.json ("pixal3d") and config.local.json (paths, with defaults under work_dir); nothing here is a literal path or flag.
Must be started with nothing else on the GPU (the plugin tool and its hooks enforce the lock, the RAM gate and the input gate)."""
import argparse, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

ap = argparse.ArgumentParser()
ap.add_argument("asset_id")
ap.add_argument("image")
ap.add_argument("--preset", default=None)
ap.add_argument("--seed", type=int, default=None)
a = ap.parse_args()

cfg = cf_common.load_config()
loc, px = cfg["local"], cfg["pixal3d"]
preset_name = a.preset or px["default_preset"]
if preset_name not in px["presets"]:
    sys.exit(f"unknown preset {preset_name}; choose from {sorted(px['presets'])}")
preset = px["presets"][preset_name]
seed = a.seed if a.seed is not None else cfg["defaults"]["seed"]
work = loc["work_dir"]


def path(key, default):
    return loc.get(key) or os.path.join(work, *default)


repo = path("pixal3d_repo_dir", ["Pixal3D"])
py = path("pixal3d_python", ["Pixal3D-312", ".venv", "Scripts", "python.exe"])
dirs = {"model": path("pixal3d_model_dir", ["models", "Pixal3D"]), "dino": path("pixal3d_dino_dir", ["models", "dinov3-vitl16-pretrain-lvd1689m"]),
        "moge": path("pixal3d_moge_dir", ["models", "moge-2-vitl"]), "birefnet": path("pixal3d_birefnet_dir", ["models", "BiRefNet"])}
missing = [p for p in (repo, py, *dirs.values()) if not os.path.exists(p)]
if missing:
    sys.exit("missing: " + ", ".join(missing))

out_dir = os.path.join(loc["outputs_dir"], "3d", a.asset_id)
os.makedirs(out_dir, exist_ok=True)
glb = os.path.join(out_dir, f"pixal3d_{preset_name}.glb")
env = dict(os.environ, **px["env"])
cmd = [py, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pixal3d_entry.py"), a.image, glb, "--repo", repo, "--model-dir", dirs["model"],
       "--dino-dir", dirs["dino"], "--moge-dir", dirs["moge"], "--birefnet-dir", dirs["birefnet"], "--seed", str(seed), "--resolution", str(preset["resolution"])]
if preset.get("low_vram"):
    cmd.append("--low-vram")

cf_common.wait_for_free_ram(cfg, "pixal3d")
ram = cf_common.FreeRamWatch(); ram.start()
log = os.path.join(loc["logs_dir"], f"pixal3d_{a.asset_id}_{preset_name}.txt")
t0 = time.time()
with open(log, "w", encoding="utf-8") as lf:
    r = subprocess.run(cmd, cwd=repo, env=env, stdout=lf, stderr=subprocess.STDOUT)
try:
    commit = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=20).stdout.strip()
except Exception:
    commit = ""
ok = r.returncode == 0 and os.path.exists(glb)
cf_common.write_receipt(cfg, a.asset_id, "pixal3d", tool="pixal3d_entry.py", model={"id": "Pixal3D (TencentARC, MIT)", "repo": repo, "commit": commit, "weights": dirs["model"]},
                        inputs=[a.image], params={"preset": preset_name, **preset, "seed": seed, "env": px["env"], "log": log, "helpers": dirs, "rembg": "BiRefNet (MIT), not RMBG-2.0"},
                        timing={"wall_s": round(time.time() - t0, 1)}, memory={"lowest_free_ram_gb": ram.stop()},
                        outputs=[glb] if os.path.exists(glb) else [], status="ok" if ok else "fail", error="" if ok else f"exit {r.returncode}, see {log}")
sys.exit(0 if ok else 1)
