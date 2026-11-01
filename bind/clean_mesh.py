"""clean_mesh.py - Blender headless: clean a TRELLIS mesh before rig binding (fallback if a bind attempt still fails).
  blender --background --python clean_mesh.py -- in.glb out.glb --merge-dist M --min-shell-verts N --count-weld M
        [--voxel S --tex-size PX --bake-margin PX --bake-extrude M --report report.json]
Steps: join meshes, merge vertices by distance, delete loose shells under N vertices, recalc normals outward,
optionally voxel remesh (S > 0 metres) and re-bake the base colour from the original by nearest surface (Cycles CPU bake, 1 sample).
Counts (loose shells, non-manifold edges, vertices) are measured before and after on a position-welded copy (--count-weld) so UV seams do not inflate shells."""
import bpy, bmesh, sys, os, json

a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
pos, opt, i = [], {}, 0
while i < len(a):
    if a[i].startswith("--"):
        opt[a[i]] = a[i + 1]; i += 2
    else:
        pos.append(a[i]); i += 1
if len(pos) != 2:
    sys.exit(__doc__)
src, dst = [os.path.abspath(p) for p in pos]
MERGE = float(opt["--merge-dist"]); MINV = int(opt["--min-shell-verts"]); WELD = float(opt["--count-weld"])
VOXEL = float(opt.get("--voxel", 0)); TEX = int(opt.get("--tex-size", 2048)); MARGIN = int(opt.get("--bake-margin", 8))
EXTRUDE = float(opt.get("--bake-extrude", 0.01)); report = opt.get("--report")


def shells(bm):
    seen, out = set(), []
    for v in bm.verts:
        if v.index in seen:
            continue
        comp, stack = [], [v]; seen.add(v.index)
        while stack:
            c = stack.pop(); comp.append(c)
            for e in c.link_edges:
                o = e.other_vert(c)
                if o.index not in seen:
                    seen.add(o.index); stack.append(o)
        out.append(comp)
    return out


def counts(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=WELD)
    bm.verts.ensure_lookup_table()
    c = {"vertices": len(bm.verts), "loose_shells": len(shells(bm)), "non_manifold_edges": sum(1 for e in bm.edges if not e.is_manifold)}
    bm.free(); return c


bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
if not meshes:
    sys.exit("no mesh in " + src)
bpy.ops.object.select_all(action="DESELECT")
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
rep = {"input": src, "output": dst, "merge_dist": MERGE, "min_shell_verts": MINV, "voxel": VOXEL, "before": counts(obj)}

# merge by distance, delete small shells, outward normals
bm = bmesh.new(); bm.from_mesh(obj.data)
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=MERGE)
bm.verts.ensure_lookup_table()
dead = [v for comp in shells(bm) if len(comp) < MINV for v in comp]
rep["deleted_shell_verts"] = len(dead)
if dead:
    bmesh.ops.delete(bm, geom=dead, context="VERTS")
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.to_mesh(obj.data); bm.free(); obj.data.update()

if VOXEL > 0:
    orig = obj
    orig.name = "orig"
    new = orig.copy(); new.data = orig.data.copy(); new.name = "clean"
    bpy.context.collection.objects.link(new)
    while new.data.uv_layers:
        new.data.uv_layers.remove(new.data.uv_layers[0])
    new.data.remesh_voxel_size = VOXEL
    new.data.remesh_voxel_adaptivity = 0.0
    bpy.ops.object.select_all(action="DESELECT"); new.select_set(True); bpy.context.view_layer.objects.active = new
    bpy.ops.object.voxel_remesh()
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.003)
    bpy.ops.object.mode_set(mode="OBJECT")
    # target material with an empty image; bake the original's base colour onto it (selected to active, nearest surface via cage extrusion)
    img = bpy.data.images.new("clean_basecolor", TEX, TEX, alpha=False)
    mat = bpy.data.materials.new("clean_mat"); mat.use_nodes = True
    nt = mat.node_tree; tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = img
    nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    nt.nodes.active = tex
    new.data.materials.clear(); new.data.materials.append(mat)
    sc = bpy.context.scene; sc.render.engine = "CYCLES"; sc.cycles.device = "CPU"; sc.cycles.samples = 1
    bpy.ops.object.select_all(action="DESELECT"); orig.select_set(True); new.select_set(True); bpy.context.view_layer.objects.active = new
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, use_selected_to_active=True, cage_extrusion=EXTRUDE,
                        max_ray_distance=0.0, margin=MARGIN, margin_type="EXTEND")
    tmp = os.path.splitext(dst)[0] + "_basecolor.png"
    img.filepath_raw = tmp; img.file_format = "PNG"; img.save()
    bpy.data.objects.remove(orig, do_unlink=True)
    obj = new
    rep["baked_texture"] = tmp

rep["after"] = counts(obj)
bpy.ops.object.select_all(action="DESELECT"); obj.select_set(True); bpy.context.view_layer.objects.active = obj
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_image_format="AUTO")
if report:
    json.dump(rep, open(report, "w"), indent=2)
print("CLEAN", json.dumps(rep))
