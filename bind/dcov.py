import numpy as np, glob, bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=r'C:\HANDOVER-Macbook\extracted\batch1-2\dev\demon2-build\ref\psyche-106-v3.glb')
rig=[o for o in bpy.data.objects if o.type=='ARMATURE'][0]
body=[b.name for b in rig.data.bones if b.name.startswith('mixamorig:') or '_twist_' in b.name]
for f in sorted(glob.glob('donor_cache/*.npz')):
    z=np.load(f); J=z['J'].astype(int);W=z['W']
    cnt=np.bincount(J[W>0.05].ravel(),minlength=80)
    print('DC',f[-14:], 'zero bones:',[body[i].replace('mixamorig:','') for i in range(80) if cnt[i]==0])
    if 'male-01' in f: print('DC counts', {body[i].replace('mixamorig:',''):int(cnt[i]) for i in range(80) if 'Hand' in body[i]})
