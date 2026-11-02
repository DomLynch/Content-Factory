"""make_proof_cloak.py - Blender-side (background, CPU): a clean UV-gridded PROOF cloak panel sized to a T-posed bound body, used only to prove the cloth stage when no cloak part mesh exists
(the vampire's `cloak_alone` row was never built into a mesh; the factory only has the fused one-piece figure and the coat). NOT a production cloak.
  blender --background --python make_proof_cloak.py -- <args.json>   args: body, out, result, rig_name, shoulder_patterns, grid [nu, nv], wrap_deg, standoff_m, flare_m, bottom_frac, material
The panel wraps from the sides round the BACK (+Y after the glTF import, the figure faces -Y) like a cape hanging from the neck and shoulders: at each height the ellipse radii are measured
from the body (torso only: arms of the T-pose are excluded by the shoulder joint x) plus standoff_m, and flare_m widens it toward the hem. It starts clear of the body; the sim drapes it."""
import json, math, os, sys, time

import bpy, bmesh
import numpy as np

A = json.load(open(sys.argv[sys.argv.index('--') + 1], encoding='utf-8'))
R = {}
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=A['body'])
rig = bpy.data.objects.get(A['rig_name']) or next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
rig.data.pose_position = 'REST'
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
pts = []
for o in [o for o in bpy.data.objects if o.type == 'MESH' and (o.name == A.get('body_name') or any(m.type == 'ARMATURE' for m in o.modifiers))]:   # not the rig's bone-shape icospheres
    eo = o.evaluated_get(dg); me = eo.to_mesh()
    a = np.empty(len(me.vertices) * 3); me.vertices.foreach_get('co', a)
    m = np.array(o.matrix_world); a = a.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
    pts.append(a); eo.to_mesh_clear()
P = np.vstack(pts)
zmin, zmax = float(P[:, 2].min()), float(P[:, 2].max()); H = zmax - zmin
def bone(name_part, tail=False):
    for b in rig.data.bones:
        if name_part in b.name.lower():
            v = rig.matrix_world @ (b.tail_local if tail else b.head_local)
            return np.array(v)
neck, arm_l = bone('neck'), bone('leftarm')
arm_x = abs(float(arm_l[0])) if arm_l is not None else 0.2
shoulder_z = max(float(neck[2]) - 0.0, float(arm_l[2]) if arm_l is not None else float(neck[2]))
R.update(height_m=round(H, 3), neck=[round(float(x), 3) for x in neck], arm_joint_x=round(arm_x, 3), shoulder_z=round(shoulder_z, 3))

top_z = shoulder_z + 0.03                        # a little collar above the shoulder line
bot_z = zmin + H * A['bottom_frac']
nu, nv = A['grid']
wrap = math.radians(A['wrap_deg'])
d = A['standoff_m']; flare = A['flare_m']
arm_z = float(arm_l[2]) if arm_l is not None else shoulder_z
cx0 = float(neck[0])                              # the T-pose body is symmetric about the neck/rig centre line
torso = P[(np.abs(P[:, 0] - cx0) <= arm_x + 0.03) | ((P[:, 2] < arm_z - 0.25) & (np.abs(P[:, 0] - cx0) <= 0.45))]   # excludes the T-pose arms (they are at shoulder height)
def radii(z):
    band = torso[(torso[:, 2] > z - 0.04) & (torso[:, 2] < z + 0.04)]
    if len(band) < 10:
        return None
    cx = cx0; cy = float(np.median(band[:, 1]))
    return cx, cy, float(np.quantile(np.abs(band[:, 0] - cx), 0.98)), float(np.quantile(np.abs(band[:, 1] - cy), 0.98))
prev = None
rows = []
for j in range(nv + 1):
    v = j / nv
    z = top_z + (bot_z - top_z) * v
    r = radii(min(z, top_z - 0.05))   # above the neck the torso band is empty: reuse the highest valid band
    if r is None:
        r = prev
    prev = r
    rows.append((z, r))
mesh = bpy.data.meshes.new('cloak'); bm = bmesh.new()
uv = bm.loops.layers.uv.new('UVMap')
verts = []
for j, (z, (cx, cy, rx, ry)) in enumerate(rows):
    v = j / nv
    e = flare * v ** 1.5
    row = []
    for i in range(nu + 1):
        u = i / nu
        th = -wrap + 2 * wrap * u
        row.append(bm.verts.new((cx + (rx + d + e) * math.sin(th), cy + (ry + d + e * 0.6) * math.cos(th), z)))
    verts.append(row)
for j in range(nv):
    for i in range(nu):
        f = bm.faces.new((verts[j][i], verts[j][i + 1], verts[j + 1][i + 1], verts[j + 1][i]))
        for lp, (uu, vv) in zip(f.loops, ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))):
            lp[uv].uv = (uu / nu, 1 - vv / nv)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
bm.to_mesh(mesh); bm.free()
mesh.shade_smooth()
# the panel must face OUTWARD (away from the body): flip if the first face normal points toward the body axis
p0 = mesh.polygons[nu // 2 + nv // 2 * nu]
c = np.array(p0.center); n = np.array(p0.normal)
axis = np.array([rows[nv // 2][1][0], rows[nv // 2][1][1], c[2]])
if np.dot(n, c - axis) < 0:
    bm = bmesh.new(); bm.from_mesh(mesh); bmesh.ops.reverse_faces(bm, faces=bm.faces[:]); bm.to_mesh(mesh); bm.free()
mat = bpy.data.materials.new(A['material']['name']); mat.use_nodes = True
pb = mat.node_tree.nodes['Principled BSDF']
pb.inputs['Base Color'].default_value = (*A['material']['color'], 1.0); pb.inputs['Roughness'].default_value = A['material']['roughness']
mesh.materials.append(mat)
obj = bpy.data.objects.new('cloak', mesh); bpy.context.scene.collection.objects.link(obj)
bpy.context.view_layer.objects.active = obj
for o in list(bpy.data.objects):
    if o is not obj:
        bpy.data.objects.remove(o, do_unlink=True)
bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True)
bpy.ops.export_scene.gltf(filepath=A['out'], export_format='GLB', use_selection=True, export_apply=False, export_skins=False, export_animations=False, export_yup=True)
R.update(verts=len(mesh.vertices), faces=len(mesh.polygons), top_z=round(top_z, 3), bottom_z=round(bot_z, 3), out=A['out'])
json.dump(R, open(A['result'], 'w'), indent=1)
print('PROOFCLOAK>', json.dumps(R))
