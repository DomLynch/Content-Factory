"""MATERIALS stage (plugin 0.1.17): render ONE gear-part GLB twice with the SAME camera and the SAME three-point rig, once with its baked texture and once with a scanned CC0 PBR
material (Poly Haven / ambientCG, config/materials.json), then put the frames side by side (baked LEFT, scanned RIGHT). One receipt, stage `materials`.
Usage: run_materials.py <asset_id> <part.glb> [--part coat|cloak|boots] [--swatch <name>] [--materials-dir DIR] [--angles 0 45 135] [--plan]
  --plan    resolve the swatch to its scanned set, check the files on disk against their manifest sha256, print the Blender commands; starts nothing.
CPU only: Cycles on CPU through tools/ab/turntable_blender.py (the A/B rig) and tools/materials/apply_materials_blender.py. NO GPU, so no GPU lock.
Before ANY Blender process (tools/materials/blender_guard.py, config materials.*): free RAM at least min_free_ram_gb (10), no Flux model load in the last 60 s, and the exclusive lock file
<work_dir>/blender_job.lock (wait up to lock_wait_s, then give up). Nothing is downloaded here: the sets come from tools/materials/fetch_materials.py (run once, with the owner's yes).
A swatch with no scan (pale_skin, dark_hair, lace: config/materials.json says GAP) stops the run with that reason; it never falls back to a baked or invented texture."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ab"))
sys.path.insert(0, os.path.join(HERE, "materials"))
import cf_common  # noqa: E402
import blender_guard  # noqa: E402

PARTS = ("coat", "cloak", "boots")


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def resolve(mapping: dict, part: str, swatch: str | None) -> tuple[dict, str]:
    """(swatch row, swatch name) for a part, or raise ValueError with the reason (unknown part or swatch, or a GAP: no scan exists)."""
    if part not in mapping["parts"]:
        raise ValueError(f"unknown part {part!r}; known: {sorted(mapping['parts'])}")
    name = swatch or mapping["parts"][part]["swatch"]
    if name not in mapping["swatches"]:
        raise ValueError(f"unknown swatch {name!r}; known: {sorted(mapping['swatches'])}")
    row = mapping["swatches"][name]
    if not row.get("material"):
        raise ValueError(f"swatch {name} has no scanned set: {row['fit']}")
    if row["material"] not in mapping["materials"]:
        raise ValueError(f"swatch {name} names {row['material']} which is not in config/materials.json materials")
    return row, name


def on_disk(mapping: dict, material_id: str, root: str, check_sha: bool = True) -> tuple[dict, list[str]]:
    """(maps {name: abs path}, problems). Every file named by the manifest must exist with its recorded sha256 (the fetch script verified it against the published checksum)."""
    folder = os.path.join(root, material_id)
    man_path = os.path.join(folder, "manifest.json")
    if not os.path.isfile(man_path):
        return {}, [f"{man_path} is missing: run tools/materials/fetch_materials.py (needs the owner's yes: it downloads)"]
    man = read_json(man_path)
    problems = []
    for f in man["files"]:
        p = os.path.join(folder, f["name"])
        if not os.path.isfile(p):
            problems.append(f"{p} is missing")
        elif check_sha and cf_common.sha256(p) != f["sha256"]:
            problems.append(f"{p} sha256 differs from the manifest")
    maps = {k: os.path.join(folder, v) for k, v in man["maps"].items()}
    need = ("base_color", "normal", "roughness")
    problems += [f"manifest has no {k} map" for k in need if k not in maps]
    return maps, problems


def material_block(mapping: dict, swatch_row: dict, part: str, maps: dict, cfg_m: dict) -> dict:
    m = mapping["materials"][swatch_row["material"]]
    kind = "fabric" if m["source"] == "polyhaven" else ("metal" if m.get("metalness") else "leather")
    block = {"name": m["asset_id"], "source": m["source"], "maps": maps, "normal_convention": m["normal_convention"], "tile_m": m["tile_m"], "tile_m_basis": m["tile_m_basis"],
             "part_height_m": mapping["parts"][part]["part_height_m"], "kind": kind, "bump_strength": cfg_m["bump_strength"], "bump_distance_m": cfg_m["bump_distance_m"],
             "normal_strength": cfg_m["normal_strength"], "sheen_weight": cfg_m["sheen_weight"]}
    if swatch_row.get("tint"):
        block["tint"] = swatch_row["tint"]
    return block


def render_config(cfg: dict, glb: str, out_dir: str, label: str, angles: list[int] | None) -> dict:
    """The A/B turntable config of config.json ab.render (same camera, three-point rig and world, via ab_core.matched_render_config); only frames, head crops and the label differ."""
    import ab_core
    matched = ab_core.matched_render_config(cfg["ab"])
    matched["angles_deg"] = list(angles or cfg["materials"]["angles_deg"])
    matched["head_crops"] = cfg["materials"]["head_crops"]
    return {**matched, "glb": glb, "out_dir": out_dir, "label": label, "config_hash": ab_core.config_hash(matched), "glb_sha256": ab_core.sha256_file(glb)}


def run_blender(blender: str, script: str, cfg_path: str, log: str, timeout: int, extra: list[str] | None = None) -> object:
    with open(log, "w", encoding="utf-8") as lf:
        try:
            return subprocess.run([blender, "--background", "--python", script, "--", *(extra or ["--config", cfg_path])], stdout=lf, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return "timeout"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("asset_id")
    ap.add_argument("glb")
    ap.add_argument("--part", default="coat", choices=PARTS)
    ap.add_argument("--swatch")
    ap.add_argument("--materials-dir")
    ap.add_argument("--angles", nargs="*", type=int)
    ap.add_argument("--plan", action="store_true")
    a = ap.parse_args(argv)
    cfg = cf_common.load_config()
    loc, cm = cfg["local"], cfg["materials"]
    mapping = read_json(os.path.join(cf_common.REPO, cm["mapping"]))
    glb = os.path.abspath(a.glb)
    root = a.materials_dir or loc.get("materials_dir") or os.path.join(loc["work_dir"], "materials")
    out = os.path.join(loc["outputs_dir"], "3d", a.asset_id, cm["subdir"], a.part)
    problems: list[str] = []
    row = name = None
    try:
        row, name = resolve(mapping, a.part, a.swatch)
    except ValueError as e:
        problems.append(str(e))
    maps: dict = {}
    if row:
        maps, p = on_disk(mapping, row["material"], root)
        problems += p
    if not os.path.isfile(glb):
        problems.append(f"glb not found: {glb}")
    if not os.path.exists(loc["blender_exe"]):
        problems.append(f"blender_exe not found: {loc['blender_exe']}")
    ab_script = os.path.join(cf_common.REPO, cfg["ab"]["render"]["script"])
    apply_script = os.path.join(HERE, "materials", "apply_materials_blender.py")
    compose_script = os.path.join(HERE, "materials", "compose_blender.py")
    for s in (ab_script, apply_script, compose_script):
        if not os.path.isfile(s):
            problems.append(f"script not found: {s}")

    def receipt(status, error="", outs=(), timing=None, mem=None, extra=None):
        return cf_common.write_receipt(cfg, a.asset_id, "materials", tool="run_materials.py (Blender Cycles CPU)", model={"id": "scanned CC0 PBR " + (row["material"] if row else "none")},
                                       inputs=[glb] + list(maps.values()), params={"part": a.part, "swatch": name, "material": row and row["material"], "swatch_fit": row and row["fit"],
                                                                                  "lighting_tier": "three-point (config ab.render)", "licence": "CC0-1.0", **(extra or {})},
                                       timing=timing or {}, memory=mem or {}, outputs=list(outs), status=status, error=error)

    if a.plan:
        print(json.dumps({"part": a.part, "swatch": name, "material": row and row["material"], "fit": row and row["fit"], "maps": maps, "out": out, "problems": problems,
                          "blender_cmds": [[loc["blender_exe"], "--background", "--python", s, "--", "--config", "<cfg>"] for s in (ab_script, apply_script)]}, indent=2))
        return 1 if problems else 0
    if problems:
        receipt("fail", "; ".join(problems))
        sys.exit("cannot run materials:\n  " + "\n  ".join(problems))

    os.makedirs(out, exist_ok=True)
    bake_dir, scan_dir, sbs_dir = (os.path.join(out, d) for d in ("baked", "scanned", "side_by_side"))
    for d in (bake_dir, scan_dir, sbs_dir):
        os.makedirs(d, exist_ok=True)
    lock = os.path.join(loc["work_dir"], cm["lock_file"])
    why = blender_guard.wait_ready(cm["min_free_ram_gb"], loc["logs_dir"], cm["flux_load_window_s"], cm["ram_wait_s"])
    if why:
        receipt("fail", "not started: " + why)
        sys.exit("not started: " + why)
    try:
        blender_guard.acquire(lock, cm["lock_wait_s"])
    except blender_guard.LockBusy as e:
        receipt("fail", f"not started: {e}")
        sys.exit(f"not started: {e}")
    ram = cf_common.FreeRamWatch()
    ram.start()
    t0 = time.time()
    results: dict = {}
    try:
        steps = [("baked", ab_script, render_config(cfg, glb, bake_dir, "baked", a.angles), bake_dir),
                 ("scanned", apply_script, {**render_config(cfg, glb, scan_dir, "scanned", a.angles), "turntable_script": ab_script,
                                           "materials": material_block(mapping, row, a.part, maps, cm)}, scan_dir)]
        for label, script, rcfg, d in steps:   # one Blender process at a time, each re-checks RAM and the Flux load first
            if label != "baked":
                why = blender_guard.wait_ready(cm["min_free_ram_gb"], loc["logs_dir"], cm["flux_load_window_s"], cm["ram_wait_s"])
                if why:
                    results[label] = f"not started: {why}"
                    break
            cfg_path = os.path.join(d, "render_config.json")
            with open(cfg_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(rcfg, f, indent=2)
            for stale in (os.path.join(d, "turntable.json"),):
                if os.path.exists(stale):
                    os.remove(stale)
            rc = run_blender(loc["blender_exe"], script, cfg_path, os.path.join(loc["logs_dir"], f"materials_{a.asset_id}_{a.part}_{label}.txt"), cm["timeout_s"])
            results[label] = rc
            if rc != 0 or not os.path.exists(os.path.join(d, "turntable.json")):
                break
        else:
            why = blender_guard.wait_ready(cm["min_free_ram_gb"], loc["logs_dir"], cm["flux_load_window_s"], cm["ram_wait_s"])
            if why:
                results["compose"] = f"not started: {why}"
            else:
                results["compose"] = run_blender(loc["blender_exe"], compose_script, "", os.path.join(loc["logs_dir"], f"materials_{a.asset_id}_{a.part}_compose.txt"),
                                                 cm["timeout_s"], extra=[bake_dir, scan_dir, sbs_dir])
    finally:
        blender_guard.release(lock)
    sbs = sorted(os.path.join(sbs_dir, f) for f in os.listdir(sbs_dir) if f.startswith("side_by_side_") and f.endswith(".png"))
    applied_p = os.path.join(scan_dir, "materials_applied.json")
    applied = read_json(applied_p) if os.path.exists(applied_p) else {}
    ok = results.get("compose") == 0 and bool(sbs)
    outs = sbs + [os.path.join(d, f) for d in (bake_dir, scan_dir) for f in sorted(os.listdir(d)) if f.startswith("turn_") and f.endswith(".png")]
    receipt("ok" if ok else "fail", "" if ok else f"blender results {results}, see logs", outs=outs, timing={"wall_s": round(time.time() - t0, 1)}, mem={"lowest_free_ram_gb": ram.stop()},
            extra={"results": results, "applied": applied, "uv_tiling_assumption": "box projection in metres / tile_m; part_height_m is the part's assumed height in a 1.8 m character"})
    print("materials", "ok" if ok else "FAIL", "->", sbs_dir)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
