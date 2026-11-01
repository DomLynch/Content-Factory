import bpy, math
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=r'C:\Users\dom12\Documents\agent_tests\mpfb\male_muscular.glb', merge_vertices=True)
ms=[o for o in bpy.data.objects if o.type=='MESH' and len(o.data.polygons)>0 and o.name!='Icosphere']
for o in list(bpy.data.objects):
    if o not in ms: bpy.data.objects.remove(o,do_unlink=True)
for o in ms:
    o.parent=None; o.vertex_groups.clear()
    for m in list(o.modifiers): o.modifiers.remove(m)
bpy.ops.object.select_all(action='DESELECT')
for o in ms: o.select_set(True)
bpy.context.view_layer.objects.active=ms[0]; bpy.ops.object.join()
b=bpy.context.view_layer.objects.active
b.data.materials.clear()
b.data.remesh_voxel_size=0.014; bpy.ops.object.voxel_remesh()
# a crude loose "tunic": displace torso/upper-leg verts outward to fuse clothing with body
import bmesh
bm=bmesh.new(); bm.from_mesh(b.data)
zs=[v.co.z for v in bm.verts]; zmin=min(zs); H=max(zs)-zmin
for v in bm.verts:
    t=(v.co.z-zmin)/H
    if 0.50<t<0.82 and abs(v.co.x)<0.3*H*0.5+0.1 and v.co.y<0.2:
        k=0.035*math.sin((t-0.5)/0.32*math.pi)
        v.co.x+=k*(1 if v.co.x>0 else -1); v.co.y-=k*0.5
bm.to_mesh(b.data); bm.free()
b.rotation_euler=(0,0,math.pi); bpy.ops.object.transform_apply(rotation=True)
print('R> remeshed verts',len(b.data.vertices))
bpy.ops.export_scene.gltf(filepath=r'C:\Users\dom12\Documents\agent_tests\bind2\trellis_like.glb', export_format='GLB')
