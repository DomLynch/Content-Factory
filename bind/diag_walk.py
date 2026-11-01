"""
diag_walk.py - compare SOURCE Mixamo armature vs retargeted armature for the arm chain while playing Walking (100440903).
  blender --background --python diag_walk.py -- <file.blend|file.glb> <out.json> <tag> [--stills DIR]
Target armature = object 'Rig' (blend) or the first armature (glb). Retarget = identical algorithm to metrics2.retarget (world-rotation delta).
Dumps every 5th frame (1,5,10,...) world direction (tail-head) and world quaternion of Arm/ForeArm/Hand (both sides), the elbow angle (unsigned, deg, between
upper-arm and forearm direction) and a SIGNED flexion angle about the hinge axis that is the rest-pose hinge carried along by the upper arm's delta rotation.
"""
import bpy, sys, os, json, math
import numpy as np
from mathutils import Vector, Matrix, Quaternion
argv = sys.argv[sys.argv.index('--') + 1:]
SRC, OUTJ, TAG = argv[0], os.path.abspath(argv[1]), argv[2]
STILLS = os.path.abspath(argv[argv.index('--stills') + 1]) if '--stills' in argv else None
WALK = r'C:\HANDOVER-Macbook\extracted\batch2-10\Mixamo-Animations-XBot-FBX30\Individual\100440903--Walking.fbx'
if SRC.lower().endswith('.blend'):
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(SRC)); tgt = bpy.data.objects['Rig']
else:
    bpy.ops.wm.read_factory_settings(use_empty=True); bpy.ops.import_scene.gltf(filepath=SRC); tgt = [o for o in bpy.data.objects if o.type == 'ARMATURE'][0]
print('DW> actions in target file:', [a.name for a in bpy.data.actions]); 
if tgt.animation_data: tgt.animation_data_clear()
tgt_objs_before = set(bpy.data.objects)
bpy.ops.import_scene.fbx(filepath=WALK)
src = [o for o in bpy.data.objects if o not in tgt_objs_before and o.type == 'ARMATURE'][0]; act = src.animation_data.action
names = [b.name for b in src.data.bones]; r_rest = {b.name: tgt.matrix_world @ b.matrix_local for b in tgt.data.bones}
mapped = [n for n in names if n in r_rest]; unm_s = [n for n in names if n not in r_rest]; unm_t = [n for n in r_rest if n not in names]
sw = src.matrix_world; s_rest = {n: sw @ src.data.bones[n].matrix_local for n in mapped}
hscale = r_rest['mixamorig:Hips'].translation.z / s_rest['mixamorig:Hips'].translation.z
def depth(b):
    d = 0
    while b.parent: b = b.parent; d += 1
    return d
order = sorted(tgt.data.bones, key=depth); hips_rest_t = s_rest['mixamorig:Hips'].translation.copy()
def retarget_pose(f):
    bpy.context.scene.frame_set(f); s_pose = {n: sw @ src.pose.bones[n].matrix for n in mapped}; pose_w = {}; fr = {}
    for b in order:
        n = b.name; par = b.parent
        chain = r_rest[n] if par is None else pose_w[par.name] @ r_rest[par.name].inverted() @ r_rest[n]
        if n in s_pose:
            dR = s_pose[n].to_3x3() @ s_rest[n].to_3x3().inverted(); t = (dR @ r_rest[n].to_3x3()).to_4x4(); tr = chain.translation.copy()
            if n == 'mixamorig:Hips': tr = r_rest[n].translation + (s_pose[n].translation - hips_rest_t) * hscale
            t.translation = tr
        else: t = chain
        pose_w[n] = t
        if n in s_pose:
            basis = (r_rest[n].inverted() @ t) if par is None else (pose_w[par.name] @ r_rest[par.name].inverted() @ r_rest[n]).inverted() @ t
            fr[n] = (basis.to_quaternion(), basis.to_translation() if n == 'mixamorig:Hips' else None)
    return fr
def apply(fr):
    for pb in tgt.pose.bones: pb.rotation_mode = 'QUATERNION'; pb.rotation_quaternion = (1, 0, 0, 0); pb.location = (0, 0, 0)
    for n, (q, loc) in fr.items():
        pb = tgt.pose.bones[n]; pb.rotation_quaternion = q
        if loc is not None: pb.location = loc
    bpy.context.view_layer.update()
def wdir(arm, n):
    pb = arm.pose.bones[n]; h = arm.matrix_world @ pb.head; t = arm.matrix_world @ pb.tail; d = (t - h).normalized(); q = (arm.matrix_world @ pb.matrix).to_quaternion(); return d, q
def rest_dir(arm, n):
    b = arm.data.bones[n]; return (arm.matrix_world.to_3x3() @ (b.tail_local - b.head_local)).normalized()
def ang(a, b): return math.degrees(math.acos(max(-1.0, min(1.0, a.dot(b)))))
SIDES = ('Left', 'Right'); JN = ('Arm', 'ForeArm', 'Hand')
def elbow(arm, side, rest_up, rest_fore):
    a, qa = wdir(arm, 'mixamorig:%sArm' % side); f, _ = wdir(arm, 'mixamorig:%sForeArm' % side)
    hinge0 = Vector((0, 0, 1))                                    # rest hinge (forearm flexes in the horizontal plane in a T-pose); carried by the upper arm's delta rotation
    dq = qa.to_matrix() @ Matrix(Quaternion((1, 0, 0, 0)).to_matrix()) # placeholder, replaced below
    ra = rest_up
    Rd = Quaternion(a.rotation_difference(ra)).inverted() if False else None
    return a, f
def flex(arm, side, rest_a):
    a, qa = wdir(arm, 'mixamorig:%sArm' % side); f, _ = wdir(arm, 'mixamorig:%sForeArm' % side)
    rot = a.rotation_difference(rest_a).inverted()                # rotation taking the rest upper-arm dir to the current one (minimal rotation)
    h = rot @ Vector((0, 0, 1)); ax = a.cross(f)
    return ang(a, f), math.degrees(math.atan2(ax.dot(h), a.dot(f)))
frames = [1] + list(range(5, 65, 5))
out = {'tag': TAG, 'source': SRC, 'matched': len(mapped), 'src_bones': len(names), 'unmatched_source_bones': unm_s, 'target_bones_not_in_source_count': len(unm_t), 'rest': {}, 'frames': {}}
# rest (pose = identity)
for pb in tgt.pose.bones: pb.rotation_mode = 'QUATERNION'; pb.rotation_quaternion = (1, 0, 0, 0); pb.location = (0, 0, 0)
bpy.context.view_layer.update(); bpy.context.scene.frame_set(1)
for pb in src.pose.bones: pb.rotation_mode = 'QUATERNION'; pb.rotation_quaternion = (1, 0, 0, 0); pb.location = (0, 0, 0)
# source REST directions from bone rest matrices (independent of the action)
for s in SIDES:
    for j in JN:
        n = 'mixamorig:%s%s' % (s, j); rs = (sw.to_3x3() @ (src.data.bones[n].tail_local - src.data.bones[n].head_local)).normalized(); rt = rest_dir(tgt, n)
        out['rest'][n] = {'src_dir': [round(x, 3) for x in rs], 'tgt_dir': [round(x, 3) for x in rt], 'angle_between_deg': round(ang(rs, rt), 2)}
    out['rest']['%s_elbow_rest_src_deg' % s] = round(ang(Vector(out['rest']['mixamorig:%sArm' % s]['src_dir']), Vector(out['rest']['mixamorig:%sForeArm' % s]['src_dir'])), 2)
    out['rest']['%s_elbow_rest_tgt_deg' % s] = round(ang(Vector(out['rest']['mixamorig:%sArm' % s]['tgt_dir']), Vector(out['rest']['mixamorig:%sForeArm' % s]['tgt_dir'])), 2)
rest_a_src = {s: (sw.to_3x3() @ (src.data.bones['mixamorig:%sArm' % s].tail_local - src.data.bones['mixamorig:%sArm' % s].head_local)).normalized() for s in SIDES}
rest_a_tgt = {s: rest_dir(tgt, 'mixamorig:%sArm' % s) for s in SIDES}
poses = {}
for f in frames:
    fr = retarget_pose(f); poses[f] = fr                      # leaves the SOURCE evaluated at frame f
    rec = {}
    for s in SIDES:
        fs, ss = flex(src, s, rest_a_src[s])
        rec['%s_src' % s] = {'elbow_deg': round(fs, 1), 'signed_flex_deg': round(ss, 1)}
        for j in JN:
            d, q = wdir(src, 'mixamorig:%s%s' % (s, j)); rec['%s_src' % s][j + '_dir'] = [round(x, 3) for x in d]; rec['%s_src' % s][j + '_q'] = [round(x, 3) for x in q]
    apply(fr)
    for s in SIDES:
        ft, st = flex(tgt, s, rest_a_tgt[s]); rec['%s_tgt' % s] = {'elbow_deg': round(ft, 1), 'signed_flex_deg': round(st, 1)}
        for j in JN:
            d, q = wdir(tgt, 'mixamorig:%s%s' % (s, j)); rec['%s_tgt' % s][j + '_dir'] = [round(x, 3) for x in d]; rec['%s_tgt' % s][j + '_q'] = [round(x, 3) for x in q]
            ds = Vector(rec['%s_src' % s][j + '_dir']); rec['%s_tgt' % s][j + '_dir_err_vs_src_deg'] = round(ang(ds, d), 1)
            # rotation delta vs rest on both sides (rest-compensated comparison)
    out['frames'][f] = rec
print('DW> ' + TAG, 'matched %d/%d' % (len(mapped), len(names)), 'unmatched source', unm_s)
print('DW> rest angle between src and tgt bone dirs (deg):', {k: v['angle_between_deg'] for k, v in out['rest'].items() if isinstance(v, dict)})
print('DW> rest elbow src/tgt', out['rest']['Left_elbow_rest_src_deg'], out['rest']['Left_elbow_rest_tgt_deg'], out['rest']['Right_elbow_rest_src_deg'], out['rest']['Right_elbow_rest_tgt_deg'])
print('DW> frame | L elbow src/tgt (signed src/tgt) | R elbow src/tgt (signed) | L upper-arm dir err | L fore err | L hand err | R upper err | R fore err | R hand err')
for f in frames:
    r = out['frames'][f]
    print('DW> %3d | L %5.1f/%5.1f (%6.1f/%6.1f) | R %5.1f/%5.1f (%6.1f/%6.1f) | L %4.1f %4.1f %4.1f | R %4.1f %4.1f %4.1f' % (f,
          r['Left_src']['elbow_deg'], r['Left_tgt']['elbow_deg'], r['Left_src']['signed_flex_deg'], r['Left_tgt']['signed_flex_deg'],
          r['Right_src']['elbow_deg'], r['Right_tgt']['elbow_deg'], r['Right_src']['signed_flex_deg'], r['Right_tgt']['signed_flex_deg'],
          r['Left_tgt']['Arm_dir_err_vs_src_deg'], r['Left_tgt']['ForeArm_dir_err_vs_src_deg'], r['Left_tgt']['Hand_dir_err_vs_src_deg'],
          r['Right_tgt']['Arm_dir_err_vs_src_deg'], r['Right_tgt']['ForeArm_dir_err_vs_src_deg'], r['Right_tgt']['Hand_dir_err_vs_src_deg']))
json.dump(out, open(OUTJ, 'w'), indent=1)
# ---------- stills: rest, walk frame 10, 20 (skinned mesh of the target)
if STILLS:
    os.makedirs(STILLS, exist_ok=True); scn = bpy.context.scene
    for o in list(bpy.data.objects):
        if o is src or o.parent is src: pass
    for o in bpy.data.objects:
        if o.type == 'MESH':
            keep = (o.name == 'Body') or o.name.startswith('Steel')
            o.hide_render = not keep
        elif o.type == 'ARMATURE': o.hide_render = True
    # remove the FBX source meshes if any
    for o in list(bpy.data.objects):
        if o.parent is src and o.type == 'MESH': o.hide_render = True
    scn.render.engine = 'BLENDER_WORKBENCH'; scn.render.resolution_x = 800; scn.render.resolution_y = 800
    sh = scn.display.shading; sh.light = 'STUDIO'; sh.color_type = 'SINGLE'; sh.single_color = (0.78, 0.64, 0.56); sh.show_cavity = False
    scn.world = bpy.data.worlds.new('w'); scn.world.color = (0.18, 0.2, 0.24)
    vis = [o for o in bpy.data.objects if o.type == 'MESH' and not o.hide_render]
    zs = [ (o.matrix_world @ Vector(c)).z for o in vis for c in o.bound_box]
    zc = 0.5 * (min(zs) + max(zs)); hh = max(zs) - min(zs)
    cd = bpy.data.cameras.new('cam'); cam = bpy.data.objects.new('cam', cd); scn.collection.objects.link(cam); cd.type = 'ORTHO'; scn.camera = cam
    for nm, f in (('rest', None), ('walk10', 10), ('walk20', 20)):
        if f is None:
            for pb in tgt.pose.bones: pb.rotation_quaternion = (1, 0, 0, 0); pb.location = (0, 0, 0)
            bpy.context.view_layer.update()
        else:
            if f not in poses: poses[f] = None
            apply(poses[f] if poses[f] else retarget_pose(f))
        cam.location = (0, -12, zc); cam.rotation_euler = (Vector((0, 0, zc)) - cam.location).to_track_quat('-Z', 'Y').to_euler(); cd.ortho_scale = hh * 1.35
        scn.render.filepath = os.path.join(STILLS, '%s_%s.png' % (TAG, nm)); bpy.ops.render.render(write_still=True)
    print('DW> stills', STILLS)
