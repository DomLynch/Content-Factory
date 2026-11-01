import bpy, bmesh, math, os, sys, json, time
import numpy as np
from mathutils import Vector, Matrix, kdtree

T0 = time.time()
def P(*a): print('R>', *a, flush=True)

# ------------------------------------------------------------------ args
argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
pos = []; opt = {}
i = 0
while i < len(argv):
    a = argv[i]
    if a.startswith('--'):
        if a in ('--no-tpose', '--no-armfit', '--no-finger-straighten'): opt[a] = True
        else: opt[a] = argv[i + 1]; i += 1
    else: pos.append(a)
    i += 1
if len(pos) != 2:
    print('usage: blender --background --python bind_donor.py -- <mesh.glb> <out.blend> [--donor auto|male|female|brute|dwarf|giant|mage|ogre|child|tall]'); sys.exit(2)
MESH, OUT = pos
RIG = opt.get('--rig', r'C:\HANDOVER-Macbook\extracted\batch1-2\dev\demon2-build\ref\psyche-106-v3.glb')
LIB = r'C:\HANDOVER-Macbook\extracted\batch2-10\Mixamo-Animations-XBot-FBX30\Individual'
IDLE = opt.get('--idle', LIB + r'\107900901--Breathing Idle.fbx')
ATTACK = opt.get('--attack', LIB + r'\121360901--Zombie Attack.fbx')
MAXF = int(opt.get('--maxf', 80))
FINGER_MODE = opt.get('--finger-pass', 'auto')
REPORT = {'mesh': MESH, 'out': OUT}

def smooth(x): x = np.clip(x, 0.0, 1.0); return x * x * (3 - 2 * x)
def get_co(me):
    a = np.empty(len(me.vertices) * 3, dtype=np.float32); me.vertices.foreach_get('co', a); return a.reshape(-1, 3).astype(np.float64)
def set_co(me, a):
    me.vertices.foreach_set('co', a.astype(np.float32).ravel()); me.update()
def sel_only(objs, active=None):
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs: o.select_set(True)
    bpy.context.view_layer.objects.active = active or objs[0]

def is_body_bone(n): return n.startswith('mixamorig:') or '_twist_' in n

# ------------------------------------------------------------------ 1. rig
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=RIG)
rig = [o for o in bpy.data.objects if o.type == 'ARMATURE'][0]
for o in list(bpy.data.objects):
    if o is not rig: bpy.data.objects.remove(o, do_unlink=True)      # sword (Steel), eyelashes, empties, custom-shape icosphere
for c in list(bpy.data.collections):
    if c.name == 'glTF_not_exported': bpy.data.collections.remove(c)
for a in list(bpy.data.actions): bpy.data.actions.remove(a)
rig.parent = None; rig.matrix_basis = Matrix.Identity(4)
rig.name = 'Rig'
bones = rig.data.bones
body_bones = [b.name for b in bones if is_body_bone(b.name)]
non_body = [b.name for b in bones if not is_body_bone(b.name)]
P('rig: %d bones, %d body (deform), %d non-body (use_deform -> False)' % (len(bones), len(body_bones), len(non_body)))
RIG_H = bones['mixamorig:Head'].tail_local.z
if '--height' in opt: RIG_H = float(opt['--height'])

# ------------------------------------------------------------------ 2. mesh
before = set(bpy.data.objects)
bpy.ops.import_scene.gltf(filepath=MESH, merge_vertices=True)
new = [o for o in bpy.data.objects if o not in before]
meshes = [o for o in new if o.type == 'MESH' and len(o.data.polygons) > 0 and o.name != 'Icosphere'
          and 'glTF_not_exported' not in [c.name for c in o.users_collection]]
P('mesh glb objects:', [(o.type, o.name) for o in new], '-> using', [(o.name, len(o.data.vertices)) for o in meshes])
if not meshes: P('ERROR no mesh in', MESH); sys.exit(3)
bpy.context.view_layer.update()
for o in meshes:
    sel_only([o])
    if o.data.shape_keys: bpy.ops.object.shape_key_remove(all=True, apply_mix=True)
    o.data.transform(o.matrix_world)
    o.parent = None; o.matrix_basis = Matrix.Identity(4)
    for m in list(o.modifiers): o.modifiers.remove(m)
    o.vertex_groups.clear()
for o in new:
    if o not in meshes: bpy.data.objects.remove(o, do_unlink=True)
sel_only(meshes)
if len(meshes) > 1: bpy.ops.object.join()
body = bpy.context.view_layer.objects.active
body.name = 'Body'; body.data.name = 'Body'
me = body.data
_bm = bmesh.new(); _bm.from_mesh(me); _n0 = len(_bm.verts)
bmesh.ops.remove_doubles(_bm, verts=_bm.verts, dist=1e-5); _bm.to_mesh(me); _bm.free(); me.update()
P('merge-by-distance 1e-5: verts', _n0, '->', len(me.vertices))
nv = len(me.vertices)
P('mesh joined: verts', nv, 'polys', len(me.polygons))
REPORT['verts'] = nv
if nv > 250000:
    P('WARNING %d verts: bone-heat will be slow/fail; decimating to ~150k' % nv)
    md = body.modifiers.new('dec', 'DECIMATE'); md.ratio = 150000.0 / nv
    sel_only([body]); bpy.ops.object.modifier_apply(modifier=md.name)
    me = body.data; nv = len(me.vertices); P('after decimate', nv)

# ------------------------------------------------------------------ 3. upright / yaw / facing / scale / floor
co = get_co(me)
if '--rotate-z' in opt:
    a = math.radians(float(opt['--rotate-z'])); R = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]]); co = co @ R.T
dims = co.max(0) - co.min(0)
if dims[1] > dims[2] * 1.2 and dims[1] > dims[0]:
    P('mesh is Y-long (%s) -> rotating +90 about X' % np.round(dims, 2)); R = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]]); co = co @ R.T
zmin, zmax = co[:, 2].min(), co[:, 2].max(); H = zmax - zmin
foot = co[co[:, 2] < zmin + 0.03 * H][:, 1].mean()
shin = co[(co[:, 2] > zmin + 0.14 * H) & (co[:, 2] < zmin + 0.19 * H)][:, 1].mean()
conf = abs(foot - shin) / H
if foot > shin:
    P('facing: toes toward +Y (foot-shin %.3f m) -> yaw 180 to face -Y' % (foot - shin)); co[:, 0] *= -1; co[:, 1] *= -1
else: P('facing: toes toward -Y already (foot-shin %.3f m)' % (foot - shin))
if conf < 0.01: P('WARNING facing check low confidence (%.4f of height); verify renders' % conf)
REPORT['facing_confidence'] = round(float(conf), 4)
sc = RIG_H / H
co *= sc
zmin = co[:, 2].min(); H = co[:, 2].max() - zmin
low = co[:, 2] < zmin + 0.04 * H
rig_foot_y = np.mean([bones[n].head_local.y for n in ('mixamorig:LeftFoot', 'mixamorig:LeftToeBase', 'mixamorig:RightFoot', 'mixamorig:RightToeBase')])
cx = co[co[:, 2] < zmin + 0.45 * H][:, 0].mean()
co[:, 0] -= cx; co[:, 1] += rig_foot_y - co[low][:, 1].mean(); co[:, 2] -= zmin
P('scale x%.4f -> height %.3f m (rig target %.3f), centred x by %.3f, feet on z=0' % (sc, H, RIG_H, cx))
REPORT.update(scale=round(float(sc), 4))

# ------------------------------------------------------------------ 4. arms -> straight T-pose along the rig's arm line (centreline "unbend")
# For each arm: estimate the arm centreline from the mesh (centroids of distance shells around the rig's shoulder joint),
# then re-lay it as a straight line from the rig shoulder to the rig middle-finger tip and carry vertices with their nearest
# centreline segment (rigid rotation of each segment onto the +/-X axis, scaled so the arm length matches the rig).
def rodrigues(o, k, th):
    c = np.cos(th)[:, None]; s = np.sin(th)[:, None]
    return o * c + np.cross(k, o) * s + k * (k * o).sum(1)[:, None] * (1 - c)
arm_rep = {}
for side, pre in ((1, 'Left'), (-1, 'Right')):
    Pv = np.array(bones['mixamorig:%sArm' % pre].head_local[:])
    tip_t = np.array(bones['mixamorig:%sHandMiddle4' % pre].tail_local[:])
    cand = np.where((side * co[:, 0] > 0.20) & (co[:, 2] > 0.3 * H))[0]
    r = np.linalg.norm(co[cand] - Pv, axis=1)
    BW = 0.03; nb_ = int(r.max() / BW) + 1; cl = [Pv]; dirv = None
    for j in range(1, nb_):                      # track the arm tube shell by shell (robust against torso/legs in the same shell)
        m = (r >= j * BW) & (r < (j + 1) * BW)
        pj = co[cand[m]]
        if len(pj) < 3:
            if dirv is None: continue
            break
        if dirv is None: pj = pj[side * pj[:, 0] > (side * pj[:, 0]).max() - 0.10]      # first shell: most lateral verts = arm
        else:
            pj = pj[np.linalg.norm(pj - (cl[-1] + dirv * BW), axis=1) < 0.10]
            if len(pj) < 3: break
        c = pj.mean(0); nd = c - cl[-1]; nd /= max(np.linalg.norm(nd), 1e-9)
        dirv = nd if dirv is None else (0.5 * dirv + 0.5 * nd) / max(np.linalg.norm(0.5 * dirv + 0.5 * nd), 1e-9)
        cl.append(c)
    cl = np.array(cl)
    if len(cl) < 6: arm_rep[pre] = 'centreline failed (%d pts)' % len(cl); continue
    if len(cl) > 4: cl[1:-1] = (cl[:-2] + cl[1:-1] * 2 + cl[2:]) / 4
    dseg = cl[1:] - cl[:-1]; ln = np.linalg.norm(dseg, axis=1); dseg /= ln[:, None]
    ds = np.array([dseg[max(0, i - 2):i + 3].mean(0) for i in range(len(dseg))]); ds /= np.linalg.norm(ds, axis=1)[:, None]
    tdir = (tip_t - Pv) / np.linalg.norm(tip_t - Pv)
    if not (0.55 < ln.sum() / np.linalg.norm(tip_t - Pv) < 1.5):
        P('WARNING %s arm centreline length %.2f m vs rig arm %.2f m: implausible, arm NOT straightened (use a T-posed mesh or --no-tpose)' % (pre, ln.sum(), np.linalg.norm(tip_t - Pv)))
        arm_rep[pre] = {'action': 'SKIPPED implausible centreline', 'arm_len_mesh': round(float(ln.sum()), 3)}; continue
    dev = np.degrees(np.arccos(np.clip(ds @ tdir, -1, 1)))
    info = {'centreline_pts': len(cl), 'arm_len_mesh': round(float(ln.sum()), 3), 'arm_len_rig': round(float(np.linalg.norm(tip_t - Pv)), 3),
            'max_dev_from_T_deg': round(float(dev.max()), 1), 'mean_dev_deg': round(float(dev.mean()), 1)}
    if opt.get('--no-tpose') or dev.max() < 6.0:
        info['action'] = 'none (already T or --no-tpose)'; arm_rep[pre] = info; continue
    sc_ = float(np.clip(np.linalg.norm(tip_t - Pv) * 0.99 / ln.sum(), 0.8, 1.25))
    cum = np.concatenate([[0], np.cumsum(ln)]); tgt = Pv + tdir[None] * (cum * sc_)[:, None]
    # nearest centreline segment for every arm-ish vertex
    sel = np.where((side * co[:, 0] > 0.08) & (co[:, 2] > 0.3 * H) & (np.linalg.norm(co - Pv, axis=1) < ln.sum() * 1.3 + 0.15))[0]
    pts = co[sel]; A_, B_ = cl[:-1], cl[1:]; AB = B_ - A_
    d = np.empty((len(pts), len(AB))); uu = np.empty_like(d)
    for j in range(len(AB)):
        u = np.clip(((pts - A_[j]) @ AB[j]) / (AB[j] @ AB[j]), 0, 1); uu[:, j] = u
        d[:, j] = np.linalg.norm(pts - (A_[j] + u[:, None] * AB[j]), axis=1)
    jn = d.argmin(1); dn = d[np.arange(len(pts)), jn]; un = uu[np.arange(len(pts)), jn]
    arc = cum[jn] + un * ln[jn]
    on = A_[jn] + un[:, None] * AB[jn]
    # hand + fingers: carried RIGIDLY with the wrist (fingers splay/curl differently, a single centreline would shear them into spikes)
    aw = 0.71 * cum[-1]                                # rig wrist sits at 71% of shoulder->fingertip
    jw = int(np.searchsorted(cum, aw) - 1); jw = min(max(jw, 0), len(ln) - 1); uw = (aw - cum[jw]) / ln[jw]
    cw = A_[jw] + uw * AB[jw]
    hand = (arc > aw) & (dn < 0.11)
    jn = np.where(hand, jw, jn); on[hand] = cw; arc = np.where(hand, aw, arc)
    kk = np.cross(ds[jn], tdir[None]); sn = np.linalg.norm(kk, axis=1); th = np.arctan2(sn, ds[jn] @ tdir)
    kk = kk / np.maximum(sn, 1e-9)[:, None]
    newp = (Pv + tdir[None] * (arc * sc_)[:, None]) + sc_ * rodrigues(pts - on, kk, th)
    dn = np.where(hand, np.minimum(dn, 0.06), dn)       # hand verts are all-in
    tmem = smooth((arc + 0.03) / 0.10) * (1 - smooth((dn - 0.06) / 0.07))
    co[sel] = pts + tmem[:, None] * (newp - pts)
    info.update(action='unbent to T', arm_scale=round(sc_, 3), moved_verts=int((tmem > 0.01).sum()))
    arm_rep[pre] = info
P('arms:', json.dumps(arm_rep))
REPORT['arms'] = arm_rep
set_co(me, co)
bb = co.max(0) - co.min(0)
P('aligned mesh dims', np.round(bb, 3).tolist(), '(rig fingertip-to-fingertip %.3f)' % (2 * abs(bones['mixamorig:LeftHandMiddle4'].tail_local.x)))
