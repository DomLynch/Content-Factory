"""
bind_donor.py - skin any humanoid GLB onto the 106-bone psyche rig by DONOR WEIGHT TRANSFER.

  blender --background --python bind_donor.py -- <mesh.glb> <out.blend> [--donor auto|male|female|brute|dwarf|giant|mage|ogre|child|tall]
         [--rotate-z DEG] [--height M] [--no-tpose] [--no-finger-straighten] [--smooth-passes 2] [--cache DIR]

Alignment (scale to rig height, feet on floor, facing -Y, arms unbent to T-pose) is lifted verbatim from bind2/bind_to_106.py.
Donor (MakeHuman 'Steel' body, already skinned to the same 106 names) is posed bone-by-bone to the psyche rest orientation, scaled the same way, then
weights are transferred with the bh_layers.transfer_weights recipe (k=4, 1/max(d,1e-4), top-4, renormalise), smoothed (2 passes, 80 body bones),
re-limited to 4 influences and normalised.
"""
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

# ================================================================== DONOR WEIGHT TRANSFER (new code)
DEFAULT_SMOOTH = 5
DONOR_DIR = r'C:\Users\dom12\Documents\donors\makehuman'
DONORS = ['male', 'female', 'brute', 'dwarf', 'giant', 'mage', 'ogre', 'child', 'tall']
CACHE = opt.get('--cache', r'C:\Users\dom12\Documents\agent_tests\bind3\donor_cache')
os.makedirs(CACHE, exist_ok=True)
SMOOTH_PASSES = int(opt.get('--smooth-passes', DEFAULT_SMOOTH)); SMOOTH_ALPHA = float(opt.get('--smooth-alpha', 0.5))
for b in bones: b.use_deform = is_body_bone(b.name)
cols = {n: i for i, n in enumerate(body_bones)}; NB = len(body_bones)
r_rest = {b.name: rig.matrix_world @ b.matrix_local for b in bones}
RAW_H = RIG_H / REPORT['scale']
T_DONOR = time.time()
MODE = opt.get('--mode', 'donor')      # donor | hybrid
REPORT['mode'] = MODE
FING_ = ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky')

def bone_depth(b):
    d = 0
    while b.parent: b = b.parent; d += 1
    return d

def prep_donor(name):
    """import donor, pose ALL its bones to the psyche-106 rest orientation (T-pose), evaluate the skinned body ('Steel'), scale to RIG_H, feet on floor.
    Cached as npz (positions + top-4 joint indices/weights in body_bones order)."""
    path = os.path.join(DONOR_DIR, 'mh-%s-01.glb' % name); cf = os.path.join(CACHE, 'mh-%s-01.npz' % name)
    st = os.stat(path); key = '%d_%d_v2' % (st.st_size, int(st.st_mtime))
    if os.path.exists(cf):
        z = np.load(cf, allow_pickle=False)
        if str(z['key']) == key: return dict(co=z['co'].astype(np.float64), J=z['J'].astype(np.int64), W=z['W'].astype(np.float64), raw_h=float(z['raw_h']), cached=True)
    before = set(bpy.data.objects); bpy.ops.import_scene.gltf(filepath=path)
    new_ = [o for o in bpy.data.objects if o not in before]
    arm = [o for o in new_ if o.type == 'ARMATURE'][0]
    cand = [o for o in new_ if o.type == 'MESH' and o.name.startswith('Steel') and len(o.vertex_groups) > 0]
    if not cand: raise RuntimeError('donor %s: no skinned Steel body' % name)
    dm = max(cand, key=lambda o: len(o.data.vertices))
    d_rest = {b.name: arm.matrix_world @ b.matrix_local for b in arm.data.bones}
    pose_w = {}
    for b in sorted(arm.data.bones, key=bone_depth):
        n = b.name; par = b.parent
        chain = d_rest[n] if par is None else pose_w[par.name] @ d_rest[par.name].inverted() @ d_rest[n]
        tgt = r_rest[n].to_3x3().to_4x4(); tgt.translation = chain.translation
        pose_w[n] = tgt
        pb = arm.pose.bones[n]; pb.rotation_mode = 'QUATERNION'
        pb.rotation_quaternion = (chain.inverted() @ tgt).to_quaternion(); pb.location = (0, 0, 0)
    bpy.context.view_layer.update()
    ev = dm.evaluated_get(bpy.context.evaluated_depsgraph_get()); m2 = ev.to_mesh()
    c = get_co(m2); ev.to_mesh_clear()
    mw = np.array(dm.matrix_world); c = c @ mw[:3, :3].T + mw[:3, 3]
    nd_ = len(dm.data.vertices); Wd = np.zeros((nd_, NB))
    gn = {g.index: g.name for g in dm.vertex_groups}
    for v in dm.data.vertices:
        for g in v.groups:
            k = cols.get(gn.get(g.group))
            if k is not None: Wd[v.index, k] = g.weight
    raw_h = float(c[:, 2].max() - c[:, 2].min())
    s = RIG_H / raw_h; c *= s
    zm = c[:, 2].min(); Hd = c[:, 2].max() - zm
    low_ = c[:, 2] < zm + 0.04 * Hd
    cx_ = c[c[:, 2] < zm + 0.45 * Hd][:, 0].mean()
    c[:, 0] -= cx_; c[:, 1] += rig_foot_y - c[low_][:, 1].mean(); c[:, 2] -= zm
    J = np.argsort(-Wd, axis=1)[:, :4]; Wt = np.take_along_axis(Wd, J, 1); Wt /= np.maximum(Wt.sum(1, keepdims=True), 1e-9)
    np.savez(cf, key=key, co=c.astype(np.float32), J=J.astype(np.int16), W=Wt.astype(np.float32), raw_h=raw_h)
    for o in new_: bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.orphans_purge(do_recursive=True)
    return dict(co=c, J=J, W=Wt, raw_h=raw_h, cached=False)

def features(c, arm_len=None, raw_h=None):
    """proportion descriptor of an aligned (rig-space, T-posed, height = RIG_H) mesh"""
    z0 = c[:, 2].min(); H_ = c[:, 2].max() - z0
    shz = bones['mixamorig:LeftArm'].head_local.z; shx = bones['mixamorig:LeftArm'].head_local.x; hipz = bones['mixamorig:Hips'].head_local.z
    def sl(zc, hw): return c[np.abs(c[:, 2] - zc) < hw]
    s_ = sl(shz - 0.09 * H_, 0.012 * H_)
    chest_w = np.percentile(s_[:, 0], 99.5) - np.percentile(s_[:, 0], 0.5); chest_d = np.percentile(s_[:, 1], 99) - np.percentile(s_[:, 1], 1)
    h_ = sl(hipz, 0.015 * H_); hip_w = np.percentile(h_[:, 0], 99.5) - np.percentile(h_[:, 0], 0.5)
    crotch = 0.5 * H_
    for zc in np.arange(0.25 * H_, 0.7 * H_, 0.01 * H_):
        if (np.abs(c[:, 0]) < 0.015 * H_)[np.abs(c[:, 2] - z0 - zc) < 0.005 * H_].sum() >= 3: crotch = zc; break
    band = c[np.abs(c[:, 2] - shz) < 0.08 * H_]
    if arm_len is None: arm_len = np.abs(band[:, 0]).max() - shx
    f = dict(chest_w=chest_w / H_, chest_d=chest_d / H_, hip_w=hip_w / H_, crotch=crotch / H_, arm=arm_len / H_)
    if raw_h is not None and 0.7 < raw_h < 3.2: f['height'] = raw_h
    return f

# ---- donors
donors = {}
for dn in DONORS: donors[dn] = prep_donor(dn)
P('donors prepared/loaded in %.1fs (cached: %s)' % (time.time() - T_DONOR, [d for d in DONORS if donors[d]['cached']]))
dfeat = {}
for dn in DONORS:
    dd = donors[dn]; dfeat[dn] = features(dd['co'], None, dd['raw_h'])
tco = get_co(me)
arm_l = [arm_rep[p].get('arm_len_mesh') for p in ('Left', 'Right') if isinstance(arm_rep.get(p), dict) and 'arm_len_mesh' in arm_rep[p]]
arm_len_t = float(np.mean(arm_l)) if arm_l else None
tfeat = features(tco, arm_len_t, RAW_H)
keys = [k for k in ('height', 'chest_w', 'chest_d', 'hip_w', 'crotch', 'arm') if k in tfeat]
sd = {k: max(np.std([dfeat[d][k] for d in DONORS]), 1e-6) for k in keys}
FW = {'height': 0.5}      # absolute height is only a weak cue (arbitrary mesh scale), half weight
dist = {d: float(np.sqrt(sum(FW.get(k, 1.0) * ((tfeat[k] - dfeat[d][k]) / sd[k]) ** 2 for k in keys))) for d in DONORS}
P('target features', {k: round(float(v), 4) for k, v in tfeat.items()}, '(raw height %.3f m%s)' % (RAW_H, '' if 'height' in keys else ' -> implausible for metres, height feature excluded'))
for d in DONORS: P('  donor %-7s dist %.3f  ' % (d, dist[d]) + ' '.join('%s=%.3f' % (k, dfeat[d][k]) for k in keys))
want = opt.get('--donor', 'auto')
choice = min(dist, key=dist.get) if want == 'auto' else want
P('DONOR CHOICE:', choice, '(requested %s)' % want)
REPORT.update(donor=choice, donor_distances={k: round(v, 3) for k, v in dist.items()}, target_features={k: round(float(v), 4) for k, v in tfeat.items()})
don = donors[choice]

# ---- fit the donor arms to the target arms (donor hands/fingers must land where the target's hands are, else finger verts snap onto the donor's wrist/forearm)
def fit_donor_arms(dc, tc):
    dc = dc.copy(); rep = {}
    shz_ = bones['mixamorig:LeftArm'].head_local.z; Hh = tc[:, 2].max() - tc[:, 2].min(); shx_ = abs(bones['mixamorig:LeftArm'].head_local.x)
    for side, pre in ((1, 'Left'), (-1, 'Right')):
        def band(c): m = (side * c[:, 0] > shx_) & (np.abs(c[:, 2] - shz_) < 0.10 * Hh); return m
        tt_ = tc[band(tc)]; dd_ = dc[band(dc)]
        if len(tt_) < 20 or len(dd_) < 20: rep[pre] = 'skipped'; continue
        tip_t = (side * tt_[:, 0]).max(); tip_d = (side * dd_[:, 0]).max()
        k = float((tip_t - shx_) / (tip_d - shx_))
        if not (0.7 < k < 1.4): rep[pre] = 'skipped: implausible arm length ratio %.2f (target arms not in T-pose / not arm-like)' % k; continue
        m = side * dc[:, 0] > shx_
        dc[m, 0] = side * (shx_ + (side * dc[m, 0] - shx_) * k)
        hand_t = tc[side * tc[:, 0] > tip_t - 0.12].mean(0); hand_d = dc[side * dc[:, 0] > side * side * (tip_t - 0.12)].mean(0) if False else dc[side * dc[:, 0] > tip_t - 0.12].mean(0)
        delta = hand_t - hand_d; delta[0] = 0
        ramp = np.clip((side * dc[m, 0] - shx_) / max(tip_t - shx_, 1e-6), 0, 1); ramp = ramp * ramp * (3 - 2 * ramp)
        dc[m] += ramp[:, None] * delta
        rep[pre] = dict(arm_scale_x=round(k, 3), hand_dyz=[round(float(delta[1]), 3), round(float(delta[2]), 3)])
    return dc, rep
dco_fit, arm_fit_rep = fit_donor_arms(don['co'], tco)
P('donor arm fit:', json.dumps(arm_fit_rep)); REPORT['donor_arm_fit'] = arm_fit_rep
don = dict(don); don['co'] = dco_fit

# ---- finger pre-straightening (query-only: applied to a COPY of the target coordinates, the mesh itself is restored/untouched)
def straighten_fingers(c):
    out = c.copy(); frep = {}
    for side, pre in ((1, 'Left'), (-1, 'Right')):
        hand = bones['mixamorig:%sHand' % pre]; wx = abs(hand.head_local.x)
        K, D, Ln = [], [], []
        for f in FING_:
            h = bones['mixamorig:%sHand%s1' % (pre, f)].head_local; t = bones['mixamorig:%sHand%s4' % (pre, f)].tail_local
            d = np.array(t[:]) - np.array(h[:]); Ln.append(np.linalg.norm(d)); D.append(d / Ln[-1]); K.append(np.array(h[:]))
        K, D, Ln = np.array(K), np.array(D), np.array(Ln)
        cand = np.where((side * c[:, 0] > wx) & (np.linalg.norm(c - np.array(hand.head_local[:]), axis=1) < 0.30))[0]
        if len(cand) == 0: continue
        pts = c[cand]; dd = np.empty((len(cand), 5)); tt = np.empty_like(dd)
        for j in range(5):
            v = pts - K[j]; t = np.clip(v @ D[j], -0.02, Ln[j] + 0.03); tt[:, j] = v @ D[j]
            dd[:, j] = np.linalg.norm(v - np.outer(t, D[j]), axis=1)
        a = dd.argmin(1); ok = (dd.min(1) < 0.035) & (tt[np.arange(len(cand)), a] > 0)
        for j in range(5):
            sel = np.where(ok & (a == j))[0]
            if len(sel) < 30: frep['%s_%s' % (pre, FING_[j])] = 'skipped (%d verts)' % len(sel); continue
            v = pts[sel] - K[j]; u = np.linalg.svd(v - v.mean(0), full_matrices=False)[2][0]
            if u @ D[j] < 0: u = -u
            ang = float(np.degrees(np.arccos(np.clip(u @ D[j], -1, 1))))
            if 4.0 < ang < 50.0:
                k_ = np.cross(u, D[j]); k_ /= max(np.linalg.norm(k_), 1e-9)
                out[cand[sel]] = K[j] + rodrigues(v, np.broadcast_to(k_, v.shape), np.full(len(v), np.radians(ang)))
                frep['%s_%s' % (pre, FING_[j])] = 'rotated %.1f deg (%d verts)' % (ang, len(sel))
            else: frep['%s_%s' % (pre, FING_[j])] = 'ok %.1f deg (%d verts)%s' % (ang, len(sel), '' if ang <= 4 else ' >50 not touched')
    return out, frep
if opt.get('--no-finger-straighten'): qco, frep = tco, {'disabled': True}
else: qco, frep = straighten_fingers(tco)
P('finger straighten (query only, target restored):', json.dumps(frep)); REPORT['finger_straighten'] = frep

# ---- THE RECIPE (ported from bh_layers.transfer_weights): k=4 nearest donor verts, 1/max(d,1e-4), top-4 joints, renormalise. KD tree instead of brute force.
t1 = time.time()
dco = don['co']; nd = len(dco)
Wd = np.zeros((nd, NB)); Wd[np.arange(nd)[:, None], don['J']] = don['W']
# tip-bone fix: the donors leave the *4 finger tips unweighted -> copy each finger's *3 weights to its *4 bone, renormalise (tips follow *3 under Mixamo animation)
for _pre in ('Left', 'Right'):
    for _f in FING_:
        Wd[:, cols['mixamorig:%sHand%s4' % (_pre, _f)]] = Wd[:, cols['mixamorig:%sHand%s3' % (_pre, _f)]]
Wd /= np.maximum(Wd.sum(1, keepdims=True), 1e-9)
kd = kdtree.KDTree(nd)
for i in range(nd): kd.insert(dco[i], i)
kd.balance()
NN = np.empty((nv, 4), np.int64); DD = np.empty((nv, 4))
for i in range(nv):
    r_ = kd.find_n(qco[i], 4)
    for q in range(4): NN[i, q] = r_[q][1]; DD[i, q] = r_[q][2]
Ws = 1.0 / np.maximum(DD, 1e-4); Ws /= Ws.sum(1, keepdims=True)
W = np.zeros((nv, NB), np.float32)
for s0 in range(0, nv, 20000):
    sl_ = slice(s0, s0 + 20000)
    for q in range(4): W[sl_] += (Ws[sl_, q:q + 1] * Wd[NN[sl_, q]]).astype(np.float32)
P('transfer: %d target verts, k=4, donor %s (%d verts), %.1fs; mean nn dist %.4f m, max %.4f m' % (nv, choice, nd, time.time() - t1, DD[:, 0].mean(), DD[:, 0].max()))
REPORT['nn_dist_mean'] = round(float(DD[:, 0].mean()), 5); REPORT['nn_dist_max'] = round(float(DD[:, 0].max()), 5)

def top4(W):
    idx = np.argsort(-W, axis=1)[:, :4]; Wl = np.zeros_like(W); rows = np.arange(len(W))[:, None]
    Wl[rows, idx] = W[rows, idx]; tot = Wl.sum(1)
    bad = tot < 1e-9
    if bad.any(): Wl[bad, 0] = 1.0
    return Wl / Wl.sum(1, keepdims=True)
W = top4(W)

# ---- smoothing: neighbour averaging on the mesh edge graph, body deform bones only (columns of W), 2 passes (vertex_group_smooth equivalent)
ed = np.empty(len(me.edges) * 2, dtype=np.int32); me.edges.foreach_get('vertices', ed); ed = ed.reshape(-1, 2)
deg = np.bincount(ed.ravel(), minlength=nv).astype(np.float32); has = deg > 0
for p_ in range(SMOOTH_PASSES):
    Wn = np.empty_like(W)
    for c_ in range(NB):
        col = W[:, c_]
        Wn[:, c_] = np.bincount(ed[:, 0], weights=col[ed[:, 1]], minlength=nv) + np.bincount(ed[:, 1], weights=col[ed[:, 0]], minlength=nv)
    Wn[has] /= deg[has, None]; Wn[~has] = W[~has]
    W = (1 - SMOOTH_ALPHA) * W + SMOOTH_ALPHA * Wn
W = top4(W.astype(np.float32)).astype(np.float64)       # <=4 influences, normalised
REPORT['smooth'] = dict(passes=SMOOTH_PASSES, alpha=SMOOTH_ALPHA)

# ---- HYBRID: bone-heat auto weights for the body shell, donor weights only near hands / shoulders / neck (smooth distance falloff)
if MODE == 'hybrid':
    sel_only([body, rig], active=rig); th_ = time.time(); bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    gn_ = {g.index: g.name for g in body.vertex_groups}; Wa = np.zeros((nv, NB), np.float32)
    for v in me.vertices:
        for g in v.groups:
            c_ = cols.get(gn_.get(g.group))
            if c_ is not None: Wa[v.index, c_] = g.weight
    unw = Wa.sum(1) < 1e-4
    P('hybrid: bone-heat %.1fs, unweighted verts %d' % (time.time() - th_, unw.sum()))
    if unw.all(): P('hybrid: bone-heat FAILED -> donor weights everywhere'); REPORT['hybrid_note'] = 'heat failed'; Wa = W.astype(np.float32)
    elif unw.any():
        good_ = np.where(~unw)[0]; kd2 = kdtree.KDTree(len(good_))
        for j_, vi_ in enumerate(good_): kd2.insert(tco[vi_], j_)
        kd2.balance()
        for vi_ in np.where(unw)[0]: Wa[vi_] = Wa[good_[kd2.find(tco[vi_])[1]]]
    body.vertex_groups.clear()
    for m_ in list(body.modifiers): body.modifiers.remove(m_)
    body.parent = None; body.matrix_basis = Matrix.Identity(4)
    Wa = top4(Wa)
    def sph_fall(centres, r_in, r_out):
        d_ = np.min([np.linalg.norm(tco - np.array(c_[:]), axis=1) for c_ in centres], axis=0); t_ = np.clip((d_ - r_in) / (r_out - r_in), 0, 1)
        return 1 - t_ * t_ * (3 - 2 * t_)
    hb_ = lambda n: bones[n].head_local
    fall = np.maximum.reduce([sph_fall([hb_('mixamorig:LeftHand'), hb_('mixamorig:RightHand')], 0.22, 0.34),      # hands + fingers (fingertip is 0.20 m from the wrist joint)
                              sph_fall([hb_('mixamorig:LeftArm'), hb_('mixamorig:RightArm')], 0.10, 0.22),          # shoulders
                              sph_fall([hb_('mixamorig:Neck')], 0.07, 0.16)])                                       # neck
    W = top4((1 - fall)[:, None] * Wa + fall[:, None] * W.astype(np.float32)).astype(np.float64)
    REPORT['hybrid_donor_fraction_mean'] = round(float(fall.mean()), 4)
    P('hybrid: donor share mean %.3f, verts with share>0.5: %d' % (fall.mean(), (fall > 0.5).sum()))

# ---- finger-chain repair: a finger bone with NO weighted vertex gets weight from vertices that already carry weight on the SAME finger chain, redistributed by
#      position along that finger's own bone chain (never from / to a neighbouring finger).
def finger_repair(W):
    rep = {}
    for pre in ('Left', 'Right'):
        for f in FING_:
            ch = [cols['mixamorig:%sHand%s%d' % (pre, f, k)] for k in (1, 2, 3, 4)]
            if not any((W[:, c] > 1e-6).sum() == 0 for c in ch): continue
            C = W[:, ch].sum(1); cand = np.where(C >= 0.05)[0]
            if len(cand) == 0: rep['%s%s' % (pre, f)] = 'no vertex carries this finger chain -> still unweighted'; continue
            hb = [np.array(bones['mixamorig:%sHand%s%d' % (pre, f, k)].head_local[:]) for k in (1, 2, 3, 4)]
            tb = [np.array(bones['mixamorig:%sHand%s%d' % (pre, f, k)].tail_local[:]) for k in (1, 2, 3, 4)]
            pts = tco[cand]; best_d = np.full(len(cand), 9.0); sp = np.zeros(len(cand))
            for k in range(4):
                d = tb[k] - hb[k]; t = np.clip(((pts - hb[k]) @ d) / max(d @ d, 1e-12), 0, 1)
                dist = np.linalg.norm(pts - (hb[k] + t[:, None] * d), axis=1); m = dist < best_d; best_d[m] = dist[m]; sp[m] = k + t[m]
            u = np.clip(sp - 0.5, 0, 3.0); lo = np.minimum(np.floor(u).astype(int), 2); fr = u - lo
            hat = np.zeros((len(cand), 4)); hat[np.arange(len(cand)), lo] = 1 - fr; hat[np.arange(len(cand)), lo + 1] = fr
            old_ = W[cand][:, ch].copy(); W[np.ix_(cand, ch)] = C[cand, None] * hat
            rep['%s%s' % (pre, f)] = 'redistributed %d verts along its own chain' % len(cand)
    return W, rep
W, frep2 = finger_repair(W); W = top4(W.astype(np.float32)).astype(np.float64)
REPORT['finger_chain_repair'] = frep2; P('finger chain repair:', json.dumps(frep2))

# ---- write vertex groups (body deform bones only) + armature modifier
body.vertex_groups.clear()
for n in body_bones: body.vertex_groups.new(name=n)
bm = bmesh.new(); bm.from_mesh(me); dl = bm.verts.layers.deform.verify()
nzr, nzc = np.nonzero(W > 0)
bm.verts.ensure_lookup_table()
for r_, c_ in zip(nzr.tolist(), nzc.tolist()): bm.verts[r_][dl][c_] = float(W[r_, c_])
bm.to_mesh(me); bm.free(); me.update()
body.parent = rig; body.matrix_parent_inverse = Matrix.Identity(4)
md = body.modifiers.new('Armature', 'ARMATURE'); md.object = rig
infl = (W > 0).sum(1); ssum = W.sum(1)
zb = sorted(body_bones[i] for i in range(NB) if (W[:, i] > 0.05).sum() == 0)
P('zero-weight verts', int((ssum < 1e-6).sum()), '| max influences', int(infl.max()), '| sum min/max %.5f %.5f' % (ssum.min(), ssum.max()), '| bones with no vert >0.05:', zb)
for n in ('mixamorig:LeftHandIndex4', 'mixamorig:LeftHandMiddle2', 'mixamorig:LeftHandRing3', 'mixamorig:LeftHandPinky4', 'mixamorig:LeftHandThumb3'):
    P('  verts>0.05 on', n, int((W[:, cols[n]] > 0.05).sum()))
REPORT.update(zero_bones=zb, max_influences=int(infl.max()))
for t_ in ([o for o in bpy.data.objects if o.type == 'EMPTY']): bpy.data.objects.remove(t_, do_unlink=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT)
REPORT['bind_time_s'] = round(time.time() - T0, 1)
with open(os.path.splitext(OUT)[0] + '.report.json', 'w') as fh: json.dump(REPORT, fh, indent=1)
P('saved', OUT, 'total %.1fs' % (time.time() - T0))
