"""Runs INSIDE Blender (--background): retarget a motion JSON (ardy_generate.py) onto the psyche-106 rig and export a clip GLB in the Mixamo clip format that
tools/run_stills.py reads: bone-named nodes (mixamorig:*), no skin, one animation named `clip`.
Usage: blender --background --python retarget_blender.py -- <args.json>   args: motion, rig_glb, out_glb, result, cfg (config.json -> motion), in_place
No GPU, no network. Same world-rotation-delta maths as the stills stage (pose_from_clip): target world rotation = source global rotation (rest = identity) applied to the
target rest, with a per-bone correction Q that turns the target rest bone direction onto the source rest bone direction (T-pose vs T-pose differences).
Bones with no source joint (fingers other than the thumb root, twist and ik bones) follow their parent rigidly; that is stated in the result."""
import bpy, json, math, os, sys, time
from mathutils import Matrix, Quaternion, Vector

A = json.load(open(sys.argv[sys.argv.index('--') + 1], encoding='utf-8'))
M = A['cfg']
T0 = time.time()
def P(*a): print('MO>', *a, flush=True)

doc = json.load(open(A['motion'], encoding='utf-8'))
assert doc['schema'] == 1, 'unknown motion schema'
names, rest_src, glob, root = doc['joints'], doc['rest_positions'], doc['global_rot'], doc['root_positions']
T = doc['frames']
C3 = Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))     # glTF (y up, +z forward) -> Blender (z up, -y forward); same C as run_stills.py
C3i = C3.inverted()
def cvec(v): return C3 @ Vector(v)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=A['rig_glb'])
rig = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
for o in list(bpy.data.objects):
    if o is not rig:
        bpy.data.objects.remove(o, do_unlink=True)
for act in list(bpy.data.actions):
    bpy.data.actions.remove(act)
if rig.animation_data:
    rig.animation_data_clear()
bpy.context.view_layer.objects.active = rig
bones = rig.data.bones
pre = M['bone_prefix']
rest_w = {b.name: rig.matrix_world @ b.matrix_local for b in bones}

# ---- mapping: target bone name -> source joint name
spec = M['retarget']['skeletons'][doc['skeleton']]
pairs = {}
for side in ('Left', 'Right'):
    for n in spec['direct']:
        pairs[side + n] = side + n
    for t, s in spec['renamed'].items():
        pairs[t] = s
    for t, s in spec.get('renamed_sided', {}).items():
        pairs[side + t] = side + s
    if spec.get('fingers'):
        for f in ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky'):
            for i in (1, 2, 3):
                pairs['%sHand%s%d' % (side, f, i)] = '%sHand%s%d' % (side, f, i)
for n in spec['center']:
    pairs[n] = spec['center'][n]
jidx = {n: i for i, n in enumerate(names)}
tmap = {pre + t: jidx[s] for t, s in pairs.items() if (pre + t) in bones and s in jidx}
unmapped_src = sorted(s for t, s in pairs.items() if s not in jidx)
missing = [pre + t for t in pairs if (pre + t) not in bones]
need = [pre + n for n in M['retarget']['required_bones']]
bad = [n for n in need if n not in tmap]
if bad:
    P('ERROR required bones not mapped:', bad); sys.exit(5)

hips = pre + 'Hips'
dirs = {pre + a: pre + b for a, b in M['retarget']['direction_children'].items()}
def rot_between(d_t, d_s):
    if d_t.length < 1e-6 or d_s.length < 1e-6: return Matrix.Identity(3)
    return d_t.normalized().rotation_difference(d_s.normalized()).to_matrix()
Q = {}
for t, j in tmap.items():
    c = dirs.get(t)
    if c in tmap:
        d_t = rest_w[c].translation - rest_w[t].translation
        d_s = cvec(rest_src[tmap[c]]) - cvec(rest_src[j])
        Q[t] = rot_between(d_t, d_s)
    else:
        Q[t] = Matrix.Identity(3)
q_angles = {t[len(pre):]: round(math.degrees(Q[t].to_quaternion().angle), 1) for t in Q}

# scale: rig hip-to-ankle height over the source's
lf = pre + 'LeftFoot'
src_leg = abs(rest_src[jidx[pairs['LeftFoot']]][1] - rest_src[jidx[pairs['Hips']]][1])
rig_leg = rest_w[hips].translation.z - rest_w[lf].translation.z
scale = rig_leg / src_leg
r0 = Vector(root[0])
P('bones mapped', len(tmap), 'of', len(bones), 'scale %.3f' % scale, 'frames', T)

def depth(b):
    d = 0
    while b.parent: b = b.parent; d += 1
    return d
order = sorted(bones, key=depth)
for pb in rig.pose.bones:
    pb.rotation_mode = 'QUATERNION'

scn = bpy.context.scene
scn.render.fps = max(1, int(round(doc['fps'])))
scn.frame_start, scn.frame_end = 1, T
act = bpy.data.actions.new(M['clip_action_name'])
rig.animation_data_create()
rig.animation_data.action = act
stats = {'max_root_travel_m': 0.0}
for f in range(T):
    G = glob[f]
    pose_w = {}
    for b in order:
        n = b.name; par = b.parent
        chain = rest_w[n] if par is None else pose_w[par.name] @ rest_w[par.name].inverted() @ rest_w[n]
        if n in tmap:
            Rs = Matrix([G[tmap[n]][0:3], G[tmap[n]][3:6], G[tmap[n]][6:9]])
            R = (C3 @ Rs @ C3i) @ Q[n] @ rest_w[n].to_3x3()
            tgt = R.to_4x4()
            tr = chain.translation.copy()
            if n == hips:
                d = cvec(root[f]) - cvec(r0)
                tr = Vector((rest_w[n].translation.x + d.x * scale, rest_w[n].translation.y + d.y * scale, rest_w[n].translation.z + d.z * scale))
                if A.get('in_place'):
                    tr.x, tr.y = rest_w[n].translation.x, rest_w[n].translation.y
                stats['max_root_travel_m'] = max(stats['max_root_travel_m'], round(math.hypot(d.x, d.y) * scale, 3))
            tgt.translation = tr
        else:
            tgt = chain
        pose_w[n] = tgt
        if n in tmap:
            basis = (rest_w[n].inverted() @ tgt) if par is None else (pose_w[par.name] @ rest_w[par.name].inverted() @ rest_w[n]).inverted() @ tgt
            pb = rig.pose.bones[n]
            pb.rotation_quaternion = basis.to_quaternion()
            pb.keyframe_insert('rotation_quaternion', frame=f + 1)
            if n == hips:
                pb.location = basis.to_translation()
                pb.keyframe_insert('location', frame=f + 1)
act.name = M['clip_action_name']
bpy.context.view_layer.update()

# ---- keep only the Mixamo bones in the clip (the stills stage maps by name; the Mixamo clips carry the 52 mixamorig bones and nothing else)
bpy.ops.object.mode_set(mode='EDIT')
eb = rig.data.edit_bones
for b in [b for b in eb if not b.name.startswith(pre)]:
    eb.remove(b)
bpy.ops.object.mode_set(mode='OBJECT')
for o in bpy.data.objects:
    o.select_set(o is rig)
out = A['out_glb']
os.makedirs(os.path.dirname(out), exist_ok=True)
kw = dict(filepath=out, export_format='GLB', use_selection=True, export_animations=True, export_skins=False, export_apply=False)
for extra in (dict(export_animation_mode='ACTIVE_ACTIONS', export_force_sampling=True), dict(export_current_frame=False)):
    kw.update(extra)
try:
    bpy.ops.export_scene.gltf(**kw)
except TypeError as e:
    P('export option rejected, retrying without the optional ones:', e)
    for k in ('export_animation_mode', 'export_force_sampling', 'export_current_frame', 'export_skins', 'export_apply'):
        kw.pop(k, None)
    bpy.ops.export_scene.gltf(**kw)
R = {'frames': T, 'fps': scn.render.fps, 'mapped_bones': sorted(n[len(pre):] for n in tmap), 'unmapped_source_names': unmapped_src, 'target_bones_without_source': len([b for b in bones if b.name.startswith(pre) and b.name not in tmap]),
     'rest_direction_correction_deg': q_angles, 'scale': round(scale, 4), 'stats': stats, 'wall_s': round(time.time() - T0, 1), 'missing_target_bones': missing}
json.dump(R, open(A['result'], 'w', encoding='utf-8'), indent=1)
P('DONE', R['wall_s'], 'exported', out)
