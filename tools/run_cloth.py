"""Cloth stage (plugin 0.1.18): drape ONE gear part (cloak first) on the T-posed bound body with a Blender cloth simulation and keep the settled mesh as the part's new REST pose, one receipt.
Usage: run_cloth.py <asset_id> <part_name> <part_mesh.glb> <bound_body.glb> [--preset silk|cotton|wool] [--mode rest|clip] [--max-frames N]
Settings come from config/config.json ("cloth", plus "bind" for the rig name and "limits") and config.local.json; nothing here is a literal path or number.
CPU only: Blender runs --background (tools/cloth/cloth_blender.py), no render, no GPU. Before Blender starts: free RAM >= cloth.guard.min_free_ram_gb, the exclusive Blender lock file
(config.local blender_lock, default <work_dir>/blender_job.lock) and no Flux model load in the last cloth.guard.flux_window_s (tools/cloth/cloth_core.py).
mode=rest simulates until settled and writes <outputs_dir>/3d/<asset_id>/cloth_<part>.glb (unskinned rest mesh: bind it afterwards with run_gear.py so it follows the skeleton).
mode=clip (per-clip sim baked as shape keys) is a stub: it refuses with NotImplemented.
Gate: exported, vertex/face counts unchanged by the sim, UVs kept, no NaN, not exploded, enough pinned vertices, max penetration of the free vertices into the FULL-resolution body <= cloth.gate.max_penetration_m."""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloth"))
import cf_common
import cloth_core

ap = argparse.ArgumentParser(usage=__doc__)
ap.add_argument("asset_id"); ap.add_argument("part"); ap.add_argument("part_mesh"); ap.add_argument("body_glb")
ap.add_argument("--preset", default=""); ap.add_argument("--mode", default="rest"); ap.add_argument("--max-frames", type=int, default=0)
a = ap.parse_args()

cfg = cf_common.load_config()
gm, bg = os.path.abspath(a.part_mesh), os.path.abspath(a.body_glb)


def receipt(status, error="", outs=(), timing=None, mem=None, extra=None):
    x = dict(extra or {})
    params = x.pop("params", {"mode": a.mode, "part": a.part})
    return cf_common.write_receipt(cfg, a.asset_id, "cloth", tool="cloth_blender.py", model={"id": "Blender cloth simulation, body as collision object, shoulder pins"},
                                   inputs=[gm, bg], params={**params, **x}, timing=timing or {}, memory=mem or {}, outputs=list(outs), status=status, error=error)


code, path = cloth_core.run(cfg, a.asset_id, a.part, gm, bg, preset=a.preset, mode=a.mode, max_frames=a.max_frames, repo=cf_common.REPO, receipt=receipt)
if path:
    print("receipt", path)
sys.exit(code)
