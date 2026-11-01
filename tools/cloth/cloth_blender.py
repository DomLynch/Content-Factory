"""cloth_blender.py - Blender-side (background, CPU) cloth drape of ONE gear part on the T-posed bound body (plugin 0.1.18, tool `cloth`).
  blender --background --python cloth_blender.py -- <args.json>
Written by tools/run_cloth.py; every number comes from the args json (config "cloth"). Mode "rest" only; mode "clip" raises NotImplementedError (per-clip sim baked as shape keys is not built).

Steps
 1. Import the bound body GLB, set the rig to its REST (T) pose, evaluate the body mesh in world space. Keep a FULL-resolution BVH for the penetration number, make a decimated
    PROXY (collision.proxy_faces) as the Collision object, read the pin bones' head/tail (bone name contains a pin pattern), drop the original body (RAM).
 2. Import the part GLB, bake world transforms, drop skin data, merge vertices at the glTF seam splits (UVs live on loops and survive), recompute nothing else, decimate to
    prep.target_faces when over prep.max_faces.
 3. PIN group: vertex weight 1 where the vertex is within pin.height_band_m below the highest pin bone head and within pin.radius_m of a pin bone segment (cloth_core.pin_weight).
 4. Cloth modifier with the preset (mass, stiffness, bending, damping, quality steps), self collision on, body Collision modifier (thickness_outer, friction). Disk point cache in the temp folder.
 5. Step frame by frame; per frame the p99 speed of the free vertices; stop when it stays under sim.settle_p99_speed_mps (cloth_core.SettleDetector) or at sim.max_frames.
 6. Write the settled coordinates into the ORIGINAL mesh (the same as applying the cloth modifier at that frame; UVs and materials untouched), remove the modifier, smooth-shade
    (normals recompute), measure penetration of the final vertices into the FULL-resolution body (nearest-face signed distance), export GLB, re-import and count.
Penetration = depth (>= 0, metres) below the body surface of cloth vertices and face centres: inside = at least 4 of 6 axis rays cross the full-res body an odd number of times (the body is not watertight), depth = distance to the nearest face; `max_free_m` ignores pinned vertices."""
import json, math, os, sys, time

import bpy, bmesh
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cloth_core as core

A = json.load(open(sys.argv[sys.argv.index('--') + 1], encoding='utf-8'))
CL, PART = A['cloth'], A['part']
T0 = time.time()
R = {'part': PART, 'mode': A.get('mode'), 'notes': [], 'blender': bpy.app.version_string}


def P(*a): print('CLOTH>', *a, flush=True)


def finish(code):
    R['total_s'] = round(time.time() - T0, 1)
    json.dump(R, open(A['result'], 'w', encoding='utf-8'), indent=1)
    sys.exit(code)


if A.get('mode') != 'rest':
    R['error'] = 'NotImplementedError: per-clip cloth simulation is not built; only mode "rest"'
    finish(4)

lowest_free = [None]


def watch_ram():
    f = core.free_ram_gb()
    lowest_free[0] = f if lowest_free[0] is None else min(lowest_free[0], f)
    R['lowest_free_ram_gb'] = round(lowest_free[0], 1)
    return f


def coords(me, n=None):
    a = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get('co', a)
    return a.reshape(-1, 3)


def bvh_from_bm(bm):
    bm = bm.copy()
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.faces.ensure_lookup_table()
    t = BVHTree.FromBMesh(bm)
    nt = len(bm.faces)
    bm.free()
    return t, nt


# ------------------------------------------------------------------ 1. body
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=A['body'])
rig = bpy.data.objects.get(A['rig_name']) or next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
bodies = [o for o in bpy.data.objects if o.type == 'MESH' and (o.name == A.get('body_name') or any(m.type == 'ARMATURE' for m in o.modifiers))]   # not the rig's bone-shape icospheres
if not bodies:
    R['error'] = 'bound body glb has no mesh'; finish(3)
if rig is not None:
    rig.data.pose_position = 'REST'
    if rig.animation_data:
        rig.animation_data_clear()
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
bm_body = bmesh.new()
for o in bodies:
    eo = o.evaluated_get(dg)
    me = eo.to_mesh()
    tmp = bmesh.new(); tmp.from_mesh(me); tmp.transform(o.matrix_world)
    tmp.to_mesh(me)   # world-space copy
    bm_body.from_mesh(me)
    tmp.free(); eo.to_mesh_clear()
body_verts = len(bm_body.verts)
bmesh.ops.remove_doubles(bm_body, verts=bm_body.verts[:], dist=1e-5)   # glTF splits vertices at UV seams; the body must be connected for the collider and the inside test
R_merged = len(bm_body.verts)
bz = np.array([v.co.z for v in bm_body.verts])
body_h = float(bz.max() - bz.min())
R['body'] = {'meshes': len(bodies), 'verts': body_verts, 'verts_merged': R_merged, 'height_m': round(body_h, 4), 'rest_pose_forced': rig is not None}
segments, bone_names_used = [], []
if rig is not None:
    names = [b.name for b in rig.data.bones]
    bone_names_used = core.select_pin_bones(names, CL['pin'])
    for n in bone_names_used:
        b = rig.data.bones[n]
        h, t = rig.matrix_world @ b.head_local, rig.matrix_world @ b.tail_local
        segments.append(((h.x, h.y, h.z), (t.x, t.y, t.z)))
R['pin_bones'] = bone_names_used
if not segments:
    R['error'] = 'no pin bones matched pin.bone_patterns in the rig (or the body GLB has no armature)'; finish(3)
full_bvh, full_tris = bvh_from_bm(bm_body)
R['body']['tris'] = full_tris
# proxy collider
proxy_me = bpy.data.meshes.new('proxy_src'); bm_body.to_mesh(proxy_me)
proxy_src = bpy.data.objects.new('proxy_src', proxy_me); bpy.context.scene.collection.objects.link(proxy_src)
want = CL['collision']['proxy_faces']
if full_tris > want:
    dm = proxy_src.modifiers.new('dec', 'DECIMATE'); dm.decimate_type = 'COLLAPSE'; dm.ratio = want / full_tris; dm.use_collapse_triangulate = True
bpy.context.view_layer.update()
proxy_eval = proxy_src.evaluated_get(bpy.context.evaluated_depsgraph_get())
pme = bpy.data.meshes.new_from_object(proxy_eval)
bm_body.free()
for o in list(bpy.data.objects):   # drop the original body, rig and the proxy source: RAM
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m is not pme:
        bpy.data.meshes.remove(m)
for coll in ('images', 'materials', 'textures', 'armatures', 'actions'):
    for d in list(getattr(bpy.data, coll)):
        getattr(bpy.data, coll).remove(d)
collider = bpy.data.objects.new('collider', pme); bpy.context.scene.collection.objects.link(collider)
R['body']['proxy_faces'] = len(pme.polygons)
proxy_tree = BVHTree.FromObject(collider, bpy.context.evaluated_depsgraph_get())
watch_ram()
P('body', json.dumps(R['body']), 'pin bones', bone_names_used)

# ------------------------------------------------------------------ 2. part
before = set(bpy.data.objects)
bpy.ops.import_scene.gltf(filepath=A['part_mesh'])
new = [o for o in bpy.data.objects if o not in before]
pm = [o for o in new if o.type == 'MESH']
if not pm:
    R['error'] = 'part glb has no mesh'; finish(3)
for o in new:
    if o.type == 'MESH':
        mw = o.matrix_world.copy(); o.parent = None; o.data.transform(mw); o.matrix_world = o.matrix_world.Identity(4)
        o.modifiers.clear(); o.vertex_groups.clear()
for o in new:
    if o.type != 'MESH':
        bpy.data.objects.remove(o, do_unlink=True)
if len(pm) > 1:
    bpy.ops.object.select_all(action='DESELECT')
    for o in pm: o.select_set(True)
    bpy.context.view_layer.objects.active = pm[0]
    bpy.ops.object.join()
cloth = bpy.context.view_layer.objects.active if len(pm) > 1 else pm[0]
cloth.name = A['cloth']['glb_prefix'] + PART
me = cloth.data
cnt = {'verts_in': len(me.vertices), 'faces_in': len(me.polygons), 'uv_layers_in': len(me.uv_layers), 'materials_in': len(me.materials)}
try:
    with bpy.context.temp_override(object=cloth, active_object=cloth):
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
except Exception as e:
    R['notes'].append(f'custom normals not cleared: {e}')
bm = bmesh.new(); bm.from_mesh(me)
bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)   # glTF splits vertices at UV seams: a cloth must be connected there; UVs are per loop and stay
bm.to_mesh(me); bm.free(); me.update()
cnt['verts_merged'] = len(me.vertices); cnt['faces_merged'] = len(me.polygons)
pre = CL['prep']
if len(me.polygons) > pre['max_faces']:
    tris = sum(len(p.vertices) - 2 for p in me.polygons)
    dm = cloth.modifiers.new('dec', 'DECIMATE'); dm.decimate_type = 'COLLAPSE'; dm.ratio = min(1.0, pre['target_faces'] / max(tris, 1)); dm.use_collapse_triangulate = True
    bpy.context.view_layer.update()
    ev = cloth.evaluated_get(bpy.context.evaluated_depsgraph_get())
    nme = bpy.data.meshes.new_from_object(ev)
    cloth.modifiers.remove(dm)
    old = cloth.data; cloth.data = nme; bpy.data.meshes.remove(old); me = nme
    R['decimated'] = {'ratio': round(dm.ratio, 4), 'faces_before': cnt['faces_merged'], 'faces_after': len(me.polygons)}
cnt['verts_sim'], cnt['faces_sim'] = len(me.vertices), len(me.polygons)
co0 = coords(me)
R['part_bbox'] = {'min': [round(float(x), 4) for x in co0.min(axis=0)], 'max': [round(float(x), 4) for x in co0.max(axis=0)]}
diag0 = float(np.linalg.norm(co0.max(axis=0) - co0.min(axis=0)))

# ------------------------------------------------------------------ 3. pin group
pin = CL['pin']
w = np.array([core.pin_weight(tuple(p), segments, pin) for p in co0])
vg = cloth.vertex_groups.new(name='pin')
idx_full = [int(i) for i in np.nonzero(w >= 0.999)[0]]
if idx_full:
    vg.add(idx_full, 1.0, 'REPLACE')
for i in np.nonzero((w > 0) & (w < 0.999))[0]:
    vg.add([int(i)], float(w[i]), 'REPLACE')
free_mask = w < 0.5
R['pins'] = {'count': int((w > 0).sum()), 'full_weight': len(idx_full), 'free': int(free_mask.sum()),
             'rule': {'bones': bone_names_used, 'height_band_m': pin['height_band_m'], 'radius_m': pin['radius_m'], 'soft_inner': pin['soft_inner']}}
P('pins', json.dumps(R['pins']))

# start penetration (diagnostic: the part should start clear of the body)
AXES = [Vector(v) for v in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]


def odd_hits(tree, p, d):
    """Parity of the ray crossings from p along d."""
    n, o = 0, Vector(p)
    for _ in range(60):
        hit = tree.ray_cast(o, d)
        if hit[0] is None:
            break
        n += 1
        o = hit[0] + d * 1e-4
    return n % 2


def penetration(co_world, tree, vote=4):
    """Per sample: depth (m) below the body surface and, for outside samples, the gap. The TRELLIS body is NOT watertight (about a fifth of its edges are open after merging the
    glTF seam splits), so the nearest face normal alone gives wrong signs: a sample counts as INSIDE only when at least `vote` of 6 axis rays cross the surface an odd number of times;
    its depth is then the distance to the nearest face. `signed` is the nearest-normal sign test, kept as a second, less reliable number."""
    depth = np.zeros(len(co_world)); gap = np.full(len(co_world), np.inf); signed = np.zeros(len(co_world))
    for i, p in enumerate(co_world):
        v = Vector(p)
        loc, nrm, fi, d = tree.find_nearest(v)
        if loc is None:
            continue
        if (v - loc).dot(nrm) < 0:
            signed[i] = d
        if sum(odd_hits(tree, v, ax) for ax in AXES) >= vote:
            depth[i] = d
        else:
            gap[i] = d
    return depth, gap, signed


def face_centres(m):
    return np.array([list(p.center) for p in m.polygons])


d0, _, d0s = penetration(co0, full_bvh)
R['start_penetration_max_m'] = round(float(d0[free_mask].max()) if free_mask.any() else 0.0, 4)
R['start_penetration_signed_max_m'] = round(float(d0s[free_mask].max()) if free_mask.any() else 0.0, 4)

# ------------------------------------------------------------------ 4. cloth + collision
sc = bpy.context.scene
sim = CL['sim']
sc.render.fps = int(sim['fps']); sc.frame_start, sc.frame_end = 1, int(sim['max_frames']); sc.gravity = (0.0, 0.0, -float(sim['gravity_mps2']))
col = CL['collision']
cmod = collider.modifiers.new('Collision', 'COLLISION')
cs_ = collider.collision
cs_.use = True; cs_.thickness_outer = col['thickness_outer_m']; cs_.thickness_inner = col['thickness_inner']; cs_.cloth_friction = col['friction']; cs_.damping = col['damping']
mod = cloth.modifiers.new('Cloth', 'CLOTH')
s = mod.settings
pv = A['preset_values']
for k in ('quality', 'mass', 'tension_stiffness', 'compression_stiffness', 'shear_stiffness', 'bending_stiffness', 'tension_damping', 'compression_damping', 'shear_damping', 'bending_damping', 'air_damping'):
    setattr(s, k, pv[k])
s.vertex_group_mass = 'pin'; s.pin_stiffness = pin['stiffness']
cc = mod.collision_settings
cc.use_collision = True; cc.distance_min = col['cloth_distance_min_m']; cc.collision_quality = int(col['collision_quality'])
cc.use_self_collision = bool(col['self_collision']); cc.self_distance_min = col['self_distance_min_m']; cc.self_friction = col['self_friction']
pc = mod.point_cache
pc.frame_start, pc.frame_end = 1, int(sim['max_frames'])
os.makedirs(A['cache_dir'], exist_ok=True)
try:
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(A['cache_dir'], 'sim.blend'))
    pc.use_disk_cache = True
    R['cache'] = {'disk': True, 'dir': A['cache_dir']}
except Exception as e:
    R['cache'] = {'disk': False, 'error': str(e)}
R['settings'] = {'preset': A['preset'], **pv, 'pin_stiffness': pin['stiffness'], 'self_collision': bool(col['self_collision']), 'cloth_distance_min_m': col['cloth_distance_min_m'],
                 'collision_quality': col['collision_quality'], 'collider_thickness_outer_m': col['thickness_outer_m'], 'collider_friction': col['friction'], 'fps': sim['fps']}

# ------------------------------------------------------------------ 5. simulate
det = core.SettleDetector(sim)
prev = co0.copy(); hist = []; frames = 0; final = co0.copy(); aborted = ''
abort_ram = A.get('abort_free_ram_gb', 0) or 0
for f in range(1, int(sim['max_frames']) + 1):
    sc.frame_set(f)
    ev = cloth.evaluated_get(bpy.context.evaluated_depsgraph_get())
    em = ev.to_mesh()
    cur = coords(em)
    ev.to_mesh_clear()
    frames = f
    if f > 1:
        spd = np.linalg.norm(cur - prev, axis=1) * sim['fps']
        p99 = float(np.quantile(spd[free_mask], 0.99)) if free_mask.any() else 0.0
        hist.append(round(p99, 4))
        done = det.feed(p99)
    else:
        done = False
    prev = cur; final = cur
    fr = watch_ram()
    if f % 10 == 0 or done:
        P(f'frame {f} p99_speed {hist[-1] if hist else 0:.4f} free_ram {fr:.1f} t {time.time() - T0:.0f}s')
    if abort_ram and fr < abort_ram:
        aborted = f'free RAM {fr:.1f} GB under the guard floor {abort_ram} GB at frame {f}'
        break
    if done:
        break
R['sim'] = {'frames': frames, 'settled': det.settled_at is not None, 'settled_at_frame': det.settled_at, 'last_p99_speed_mps': hist[-1] if hist else None,
            'p99_history_every_5': hist[::5], 'aborted': aborted, 'wall_s': round(time.time() - T0, 1)}
if aborted:
    R['error'] = aborted; finish(5)
if not det.settled_at:
    R['notes'].append(f'did not settle within {sim["max_frames"]} frames (last p99 speed {hist[-1] if hist else None} m/s): the mesh is the last frame, not a rest state')

# ------------------------------------------------------------------ 6. bake the settled mesh into the part (apply the cloth modifier at this frame), measure, export
nan = int(np.isnan(final).any(axis=1).sum())
R['nan_vertices'] = nan
if nan:
    R['error'] = f'{nan} NaN vertices after the sim'; finish(6)
cloth.modifiers.remove(mod)
me.vertices.foreach_set('co', final.ravel()); me.update()
me.shade_smooth()
me.update()
cnt['verts_out'], cnt['faces_out'] = len(me.vertices), len(me.polygons)
diag1 = float(np.linalg.norm(final.max(axis=0) - final.min(axis=0)))
R['extent_ratio'] = round(diag1 / max(diag0, 1e-9), 3)
R['moved'] = {'max_m': round(float(np.linalg.norm(final - co0, axis=1).max()), 4), 'mean_m': round(float(np.linalg.norm(final - co0, axis=1).mean()), 4)}
fc = face_centres(me)
samples = np.vstack([final, fc]); free_s = np.concatenate([free_mask, np.ones(len(fc), dtype=bool)])
dep, gap, sgn = penetration(samples, full_bvh)
dep_p, _, _ = penetration(samples, proxy_tree)
fd = dep[free_s]
R['penetration'] = {'against': 'full-resolution body, inside = majority of 6 axis rays odd, depth = distance to nearest face', 'samples': int(len(samples)), 'max_all_m': round(float(dep.max()), 5), 'max_free_m': round(float(fd.max()), 5),
                    'p99_free_m': round(float(np.quantile(fd, 0.99)), 5), 'n_free_over_1mm': int((fd > 0.001).sum()), 'n_free_over_5mm': int((fd > 0.005).sum()),
                    'min_gap_m': round(float(gap[np.isfinite(gap)].min()), 5) if np.isfinite(gap).any() else None, 'proxy_max_free_m': round(float(dep_p[free_s].max()), 5), 'signed_nearest_normal_max_free_m': round(float(sgn[free_s].max()), 5), 'n_inside_free': int((fd > 0).sum())}
P('penetration', json.dumps(R['penetration']))
known = set(bpy.ops.export_scene.gltf.get_rna_type().properties.keys())
kw = {k: v for k, v in CL['export'].items() if k in known}
R['export_skipped_keys'] = sorted(k for k in CL['export'] if k not in known)
bpy.ops.object.select_all(action='DESELECT')
cloth.select_set(True); bpy.context.view_layer.objects.active = cloth
bpy.ops.export_scene.gltf(filepath=A['out'], **kw)
R['exported'] = os.path.exists(A['out']); R['size_bytes'] = os.path.getsize(A['out']) if R['exported'] else 0
if R['exported']:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=A['out'])
    ms = [o for o in bpy.data.objects if o.type == 'MESH']
    cnt['meshes_reimport'] = len(ms)
    cnt['verts_reimport'] = sum(len(o.data.vertices) for o in ms)   # more than verts_out: the exporter splits vertices at UV seams
    cnt['uv_layers_out'] = min((len(o.data.uv_layers) for o in ms), default=0)
    cnt['materials_out'] = sum(len(o.data.materials) for o in ms)
R['counts'] = cnt
P('DONE', json.dumps({k: R[k] for k in ('counts', 'sim', 'penetration', 'exported')}))
finish(0)
