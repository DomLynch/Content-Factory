import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=r'C:\HANDOVER-Macbook\extracted\batch1-2\dev\demon2-build\ref\psyche-106-v3.glb')
for o in bpy.data.objects: print('OBJ',o.type,o.name,o.parent.name if o.parent else None, tuple(round(x,3) for x in o.dimensions), tuple(o.scale), tuple(o.rotation_euler))
rig=[o for o in bpy.data.objects if o.type=='ARMATURE'][0]
for b in rig.data.bones: print('B',b.name,b.parent.name if b.parent else None,b.use_deform,tuple(round(x,3) for x in b.head_local),tuple(round(x,3) for x in b.tail_local))
m=[o for o in bpy.data.objects if o.type=='MESH']
for o in m: print('M',o.name,len(o.data.vertices),[md.type for md in o.modifiers])
