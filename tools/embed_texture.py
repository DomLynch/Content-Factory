"""Embed textures into a GLB by editing the container directly (no Blender, no re-export).
Usage: embed_texture.py <in.glb> <basecolor.png> <out.glb> [--normal normal.png] [--mr mr.png]
Replaces the image bytes behind the material's base colour (and normal / metallic-roughness when given; adds the
texture slot if missing). The BIN chunk is rebuilt: every bufferView keeps its original bytes (4-byte aligned) except
the swapped images, so skins, joints, weights, animations and geometry stay byte-identical. The input is never modified.
Needs only the standard library."""
import argparse, json, os, struct, sys

MAGIC, JSON_T, BIN_T = 0x46546C67, 0x4E4F534A, 0x004E4942


def read_glb(path):
    d = open(path, "rb").read()
    magic, ver, total = struct.unpack_from("<III", d, 0)
    if magic != MAGIC or ver != 2 or total != len(d):
        raise ValueError(f"not a valid GLB 2.0 (magic={magic:#x} ver={ver} len={total}/{len(d)})")
    off, js, binb, extra = 12, None, None, []
    while off < len(d):
        ln, ct = struct.unpack_from("<II", d, off)
        body = d[off + 8: off + 8 + ln]
        if ct == JSON_T and js is None: js = json.loads(body.decode("utf-8"))
        elif ct == BIN_T and binb is None: binb = body
        else: extra.append((ct, body))
        off += 8 + ln
    if js is None: raise ValueError("no JSON chunk")
    return js, (binb or b""), extra


def pad(b, fill=b"\x00"):
    return b + fill * (-len(b) % 4)


def mime(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if data[:3] == b"\xff\xd8\xff": return "image/jpeg"
    raise ValueError("image must be PNG or JPEG")


def image_for_slot(js, mat, slot):
    """Return the images[] index used by the slot, or None."""
    if slot == "base": ti = (mat.get("pbrMetallicRoughness", {}).get("baseColorTexture") or {}).get("index")
    elif slot == "mr": ti = (mat.get("pbrMetallicRoughness", {}).get("metallicRoughnessTexture") or {}).get("index")
    else: ti = (mat.get("normalTexture") or {}).get("index")
    if ti is None: return None
    return js["textures"][ti].get("source")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("glb"); ap.add_argument("basecolor"); ap.add_argument("out")
    ap.add_argument("--normal"); ap.add_argument("--mr")
    a = ap.parse_args()
    if os.path.abspath(a.glb) == os.path.abspath(a.out): sys.exit("refusing to overwrite the input")
    js, binb, extra = read_glb(a.glb)
    if not js.get("materials"): sys.exit("GLB has no material")
    mat = js["materials"][0]
    pbr = mat.setdefault("pbrMetallicRoughness", {})
    new = {"base": a.basecolor, "mr": a.mr, "normal": a.normal}
    new = {k: open(v, "rb").read() for k, v in new.items() if v}
    js.setdefault("images", []); js.setdefault("textures", []); js.setdefault("bufferViews", [])
    # does any other texture/material share an image we are about to replace? then give the slot its own image
    repl = {}  # image index -> new bytes
    for slot, data in new.items():
        idx = image_for_slot(js, mat, slot)
        shared = idx is not None and any(idx == image_for_slot(js, m, s) for m in js["materials"] if m is not mat for s in ("base", "mr", "normal"))
        shared = shared or (idx is not None and idx in [image_for_slot(js, mat, s) for s in ("base", "mr", "normal") if s != slot])
        if idx is None or shared:
            js["images"].append({"mimeType": mime(data)}); idx = len(js["images"]) - 1
            js["textures"].append({"source": idx}); ti = len(js["textures"]) - 1
            if slot == "base": pbr["baseColorTexture"] = {"index": ti}
            elif slot == "mr": pbr["metallicRoughnessTexture"] = {"index": ti}
            else: mat["normalTexture"] = {"index": ti}
            print(f"{slot}: added new image/texture #{idx}")
        else:
            print(f"{slot}: replacing image #{idx}")
        repl[idx] = data
    # rebuild BIN: copy every bufferView's bytes in order, swapping image views
    bvs = js["bufferViews"]
    out_bin = bytearray()
    old_bin = binb
    # bytes of the old BIN not covered by any bufferView would be dropped; none expected
    img_bv = {}
    for i, im in enumerate(js["images"]):
        if "bufferView" in im: img_bv[im["bufferView"]] = i
        elif i in repl: pass
        elif "uri" in im and not im["uri"].startswith("data:"): pass
    order = sorted(range(len(bvs)), key=lambda i: bvs[i].get("byteOffset", 0))
    for i in order:
        bv = bvs[i]
        i_img = img_bv.get(i)
        if i_img is not None and i_img in repl:
            data = repl[i_img]; js["images"][i_img]["mimeType"] = mime(data); repl.pop(i_img)
        else:
            o = bv.get("byteOffset", 0); data = old_bin[o:o + bv["byteLength"]]
        out_bin += b"\x00" * (-len(out_bin) % 4)
        bv["byteOffset"] = len(out_bin); bv["byteLength"] = len(data)
        out_bin += data
    # images that had no bufferView (new or uri): append new views
    for i_img, data in repl.items():
        out_bin += b"\x00" * (-len(out_bin) % 4)
        bvs.append({"buffer": 0, "byteOffset": len(out_bin), "byteLength": len(data)})
        im = js["images"][i_img]; im.pop("uri", None); im["bufferView"] = len(bvs) - 1; im["mimeType"] = mime(data)
        out_bin += data
    out_bin += b"\x00" * (-len(out_bin) % 4)
    js.setdefault("buffers", [{}])
    js["buffers"][0]["byteLength"] = len(out_bin); js["buffers"][0].pop("uri", None)
    jb = pad(json.dumps(js, separators=(",", ":")).encode("utf-8"), b" ")
    chunks = struct.pack("<II", len(jb), JSON_T) + jb + struct.pack("<II", len(out_bin), BIN_T) + bytes(out_bin)
    for ct, body in extra:
        b = pad(body); chunks += struct.pack("<II", len(b), ct) + b
    glb = struct.pack("<III", MAGIC, 2, 12 + len(chunks)) + chunks
    with open(a.out, "wb") as f: f.write(glb)
    verify(a.glb, a.out)


def verify(src, dst):
    js0, b0, _ = read_glb(src); js1, b1, _ = read_glb(dst)
    errs = []
    if len(js1["buffers"]) != 1 or js1["buffers"][0]["byteLength"] != len(b1): errs.append("buffer length mismatch")
    for i, bv in enumerate(js1["bufferViews"]):
        if bv.get("byteOffset", 0) + bv["byteLength"] > len(b1) or bv.get("byteOffset", 0) % 4: errs.append(f"bufferView {i} bad range/alignment")
    for i, ac in enumerate(js1.get("accessors", [])):
        if "bufferView" in ac and not (0 <= ac["bufferView"] < len(js1["bufferViews"])): errs.append(f"accessor {i} bad bufferView")
    # every non-image bufferView must be byte-identical
    img0 = {im["bufferView"] for im in js0.get("images", []) if "bufferView" in im}
    for i, bv in enumerate(js0["bufferViews"]):
        if i in img0 or i >= len(js1["bufferViews"]): continue
        n = js1["bufferViews"][i]
        if b0[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]] != b1[n["byteOffset"]:n["byteOffset"] + n["byteLength"]]: errs.append(f"bufferView {i} bytes changed")
    skip = {"images", "textures", "bufferViews", "buffers", "materials"}
    for k in set(js0) | set(js1):
        if k not in skip and js0.get(k) != js1.get(k): errs.append(f"JSON key '{k}' differs")
    sk0 = [(len(s.get("joints", [])), s.get("inverseBindMatrices")) for s in js0.get("skins", [])]
    sk1 = [(len(s.get("joints", [])), s.get("inverseBindMatrices")) for s in js1.get("skins", [])]
    print(f"skins {len(sk0)}->{len(sk1)}, joint counts {[s[0] for s in sk0]}->{[s[0] for s in sk1]}, animations {len(js0.get('animations', []))}->{len(js1.get('animations', []))}")
    if sk0 != sk1: errs.append("skin/joint mismatch")
    print(f"bufferViews {len(js0['bufferViews'])}->{len(js1['bufferViews'])}, accessors {len(js0.get('accessors', []))}->{len(js1.get('accessors', []))}, BIN {len(b0)}->{len(b1)} bytes")
    try:
        import trimesh
        s = trimesh.load(dst); print("trimesh loads:", {k: len(v.vertices) for k, v in s.geometry.items()})
    except ImportError: print("trimesh not available")
    except Exception as e: errs.append(f"trimesh load failed: {e}")
    if errs: sys.exit("VERIFY FAILED: " + "; ".join(errs))
    print("verify ok:", dst)


if __name__ == "__main__":
    main()
