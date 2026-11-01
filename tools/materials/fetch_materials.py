"""Download the scanned CC0 PBR sets named in config/materials.json (Poly Haven, ambientCG), one file at a time, and write a manifest.json per material.
Usage:
  fetch_materials.py --list                      print the exact download list with sizes and checksums; downloads nothing
  fetch_materials.py [--only <asset_id> ...]     download (needs --approved, see below)
Safety: a download only starts with --approved, which stands for the owner's yes to THIS list (the list is printed by --list and recorded in the PR).
Rules: IPv4 only (curl --ipv4), one file at a time, resume (curl -C -, a half file is a .part and never counts), every file is verified against the published checksum
(Poly Haven: md5 and size from api.polyhaven.com/files; ambientCG publishes no checksum, so size from its API, zip CRC test and a recorded sha256), a mismatch deletes
the .part and fails the material, an already verified file is not downloaded again. A hard cap (--max-total-mb, default 1024) refuses a list over the cap.
Layout: <materials_dir>/<asset_id>/<files> + manifest.json (name, source, licence, url, resolution, map conventions, sha256 per file). materials_dir comes from
config.local.json (materials_dir) and defaults to <work_dir>/materials. No GPU, no model; standard library plus curl.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
MAPPING = os.path.join(REPO, "config", "materials.json")
UA = "content-factory-materials/1 (CC0 PBR fetch, one file at a time)"


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def materials_dir(local: dict | None = None) -> str:
    if local is None:
        p = os.path.join(REPO, "config", "config.local.json")
        local = read_json(p) if os.path.exists(p) else {}
    return local.get("materials_dir") or os.path.join(local.get("work_dir", os.path.expanduser("~/Documents")), "materials")


def digest(path: str, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def items(mapping: dict, only: list[str] | None = None) -> list[dict]:
    """The flat download list: one row per file (Poly Haven maps, ambientCG zip), in a stable order."""
    rows = []
    for aid, m in mapping["materials"].items():
        if only and aid not in only:
            continue
        for f in m.get("files", []):
            rows.append({"asset": aid, "source": m["source"], "name": f["name"], "url": f["url"], "bytes": f["bytes"], "md5": f.get("md5"), "map": f["map"], "zip": False})
        if "zip" in m:
            z = m["zip"]
            rows.append({"asset": aid, "source": m["source"], "name": z["name"], "url": z["url"], "bytes": z["bytes"], "md5": z.get("md5"), "map": "zip", "zip": True})
    return rows


def total_bytes(rows: list[dict]) -> int:
    return sum(r["bytes"] for r in rows)


def verify(path: str, row: dict) -> str:
    """'' when the file matches its published size and checksum, else why not."""
    if not os.path.isfile(path):
        return "missing"
    size = os.path.getsize(path)
    if size != row["bytes"]:
        return f"size {size} != published {row['bytes']}"
    if row.get("md5") and digest(path, "md5") != row["md5"]:
        return "md5 differs from the published md5"
    if row["zip"]:
        try:
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
            if bad:
                return f"zip member {bad} fails its CRC"
        except zipfile.BadZipFile:
            return "not a valid zip"
    return ""


def curl_cmd(curl: str, url: str, part: str) -> list[str]:
    return [curl, "--ipv4", "--location", "--fail", "--silent", "--show-error", "-C", "-", "--retry", "3", "--retry-delay", "5", "--connect-timeout", "30",
            "--user-agent", UA, "-o", part, url]


def fetch_one(row: dict, folder: str, curl: str) -> str:
    """Download (or resume) one file into folder, verify it, return its final path. Raises RuntimeError on a bad file (the .part is removed)."""
    final = os.path.join(folder, row["name"])
    if not verify(final, row):
        return final
    part = final + ".part"
    if os.path.exists(part) and os.path.getsize(part) > row["bytes"]:
        os.remove(part)   # larger than published: not a resumable prefix
    r = subprocess.run(curl_cmd(curl, row["url"], part), capture_output=True, text=True)
    if r.returncode == 33 and os.path.exists(part) and os.path.getsize(part) == row["bytes"]:
        r = subprocess.CompletedProcess(r.args, 0)   # curl: range not satisfiable because the .part was already complete
    if r.returncode != 0:
        raise RuntimeError(f"curl exit {r.returncode} for {row['name']}: {r.stderr.strip()[-300:]} (the .part is kept for a resume)")
    os.replace(part, final)
    why = verify(final, row)
    if why:
        os.remove(final)
        raise RuntimeError(f"{row['name']}: {why}; file removed")
    return final


def build_manifest(aid: str, m: dict, folder: str, rows: list[dict], res: str, extracted: dict[str, str]) -> dict:
    files = []
    for r in rows:
        p = os.path.join(folder, r["name"])
        files.append({"name": r["name"], "map": r["map"], "url": r["url"], "bytes": os.path.getsize(p), "md5_published": r.get("md5"), "md5_verified": bool(r.get("md5")),
                      "sha256": digest(p, "sha256")})
    for mp, name in extracted.items():
        p = os.path.join(folder, name)
        files.append({"name": name, "map": mp, "extracted_from": rows[0]["name"], "bytes": os.path.getsize(p), "sha256": digest(p, "sha256")})
    maps = dict(extracted) if extracted else {f["map"]: f["name"] for f in files}
    return {"schema": 1, "name": aid, "source": m["source"], "licence": m["licence"], "url": m["url"], "authors": m.get("authors"), "resolution": res, "format": "jpg",
            "map_conventions": {"normal": m["normal_convention"] + " (OpenGL, +Y up; Blender Normal Map node default)", "roughness": "linear, non-colour", "height": "linear, non-colour, 0.5 = flat",
                                "ao": "linear, non-colour", "metalness": "linear, non-colour" if m.get("metalness") else "none (non-metal)", "base_color": "sRGB"},
            "maps": maps, "tile_m": m["tile_m"], "tile_m_basis": m["tile_m_basis"], "files": files,
            "fetched_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}


def extract_maps(m: dict, folder: str, zip_row: dict) -> dict[str, str]:
    out = {}
    with zipfile.ZipFile(os.path.join(folder, zip_row["name"])) as z:
        names = set(z.namelist())
        for mp, member in m["zip_members"].items():
            if member not in names:
                raise RuntimeError(f"{member} is not in {zip_row['name']} (members: {sorted(names)})")
            target = os.path.join(folder, member)
            with z.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            out[mp] = member
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true", help="print the download list and exit")
    ap.add_argument("--approved", action="store_true", help="the owner approved this list: required to download")
    ap.add_argument("--only", nargs="*", help="asset ids to fetch (default all)")
    ap.add_argument("--dest", help="materials dir (default config.local materials_dir)")
    ap.add_argument("--max-total-mb", type=float, default=1024.0)
    ap.add_argument("--curl", default=shutil.which("curl.exe") or shutil.which("curl") or "curl")
    a = ap.parse_args(argv)
    mapping = read_json(MAPPING)
    rows = items(mapping, a.only)
    if not rows:
        print("nothing matches --only", file=sys.stderr)
        return 2
    total = total_bytes(rows)
    print(f"{len(rows)} files, {total / 1e6:.1f} MB (cap {a.max_total_mb:.0f} MB)")
    for r in rows:
        print(f"  {r['asset']:28s} {r['map']:10s} {r['bytes']:>10d} B  {r['md5'] or 'no published md5 (size + zip CRC + sha256)'}  {r['url']}")
    if a.list:
        return 0
    if total > a.max_total_mb * 1e6:
        print(f"refusing: {total / 1e6:.1f} MB is over the {a.max_total_mb:.0f} MB cap; tell the owner first", file=sys.stderr)
        return 2
    if not a.approved:
        print("refusing: no download without --approved (the owner's yes to this list)", file=sys.stderr)
        return 2
    dest = a.dest or materials_dir()
    os.makedirs(dest, exist_ok=True)
    failed = []
    for aid, m in mapping["materials"].items():
        mine = [r for r in rows if r["asset"] == aid]
        if not mine:
            continue
        folder = os.path.join(dest, aid)
        os.makedirs(folder, exist_ok=True)
        try:
            for r in mine:   # strictly one file at a time
                print(f"fetch {aid}/{r['name']} ({r['bytes']} B)", flush=True)
                fetch_one(r, folder, a.curl)
            extracted = extract_maps(m, folder, mine[0]) if m["source"] == "ambientcg" else {}
            man = build_manifest(aid, m, folder, mine, mapping["resolution"], extracted)
            with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
                json.dump(man, f, indent=2)
            print(f"ok {aid}: {len(man['files'])} files, manifest written")
        except Exception as e:   # one material failing does not stop the others
            failed.append(aid)
            print(f"FAIL {aid}: {e}", file=sys.stderr)
    print("done" if not failed else f"failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
