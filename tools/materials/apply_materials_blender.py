"""Scanned-material lit stills of ONE gear-part GLB, inside headless Blender (plugin 0.1.17; Cycles on CPU, no GPU).
  blender.exe --background --python tools/materials/apply_materials_blender.py -- --config <render_config.json>
The config is the A/B turntable config (tools/run_ab.py / tools/run_materials.py build it from config/config.json ab.render) plus a "materials" block. This script does NOT build a
light rig of its own: it executes tools/ab/turntable_blender.py (cfg "turntable_script") and gives it a post-import hook, so the camera, the three-point rig (key, fill, rim), the
neutral world, the 1.8 m normalisation, resolution and samples are the SAME code and the SAME numbers as the baked-texture render of the same GLB.
The hook replaces the part's baked material with a Principled BSDF built from the scanned maps:
  - base colour (sRGB) x optional tint x AO (if the set has one); roughness, height and metalness are Non-Color data;
  - normal map: cfg normal_convention "gl" is used as is (Blender's Normal Map node is OpenGL, +Y up); "dx" gets its green channel inverted;
  - height drives a small Bump node on top of the normal map (strength cfg bump_strength);
  - fabrics get a sheen lobe (cfg sheen_weight) so velvet and pile read at grazing angles.
UV tiling assumption (documented, tested on the first proof): the TRELLIS atlas UVs have no common scale, so they cannot tile a scan. The hook writes a second UV layer
`cf_mat_uv` by box projection: every face is projected along its dominant normal axis in METRES, u,v = world coordinate / tile_m, so one texture repeat always covers the
scan's real sample size (tile_m, from the mapping) wherever the part sits. Metres come from the part's assumed real height: the part is `part_height_m` tall in a 1.8 m character
(coat 1.05 m, cloak 1.45 m, boots 0.55 m, config/materials.json parts), so metres per Blender unit = part_height_m / native height of the imported part. The turntable then scales the
part to 1.8 m like the baked render; the texture scale is unaffected because the UVs are already in tile units. Seams between projection planes are not blended.
Writes materials_applied.json next to the frames (material, tile_m, metres per unit, UV span against mesh span, maps used)."""
import json
import math
import os
import sys

import bpy
import bmesh


def cli_cfg():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return json.load(open(argv[argv.index("--config") + 1], encoding="utf-8"))


def load_image(path, srgb):
    img = bpy.data.images.load(path, check_existing=True)
    img.colorspace_settings.name = "sRGB" if srgb else "Non-Color"
    return img


def build_material(m):
    mat = bpy.data.materials.new("scan_" + m["name"])
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "cf_mat_uv"

    def tex(key, srgb):
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = load_image(m["maps"][key], srgb)
        n.interpolation = "Linear"
        n.extension = "REPEAT"
        nt.links.new(uv.outputs["UV"], n.inputs["Vector"])
        return n

    col = tex("base_color", True).outputs["Color"]
    tint = m.get("tint")
    if tint:   # multiply: a beige pile becomes black fur, a silver steel becomes dark steel; recorded in materials_applied.json
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.inputs[0].default_value = 1.0
        nt.links.new(col, mix.inputs[6])
        mix.inputs[7].default_value = (*tint, 1.0)
        col = mix.outputs[2]
    if "ao" in m["maps"]:
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.inputs[0].default_value = 1.0
        nt.links.new(col, mix.inputs[6])
        nt.links.new(tex("ao", False).outputs["Color"], mix.inputs[7])
        col = mix.outputs[2]
    nt.links.new(col, bsdf.inputs["Base Color"])
    nt.links.new(tex("roughness", False).outputs["Color"], bsdf.inputs["Roughness"])
    if "metalness" in m["maps"]:
        nt.links.new(tex("metalness", False).outputs["Color"], bsdf.inputs["Metallic"])
    nrm = nt.nodes.new("ShaderNodeNormalMap")
    nrm.space = "TANGENT"
    nrm.uv_map = "cf_mat_uv"
    nrm.inputs["Strength"].default_value = m.get("normal_strength", 1.0)
    ncol = tex("normal", False).outputs["Color"]
    if m["normal_convention"] == "dx":
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        inv = nt.nodes.new("ShaderNodeMath")
        inv.operation = "SUBTRACT"
        inv.inputs[0].default_value = 1.0
        comb = nt.nodes.new("ShaderNodeCombineColor")
        nt.links.new(ncol, sep.inputs["Color"])
        nt.links.new(sep.outputs["Green"], inv.inputs[1])
        nt.links.new(sep.outputs["Red"], comb.inputs["Red"])
        nt.links.new(inv.outputs["Value"], comb.inputs["Green"])
        nt.links.new(sep.outputs["Blue"], comb.inputs["Blue"])
        ncol = comb.outputs["Color"]
    nt.links.new(ncol, nrm.inputs["Color"])
    last = nrm.outputs["Normal"]
    if "height" in m["maps"]:
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = m.get("bump_strength", 0.15)
        bump.inputs["Distance"].default_value = m.get("bump_distance_m", 0.002)
        nt.links.new(tex("height", False).outputs["Color"], bump.inputs["Height"])
        nt.links.new(last, bump.inputs["Normal"])
        last = bump.outputs["Normal"]
    nt.links.new(last, bsdf.inputs["Normal"])
    if m.get("kind") == "fabric":
        for k, v in (("Sheen Weight", m.get("sheen_weight", 0.6)), ("Sheen Roughness", 0.4)):
            if k in bsdf.inputs:
                bsdf.inputs[k].default_value = v
    return mat


def box_uv(obj, mpu, tile_m):
    """Write the cf_mat_uv layer: per-face box projection in metres / tile_m. Returns (uv_span, mesh_span_m) as 2-lists for the log."""
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    layer = bm.loops.layers.uv.get("cf_mat_uv") or bm.loops.layers.uv.new("cf_mat_uv")
    M = obj.matrix_world
    N = M.to_3x3().inverted_safe().transposed()
    lo = [1e18] * 3
    hi = [-1e18] * 3
    umin, umax, vmin, vmax = 1e18, -1e18, 1e18, -1e18
    for f in bm.faces:
        n = N @ f.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        for loop in f.loops:
            w = M @ loop.vert.co
            c = (w.x * mpu / tile_m, w.y * mpu / tile_m, w.z * mpu / tile_m)
            u, v = ((c[1], c[2]), (c[0], c[2]), (c[0], c[1]))[ax]
            loop[layer].uv = (u, v)
            umin, umax, vmin, vmax = min(umin, u), max(umax, u), min(vmin, v), max(vmax, v)
            for i in range(3):
                lo[i], hi[i] = min(lo[i], w[i]), max(hi[i], w[i])
    bm.to_mesh(me)
    bm.free()
    me.update()
    return [round(umax - umin, 3), round(vmax - vmin, 3)], [round((hi[i] - lo[i]) * mpu, 3) for i in range(3)]


def main():
    cfg = cli_cfg()
    m = cfg["materials"]
    tt = cfg["turntable_script"]
    ns = {"__name__": "cf_turntable", "__file__": tt, "CF_NO_AUTORUN": True}
    exec(compile(open(tt, encoding="utf-8").read(), tt, "exec"), ns)
    report = {"material": m["name"], "tile_m": m["tile_m"], "part_height_m": m["part_height_m"], "tint": m.get("tint"), "normal_convention": m["normal_convention"],
              "maps": {k: os.path.basename(v) for k, v in m["maps"].items()}}

    def hook(objs, lo, hi, c):
        native_h = max(hi.z - lo.z, 1e-6)
        mpu = m["part_height_m"] / native_h
        mat = build_material(m)
        report["metres_per_unit"] = mpu
        report["native_height_units"] = native_h
        report["meshes"] = []
        for o in objs:
            span_uv, span_m = box_uv(o, mpu, m["tile_m"])
            o.data.materials.clear()
            o.data.materials.append(mat)
            for p in o.data.polygons:
                p.material_index = 0
            report["meshes"].append({"name": o.name, "uv_span_tiles": span_uv, "mesh_span_m": span_m,
                                     "expected_tiles_from_metres": [round(s / m["tile_m"], 3) for s in span_m]})

    ns["main"](post_import=hook)
    with open(os.path.join(cfg["out_dir"], "materials_applied.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


main()
