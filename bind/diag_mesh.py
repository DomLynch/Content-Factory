import bpy, sys, math, numpy as np
sys.argv = sys.argv  # args after --: blend
exec(open(r'C:\Users\dom12\Documents\agent_tests\bind3\diag_walk.py').read().split("frames = [1]")[0].replace("SRC, OUTJ, TAG = argv[0], os.path.abspath(argv[1]), argv[2]","SRC, OUTJ, TAG = argv[0], 'x', 'x'"))
body = bpy.data.objects['Body']; gn = {g.index: g.name for g in body.vertex_groups}
dom = np.array([gn[max(v.groups, key=lambda g: g.weight).group] for v in body.data.vertices])
def mesh_co():
    ev = body.evaluated_get(bpy.context.evaluated_depsgraph_get()); m = ev.to_mesh(); a = np.empty(len(m.vertices) * 3, np.float32); m.vertices.foreach_get('co', a); ev.to_mesh_clear(); return a.reshape(-1, 3).astype(float)
def angv(a, b): a = a / np.linalg.norm(a); b = b / np.linalg.norm(b); return math.degrees(math.acos(np.clip(a @ b, -1, 1)))
def report(label):
    c = mesh_co(); s = []
    for side in ('Left', 'Right'):
        cen = {j: c[dom == 'mixamorig:%s%s' % (side, j)].mean(0) for j in ('Arm', 'ForeArm', 'Hand')}
        bh = {j: np.array(tgt.matrix_world @ tgt.pose.bones['mixamorig:%s%s' % (side, j)].head) for j in ('Arm', 'ForeArm', 'Hand')}
        em = angv(cen['ForeArm'] - cen['Arm'], cen['Hand'] - cen['ForeArm']); eb = angv(bh['ForeArm'] - bh['Arm'], bh['Hand'] - bh['ForeArm'])
        off = np.linalg.norm(c[dom == 'mixamorig:%sForeArm' % side] - bh['ForeArm'], axis=1).mean()
        s.append('%s elbow mesh-centroid %.1f deg vs bone %.1f deg' % (side, em, eb))
    print('DM>', label, ' | '.join(s))
for pb in tgt.pose.bones: pb.rotation_mode = 'QUATERNION'; pb.rotation_quaternion = (1, 0, 0, 0); pb.location = (0, 0, 0)
bpy.context.view_layer.update(); report('rest')
for f in (10, 20, 45, 50):
    apply(retarget_pose(f)); report('walk%d' % f)
