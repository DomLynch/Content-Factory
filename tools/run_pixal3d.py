"""One picked, cut-out image to a textured GLB with Pixal3D, one receipt (stage "pixal3d", same shape as the trellis receipt).
Usage: run_pixal3d.py <asset_id> <cutout.png> [--preset NAME] [--seed N] [--no-naf]
       run_pixal3d.py <asset_id> --views <folder | mapping.json | inline json> [--mirror-profile] [--no-three-quarter]   (plugin 0.1.18: multi-view, tools/pixal3d_mv.py)
Settings come from config/config.json ("pixal3d") and config.local.json (paths, with defaults under work_dir); nothing here is a literal path or flag.
Must be started with nothing else on the GPU (the plugin tool and its hooks enforce the lock, the RAM gate and the input gate)."""
import argparse, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

cf_common.pop_allow_stale()
ap = argparse.ArgumentParser()
ap.add_argument("asset_id")
ap.add_argument("image", nargs="?", default=None)
ap.add_argument("--views", default=None, help="multi-view: sheet cut-outs (folder, mapping json or inline json cell->png); mode multiview, see tools/pixal3d_mv.py")
ap.add_argument("--mirror-profile", action="store_true", default=None, help="multi-view: stand in for the missing opposite profile with a mirrored one (flagged assumption)")
ap.add_argument("--no-three-quarter", action="store_true", help="multi-view: leave the three-quarter cell out")
ap.add_argument("--preset", default=None)
ap.add_argument("--seed", type=int, default=None)
ap.add_argument("--no-naf", action="store_true", help="ROUGH fallback: bicubic feature upsample instead of NAF (also config pixal3d.naf = false)")
a = ap.parse_args()
cf_common.plugin_gate("models", "pixal3d")   # latest-models gate (plugin 0.1.27)
cf_common.keep_awake()

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
if a.views:   # multi-view loads its own model folder (patched offline pipeline_mv.json, ckpts link): config pixal3d.mv_model_dir
    import pixal3d_mv
    dirs["model"] = pixal3d_mv.mv_model_dir(cfg)
missing = [p for p in (repo, py, *dirs.values()) if not os.path.exists(p)]
if missing:
    sys.exit("missing: " + ", ".join(missing))

naf = bool(px.get("naf", True)) and not a.no_naf
quality_label = "" if naf else px.get("naf_off_label", "ROUGH / NAF-off")
shim_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), *px.get("shim_dir", "pixal3d_shims").split("/"))
torch_home = path("pixal3d_torch_home", ["models", "torch-hub"])
if naf and not os.path.isfile(os.path.join(shim_dir, "natten", "__init__.py")):
    sys.exit(f"missing natten shim: {shim_dir}")

out_dir = os.path.join(loc["outputs_dir"], "3d", a.asset_id)
os.makedirs(out_dir, exist_ok=True)
mv = None
if a.views:
    import pixal3d_mv
    try:
        mv = pixal3d_mv.prepare(dirs["model"], a.views, os.path.join(out_dir, "mv_input"), a.mirror_profile, False if a.no_three_quarter else None)
    except pixal3d_mv.MVError as e:
        sys.exit(f"REFUSED: {e}")
    a.image = mv["front"]
elif not a.image:
    sys.exit("give an image, or --views")
glb = os.path.join(out_dir, f"pixal3d_{'mv_' if mv else ''}{preset_name}.glb")
env = dict(os.environ, **px["env"])
cmd = [py, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pixal3d_entry.py"), a.image, glb, "--repo", repo, "--model-dir", dirs["model"],
       "--dino-dir", dirs["dino"], "--moge-dir", dirs["moge"], "--birefnet-dir", dirs["birefnet"], "--seed", str(seed), "--resolution", str(preset["resolution"]),
       "--shim-dir", shim_dir, "--torch-home", torch_home]
if mv:
    cmd += ["--mv-dir", mv["dir"]]
if not naf:
    cmd.append("--no-naf")
if preset.get("low_vram"):
    cmd.append("--low-vram")
naf = bool(px.get("naf", True)) and not a.no_naf
if not naf:
    cmd.append("--no-naf")

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
                        inputs=[v["source"] for v in mv["views"] if not v["mirrored_from"]] if mv else [a.image], params={**(pixal3d_mv.receipt_params(mv) if mv else {}), "preset": preset_name, **preset, "seed": seed, "naf": naf, "quality_label": quality_label, "natten_shim": shim_dir if naf else "", "torch_home": torch_home, "env": px["env"], "log": log, "helpers": dirs, "rembg": "BiRefNet (MIT), not RMBG-2.0", **({"export_module": px.get("export", {}).get("module", "") or "upstream cumesh"} if mv else {})},
                        timing={"wall_s": round(time.time() - t0, 1)}, memory={"lowest_free_ram_gb": ram.stop()},
                        outputs=[glb] if os.path.exists(glb) else [], status="ok" if ok else "fail", error="" if ok else f"exit {r.returncode}, see {log}")
sys.exit(0 if ok else 1)
