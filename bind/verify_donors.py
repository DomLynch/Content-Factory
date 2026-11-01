import bpy, json, glob, os
RIG=r'C:\HANDOVER-Macbook\extracted\batch1-2\dev\demon2-build\ref\psyche-106-v3.glb'
def load(p):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=p)
    arms=[o for o in bpy.data.objects if o.type=='ARMATURE']
    meshes=[o for o in bpy.data.objects if o.type=='MESH']
    return arms,meshes
arms,_=load(RIG); a=arms[0]
rb={b.name:(b.parent.name if b.parent else None) for b in a.data.bones}
print('REF bones',len(rb),'armatures',len(arms))
out={}
for p in sorted(glob.glob(r'C:\Users\dom12\Documents\donors\makehuman\mh-*-01.glb')):
    arms,meshes=load(p)
    r={'armatures':len(arms),'meshes':[(m.name,len(m.data.vertices),[md.type for md in m.modifiers],len(m.vertex_groups)) for m in meshes]}
    if arms:
        db={b.name:(b.parent.name if b.parent else None) for b in arms[0].data.bones}
        r['bones']=len(db); r['missing']=sorted(set(rb)-set(db)); r['extra']=sorted(set(db)-set(rb))
        r['parent_mismatch']=[n for n in db if n in rb and db[n]!=rb[n]]
        # vgroups vs bones
        vg=set()
        for m in meshes: vg|={g.name for g in m.vertex_groups}
        r['vg_not_in_ref']=sorted(vg-set(rb))
        r['ref_bones_with_vg']=len(vg&set(rb))
        zs=[]
        for m in meshes:
            zs+= [v.co.z for v in m.data.vertices]
        r['zrange']=(min(zs),max(zs)) if zs else None
        r['arm_matrix_scale']=tuple(arms[0].matrix_world.to_scale()); 
    out[os.path.basename(p)]=r
    print(os.path.basename(p), json.dumps(r)[:1500])
