"""Blender (background, CPU): build the SkinTokens input = the body mesh (no skin) + the target rig's armature, one GLB.
  blender --background --python prep_input.py -- <body.glb> <rig.glb> <out_input.glb> <out_report.json>
SkinTokens `demo.py --use_skeleton` skins the mesh onto the skeleton it finds in the file. The rig's own meshes are dropped; the body keeps no vertex groups.
The report lists the rig's bone names so the export step can check the result kept them (metrics2.py retargets by mixamorig:* name)."""
import json
import sys

import bpy

body_glb, rig_glb, out_glb, out_json = sys.argv[sys.argv.index("--") + 1:][:4]


def import_glb(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


bpy.ops.wm.read_factory_settings(use_empty=True)
body_objs = import_glb(body_glb)
rig_objs = import_glb(rig_glb)
arms = [o for o in rig_objs if o.type == "ARMATURE"]
if len(arms) != 1:
    raise SystemExit(f"rig must hold exactly one armature, found {len(arms)}")
for o in rig_objs:
    if o.type != "ARMATURE":
        bpy.data.objects.remove(o, do_unlink=True)
meshes = [o for o in body_objs if o.type == "MESH"]
for o in body_objs:
    if o.type not in ("MESH",):
        bpy.data.objects.remove(o, do_unlink=True)
if not meshes:
    raise SystemExit("body glb holds no mesh")
bpy.ops.object.select_all(action="DESELECT")
for o in meshes:
    o.parent = None
    o.vertex_groups.clear()
    for m in list(o.modifiers):
        o.modifiers.remove(m)
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
body = bpy.context.view_layer.objects.active
rig = arms[0]
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.export_scene.gltf(filepath=out_glb, export_format="GLB", use_selection=True, export_apply=False)
with open(out_json, "w", encoding="utf-8") as f:
    json.dump({"rig_bones": [b.name for b in rig.data.bones], "deform_bones": [b.name for b in rig.data.bones if b.use_deform],
               "body_verts": len(body.data.vertices), "body_faces": len(body.data.polygons), "meshes_joined": len(meshes)}, f, indent=1)
print("PREP_OK", out_glb)
