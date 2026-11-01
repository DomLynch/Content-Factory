import bpy, sys, numpy as np
NAME=sys.argv[sys.argv.index('--')+1]; OUTG=sys.argv[sys.argv.index('--')+2]
from mathutils import Matrix
RIG=r'C:\HANDOVER-Macbook\extracted\batch1-2\dev\demon2-build\ref\psyche-106-v3.glb'
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=RIG)
rig=[o for o in bpy.data.objects if o.type=='ARMATURE'][0]
r_rest={b.name: rig.matrix_world@b.matrix_local for b in rig.data.bones}
b4=set(bpy.data.objects)
bpy.ops.import_scene.gltf(filepath=r'C:\Users\dom12\Documents\donors\makehuman\mh-%s-01.glb' % NAME)
new=[o for o in bpy.data.objects if o not in b4]
arm=[o for o in new if o.type=='ARMATURE'][0]; dm=[o for o in new if o.name.startswith('Steel') and o.type=='MESH' and len(o.vertex_groups)][0]
d_rest={b.name: arm.matrix_world@b.matrix_local for b in arm.data.bones}; pw={}
def dep(b):
    d=0
    while b.parent: b=b.parent; d+=1
    return d
for b in sorted(arm.data.bones,key=dep):
    n=b.name; par=b.parent
    ch=d_rest[n] if par is None else pw[par.name]@d_rest[par.name].inverted()@d_rest[n]
    t=r_rest[n].to_3x3().to_4x4(); t.translation=ch.translation; pw[n]=t
    pb=arm.pose.bones[n]; pb.rotation_mode='QUATERNION'; pb.rotation_quaternion=(ch.inverted()@t).to_quaternion()
bpy.context.view_layer.update()
ev=dm.evaluated_get(bpy.context.evaluated_depsgraph_get()); m=bpy.data.meshes.new_from_object(ev)
o=bpy.data.objects.new('donor_male_tpose',m); bpy.context.scene.collection.objects.link(o); o.matrix_world=dm.matrix_world
for x in list(bpy.data.objects):
    if x is not o: bpy.data.objects.remove(x,do_unlink=True)
bpy.ops.object.select_all(action='DESELECT'); o.select_set(True)
bpy.ops.export_scene.gltf(filepath=OUTG, use_selection=True, export_apply=False)
print('MK done',len(m.vertices))
