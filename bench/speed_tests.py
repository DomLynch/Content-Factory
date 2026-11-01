"""Helpers for the Flux speed tests (plugin 0.1.23): bench/ref_cache_ab.ps1 and bench/compile_probe.ps1 call these. CPU only, standard library.

  kit      <src_kit.json> <row_id> <variant> <stamp> [--no-ref]   write a one-row throw-away kit named _speed_tests and print {"kit","row_id","png"} as JSON
  ab       <off_receipt.json> <on_receipt.json> <out.json>        compare the two renders: sha256 of both PNGs, pixel-identical yes/no, gen_s and wall_s each
  probe    <eager.json> <compiled.json> <out.json>                compare the two probe result files

Why a throw-away kit: the output folder is <outputs_dir>/_speed_tests, never kits_out/vampire_gpt_sheet, and the receipts are keyed by the kit name, so the
runs can never count as rolls of a real sheet cell (re-roll cap). Each run gets its own row id (<row>_<variant>_<stamp>), and the row is typed `other` so the
reference-first rule (which needs a verdict on the identity cell of a real kit) does not hold a speed test; every other Flux policy rule still applies.
"""
import hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
import cf_common


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def cmd_kit(argv):
    no_ref = "--no-ref" in argv
    src, row_id, variant, stamp = [a for a in argv if not a.startswith("--")][:4]
    cfg = cf_common.load_config()
    kit = json.load(open(src, encoding="utf-8-sig"))
    row = dict(next(r for r in kit["rows"] if r.get("row_id", r["name"]) == row_id))
    new_id = f"{row_id}_{variant}_{stamp}"
    row.update({"name": new_id, "row_id": new_id, "row_type": "other"})
    for k in ("group", "part", "mirror_of"):
        row.pop(k, None)
    if no_ref:
        row["use_ref"] = False
    out = {**{k: v for k, v in kit.items() if k not in ("rows", "identity")}, "kit": "_speed_tests", "rows": [row]}
    d = os.path.join(cfg["local"]["work_dir"], "speed_tests")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"kit_{new_id}.json")
    json.dump(out, open(path, "w"), indent=1)
    W, H = row.get("w", kit.get("w", cfg["defaults"]["w"])), row.get("h", kit.get("h", cfg["defaults"]["h"]))
    print(json.dumps({"kit": path, "row_id": new_id, "png": os.path.join(cfg["local"]["outputs_dir"], "_speed_tests", f"{new_id}_{W}x{H}.png")}))


def cmd_ab(argv):
    off, on, out = argv[:3]
    r = [json.load(open(p, encoding="utf-8-sig")) for p in (off, on)]
    pngs = [x["outputs"][0]["path"] for x in r]
    sha = [sha256(p) for p in pngs]
    identical = sha[0] == sha[1]
    rep = {"pixel_identical": identical, "sha256_off": sha[0], "sha256_on": sha[1], "png_off": pngs[0], "png_on": pngs[1],
           "off": {"gen_s": r[0]["timing"].get("generate_s"), "wall_s": r[0]["timing"].get("wall_s"), "ref_cache": r[0]["params"].get("ref_cache")},
           "on": {"gen_s": r[1]["timing"].get("generate_s"), "wall_s": r[1]["timing"].get("wall_s"), "ref_cache": r[1]["params"].get("ref_cache"),
                  "cache_hits": r[1]["params"].get("ref_cache_hits"), "cache_misses": r[1]["params"].get("ref_cache_misses")}}
    if not identical:   # byte-different PNGs can still be pixel-identical (metadata): compare decoded pixels
        try:
            from PIL import Image, ImageChops
            a, b = Image.open(pngs[0]).convert("RGB"), Image.open(pngs[1]).convert("RGB")
            diff = ImageChops.difference(a, b).getbbox() if a.size == b.size else (0, 0) + a.size
            rep["pixel_identical"] = diff is None
            rep["pixel_diff_bbox"] = diff
        except Exception as e:
            rep["pixel_compare_error"] = repr(e)
    g0, g1 = rep["off"]["gen_s"], rep["on"]["gen_s"]
    if g0 and g1:
        rep["gen_speedup_pct"] = round(100 * (g0 - g1) / g0, 1)
    rep["note"] = ("one row per process: the reference stream cache is keyed on the target timestep, so a single cell has no repeated key and cannot hit; "
                   "ref_cache_hits > 0 only appears when one process renders several cells. Pixel identity is the safety result here, speed needs the multi-cell run.")
    json.dump(rep, open(out, "w"), indent=1)
    print(json.dumps(rep, indent=1))


def cmd_probe(argv):
    e, c, out = [json.load(open(p, encoding="utf-8-sig")) for p in argv[:2]] + [None]
    out = argv[2]
    rep = {"compiled": bool(c.get("compiled")), "fell_back": bool(c.get("fell_back")), "error": c.get("error"),
           "eager": {k: e.get(k) for k in ("gen_s", "steps", "s_per_step_median", "first_step_s", "sha256")},
           "compiled_run": {k: c.get(k) for k in ("gen_s", "steps", "s_per_step_median", "first_step_s", "compile_s_estimate", "sha256")}}
    if e.get("s_per_step_median") and c.get("s_per_step_median"):
        rep["steady_state_speedup_pct"] = round(100 * (e["s_per_step_median"] - c["s_per_step_median"]) / e["s_per_step_median"], 1)
    rep["pixel_identical"] = bool(e.get("sha256") and e.get("sha256") == c.get("sha256"))
    json.dump(rep, open(out, "w"), indent=1)
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    cmds = {"kit": cmd_kit, "ab": cmd_ab, "probe": cmd_probe}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]](sys.argv[2:])
