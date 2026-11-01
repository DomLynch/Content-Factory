"""
metrics.py - skinning acceptance metrics for a bound .blend (Rig + Body) produced by bind_donor.py or bind_to_106.py

  blender --background --python metrics.py -- <bound.blend> <out.json> [--stills DIR] [--maxframes 60]

Clips (Mixamo XBot library), retargeted by mixamorig:* bone name (same retarget code as bind_to_106.py bake_clip):
  Idle   107900901--Breathing Idle.fbx
  Walk   100440903--Walking.fbx
  Attack 121360901--Zombie Attack.fbx
Frames: every frame of the clip if <= maxframes, else a uniform stride that still includes first and last frame.
Per frame the evaluated (skinned) mesh edge lengths are compared with the rest-pose lengths.
"""
import bpy, bmesh, os, sys, json, time, math
import numpy as np
from mathutils import Vector, Matrix

def P(*a): print('M>', *a, flush=True)
argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
pos = []; opt = {}; i = 0
while i < len(argv):
    if argv[i].startswith('--'): opt[argv[i]] = argv[i + 1]; i += 2
    else: pos.append(argv[i]); i += 1
BLEND, OUTJ = [os.path.abspath(x) for x in pos]
MAXF = int(opt.get('--maxframes', 60)); STILLS = os.path.abspath(opt['--stills']) if '--stills' in opt else None
LIB = r'C:\HANDOVER-Macbook\extracted\batch2-10\Mixamo-Animations-XBot-FBX30\Individual'
CLIPS = [('Idle', LIB + r'\107900901--Breathing Idle.fbx'), ('Walk', LIB + r'\100440903--Walking.fbx'), ('Attack', LIB + r'\121360901--Zombie Attack.fbx')]
ALLOWED_ZERO_BONES = {'mixamorig:LeftToe_End', 'mixamorig:RightToe_End'}
T0 = time.time()

bpy.ops.wm.open_mainfile(filepath=BLEND)
rig = bpy.data.objects['Rig']; body = bpy.data.objects['Body']; me = body.data; nv = len(me.vertices)
def get_co(m):
    a = np.empty(len(m.vertices) * 3, dtype=np.float32); m.vertices.foreach_get('co', a); return a.reshape(-1, 3).astype(np.float64)
R0 = {}
if not any(m.type == 'ARMATURE' and m.object == rig for m in body.modifiers):
    md_ = body.modifiers.new('Armature', 'ARMATURE'); md_.object = rig; body.parent = rig; body.matrix_parent_inverse = Matrix.Identity(4)
    R0['note'] = 'bound .blend had NO armature modifier on Body (bind_to_106 fallback path unbinds the body); modifier added by metrics.py so the weights are exercised'
deform_bones = [b.name for b in rig.data.bones if b.use_deform]
R = {**R0, 'blend': BLEND, 'verts': nv, 'edges': len(me.edges), 'faces': len(me.polygons), 'deform_bones': len(deform_bones)}

# ------------------------------------------------------------ weight metrics
gname = {g.index: g.name for g in body.vertex_groups}
cnt = np.zeros(nv, np.int32); ssum = np.zeros(nv); bone_vcount = {n: 0 for n in deform_bones}; nondef = 0
for v in me.vertices:
    c = 0; s = 0.0
    for g in v.groups:
        if g.weight > 1e-6:
            c += 1; s += g.weight; n = gname[g.group]
            if n in bone_vcount: bone_vcount[n] += 1
            else: nondef += 1
    cnt[v.index] = c; ssum[v.index] = s
zero_v = int((cnt == 0).sum())
wv = cnt > 0
R['weights'] = {'zero_weight_vertices': zero_v, 'max_influences': int(cnt.max()), 'verts_gt4_influences': int((cnt > 4).sum()),
                'weight_sum_max_dev': float(np.abs(ssum[wv] - 1).max()) if wv.any() else None,
                'weight_on_nondeform_groups': nondef,
                'bones_without_weight': sorted(n for n, c in bone_vcount.items() if c == 0)}
R['weights']['bones_without_weight_unexpected'] = [n for n in R['weights']['bones_without_weight'] if n not in ALLOWED_ZERO_BONES]
P('weights', json.dumps(R['weights']))

# ------------------------------------------------------------ retarget (bone-name based) -> list of per-frame pose dicts
bones = rig.data.bones
r_rest = {b.name: rig.matrix_world @ b.matrix_local for b in bones}
def depth(b):
    d = 0
    while b.parent: b = b.parent; d += 1
    return d
order = sorted(bones, key=depth)
def retarget(path):
    before = set(bpy.data.objects); bpy.ops.import_scene.fbx(filepath=path)
    new_ = [o for o in bpy.data.objects if o not in before]
    src = [o for o in new_ if o.type == 'ARMATURE'][0]; act = src.animation_data.action
    sn = [b.name for b in src.data.bones]; mapped = [n for n in sn if n in r_rest]
    sw = src.matrix_world; s_rest = {n: sw @ src.data.bones[n].matrix_local for n in mapped}
    hscale = r_rest['mixamorig:Hips'].translation.z / s_rest['mixamorig:Hips'].translation.z
    f0 = int(act.frame_range[0]); f1 = int(act.frame_range[1]); nfr = f1 - f0 + 1
    stride = 1 if nfr <= MAXF else int(math.ceil((nfr - 1) / (MAXF - 1)))
    frames = list(range(f0, f1 + 1, stride))
    if frames[-1] != f1: frames.append(f1)
    hips_rest_t = s_rest['mixamorig:Hips'].translation.copy(); out = []
    for f in frames:
        bpy.context.scene.frame_set(f); s_pose = {n: sw @ src.pose.bones[n].matrix for n in mapped}; pose_w = {}; fr = {}
        for b in order:
            n = b.name; par = b.parent
            chain = r_rest[n] if par is None else pose_w[par.name] @ r_rest[par.name].inverted() @ r_rest[n]
            if n in s_pose:
                dR = s_pose[n].to_3x3() @ s_rest[n].to_3x3().inverted(); tgt = (dR @ r_rest[n].to_3x3()).to_4x4(); tr = chain.translation.copy()
                if n == 'mixamorig:Hips': tr = r_rest[n].translation + (s_pose[n].translation - hips_rest_t) * hscale
                tgt.translation = tr
            else: tgt = chain
            pose_w[n] = tgt
            if n in s_pose:
                basis = (r_rest[n].inverted() @ tgt) if par is None else (pose_w[par.name] @ r_rest[par.name].inverted() @ r_rest[n]).inverted() @ tgt
                fr[n] = (basis.to_quaternion(), basis.to_translation() if n == 'mixamorig:Hips' else None)
        out.append(fr)
    for o in new_: bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.orphans_purge(do_recursive=True)
    return out, frames, (f0, f1), len(mapped), len(sn)
def set_pose(fr):
    for pb in rig.pose.bones: pb.rotation_mode = 'QUATERNION'; pb.rotation_quaternion = (1, 0, 0, 0); pb.location = (0, 0, 0)
    if fr:
        for n, (q, loc) in fr.items():
            pb = rig.pose.bones[n]; pb.rotation_quaternion = q
            if loc is not None: pb.location = loc
    bpy.context.view_layer.update()
def eval_co():
    ev = body.evaluated_get(bpy.context.evaluated_depsgraph_get()); m2 = ev.to_mesh(); c = get_co(m2); ev.to_mesh_clear(); return c

# ------------------------------------------------------------ edge stretch / compression
set_pose(None)
rest = get_co(me); ed = np.empty(len(me.edges) * 2, dtype=np.int32); me.edges.foreach_get('vertices', ed); ed = ed.reshape(-1, 2)
L0 = np.linalg.norm(rest[ed[:, 0]] - rest[ed[:, 1]], axis=1)
Hh = rest[:, 2].max() - rest[:, 2].min(); ok = L0 > 1e-6 * Hh; big = L0 > 0.002          # 'big' = rest edge longer than 2 mm (ignores sliver edges of TRELLIS meshes)
R['rest_edges_used'] = int(ok.sum()); R['rest_edges_gt2mm'] = int(big.sum())
clips = {}; R['clips'] = {}; viol = np.zeros(len(L0), bool)
topb = np.zeros(nv, np.int32); _dn = {n: i for i, n in enumerate(deform_bones)}
for v in me.vertices:
    if len(v.groups): g = max(v.groups, key=lambda x: x.weight); topb[v.index] = _dn.get(gname[g.group], 0)
for cname, path in CLIPS:
    t1 = time.time()
    try: fr, frames, rng, nm, ns = retarget(path)
    except Exception as e: P('CLIP ERROR', cname, repr(e)); R['clips'][cname] = {'error': repr(e)}; continue
    clips[cname] = (fr, frames)
    worst_e = None; mx = 0; mn = 9; mxb = 0; mnb = 9; ge2 = 0; le05 = 0; worst = None; vmx = None
    for k, f in enumerate(fr):
        set_pose(f); c = eval_co()
        L1 = np.linalg.norm(c[ed[:, 0]] - c[ed[:, 1]], axis=1); ratio = np.ones_like(L0); ratio[ok] = L1[ok] / L0[ok]
        rmax = ratio.max(); rmin = ratio.min(); viol |= (ratio > 2.0) | (ratio < 0.5)
        if rmax > mx: mx = rmax; ew = int(ratio.argmax()); worst = (frames[k], rest[ed[ew, 0]].round(3).tolist())
        mn = min(mn, rmin)
        if worst_e is None or rmax > worst_e[0]:
            wv_ = np.argsort(-np.maximum(ratio, 1.0 / np.maximum(ratio, 1e-6)))[:8]
            worst_e = (rmax, [(int(e), cname, frames[k], round(float(ratio[e]), 2), round(float(L0[e]), 4), rest[ed[e, 0]].round(3).tolist()) for e in wv_])
        mxb = max(mxb, ratio[big].max()); mnb = min(mnb, ratio[big].min())
        ge2 = max(ge2, int((ratio > 2.0).sum())); le05 = max(le05, int((ratio < 0.5).sum()))
        if not np.isfinite(c).all(): P('NONFINITE coordinates in', cname, frames[k])
    R['clips'][cname] = {'file': os.path.basename(path), 'src_range': rng, 'frames_evaluated': len(fr), 'matched_bones': '%d/%d' % (nm, ns),
                         'max_stretch': round(float(mx), 3), 'min_compression': round(float(mn), 3),
                         'max_stretch_gt2mm_edges': round(float(mxb), 3), 'min_compression_gt2mm_edges': round(float(mnb), 3),
                         'max_edges_over_2x_in_a_frame': ge2, 'max_edges_under_0.5x_in_a_frame': le05, 'worst_stretch_frame_and_rest_xyz': worst}
    R['clips'][cname]['worst_edges(e,clip,frame,ratio,restLen,restXYZ)'] = worst_e[1]
    P(cname, json.dumps(R['clips'][cname]), '%.1fs' % (time.time() - t1))
allc = [v for v in R['clips'].values() if 'max_stretch' in v]
_vb = {}
for e in np.where(viol)[0]: _vb[deform_bones[topb[ed[e, 0]]]] = _vb.get(deform_bones[topb[ed[e, 0]]], 0) + 1
R['violating_edges_any_frame'] = int(viol.sum()); R['violating_edges_by_top_bone'] = dict(sorted(_vb.items(), key=lambda kv: -kv[1])[:12])
# connected shells (eyes, teeth, lashes... are separate shells in MakeHuman exports)
_bm = bmesh.new(); _bm.from_mesh(me); _lab = -np.ones(nv, np.int32); _nl = 0
for _v0 in _bm.verts:
    if _lab[_v0.index] >= 0: continue
    _st = [_v0]; _lab[_v0.index] = _nl
    while _st:
        _x = _st.pop()
        for _e in _x.link_edges:
            _o = _e.other_vert(_x)
            if _lab[_o.index] < 0: _lab[_o.index] = _nl; _st.append(_o)
    _nl += 1
_bm.free()
_sz = np.bincount(_lab); _ev = _lab[ed[viol, 0]]; _vs = np.bincount(_ev, minlength=_nl)
_big = np.argsort(-_vs)[:6]
R['violating_edges_by_shell'] = [dict(shell_verts=int(_sz[k]), mean_z=round(float(rest[_lab == k][:, 2].mean()), 2), violating_edges=int(_vs[k])) for k in _big if _vs[k] > 0]
R['shells'] = int(_nl)
P('shells', _nl, 'violations by shell', json.dumps(R['violating_edges_by_shell']))
P('violating edges (any frame, any clip):', int(viol.sum()), json.dumps(R['violating_edges_by_top_bone']))
R['summary'] = {'max_stretch': max(v['max_stretch'] for v in allc), 'min_compression': min(v['min_compression'] for v in allc),
                'max_stretch_gt2mm_edges': max(v['max_stretch_gt2mm_edges'] for v in allc), 'min_compression_gt2mm_edges': min(v['min_compression_gt2mm_edges'] for v in allc)}
w = R['weights']; s = R['summary']
R['gate'] = {'stretch<=2.0': s['max_stretch'] <= 2.0, 'compression>=0.5': s['min_compression'] >= 0.5, 'zero_weight_verts==0': w['zero_weight_vertices'] == 0,
             'max_infl<=4': w['max_influences'] <= 4, 'sum_dev<=1e-3': (w['weight_sum_max_dev'] is not None and w['weight_sum_max_dev'] <= 1e-3),
             'only_toe_end_bones_unweighted': len(w['bones_without_weight_unexpected']) == 0}
R['gate']['PASS_all'] = all(R['gate'].values())
P('SUMMARY', json.dumps(R['summary']), 'GATE', json.dumps(R['gate']))

# ------------------------------------------------------------ stills (workbench, 800x800)
if STILLS and clips:
    os.makedirs(STILLS, exist_ok=True); scn = bpy.context.scene
    scn.render.engine = 'BLENDER_WORKBENCH'; scn.render.resolution_x = 800; scn.render.resolution_y = 800; scn.render.resolution_percentage = 100
    scn.render.image_settings.file_format = 'PNG'
    sh = scn.display.shading; sh.light = 'STUDIO'; sh.color_type = 'SINGLE'; sh.single_color = (0.78, 0.64, 0.56); sh.show_cavity = False
    scn.world = bpy.data.worlds.new('w'); scn.world.color = (0.18, 0.2, 0.24)
    rig.hide_render = True
    cd = bpy.data.cameras.new('cam'); cam = bpy.data.objects.new('cam', cd); scn.collection.objects.link(cam); cd.type = 'ORTHO'; scn.camera = cam
    def shot(name, loc, target, scale):
        cam.location = loc; cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler(); cd.ortho_scale = scale
        scn.render.filepath = os.path.join(STILLS, name + '.png'); bpy.ops.render.render(write_still=True)
    tag = os.path.splitext(os.path.basename(BLEND))[0]
    plan = [('1_rest', None), ('2_idle_mid', 'Idle'), ('3_walk_mid', 'Walk'), ('4_attack_mid', 'Attack')]
    for nm, cn in plan:
        if cn: fr, frames = clips[cn]; set_pose(fr[len(fr) // 2])
        else: set_pose(None)
        shot('%s_%s' % (tag, nm), (0, -8, 0.9), (0, 0, 0.9), 2.6)
        if cn == 'Attack' or cn is None:
            lh = rig.matrix_world @ rig.pose.bones['mixamorig:LeftHand'].head
            shot('%s_%s_lefthand' % (tag, nm), (lh.x, lh.y - 3, lh.z), tuple(lh), 0.6)
    P('stills ->', STILLS)
R['time_s'] = round(time.time() - T0, 1)
with open(OUTJ, 'w') as fh: json.dump(R, fh, indent=1)
P('DONE %.1fs' % (time.time() - T0))
