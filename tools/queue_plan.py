"""queue_plan.py: the decisions of bench/flux_queue.ps1, kept in Python so they can be tested without a GPU.

  python tools/queue_plan.py info   <kit.json>            JSON: kit name, rows in the KIT'S OWN ORDER, hold file, wait settings, re-roll cap
  python tools/queue_plan.py decide <kit.json> <row_id>   JSON: {"action": "render"|"skip"|"wait", "reason": ..., "wait_for": [...], "asset_id": ...}

Rules (all data comes from the kit and config/config.json `queue`, nothing about a character is written here):
  1 order        the kit's row order is the queue order (tools/make_kit.py emits identity cells first, 3D-feeding cells next, close-ups last).
  2 hold         a row id listed in the hold file is skipped. Path: config `queue.hold_file`, else <outputs_dir>/_queue/<kit>_hold.txt.
  3 mirror       a row with `mirror_of` is made by tools/mirror_cell.py, never rendered.
  4 png exists   a row whose png exists is done. A `swatch_of` row is a crop made by tools/swatch_crop.py: skipped only when that png exists,
                 otherwise it renders like any other row.
  5 re-roll cap  asset_id = sha256(prompt, effective seed, reference sha256). When `queue.reroll_cap` (2) successful flux receipts carry the same
                 asset_id the row is skipped. A changed prompt, seed or reference is a new asset_id and is allowed.
  6 reference    a row that is not an identity cell (kit row key `identity: true`) waits until manifests/<kit>.json rows[<identity>].verdict is
     first       PASS. Its identity cells are those of its own group; a group with none waits for all identity cells of the kit.
CPU only, standard library."""
import argparse, glob, hashlib, json, os, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = {"hold_file": "", "identity_wait_max_s": 3600, "identity_poll_s": 60, "reroll_cap": 2}


def read_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def load_config(repo=REPO):
    """(config.json, config.local.json); a missing local file reads as {}."""
    cfg = read_json(os.path.join(repo, "config", "config.json"))
    lp = os.path.join(repo, "config", "config.local.json")
    return cfg, (read_json(lp) if os.path.exists(lp) else {})


def queue_cfg(cfg):
    return {**DEFAULTS, **(cfg.get("queue") or {})}


def row_id(row):
    return row.get("row_id", row.get("name"))


def ordered_rows(kit):
    """The kit's own row order, unchanged."""
    return list(kit["rows"])


def hold_file(qcfg, local, kit_name):
    if qcfg.get("hold_file"):
        return qcfg["hold_file"]
    return os.path.join(local.get("outputs_dir", ""), "_queue", f"{kit_name}_hold.txt")


def read_hold(path):
    """Row ids held until their reference or prompt fix lands: one per line, blank lines and # comments ignored."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return {ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")}
    except OSError:
        return set()


def identity_ids(kit):
    """Row ids of the identity cells: rows with `identity: true`, in kit order."""
    return [row_id(r) for r in kit["rows"] if r.get("identity") is True]


def required_identity(kit, row):
    """The identity cells a row waits for: [] for an identity cell itself; its own group's identity cells; with none in its group, all of them."""
    ids = identity_ids(kit)
    rid = row_id(row)
    if rid in ids:
        return []
    group = {row_id(r): r.get("group") for r in kit["rows"]}
    same = [i for i in ids if group.get(i) == row.get("group")]
    return same or ids


def verdict(repo, kit_name, rid):
    """rows[rid].verdict of manifests/<kit>.json upper-cased ('' when there is none)."""
    try:
        m = read_json(os.path.join(repo, "manifests", f"{kit_name}.json"))
        return str(m["rows"][rid].get("verdict") or "").upper()
    except Exception:
        return ""


def effective_seed(kit, row, default=42):
    """The seed flux_kit.py uses: the row's own seed (a documented re-roll), else the kit's."""
    return row.get("seed", kit.get("seed", default))


def effective_prompt(kit, row):
    """The prompt exactly as flux_kit.py builds it."""
    prompt = " ".join([row["prompt"], kit["subject"] if row.get("use_subject", True) else "", kit["style"]]).strip()
    if row_ref(kit, row):
        prompt = row.get("ref_prefix", kit["ref_prefix"]) + " " + prompt
    return prompt


def row_ref(kit, row):
    ref = row.get("ref", kit.get("ref"))
    return ref if ref and row.get("use_ref", True) else None


def file_sha(path):
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def asset_key(prompt, seed, ref_sha):
    """The asset_id of a render: same prompt + seed + reference bytes, same id."""
    return hashlib.sha256(f"{prompt}\n{seed}\n{ref_sha or ''}".encode("utf-8")).hexdigest()[:16]


def count_ok_renders(repo, kit_name, rid, key):
    """Successful flux receipts of this row whose prompt, seed and reference sha give the asset id `key`."""
    n = 0
    for p in glob.glob(os.path.join(glob.escape(repo), "receipts", glob.escape(f"{kit_name}-{rid}-") + "*", "flux-*.json")):
        try:
            r = read_json(p)
        except Exception:
            continue
        pr = r.get("params") or {}
        if r.get("status") == "ok" and pr.get("row_id", rid) == rid and asset_key(pr.get("prompt"), pr.get("seed"), pr.get("ref_sha256")) == key:
            n += 1
    return n


def png_exists(local, kit, row):
    pat = os.path.join(glob.escape(os.path.join(local.get("outputs_dir", ""), kit["kit"])), glob.escape(row["name"]) + "_*.png")
    return bool(glob.glob(pat))


def decide(kit, row, repo=REPO, cfg=None, local=None):
    if cfg is None or local is None:
        cfg, local = load_config(repo)
    q = queue_cfg(cfg)
    rid = row_id(row)
    out = {"row_id": rid, "action": "render", "reason": "", "wait_for": [], "asset_id": ""}
    if rid in read_hold(hold_file(q, local, kit["kit"])):
        return {**out, "action": "skip", "reason": "hold file"}
    if row.get("mirror_of"):
        return {**out, "action": "skip", "reason": f"mirror of {row['mirror_of']} (tools/mirror_cell.py)"}
    if png_exists(local, kit, row):
        return {**out, "action": "skip", "reason": "png exists"}
    seed = effective_seed(kit, row, (cfg.get("defaults") or {}).get("seed", 42))
    key = asset_key(effective_prompt(kit, row), seed, file_sha(row_ref(kit, row)))
    out["asset_id"] = key
    n = count_ok_renders(repo, kit["kit"], rid, key)
    if n >= int(q["reroll_cap"]):
        return {**out, "action": "skip", "reason": f"REROLL_CAP asset_id {key} rendered ok {n} times; change the prompt, the seed or the reference for another roll"}
    pending = [i for i in required_identity(kit, row) if verdict(repo, kit["kit"], i) != "PASS"]
    if pending:
        now = ", ".join(f"{i}={verdict(repo, kit['kit'], i) or 'none'}" for i in pending)
        return {**out, "action": "wait", "wait_for": pending, "reason": f"reference first: identity cell not PASS yet ({now})"}
    return out


def info(kit, repo=REPO):
    cfg, local = load_config(repo)
    q = queue_cfg(cfg)
    return {"kit": kit["kit"], "rows": [{"row_id": row_id(r), "name": r["name"]} for r in ordered_rows(kit)],
            "hold_file": hold_file(q, local, kit["kit"]), "identity_wait_max_s": q["identity_wait_max_s"],
            "identity_poll_s": q["identity_poll_s"], "reroll_cap": q["reroll_cap"], "identity": identity_ids(kit)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["info", "decide"])
    ap.add_argument("kit")
    ap.add_argument("row_id", nargs="?")
    ap.add_argument("--repo", default=REPO)
    a = ap.parse_args(argv)
    kit = read_json(a.kit)
    if a.cmd == "info":
        print(json.dumps(info(kit, a.repo)))
        return 0
    row = next((r for r in kit["rows"] if row_id(r) == a.row_id), None)
    if row is None:
        print(json.dumps({"row_id": a.row_id, "action": "skip", "reason": "no such row in the kit", "wait_for": [], "asset_id": ""}))
        return 2
    print(json.dumps(decide(kit, row, a.repo)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
