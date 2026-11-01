import numpy as np, bpy, sys
bpy.ops.wm.open_mainfile(filepath=r'C:\Users\dom12\Documents\agent_tests\bind3\out\male_muscular.blend')
body=bpy.data.objects['Body']; rig=bpy.data.objects['Rig']; me=body.data
co=np.array([v.co[:] for v in me.vertices]); gn={g.index:g.name for g in body.vertex_groups}
ed=np.array([e.vertices[:] for e in me.edges])
# weight dict
W=[{gn[g.group].replace('mixamorig:',''):round(g.weight,2) for g in v.groups} for v in me.vertices]
# head region: z>1.5
# find edges with large weight difference between endpoints (L1 distance over bone weights) in head region
def l1(a,b):
    ks=set(a)|set(b); return sum(abs(a.get(k,0)-b.get(k,0)) for k in ks)
d=np.array([l1(W[a],W[b]) for a,b in ed])
mid=(co[ed[:,0]]+co[ed[:,1]])/2
L=np.linalg.norm(co[ed[:,0]]-co[ed[:,1]],axis=1)
for nm,m in (('head z>1.5',mid[:,2]>1.5),('all',mid[:,2]>0)):
    print('DB',nm,'edges',m.sum(),'frac L1>1.0:',(d[m]>1.0).mean(),'>0.5',(d[m]>0.5).mean())
idx=np.where((mid[:,2]>1.5)&(d>1.0))[0][:12]
for i in idx: print('DB',np.round(mid[i],3),'L',round(L[i],4),W[ed[i,0]],W[ed[i,1]])
