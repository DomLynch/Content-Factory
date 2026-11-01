"""Multi-cell Flux probe engine (plugin 0.1.23): ONE process, the model loaded once, a PLAN of jobs rendered one after another, per-job switches changed between them.

  flux_multi.py <kit.json> --plan '<json list>' | --plan-file plan.json --out result.json [--tag NAME] [--timeout-s N]
  flux_multi.py --compare A.json B.json OUT.json [--receipts-dir DIR]     pair the jobs of two runs by (row, tag): sha256, mean abs pixel diff, gen_s, speedup %

Each job: {"row": "<row_id>", "tag": "base", "use_ref": null|true|false, "vae_tiling": null|true|false, "tunable": null|"off"|"tune"|"reuse",
           "nan_diag": false, "out_list": false, "skip_gc": false, "ref_cache": false}
Switches are exactly the ones Strategy listed: VAE tiling on/off, PyTorch TunableOp off/tune/reuse (csv written next to the result), output=['images'] only,
CF_NAN_DIAG-style isnan on latents and decoded tensor, skip the per-row gc/empty_cache, the reference-stream cache (flux/patches/flux3_ref_cache.py).
Per job it reports: gen_s, s/step (median of the steps after the first, from scheduler.step timestamps), first step, VAE decode seconds, mean brightness, sha256 of the
PNG, mean-abs and max pixel diff against the first job of the same row, cache hits/misses, errors. Prompts, seed, size and reference come from the kit row exactly as
flux_kit.py builds them; PNGs go ONLY to <outputs_dir>/_speed_tests/ and NO receipt is written (the runs can never count as rolls of a sheet cell).
A black job (mean < 3) is marked and the plan goes on. CF_DRY=1 prints the expanded plan and exits before torch is imported. Hard stop: --timeout-s watchdog
(faulthandler exit) inside, the .ps1 runner adds a process kill. NO package is installed by this script.
"""
import argparse, faulthandler, gc, glob, hashlib, json, os, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(REPO, "plugins", "factory", "lib"))
sys.path.insert(0, os.path.join(REPO, "flux", "patches"))
import cf_common

DEFAULT_JOB = {"use_ref": None, "vae_tiling": None, "tunable": None, "nan_diag": False, "out_list": False, "skip_gc": False, "ref_cache": False}


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def pixel_diff(pa, pb):
    try:
        from PIL import Image, ImageChops, ImageStat
        a, b = Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB")
        if a.size != b.size:
            return {"mean_abs_diff": None, "max_abs_diff": None, "note": f"sizes differ {a.size} vs {b.size}"}
        d = ImageChops.difference(a, b)
        return {"mean_abs_diff": round(sum(ImageStat.Stat(d).mean) / 3, 4), "max_abs_diff": max(hi for _, hi in d.getextrema())}
    except Exception as e:
        return {"mean_abs_diff": None, "max_abs_diff": None, "note": repr(e)}


def queue_gen_s(receipts_dir, kit_name, row):
    """generate_s of the newest ok flux receipt of this sheet row in a (Factory) receipts folder, else None."""
    best = None
    for f in glob.glob(os.path.join(receipts_dir, f"{kit_name}-{row}-*", "flux-*.json")):
        try:
            r = json.load(open(f, encoding="utf-8-sig"))
        except Exception:
            continue
        if r.get("status") == "ok" and (r.get("timing") or {}).get("generate_s") and (best is None or f > best[0]):
            best = (f, r["timing"]["generate_s"], r["timing"].get("wall_s"), (r.get("timing") or {}).get("row_index_in_process"))
    return None if best is None else {"receipt": best[0], "generate_s": best[1], "wall_s": best[2], "row_index_in_process": best[3]}


def compare(a_path, b_path, out, receipts_dir=None):
    A, B = [json.load(open(p, encoding="utf-8-sig")) for p in (a_path, b_path)]
    rows = []
    bmap = {(j["row"], j["tag"]): j for j in B["jobs"]}
    for ja in A["jobs"]:
        jb = bmap.get((ja["row"], ja["tag"]))
        if not jb or not ja.get("png") or not jb.get("png"):
            continue
        d = pixel_diff(ja["png"], jb["png"])
        item = {"row": ja["row"], "tag": ja["tag"], "identical_sha256": ja["sha256"] == jb["sha256"], **d,
                "a": {k: ja.get(k) for k in ("gen_s", "wall_s", "s_per_step_median", "decode_s", "cache_hits", "cache_misses")},
                "b": {k: jb.get(k) for k in ("gen_s", "wall_s", "s_per_step_median", "decode_s", "cache_hits", "cache_misses")}}
        if ja.get("gen_s") and jb.get("gen_s"):
            item["gen_speedup_pct_b_vs_a"] = round(100 * (ja["gen_s"] - jb["gen_s"]) / ja["gen_s"], 1)
        if receipts_dir:
            item["queue_receipt"] = queue_gen_s(receipts_dir, A.get("kit_name", ""), ja["row"])
        rows.append(item)
    rep = {"a": a_path, "b": b_path, "pairs": rows,
           "pixel_identical_all": bool(rows) and all(r["identical_sha256"] or r.get("mean_abs_diff") == 0 for r in rows)}
    json.dump(rep, open(out, "w"), indent=1)
    print(json.dumps(rep, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("kit", nargs="?")
    ap.add_argument("--plan")
    ap.add_argument("--plan-file")
    ap.add_argument("--out")
    ap.add_argument("--tag", default="run")
    ap.add_argument("--timeout-s", type=int, default=600)
    ap.add_argument("--compare", nargs=3, metavar=("A", "B", "OUT"))
    ap.add_argument("--receipts-dir")
    a = ap.parse_args()
    if a.compare:
        return compare(*a.compare, receipts_dir=a.receipts_dir)
    if not (a.kit and a.out and (a.plan or a.plan_file)):
        sys.exit(__doc__)
    import cf_flux
    cfg = cf_common.load_config()
    LOC = cfg["local"]
    kit = json.load(open(a.kit, encoding="utf-8-sig"))
    plan = json.loads(a.plan) if a.plan else json.load(open(a.plan_file, encoding="utf-8-sig"))
    out_dir = os.path.join(LOC["outputs_dir"], "_speed_tests")
    stamp = os.path.splitext(os.path.basename(a.out))[0]
    jobs = []
    for i, j in enumerate(plan):
        job = {**DEFAULT_JOB, **j}
        row = next((r for r in kit["rows"] if r.get("row_id", r["name"]) == job["row"]), None)
        if row is None:
            sys.exit(f"row not in kit: {job['row']}")
        prompt, seed = cf_flux.row_prompt_seed(kit, row, cfg["defaults"]["seed"])
        W, H = row.get("w", kit.get("w", cfg["defaults"]["w"])), row.get("h", kit.get("h", cfg["defaults"]["h"]))
        ref = row.get("ref", kit.get("ref"))
        use_ref = (bool(ref) and row.get("use_ref", True)) if job["use_ref"] is None else (bool(job["use_ref"]) and bool(ref))
        if not use_ref and (job["use_ref"] is not None) and row.get("use_ref", True) and ref:   # a no-reference job drops the reference prefix too
            prompt = " ".join([row["prompt"], kit["subject"] if row.get("use_subject", True) else "", kit["style"]]).strip()
        job.update(index=i, prompt=prompt, seed=seed, W=W, H=H, ref=ref if use_ref else None,
                   png=os.path.join(out_dir, f"{stamp}_{i:02d}_{job.get('tag', 'job')}_{job['row']}_{W}x{H}.png"))
        job.setdefault("tag", "job")
        jobs.append(job)
    if os.environ.get("CF_DRY"):
        print(json.dumps({"kit": kit["kit"], "out": a.out, "jobs": [{k: j[k] for k in ("index", "row", "tag", "W", "H", "ref", "vae_tiling", "tunable", "nan_diag", "out_list", "skip_gc", "ref_cache", "png")} for j in jobs]}, indent=1))
        return
    os.makedirs(out_dir, exist_ok=True)
    sys.path.insert(0, LOC["flux_pkgs_dir"])
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    import numpy as np
    import torch
    from diffusers import Flux3DistilledAutoBlocks
    import flux3_ref_cache
    cf_common.wait_for_free_ram(cfg, "flux multi probe")
    t_proc = time.time()
    fm = cfg["models"]["flux"]
    blocks = Flux3DistilledAutoBlocks()
    blocks.sub_blocks.pop("prompt_upsampler")
    pipe = blocks.init_pipeline(LOC["flux_model_dir"])
    pipe.load_components(names=["text_encoder", "tokenizer", "transformer", "vae"], torch_dtype=getattr(torch, fm["dtype"]))
    pipe.to("cuda")
    cfg_tiling, cfg_slicing = bool(fm.get("vae_tiling")), bool(fm.get("vae_slicing"))
    (pipe.vae.enable_slicing if cfg_slicing else pipe.vae.disable_slicing)()
    torch.cuda.synchronize()
    proc = {"load_s": round(time.time() - t_proc, 1), "torch": torch.__version__, "hip": getattr(torch.version, "hip", None), "device": torch.cuda.get_device_name(0),
            "config_vae_tiling": cfg_tiling, "config_vae_slicing": cfg_slicing}
    faulthandler.dump_traceback_later(a.timeout_s, exit=True)   # starts after the load: the .ps1 kill covers a hung load
    stamps, dec_t = [], []
    orig_step = pipe.scheduler.step

    def timed_step(*x, **k):
        torch.cuda.synchronize()
        stamps.append(time.time())
        return orig_step(*x, **k)
    pipe.scheduler.step = timed_step
    orig_decode = pipe.vae.decode
    nan_flags = []

    def timed_decode(*x, **k):
        torch.cuda.synchronize()
        t = time.time()
        out = orig_decode(*x, **k)
        torch.cuda.synchronize()
        dec_t.append(time.time() - t)
        if nan_state["on"]:
            tt = out[0] if isinstance(out, (tuple, list)) else getattr(out, "sample", out)
            nan_flags.append(bool(torch.isnan(tt).any().item()))
        return out
    nan_state = {"on": False}
    pipe.vae.decode = timed_decode
    cache = None
    csv = os.path.splitext(a.out)[0] + "_tunableop.csv"
    tunable_note = None
    results, first_by_row = [], {}
    for job in jobs:
        res = {k: job[k] for k in ("index", "row", "tag", "W", "H", "ref_cache", "tunable", "nan_diag", "out_list", "skip_gc")}
        res.update(reference=job["ref"] is not None, vae_tiling=cfg_tiling if job["vae_tiling"] is None else bool(job["vae_tiling"]), png=job["png"], error=None)
        t0 = time.time()
        try:
            (pipe.vae.enable_tiling if res["vae_tiling"] else pipe.vae.disable_tiling)()
            if job["tunable"]:
                try:
                    t = torch.cuda.tunable
                    t.set_filename(csv, insert_device_ordinal=False)
                    if job["tunable"] == "reuse" and os.path.isfile(csv):
                        t.read_file(csv)   # load the tuned results from disk, so reuse is not only the in-memory table
                        res["tunableop_csv_read"] = True
                    t.enable(job["tunable"] != "off")
                    t.tuning_enable(job["tunable"] == "tune")
                    res["tunable_enabled"] = bool(t.is_enabled())
                except Exception as e:
                    tunable_note = "TunableOp API unavailable or failed: " + repr(e)[:300]
                    res["tunable_error"] = tunable_note
            if job["ref_cache"] and cache is None:
                cache = flux3_ref_cache.install(pipe.transformer)
            elif not job["ref_cache"] and cache is not None:
                flux3_ref_cache.uninstall(pipe.transformer)
                cache = None
            h0, m0 = (cache.hits, cache.misses) if cache else (0, 0)
            sch = pipe.scheduler
            for attr in ("_step_index", "_begin_index"):
                if hasattr(sch, attr):
                    setattr(sch, attr, None)
            if not job["skip_gc"]:
                gc.collect()
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
            gen = torch.Generator().manual_seed(job["seed"])
            extra = {}
            if job["ref"]:
                from PIL import Image
                extra["image"] = [Image.open(job["ref"]).convert("RGB")]
            try:
                c = int(pipe.num_channels_latents)
                f = int(pipe.vae_scale_factor) * 2
                extra["latents"] = torch.randn((1, c, job["H"] // f, job["W"] // f), generator=gen, dtype=torch.float32)
            except Exception:
                pass   # the pipeline draws the latents itself from the same generator
            torch.cuda.reset_peak_memory_stats()
            stamps.clear(); dec_t.clear(); nan_flags.clear()
            nan_state["on"] = bool(job["nan_diag"])
            t1 = time.time()
            with torch.no_grad():
                if job["nan_diag"]:
                    r = pipe(prompt=job["prompt"], width=job["W"], height=job["H"], generator=gen, output=["images", "latents"], **extra)
                    image = r["images"][0]
                    res["latents_nan"] = bool(torch.isnan(r["latents"]).any().item())
                    res["decoded_nan"] = any(nan_flags) if nan_flags else None
                elif job["out_list"]:
                    image = pipe(prompt=job["prompt"], width=job["W"], height=job["H"], generator=gen, output=["images"], **extra)["images"][0]
                else:
                    image = pipe(prompt=job["prompt"], width=job["W"], height=job["H"], generator=gen, output="images", **extra)[0]
            torch.cuda.synchronize()
            gen_s = time.time() - t1
            gaps = [y - x for x, y in zip([t1] + stamps[:-1], stamps)]
            mean = float(np.asarray(image.convert("RGB")).mean())
            res.update(gen_s=round(gen_s, 2), wall_s=round(time.time() - t0, 2), steps=len(stamps), decode_s=round(sum(dec_t), 2), mean_brightness=round(mean, 1),
                       peak_alloc_gb=round(torch.cuda.max_memory_allocated() / 2**30, 1), cache_hits=(cache.hits - h0) if cache else None, cache_misses=(cache.misses - m0) if cache else None)
            if gaps:
                rest = gaps[1:] or gaps
                res["first_step_s"], res["s_per_step_median"] = round(gaps[0], 2), round(statistics.median(rest), 3)
            if mean < 3:
                res["black"] = True
            else:
                image.save(job["png"])
                res["sha256"] = sha256_file(job["png"])
                if job["row"] in first_by_row:
                    res["diff_vs_first_of_row"] = {"vs_index": first_by_row[job["row"]][0], **pixel_diff(first_by_row[job["row"]][1], job["png"])}
                else:
                    first_by_row[job["row"]] = (job["index"], job["png"])
            if job["tunable"] == "tune":
                try:
                    torch.cuda.tunable.write_file()
                    res["tunableop_csv"] = csv
                    res["tunableop_csv_bytes"] = os.path.getsize(csv) if os.path.isfile(csv) else 0
                except Exception as e:
                    res["tunable_error"] = repr(e)[:300]
            del image
        except Exception as e:
            res["error"] = repr(e)[:500]
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
        results.append(res)
        print(f"RESULT {res['index']} {res['tag']} {res['row']} gen_s={res.get('gen_s')} s_per_step={res.get('s_per_step_median')} decode_s={res.get('decode_s')} "
              f"hits={res.get('cache_hits')} sha={str(res.get('sha256'))[:12]} err={res['error']}", flush=True)
        json.dump({"kit_name": kit["kit"], "kit": a.kit, "process": proc, "jobs": results, "tunable_note": tunable_note}, open(a.out, "w"), indent=1)   # after every job: a timeout keeps what ran
    faulthandler.cancel_dump_traceback_later()
    print(json.dumps({"process": proc, "jobs": len(results)}, indent=1))


if __name__ == "__main__":
    main()
