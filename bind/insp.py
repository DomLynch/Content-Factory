import bpy, glob, os, numpy as np
RIG=r'C:\HANDOVER-Macbook\extracted\batch1-2\dev\demon2-build\ref\psyche-106-v3.glb'
K=['mixamorig:Head','mixamorig:Hips','mixamorig:LeftArm','mixamorig:LeftForeArm','mixamorig:LeftHand','mixamorig:LeftHandMiddle4','mixamorig:LeftUpLeg','mixamorig:LeftLeg','mixamorig:LeftFoot','mixamorig:LeftToeBase']
def rep(p):
    bpy.ops.wm.read_factory_settings(use_empty=True); bpy.ops.import_scene.gltf(filepath=p)
    a=[o for o in bpy.data.objects if o.type=='ARMATURE'][0]
    print('==',os.path.basename(p),'arm parent',a.parent,'loc',tuple(round(x,3) for x in a.matrix_world.translation),'scale',tuple(a.matrix_world.to_scale()))
    for k in K:
        b=a.data.bones[k]; h=a.matrix_world@b.head_local; t=a.matrix_world@b.tail_local
        print('  ',k,np.round(h,3),np.round(t,3))
    for m in bpy.data.objects:
        if m.type=='MESH' and m.name in('Steel','Layer_Tunic'):
            co=np.array([(m.matrix_world@v.co)[:] for v in m.data.vertices]); print('  mesh',m.name,'min',np.round(co.min(0),3),'max',np.round(co.max(0),3),'shapekeys',bool(m.data.shape_keys),'parent',m.parent.name if m.parent else None, 'faces',len(m.data.polygons))
rep(RIG)
for p in sorted(glob.glob(r'C:\Users\dom12\Documents\donors\makehuman\mh-*-01.glb')): rep(p)
