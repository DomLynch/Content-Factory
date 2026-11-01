"""bind_gear.py - Blender-side (background, CPU) skin transfer from a bound body to one GEAR mesh.
  blender --background --python bind_gear.py -- <args.json>
Written by tools/run_gear.py. Every number and name comes from the args json (config "gear", "stills", "bind"); nothing is hard-coded here.

Steps: import bound body GLB (Rig + Body) -> import gear GLB -> optional fit -> nearest-surface-point weight transfer from the body
(barycentric over the hit triangle) -> optional blend onto rig helper bones (cape/skirt) -> smoothing passes -> limit influences, normalise
-> vertex groups + Armature modifier -> measure (weights, edge stretch over the configured clips) -> export Rig + gear to GLB.
The gate itself is judged by run_gear.py from the metrics written to the result json."""
import bpy, bmesh, json, math, os, struct, sys, time
import numpy as np
from mathutils import Vector, Matrix, Quaternion
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

A = json.load(open(sys.argv[sys.argv.index('--') + 1], encoding='utf-8'))
G, S, B, PART = A['gear'], A['stills'], A['bind'], A['part']
T0 = time.time()
R = {'part': PART, 'notes': []}


def P(*a): print('GEAR>', *a, flush=True)


def finish(code):
    R['total_s'] = round(time.time() - T0, 1)
    json.dump(R, open(A['result'], 'w', encoding='utf-8'), indent=1)
    sys.exit(code)


def mesh_co(me, world=None):
    a = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get('co', a)
    a = a.reshape(-1, 3)
    if world is not None:
        m = np.array(world)
        a = a @ m[:3, :3].T + m[:3, 3]
    return a


# ---------------------------------------------------------------- import
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=A['body'])
rig = bpy.data.objects.get(B['rig_name']) or next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
bodies = [o for o in bpy.data.objects if o.type == 'MESH']
body = bpy.data.objects.get(B['body_name']) or (max(bodies, key=lambda o: len(o.data.vertices)) if bodies else None)
if rig is None or body is None:
    R['error'] = 'bound body glb has no armature or no mesh'; finish(3)
before = set(bpy.data.objects)
bpy.ops.import_scene.gltf(filepath=A['gear_glb'])
new = [o for o in bpy.data.objects if o not in before]
gmeshes = [o for o in new if o.type == 'MESH']
if not gmeshes:
    R['error'] = 'gear glb has no mesh'; finish(3)
for o in new:  # bake world transforms into the mesh data and drop parents so the gear is an unparented world-space mesh
    if o.type == 'MESH':
        mw = o.matrix_world.copy(); o.parent = None; o.data.transform(mw); o.matrix_world = Matrix.Identity(4)
        o.modifiers.clear(); o.vertex_groups.clear()
garms = [o for o in new if o.type == 'ARMATURE']
if len(gmeshes) > 1:
    bpy.ops.object.select_all(action='DESELECT')
    for o in gmeshes: o.select_set(True)
    bpy.context.view_layer.objects.active = gmeshes[0]
    bpy.ops.object.join()
gear = bpy.context.view_layer.objects.active if len(gmeshes) > 1 else gmeshes[0]
for o in garms: bpy.data.objects.remove(o, do_unlink=True)
gear.name = G['glb_prefix'] + PART
me = gear.data
bpy.context.view_layer.update()
bones = rig.data.bones
bone_names = {b.name for b in bones}
rig_mw = np.array(rig.matrix_world)
body_co = mesh_co(body.data, body.matrix_world)
zmin_b, zmax_b = float(body_co[:, 2].min()), float(body_co[:, 2].max()); H = zmax_b - zmin_b
R['body'] = {'verts': len(body.data.vertices), 'height_m': round(H, 4)}

# ---------------------------------------------------------------- optional fit
fit = G['fit']
gco = mesh_co(me)
if fit['mode'] == 'z_frac' and PART in fit['parts']:
    lo, hi = fit['parts'][PART]['z_frac']
    gh = float(gco[:, 2].max() - gco[:, 2].min())
    s = (H * (hi - lo)) / gh if gh > 0 else 1.0
    c_b = 0.5 * (body_co.min(axis=0) + body_co.max(axis=0)); c_g = 0.5 * (gco.min(axis=0) + gco.max(axis=0))
    tgt = Vector((c_b[0], c_b[1], zmin_b + H * lo))
    low = Vector((c_g[0], c_g[1], float(gco[:, 2].min())))
    mat = Matrix.Translation(tgt) @ Matrix.Scale(s, 4) @ Matrix.Translation(-low)
    me.transform(mat); gco = mesh_co(me)
    R['fit'] = {'mode': 'z_frac', 'z_frac': [lo, hi], 'scale': round(s, 4)}
else:
    R['fit'] = {'mode': 'none'}
nv = len(me.vertices)
R['gear'] = {'verts': nv, 'faces': len(me.polygons), 'bbox_min': [round(float(x), 4) for x in gco.min(axis=0)], 'bbox_max': [round(float(x), 4) for x in gco.max(axis=0)]}

# ---------------------------------------------------------------- body weights and BVH
gname = {g.index: g.name for g in body.vertex_groups}
bw = []
for v in body.data.vertices:
    bw.append([(gname[g.group], g.weight) for g in v.groups if g.weight > G['weight_epsilon'] and gname.get(g.group) in bone_names])
bm = bmesh.new(); bm.from_mesh(body.data); bm.transform(body.matrix_world)
bmesh.ops.triangulate(bm, faces=bm.faces[:])
bm.faces.ensure_lookup_table()
tree = BVHTree.FromBMesh(bm)
tri_verts = [[v.index for v in f.verts] for f in bm.faces]
tri_co = [[Vector(v.co) for v in f.verts] for f in bm.faces]
bm.free()

# ---------------------------------------------------------------- nearest-point transfer
W = [dict() for _ in range(nv)]
dists = np.zeros(nv)
fallback = S['hips_bone']
for i in range(nv):
    loc, nrm, fi, d = tree.find_nearest(Vector(gco[i]))
    dists[i] = d
    bc = poly_3d_calc(tri_co[fi], loc)
    acc = W[i]
    for vi, wgt in zip(tri_verts[fi], bc):
        for bn, bwgt in bw[vi]:
            acc[bn] = acc.get(bn, 0.0) + bwgt * wgt
R['transfer'] = {'dist_mean_m': round(float(dists.mean()), 4), 'dist_p99_m': round(float(np.quantile(dists, 0.99)), 4), 'dist_max_m': round(float(dists.max()), 4)}

# ---------------------------------------------------------------- optional helper-bone blend (cape / skirt secondary motion)
hcfg = G['helpers']['parts'].get(PART)
R['helpers'] = {'used': False}
if hcfg:
    hb = [b for b in bones if any(p in b.name.lower() for p in hcfg['patterns'])]
    if hb:
        heads = np.array([(rig_mw @ np.append(np.array(b.head_local), 1.0))[:3] for b in hb])
        tails = np.array([(rig_mw @ np.append(np.array(b.tail_local), 1.0))[:3] for b in hb])
        seg = tails - heads; seg_len2 = np.maximum((seg * seg).sum(axis=1), 1e-12)
        anchor = zmin_b + H * hcfg['anchor_z_frac']; gzmin = float(gco[:, 2].min()); span = max(anchor - gzmin, 1e-9)
        chunk = 20000; used = set(); n_blend = 0
        for s0 in range(0, nv, chunk):
            pts = gco[s0:s0 + chunk]
            t = np.clip(((pts[:, None, :] - heads[None]) * seg[None]).sum(axis=2) / seg_len2[None], 0.0, 1.0)
            d2 = ((heads[None] + t[..., None] * seg[None]) - pts[:, None, :]); d2 = (d2 * d2).sum(axis=2)
            nearest = d2.argmin(axis=1)
            frac = np.clip((anchor - pts[:, 2]) / span, 0.0, 1.0) * hcfg['max_blend']
            for k in range(len(pts)):
                f = float(frac[k])
                if f <= 0: continue
                acc = W[s0 + k]; tot = sum(acc.values())
                if tot <= 0: continue
                for bn in acc: acc[bn] *= (1.0 - f)
                hn = hb[int(nearest[k])].name
                acc[hn] = acc.get(hn, 0.0) + f * tot; used.add(hn); n_blend += 1
        for bn in used: bones[bn].use_deform = True
        R['helpers'] = {'used': True, 'bones_available': [b.name for b in hb], 'bones_used': sorted(used), 'blended_vertices': n_blend}
    else:
        R['notes'].append('no helper bones matching %s in the rig; body weights only' % hcfg['patterns'])

# ---------------------------------------------------------------- smoothing
if G['smooth_passes'] > 0:
    ea = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get('vertices', ea); ea = ea.reshape(-1, 2)
    nbr = [[] for _ in range(nv)]
    for a, b in ea: nbr[a].append(b); nbr[b].append(a)
    f = G['smooth_factor']
    for _ in range(G['smooth_passes']):
        nxt = []
        for i in range(nv):
            cur = W[i]
            if not nbr[i]: nxt.append(cur); continue
            avg = {}
            for j in nbr[i]:
                for bn, w in W[j].items(): avg[bn] = avg.get(bn, 0.0) + w / len(nbr[i])
            out = {bn: (1 - f) * w for bn, w in cur.items()}
            for bn, w in avg.items(): out[bn] = out.get(bn, 0.0) + f * w
            # keep only the strongest influences each pass so dicts stay small
            if len(out) > G['max_influences'] * 2:
                out = dict(sorted(out.items(), key=lambda kv: -kv[1])[:G['max_influences'] * 2])
            nxt.append(out)
        W = nxt

# ---------------------------------------------------------------- limit influences, normalise
eps = G['weight_epsilon']; filled = 0
for i in range(nv):
    items = sorted(((bn, w) for bn, w in W[i].items() if w > eps), key=lambda kv: -kv[1])[:G['max_influences']]
    tot = sum(w for _, w in items)
    if tot <= 0:
        items = [(fallback, 1.0)]; tot = 1.0; filled += 1
    W[i] = {bn: w / tot for bn, w in items}
R['fallback_vertices'] = filled

# ---------------------------------------------------------------- vertex groups, parenting, modifier
vgs = {}
for i in range(nv):
    for bn, w in W[i].items():
        vg = vgs.get(bn) or vgs.setdefault(bn, gear.vertex_groups.new(name=bn))
        vg.add([i], w, 'REPLACE')
gear.parent = rig; gear.matrix_parent_inverse = rig.matrix_world.inverted()
md = gear.modifiers.new('Armature', 'ARMATURE'); md.object = rig
bpy.context.view_layer.update()

# ---------------------------------------------------------------- weight metrics
cnt = np.array([len(W[i]) for i in range(nv)]); ssum = np.array([sum(W[i].values()) for i in range(nv)])
R['weights'] = {'zero_weight_vertices': int((cnt == 0).sum()), 'max_influences': int(cnt.max()) if nv else 0,
                'weight_sum_max_dev': round(float(np.abs(ssum - 1.0).max()), 6) if nv else None, 'bones_used': len(vgs)}

# ---------------------------------------------------------------- stretch over configured clips (same retarget maths as tools/run_stills.py)
C = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def gltf_rest(path):
    with open(path, 'rb') as f:
        f.read(12); n, _ = struct.unpack('<II', f.read(8)); j = json.loads(f.read(n))
    nodes = j['nodes']; parent = {}
    for i, nd in enumerate(nodes):
        for c in nd.get('children', []): parent[c] = i

    def local(nd):
        if 'matrix' in nd:
            m = nd['matrix']; return Matrix([[m[r + 4 * c] for c in range(4)] for r in range(4)])
        x, y, z, wq = nd.get('rotation', [0, 0, 0, 1]); s = nd.get('scale', [1, 1, 1])
        return Matrix.Translation(nd.get('translation', [0, 0, 0])) @ Quaternion((wq, x, y, z)).to_matrix().to_4x4() @ Matrix.Diagonal((s[0], s[1], s[2], 1))
    memo = {}

    def world(i):
        if i not in memo: memo[i] = local(nodes[i]) if i not in parent else world(parent[i]) @ local(nodes[i])
        return memo[i]
    return {nodes[i]['name']: C @ world(i) @ C.inverted() for i in range(len(nodes))}


r_rest = {b.name: rig.matrix_world @ b.matrix_local for b in bones}


def depth(b):
    d = 0
    while b.parent: b = b.parent; d += 1
    return d


order = sorted(bones, key=depth)


def reset_pose():
    for pb in rig.pose.bones: pb.rotation_mode = 'QUATERNION'; pb.rotation_quaternion = (1, 0, 0, 0); pb.location = (0, 0, 0)
    bpy.context.view_layer.update()


def pose_frame(emp, s_rest, frame):
    bpy.context.scene.frame_set(frame)
    mapped = [n for n in emp if n in r_rest and n in s_rest]
    sp = {n: emp[n].matrix_world.copy() for n in mapped}
    hips = S['hips_bone']; hscale = r_rest[hips].translation.z / s_rest[hips].translation.z
    reset_pose(); pose_w = {}
    for b in order:
        n = b.name; par = b.parent
        chain = r_rest[n] if par is None else pose_w[par.name] @ r_rest[par.name].inverted() @ r_rest[n]
        if n in sp:
            dR = sp[n].to_3x3() @ s_rest[n].to_3x3().inverted(); tgt = (dR @ r_rest[n].to_3x3()).to_4x4(); tr = chain.translation.copy()
            if n == hips: tr = r_rest[n].translation + (sp[n].translation - s_rest[hips].translation) * hscale
            tgt.translation = tr
        else: tgt = chain
        pose_w[n] = tgt
        if n in sp:
            basis = (r_rest[n].inverted() @ tgt) if par is None else (pose_w[par.name] @ r_rest[par.name].inverted() @ r_rest[n]).inverted() @ tgt
            pb = rig.pose.bones[n]; pb.rotation_quaternion = basis.to_quaternion()
            if n == hips: pb.location = basis.to_translation()
    bpy.context.view_layer.update()
    return len(mapped)


def eval_co():
    ev = gear.evaluated_get(bpy.context.evaluated_depsgraph_get()); m2 = ev.to_mesh(); c = mesh_co(m2); ev.to_mesh_clear(); return c


def wq(vals, w, q):
    o = np.argsort(vals, kind='stable'); cw = np.cumsum(w[o]); return float(vals[o][min(np.searchsorted(cw, q * cw[-1]), len(o) - 1)])


ea = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get('vertices', ea); ea = ea.reshape(-1, 2)
rest = mesh_co(me); rl = np.linalg.norm(rest[ea[:, 0]] - rest[ea[:, 1]], axis=1)
parea = np.empty(len(me.polygons)); me.polygons.foreach_get('area', parea)
ltot = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get('loop_total', ltot)
leid = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get('edge_index', leid)
ew = np.bincount(leid, weights=np.repeat(parea, ltot), minlength=len(me.edges))
mask = (rl >= G['gate']['min_edge_mm'] / 1000.0) & (ew > 0)
R['stretch'] = {'edges_measured': int(mask.sum()), 'clips': {}}
if mask.any():
    allr = []; allw = []
    bpy.context.scene.frame_set(0)
    for key in G['stretch_clips']:
        path = A['clips'][key]; s_rest = gltf_rest(path)
        before = set(bpy.data.objects); bpy.ops.import_scene.gltf(filepath=path)
        newc = [o for o in bpy.data.objects if o not in before]
        emp = {o.name: o for o in newc if o.animation_data and o.animation_data.action}
        if not emp:
            R['notes'].append('clip %s has no animated nodes' % key); continue
        act = next(iter(emp.values())).animation_data.action
        f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
        frames = sorted({int(round(f0 + (f1 - f0) * k / max(G['stretch_frames_per_clip'] - 1, 1))) for k in range(G['stretch_frames_per_clip'])})
        crs = []
        for fr in frames:
            nm = pose_frame(emp, s_rest, fr)
            cur = eval_co(); cl = np.linalg.norm(cur[ea[mask, 0]] - cur[ea[mask, 1]], axis=1)
            crs.append(cl / rl[mask]); allr.append(cl / rl[mask]); allw.append(ew[mask])
        cr = np.concatenate(crs)
        R['stretch']['clips'][key] = {'frames': frames, 'matched_bones': nm, 'max_stretch': round(float(cr.max()), 3), 'min_ratio': round(float(cr.min()), 3)}
        for o in newc: bpy.data.objects.remove(o, do_unlink=True)
    if allr:
        r_ = np.concatenate(allr); w_ = np.concatenate(allw)
        R['stretch']['p99_stretch'] = round(wq(r_, w_, 0.99), 4); R['stretch']['p99_compression_ratio'] = round(wq(r_, w_, 0.01), 4)
reset_pose()

# ---------------------------------------------------------------- export Rig + gear (the body is not exported)
if os.path.exists(A['out']): os.remove(A['out'])
bpy.ops.object.select_all(action='DESELECT')
rig.select_set(True); gear.select_set(True); bpy.context.view_layer.objects.active = rig
bpy.ops.export_scene.gltf(filepath=A['out'], export_format='GLB', use_selection=True, export_apply=False)
R['exported'] = os.path.exists(A['out'])
P('DONE', json.dumps({k: R[k] for k in ('weights', 'stretch', 'exported')}))
finish(0)
