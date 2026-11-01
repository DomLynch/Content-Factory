"""
bind_to_106.py - bind any humanoid GLB mesh to the 106-bone "psyche" Mixamo-style rig.

  blender --background --python bind_to_106.py -- <mesh.glb> <out.blend> [options]

Options
  --render DIR     also retarget one Idle + one Attack Mixamo clip, render workbench stills (800x800) + stretch metrics into DIR
  --idle FBX       idle clip   (default: Breathing Idle from the Mixamo XBot library)
  --attack FBX     attack clip (default: Zombie Attack from the Mixamo XBot library)
  --maxf N         max frames baked per clip (default 80)
  --rig GLB        rig glb (default psyche-106-v3.glb)
  --height M       target mesh height in metres (default: rig Head-bone tail z)
  --no-tpose       do not try to bring the arms to T-pose
  --finger-pass auto|always|off   (default auto = only hands that have a finger bone with ~zero weight)
  --rotate-z DEG   extra yaw applied before the facing check (default 0)

Pipeline: import rig -> strip sword/eyelash/etc -> import mesh (all meshes joined, transforms baked, old skin removed)
 -> upright/facing/scale/feet-on-floor -> arms to T-pose + hand-tip fit -> use_deform=False on every non-body bone
 -> parent_set ARMATURE_AUTO -> post-fix (unweighted verts, finger pass, max 4 influences, normalise) -> save.
Body bones = mixamorig:* plus the *_twist_* bones.  Everything else (cape, skirt, hair, ik_*, fk_*, root) stays non-deforming.
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
        if a in ('--no-tpose', '--no-armfit'): opt[a] = True
        else: opt[a] = argv[i + 1]; i += 1
    else: pos.append(a)
    i += 1
if len(pos) != 2:
    print('usage: blender --background --python bind_to_106.py -- <mesh.glb> <out.blend> [options]'); sys.exit(2)
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

# ------------------------------------------------------------------ 5. deform flags, then bone-heat auto weights
for b in bones: b.use_deform = is_body_bone(b.name)
cols = {n: i for i, n in enumerate(body_bones)}
def heat_bind(obj):
    """parent_set ARMATURE_AUTO on obj; returns (n_verts x n_body_bones) weight matrix in obj vertex order"""
    sel_only([obj, rig], active=rig)
    t1 = time.time(); r = bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    gn = {g.index: g.name for g in obj.vertex_groups}
    Wm = np.zeros((len(obj.data.vertices), len(body_bones)))
    for v in obj.data.vertices:
        for g in v.groups:
            c = cols.get(gn.get(g.group))
            if c is not None: Wm[v.index, c] = g.weight
    P('parent_set', r, '%.1fs' % (time.time() - t1), 'weighted verts %d/%d' % ((Wm.sum(1) > 1e-4).sum(), len(Wm)))
    return Wm
def unbind(obj):
    obj.vertex_groups.clear()
    for m in list(obj.modifiers): obj.modifiers.remove(m)
    obj.parent = None; obj.matrix_basis = Matrix.Identity(4)
W = heat_bind(body)
REPORT['heat_mode'] = 'whole mesh'
if (W.sum(1) < 1e-4).mean() > 0.2:
    P('bone-heat failed on whole mesh -> retry on largest connected component only')
    unbind(body)
    bm = bmesh.new(); bm.from_mesh(me); seen = set(); best = []
    for v0 in bm.verts:
        if v0.index in seen: continue
        st = [v0]; comp = []; seen.add(v0.index)
        while st:
            x = st.pop(); comp.append(x.index)
            for e in x.link_edges:
                o = e.other_vert(x)
                if o.index not in seen: seen.add(o.index); st.append(o)
        if len(comp) > len(best): best = comp
    bm.free()
    tmp_me = me.copy(); tmp = bpy.data.objects.new('tmp_heat', tmp_me); bpy.context.scene.collection.objects.link(tmp)
    keep = np.zeros(nv, bool); keep[best] = True; kidx = np.where(keep)[0]
    bm = bmesh.new(); bm.from_mesh(tmp_me); bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.verts[i] for i in np.where(~keep)[0]], context='VERTS'); bm.to_mesh(tmp_me); bm.free()
    Wc = heat_bind(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True); bpy.data.meshes.remove(tmp_me)
    W = np.zeros((nv, len(body_bones))); W[kidx] = Wc
    REPORT['heat_mode'] = 'largest component (%d of %d verts) + nearest-vertex transfer' % (len(kidx), nv)
P('heat mode:', REPORT['heat_mode'])
def zero_bones(W, thr=0.05):
    cnt = (W > thr).sum(0)
    return {body_bones[i]: int(cnt[i]) for i in range(len(body_bones)) if cnt[i] == 0}, {body_bones[i]: int(cnt[i]) for i in range(len(body_bones)) if 0 < cnt[i] < 5}

# segments of all body bones (rig space == mesh space)
SA = np.array([bones[n].head_local[:] for n in body_bones]); SB = np.array([bones[n].tail_local[:] for n in body_bones])
def seg_dist(pts, A, B):
    AB = B - A; L2 = (AB ** 2).sum(1) + 1e-12
    out = np.empty((len(pts), len(A)))
    for i0 in range(0, len(pts), 20000):
        p = pts[i0:i0 + 20000][:, None, :]
        t = np.clip(((p - A[None]) * AB[None]).sum(2) / L2[None], 0, 1)
        out[i0:i0 + 20000] = np.linalg.norm(p - (A[None] + t[..., None] * AB[None]), axis=2)
    return out
def dist_weights(pts, A, B, power=4, eps=0.002, topn=4):
    d = seg_dist(pts, A, B); idx = np.argsort(d, axis=1)[:, :topn]; dd = np.take_along_axis(d, idx, 1)
    w = 1.0 / (dd + eps) ** power; w /= w.sum(1, keepdims=True); return idx, w, d.min(1)

co = get_co(me)
raw_unw = W.sum(1) < 1e-4
REPORT['unweighted_verts_after_auto'] = int(raw_unw.sum())
P('after bone-heat: unweighted verts %d (%.1f%%)' % (raw_unw.sum(), 100.0 * raw_unw.mean()))
if raw_unw.mean() > 0.2:
    P('WARNING bone-heat failed for most of the mesh -> global distance-to-bone-segment weights')
    idx, w, _ = dist_weights(co, SA, SB); W[:] = 0
    for k in range(idx.shape[1]): W[np.arange(nv), idx[:, k]] = w[:, k]
    REPORT['global_distance_fallback'] = True
elif raw_unw.any():
    kd = kdtree.KDTree(int((~raw_unw).sum())); good = np.where(~raw_unw)[0]
    for j, vi in enumerate(good): kd.insert(co[vi], j)
    kd.balance()
    for vi in np.where(raw_unw)[0]: W[vi] = W[good[kd.find(co[vi])[1]]]
    P('unweighted verts filled from nearest weighted vertex')

zb_before, weak_before = zero_bones(W)
P('WEAK bones (<5 verts >0.05) BEFORE:', weak_before)
P('ZERO-WEIGHT bones BEFORE finger pass (%d): %s' % (len(zb_before), sorted(zb_before)))
REPORT['zero_before'] = sorted(zb_before)

# ------------------------------------------------------------------ 6. finger pass
FING = ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky')
fp_rep = {}
for side, pre in ((1, 'Left'), (-1, 'Right')):
    fb = ['mixamorig:%sHand%s%d' % (pre, f, k) for f in FING for k in (1, 2, 3, 4)]
    zero_f = [n for n in fb if n in zb_before or n in weak_before]      # ~zero = no vertex >0.05, or fewer than 5
    run = FINGER_MODE == 'always' or (FINGER_MODE == 'auto' and zero_f)
    if not run: fp_rep[pre] = {'ran': False, 'zero_fingers': zero_f}; continue
    hand = 'mixamorig:%sHand' % pre
    f1h = np.array([bones['mixamorig:%sHand%s1' % (pre, f)].head_local[:] for f in FING if f != 'Thumb'])
    names = [hand] + fb
    A = np.array([bones[hand].head_local[:]] + [bones[n].head_local[:] for n in fb])
    B = np.array([f1h.mean(0)] + [bones[n].tail_local[:] for n in fb])            # hand segment: wrist -> knuckles
    x0 = abs(bones[hand].tail_local.x) - 0.015; x1 = abs(f1h[:, 0].mean()) + 0.005
    sx = side * co[:, 0]
    cand = np.where(sx > x0)[0]
    idx, w, dmin = dist_weights(co[cand], A, B)
    keep = dmin < 0.10
    cand, idx, w = cand[keep], idx[keep], w[keep]
    t = smooth((sx[cand] - x0) / (x1 - x0))
    Wn = np.zeros((len(cand), len(body_bones)))
    for k in range(idx.shape[1]): Wn[np.arange(len(cand)), [cols[names[j]] for j in idx[:, k]]] = w[:, k]
    W[cand] = (1 - t)[:, None] * W[cand] + t[:, None] * Wn
    fp_rep[pre] = {'ran': True, 'zero_fingers_before': zero_f, 'hand_region_verts': int(len(cand))}
P('finger pass:', json.dumps(fp_rep))
REPORT['finger_pass'] = fp_rep

# ------------------------------------------------------------------ 7. max 4 influences, normalise, write back
idx = np.argsort(-W, axis=1)[:, :4]
Wl = np.zeros_like(W); rows = np.arange(nv)[:, None]
Wl[rows, idx] = W[rows, idx]
Wl[Wl < 0.01] = 0
bad = Wl.sum(1) < 1e-6
Wl[bad, np.argmax(W[bad], axis=1)] = 1.0
Wl /= Wl.sum(1, keepdims=True)
W = Wl
body.vertex_groups.clear()
vgs = [body.vertex_groups.new(name=n) for n in body_bones]
for c, vg in enumerate(vgs):
    for vi in np.nonzero(W[:, c] > 0)[0]: vg.add([int(vi)], float(W[vi, c]), 'REPLACE')
zb_after, weak_after = zero_bones(W)
infl = (W > 0).sum(1)
P('ZERO-WEIGHT bones AFTER (%d): %s' % (len(zb_after), sorted(zb_after)))
P('weak bones (<5 verts >0.05) after:', weak_after)
P('max influences/vert', int(infl.max()), '| weight sum min/max', round(float(W.sum(1).min()), 4), round(float(W.sum(1).max()), 4))
# left/right contamination
cross = 0
for n, c in cols.items():
    if 'Left' in n or n.endswith('_l'): cross += int(((W[:, c] > 0.1) & (co[:, 0] < -0.03)).sum())
    if 'Right' in n or n.endswith('_r'): cross += int(((W[:, c] > 0.1) & (co[:, 0] > 0.03)).sum())
P('vertex-bone pairs (w>0.1) across the midline:', cross)
REPORT.update(zero_after=sorted(zb_after), weak_after=weak_after, max_influences=int(infl.max()), cross_side_pairs=cross)
for n in ('mixamorig:LeftHandIndex4', 'mixamorig:LeftHandMiddle2', 'mixamorig:LeftHandRing3', 'mixamorig:LeftHandPinky4', 'mixamorig:LeftHandThumb3'):
    P('  verts>0.05 on', n, int((W[:, cols[n]] > 0.05).sum()))
for t_ in ([o for o in bpy.data.objects if o.type == 'EMPTY']): bpy.data.objects.remove(t_, do_unlink=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT)
REPORT['bind_time_s'] = round(time.time() - T0, 1)
P('saved', OUT, 'bind time %.1fs' % (time.time() - T0))

# ------------------------------------------------------------------ 8. optional: retarget Idle/Attack, render stills, stretch metrics
RDIR = opt.get('--render')
def bake_clip(cname, path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path)
    new_ = [o for o in bpy.data.objects if o not in before]
    src = [o for o in new_ if o.type == 'ARMATURE'][0]
    act = src.animation_data.action
    sn = [b.name for b in src.data.bones]; rn = set(b.name for b in rig.data.bones)
    mapped = [n for n in sn if n in rn]
    P(cname, 'src bones', len(sn), 'matched', len(mapped), 'frames', tuple(act.frame_range))
    sw = src.matrix_world
    s_rest = {n: sw @ src.data.bones[n].matrix_local for n in mapped}
    r_rest = {b.name: rig.matrix_world @ b.matrix_local for b in rig.data.bones}
    hscale = r_rest['mixamorig:Hips'].translation.z / s_rest['mixamorig:Hips'].translation.z
    f0 = int(act.frame_range[0]); f1 = min(int(act.frame_range[1]), f0 + MAXF - 1)
    def depth(b):
        d = 0
        while b.parent: b = b.parent; d += 1
        return d
    order = sorted(rig.data.bones, key=depth)
    na = bpy.data.actions.new(cname + '_retarget'); rig.animation_data_create(); rig.animation_data.action = na
    hips_rest_t = s_rest['mixamorig:Hips'].translation.copy()
    for f in range(f0, f1 + 1):
        bpy.context.scene.frame_set(f)
        s_pose = {n: sw @ src.pose.bones[n].matrix for n in mapped}
        pose_w = {}
        for b in order:
            n = b.name; par = b.parent
            chain = r_rest[n] if par is None else pose_w[par.name] @ r_rest[par.name].inverted() @ r_rest[n]
            if n in s_pose:
                dR = s_pose[n].to_3x3() @ s_rest[n].to_3x3().inverted()
                tgt = (dR @ r_rest[n].to_3x3()).to_4x4(); tr = chain.translation.copy()
                if n == 'mixamorig:Hips': tr = r_rest[n].translation + (s_pose[n].translation - hips_rest_t) * hscale
                tgt.translation = tr
            else: tgt = chain
            pose_w[n] = tgt
            if n in s_pose:
                basis = (r_rest[n].inverted() @ tgt) if par is None else (pose_w[par.name] @ r_rest[par.name].inverted() @ r_rest[n]).inverted() @ tgt
                pb = rig.pose.bones[n]; pb.rotation_mode = 'QUATERNION'
                pb.rotation_quaternion = basis.to_quaternion(); pb.keyframe_insert('rotation_quaternion', frame=f)
                if n == 'mixamorig:Hips': pb.location = basis.to_translation(); pb.keyframe_insert('location', frame=f)
    na.use_fake_user = True
    for o in new_: bpy.data.objects.remove(o, do_unlink=True)
    return na, f0, f1

if RDIR:
    os.makedirs(RDIR, exist_ok=True)
    t2 = time.time()
    clips = {}
    for cname, path in (('Idle', IDLE), ('Attack', ATTACK)):
        try: clips[cname] = bake_clip(cname, path)
        except Exception as e: P('CLIP ERROR', cname, repr(e))
    rig.animation_data.action = None
    for pb in rig.pose.bones: pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
    P('baked', {k: (v[1], v[2]) for k, v in clips.items()}, '%.1fs' % (time.time() - t2))
    scn = bpy.context.scene
    scn.render.engine = 'BLENDER_WORKBENCH'; scn.render.resolution_x = 800; scn.render.resolution_y = 800; scn.render.resolution_percentage = 100
    scn.render.image_settings.file_format = 'PNG'
    sh = scn.display.shading; sh.light = 'STUDIO'; sh.color_type = 'SINGLE'; sh.single_color = (0.78, 0.64, 0.56); sh.show_cavity = False
    scn.world = bpy.data.worlds.new('w'); scn.world.color = (0.18, 0.2, 0.24)
    rig.hide_render = True
    cd = bpy.data.cameras.new('cam'); cam = bpy.data.objects.new('cam', cd); scn.collection.objects.link(cam); cd.type = 'ORTHO'; scn.camera = cam
    def shot(name, loc, target, scale):
        cam.location = loc; cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler(); cd.ortho_scale = scale
        scn.render.filepath = os.path.join(RDIR, name + '.png'); bpy.ops.render.render(write_still=True)
    def setpose(cname, frame):
        if cname is None:
            rig.animation_data.action = None
            for pb in rig.pose.bones: pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
        else:
            rig.animation_data.action = clips[cname][0]
            ad = rig.animation_data
            if getattr(ad, 'action_slot', None) is None and len(ad.action.slots): ad.action_slot = ad.action.slots[0]
            scn.frame_set(frame)
        bpy.context.view_layer.update()
    rest_co = get_co(me)
    ed = np.empty(len(me.edges) * 2, dtype=np.int32); me.edges.foreach_get('vertices', ed); ed = ed.reshape(-1, 2)
    L0 = np.linalg.norm(rest_co[ed[:, 0]] - rest_co[ed[:, 1]], axis=1)
    def stretch(label):
        ev = body.evaluated_get(bpy.context.evaluated_depsgraph_get()); m2 = ev.to_mesh(); c2 = get_co(m2); ev.to_mesh_clear()
        L1 = np.linalg.norm(c2[ed[:, 0]] - c2[ed[:, 1]], axis=1); ok = L0 > 1e-6; ratio = np.ones_like(L0); ratio[ok] = L1[ok] / L0[ok]
        vs = np.ones(nv); np.maximum.at(vs, ed[:, 0], ratio); np.maximum.at(vs, ed[:, 1], ratio)
        vc = np.ones(nv); np.minimum.at(vc, ed[:, 0], ratio); np.minimum.at(vc, ed[:, 1], ratio)
        worst = np.argsort(-vs)[:6]
        P(label, 'edges stretched >1.8x: %d, >3x: %d, compressed <0.4x: %d | max stretch %.2f' % ((ratio > 1.8).sum(), (ratio > 3).sum(), (ratio < 0.4).sum(), ratio.max()))
        for v in worst: P('   worst vert', int(v), 'rest xyz', np.round(rest_co[v], 2).tolist(), 'stretch %.2f' % vs[v], 'top bone', body_bones[int(np.argmax(W[v]))])
        REPORT.setdefault('stretch', {})[label] = {'gt1.8': int((ratio > 1.8).sum()), 'gt3': int((ratio > 3).sum()), 'lt0.4': int((ratio < 0.4).sum()), 'max': round(float(ratio.max()), 2)}
    def mid(c): return (clips[c][1] + clips[c][2]) // 2
    plan = [('still_1_rest', None, 1), ('still_2_idle_mid', 'Idle', None), ('still_3_attack_mid', 'Attack', None)]
    for name, c, fr in plan:
        if c and c not in clips: continue
        fr = fr or (mid(c) if c else 1)
        setpose(c, fr); P(name, 'frame', fr)
        if c: stretch(name)
        shot(name, (0, -8, 0.9), (0, 0, 0.9), 2.6)
        shot('extra_side_' + name[6:], (8, 0, 0.9), (0, 0, 0.9), 2.6)
        lh = rig.matrix_world @ rig.pose.bones['mixamorig:LeftHand'].head
        shot('extra_hand_' + name[6:], (lh.x, lh.y - 3, lh.z), tuple(lh), 0.6)
    REPORT['render_dir'] = RDIR
    bpy.ops.wm.save_as_mainfile(filepath=os.path.splitext(OUT)[0] + '_anim.blend')
with open(os.path.splitext(OUT)[0] + '.report.json', 'w') as fh: json.dump(REPORT, fh, indent=1)
P('DONE total %.1fs' % (time.time() - T0))
