import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=r'C:\Users\dom12\Documents\agent_tests\mpfb\male_muscular.glb')
for o in bpy.data.objects: print('OBJ',o.type,o.name,len(o.data.vertices) if o.type=='MESH' else '',tuple(round(x,3) for x in o.dimensions),[g.name for g in o.vertex_groups][:3] if o.type=='MESH' else '', [m.type for m in o.modifiers] if o.type=='MESH' else '')
