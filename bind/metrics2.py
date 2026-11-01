"""
metrics2.py - NEW GATE for a bound .blend (objects 'Rig' + 'Body').

  blender --background --python metrics2.py -- <bound.blend> <out.json> [--control <control.metrics.json>] [--stills DIR] [--maxframes 1000]

Clips (Mixamo XBot library, retargeted by mixamorig:* bone name, EVERY frame): Idle 107900901 Breathing Idle, Walk 100440903 Walking, Attack 121360901 Zombie Attack.

GATE (all required)
 (a) only edges with REST length >= 5 mm; each edge weighted by the summed REST area of its adjacent faces (area-weighted quantiles)
 (b) area-weighted p99 stretch (current/rest edge length) <= 2.0 and area-weighted p01 of the ratio (= p99 compression) >= 0.5, samples pooled over all frames of the 3 clips
 (c) relative to the DONOR CONTROL json: p99 stretch <= control*1.10 and p01 ratio >= control*0.90   (skipped when no --control is given)
 plus: zero-weight vertices == 0, max influences <= 4, weight sums within 1e-3. Unweighted bones are reported (Toe_End is allowed by convention).
 Max edge numbers are diagnostics only.

REGIONS (violating edge = measured edge whose ratio leaves [0.5, 2.0] in any frame; edge assigned by its REST midpoint, first match wins, rig rest joints):
 hands    : within 0.22 m of LeftHand/RightHand head   (the fingertip is 0.20 m from the wrist joint)
 feet     : within 0.25 m of LeftFoot/RightFoot head   (reaches the toe tips)
 neck     : within 0.10 m of the Neck head
 shoulders: within 0.13 m of LeftArm/RightArm head
 crotch   : within 0.12 m of the midpoint of the two UpLeg heads
 other    : everything else
"""
import bpy, bmesh, os, sys, json, time, math
import numpy as np
from mathutils import Vector, Matrix

def P(*a): print('M2>', *a, flush=True)
argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
pos = []; opt = {}; i = 0
while i < len(argv):
    if argv[i].startswith('--'): opt[argv[i]] = argv[i + 1]; i += 2
    else: pos.append(argv[i]); i += 1
BLEND, OUTJ = [os.path.abspath(x) for x in pos]
MAXF = int(opt.get('--maxframes', 1000)); STILLS = os.path.abspath(opt['--stills']) if '--stills' in opt else None
CONTROL = json.load(open(opt['--control'])) if '--control' in opt else None
LIB = r'C:\HANDOVER-Macbook\extracted\batch2-10\Mixamo-Animations-XBot-FBX30\Individual'
CLIPS = [('Idle', LIB + r'\107900901--Breathing Idle.fbx'), ('Walk', LIB + r'\100440903--Walking.fbx'), ('Attack', LIB + r'\121360901--Zombie Attack.fbx')]
ALLOWED_ZERO_BONES = {'mixamorig:LeftToe_End', 'mixamorig:RightToe_End'}
T0 = time.time()

bpy.ops.wm.open_mainfile(filepath=BLEND)
rig = bpy.data.objects['Rig']; body = bpy.data.objects['Body']; me = body.data; nv = len(me.vertices)
R0 = {}
if not any(m.type == 'ARMATURE' and m.object == rig for m in body.modifiers):
    md_ = body.modifiers.new('Armature', 'ARMATURE'); md_.object = rig; body.parent = rig; body.matrix_parent_inverse = Matrix.Identity(4)
    R0['note'] = 'no armature modifier on Body; added by metrics2.py'
def get_co(m):
    a = np.empty(len(m.vertices) * 3, dtype=np.float32); m.vertices.foreach_get('co', a); return a.reshape(-1, 3).astype(np.float64)
deform_bones = [b.name for b in rig.data.bones if b.use_deform]
R = {**R0, 'blend': BLEND, 'verts': nv, 'edges': len(me.edges), 'faces': len(me.polygons)}

# ---------------- weights
gname = {g.index: g.name for g in body.vertex_groups}
cnt = np.zeros(nv, np.int32); ssum = np.zeros(nv); bvc = {n: 0 for n in deform_bones}
for v in me.vertices:
    c = 0; s = 0.0
    for g in v.groups:
        if g.weight > 1e-6:
            c += 1; s += g.weight; n = gname[g.group]
            if n in bvc: bvc[n] += 1
    cnt[v.index] = c; ssum[v.index] = s
wv = cnt > 0
unw = sorted(n for n, c in bvc.items() if c == 0)
R['weights'] = {'zero_weight_vertices': int((cnt == 0).sum()), 'max_influences': int(cnt.max()),
                'weight_sum_max_dev': float(np.abs(ssum[wv] - 1).max()) if wv.any() else None,
                'unweighted_bones': unw, 'unweighted_bones_other_than_toe_end': [n for n in unw if n not in ALLOWED_ZERO_BONES]}

# ---------------- rest edges, area weights, regions
rest = get_co(me)
ed = np.empty(len(me.edges) * 2, dtype=np.int32); me.edges.foreach_get('vertices', ed); ed = ed.reshape(-1, 2)
L0 = np.linalg.norm(rest[ed[:, 0]] - rest[ed[:, 1]], axis=1)
bm = bmesh.new(); bm.from_mesh(me); bm.edges.ensure_lookup_table()
EW = np.array([sum(f.calc_area() for f in e.link_faces) for e in bm.edges]); bm.free()
meas = L0 >= 0.005
Em = np.where(meas)[0]; Wt = EW[Em]; Wt = Wt / Wt.sum()
R['edges_measured_ge_5mm'] = int(meas.sum())
bn = rig.data.bones
def hd(n): return np.array(bn[n].head_local[:])
mid = 0.5 * (rest[ed[Em, 0]] + rest[ed[Em, 1]])
def dmin(cs): return np.min([np.linalg.norm(mid - c, axis=1) for c in cs], axis=0)
reg = np.full(len(Em), 5)         # 0 hands 1 feet 2 neck 3 shoulders 4 crotch 5 other
rn = ['hands', 'feet', 'neck', 'shoulders', 'crotch', 'other']
tests = [dmin([hd('mixamorig:LeftHand'), hd('mixamorig:RightHand')]) <= 0.22, dmin([hd('mixamorig:LeftFoot'), hd('mixamorig:RightFoot')]) <= 0.25,
         dmin([hd('mixamorig:Neck')]) <= 0.10, dmin([hd('mixamorig:LeftArm'), hd('mixamorig:RightArm')]) <= 0.13,
         dmin([0.5 * (hd('mixamorig:LeftUpLeg') + hd('mixamorig:RightUpLeg'))]) <= 0.12]
for k in range(4, -1, -1): reg[tests[k]] = k

# ---------------- retarget (same code as bind_to_106 bake_clip, per-frame pose dicts)
r_rest = {b.name: rig.matrix_world @ b.matrix_local for b in bn}
def depth(b):
    d = 0
    while b.parent: b = b.parent; d += 1
    return d
order = sorted(bn, key=depth)
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
def wq(vals, w, q):
    o = np.argsort(vals, kind='stable'); cw = np.cumsum(w[o]); return float(vals[o][min(np.searchsorted(cw, q * cw[-1]), len(o) - 1)])

set_pose(None); i0 = ed[Em, 0]; i1 = ed[Em, 1]; L0m = L0[Em]
viol = np.zeros(len(Em), bool); clips = {}; R['clips'] = {}; allr = []; allw = []
for cname, path in CLIPS:
    t1 = time.time(); fr, frames, rng, nm, ns = retarget(path); clips[cname] = (fr, frames)
    rs = []
    for f in fr:
        set_pose(f); c = eval_co(); ratio = np.linalg.norm(c[i0] - c[i1], axis=1) / L0m
        viol |= (ratio > 2.0) | (ratio < 0.5); rs.append(ratio.astype(np.float32))
    rs = np.concatenate(rs); ws = np.tile(Wt.astype(np.float32), len(fr)); allr.append(rs); allw.append(ws)
    R['clips'][cname] = {'file': os.path.basename(path), 'frames': len(fr), 'matched_bones': '%d/%d' % (nm, ns), 'p99_stretch': round(wq(rs, ws, 0.99), 3), 'p01_ratio': round(wq(rs, ws, 0.01), 3),
                         'diag_max_stretch': round(float(rs.max()), 2), 'diag_min_ratio': round(float(rs.min()), 3)}
    P(cname, json.dumps(R['clips'][cname]), '%.1fs' % (time.time() - t1))
allr = np.concatenate(allr); allw = np.concatenate(allw)
p99s = wq(allr, allw, 0.99); p01c = wq(allr, allw, 0.01)
R['p99_stretch'] = round(p99s, 4); R['p99_compression_ratio'] = round(p01c, 4)      # compression is reported as the ratio (>= 0.5 required)
R['diag_max_stretch'] = round(float(allr.max()), 2); R['diag_min_ratio'] = round(float(allr.min()), 4)
R['violating_edges_by_region'] = {rn[k]: int((viol & (reg == k)).sum()) for k in range(6)}
R['violating_edges_total'] = int(viol.sum()); R['violating_edge_fraction'] = round(float(viol.mean()), 4)
rel = None
if CONTROL:
    cs, cc = CONTROL['p99_stretch'], CONTROL['p99_compression_ratio']
    rel = bool(p99s <= cs * 1.10 and p01c >= cc * 0.90)
    R['control'] = {'p99_stretch': cs, 'p99_compression_ratio': cc, 'stretch_limit': round(cs * 1.10, 4), 'compression_limit': round(cc * 0.90, 4)}
w = R['weights']
G = {'abs_p99_stretch<=2.0': p99s <= 2.0, 'abs_p99_compression>=0.5': p01c >= 0.5, 'relative_to_control': rel,
     'zero_weight_verts==0': w['zero_weight_vertices'] == 0, 'max_infl<=4': w['max_influences'] <= 4, 'sum_dev<=1e-3': bool(w['weight_sum_max_dev'] is not None and w['weight_sum_max_dev'] <= 1e-3)}
R['gate'] = G; R['gate_pass'] = all(v for v in G.values() if v is not None)
R['score'] = round(max(p99s / 2.0, 0.5 / max(p01c, 1e-6)), 4)           # <1 passes the absolute part; lower is better
P('p99 stretch %.3f  p01 ratio %.3f  rel %s  regions %s  weights %s  PASS %s' % (p99s, p01c, rel, json.dumps(R['violating_edges_by_region']), json.dumps({k: v for k, v in w.items() if k != 'unweighted_bones'}), R['gate_pass']))

# ---------------- stills
if STILLS:
    os.makedirs(STILLS, exist_ok=True); scn = bpy.context.scene
    scn.render.engine = 'BLENDER_WORKBENCH'; scn.render.resolution_x = 800; scn.render.resolution_y = 800; scn.render.resolution_percentage = 100
    scn.render.image_settings.file_format = 'PNG'
    sh = scn.display.shading; sh.light = 'STUDIO'; sh.color_type = 'SINGLE'; sh.single_color = (0.78, 0.64, 0.56); sh.show_cavity = False
    scn.world = bpy.data.worlds.new('w'); scn.world.color = (0.18, 0.2, 0.24); rig.hide_render = True
    cd = bpy.data.cameras.new('cam'); cam = bpy.data.objects.new('cam', cd); scn.collection.objects.link(cam); cd.type = 'ORTHO'; scn.camera = cam
    def shot(name, loc, target, scale):
        cam.location = loc; cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler(); cd.ortho_scale = scale
        scn.render.filepath = os.path.join(STILLS, name + '.png'); bpy.ops.render.render(write_still=True)
    tag = os.path.splitext(os.path.basename(BLEND))[0]
    for nm, cn in (('1_rest', None), ('2_idle_mid', 'Idle'), ('3_walk_mid', 'Walk'), ('4_attack_mid', 'Attack')):
        if cn: fr, frames = clips[cn]; set_pose(fr[len(fr) // 2])
        else: set_pose(None)
        shot('%s_%s' % (tag, nm), (0, -8, 0.9), (0, 0, 0.9), 2.6)
    P('stills ->', STILLS)
R['time_s'] = round(time.time() - T0, 1)
with open(OUTJ, 'w') as fh: json.dump(R, fh, indent=1)
P('DONE %.1fs' % (time.time() - T0))
