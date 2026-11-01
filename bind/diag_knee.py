import bpy, sys, math, numpy as np
src_txt = open(r'C:\Users\dom12\Documents\agent_tests\bind3\diag_walk.py').read().split("frames = [1]")[0]
src_txt = src_txt.replace("SRC, OUTJ, TAG = argv[0], os.path.abspath(argv[1]), argv[2]", "SRC, OUTJ, TAG = argv[0], 'x', 'x'").replace("100440903--Walking.fbx", "121360901--Zombie Attack.fbx")
exec(src_txt)
body = bpy.data.objects['Body']; me = body.data; gn = {g.index: g.name for g in body.vertex_groups}
rest = np.array([v.co[:] for v in me.vertices])
KB = ['mixamorig:LeftUpLeg', 'thigh_twist_01_l', 'thigh_twist_02_l', 'mixamorig:LeftLeg', 'calf_twist_01_l', 'calf_twist_02_l', 'mixamorig:LeftFoot']
names = [b.name for b in tgt.data.bones]
print('DK> twist bones in rig:', [n for n in names if 'twist' in n and ('thigh' in n or 'calf' in n)])
kz = tgt.data.bones['mixamorig:LeftLeg'].head_local.z
W = np.zeros((len(me.vertices), len(KB)))
for v in me.vertices:
    for g in v.groups:
        n = gn[g.group]
        if n in KB: W[v.index, KB.index(n)] = g.weight
left = rest[:, 0] > 0.03
def eval_co():
    ev = body.evaluated_get(bpy.context.evaluated_depsgraph_get()); m = ev.to_mesh(); a = np.empty(len(m.vertices) * 3, np.float32); m.vertices.foreach_get('co', a); ev.to_mesh_clear(); return a.reshape(-1, 3).astype(float)
def size(p): c = np.cov((p - p.mean(0)).T); w = np.sort(np.linalg.eigvalsh(c))[::-1]; return math.sqrt(w[0] + w[1])
bins = np.arange(kz - 0.12, kz + 0.121, 0.03)
print('DK> knee z (LeftLeg head) = %.3f' % kz)
print('DK> rest weight profile by height above/below knee (mean over left-leg verts in the bin): ' + ' '.join(k.replace('mixamorig:', '')[:10] for k in KB))
for z0 in bins:
    m = left & (rest[:, 2] >= z0) & (rest[:, 2] < z0 + 0.03)
    if m.sum() > 5: print('DK> z %.2f..%.2f n=%3d  ' % (z0, z0 + 0.03, m.sum()) + ' '.join('%.2f' % x for x in W[m].mean(0)) + '  | sum UpLeg-side(Up+thighTw) %.2f  Leg-side(Leg+calfTw) %.2f' % (W[m][:, :3].sum(1).mean(), W[m][:, 3:6].sum(1).mean()))
for f in (20, 40, 60):
    apply(retarget_pose(f)); c = eval_co(); out = []
    for z0 in bins:
        m = left & (rest[:, 2] >= z0) & (rest[:, 2] < z0 + 0.03)
        if m.sum() > 8: out.append('%.2f:%.2f' % (z0 + 0.015, size(c[m]) / size(rest[m])))
    print('DK> attack frame %d ring size posed/rest by z: ' % f + ' '.join(out))
