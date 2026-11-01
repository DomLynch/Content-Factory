"""Blender (background, CPU): turn a SkinTokens result GLB into the objects the bind gate measures ('Rig' + 'Body'), optionally clamp influences, save .blend + GLB.
  blender --background --python to_rig_body.py -- <skin.glb> <out.blend> <out_bound.glb> <out_report.json> <prep_report.json> <rig_name> <body_name> <clamp 0|4>
clamp N (N > 0): keep the N strongest influences per vertex and renormalise (Blender's vertex_group_limit_total + normalize_all). It is recorded in the report."""
import json
import sys

import bpy

skin_glb, out_blend, out_glb, out_json, prep_json, rig_name, body_name, clamp = sys.argv[sys.argv.index("--") + 1:][:8]
clamp = int(clamp)
prep = json.load(open(prep_json, encoding="utf-8"))

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=skin_glb)
arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
if len(arms) != 1 or not meshes:
    raise SystemExit(f"expected 1 armature and >=1 mesh, found {len(arms)} and {len(meshes)}")
rig = arms[0]
bpy.ops.object.select_all(action="DESELECT")
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
body = bpy.context.view_layer.objects.active
rig.name, body.name = rig_name, body_name
rig.data.name = rig_name
body.data.name = body_name
if clamp > 0:
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=clamp)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)
if not any(m.type == "ARMATURE" and m.object == rig for m in body.modifiers):
    md = body.modifiers.new("Armature", "ARMATURE")
    md.object = rig
want = set(prep["rig_bones"])
got = {b.name for b in rig.data.bones}
groups = {g.name for g in body.vertex_groups}
report = {"bones_missing": sorted(want - got), "bones_extra": sorted(got - want), "vertex_groups": len(groups), "groups_not_bones": sorted(groups - got),
          "verts": len(body.data.vertices), "influences_clamped_to": clamp or None, "rig_bone_count": len(got)}
with open(out_json, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=1)
bpy.ops.wm.save_as_mainfile(filepath=out_blend)
bpy.ops.object.select_all(action="DESELECT")
rig.select_set(True)
body.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.export_scene.gltf(filepath=out_glb, export_format="GLB", use_selection=True, export_apply=False)
print("EXPORT_OK", out_blend)
