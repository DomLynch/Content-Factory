import numpy as np, bpy
bpy.ops.wm.open_mainfile(filepath=r'C:\Users\dom12\Documents\agent_tests\bind3\out\male_muscular.blend')
rig=bpy.data.objects['Rig']; body=bpy.data.objects['Body']
co=np.array([v.co[:] for v in body.data.vertices])
for n in ('LeftHand','LeftHandMiddle1','LeftHandMiddle3','LeftHandMiddle4'):
    b=rig.data.bones['mixamorig:'+n]; print('DB rig',n,np.round(b.head_local,3),np.round(b.tail_local,3))
m=(co[:,0]>0.60)
print('DB target verts x>0.6:',m.sum(),'x range',co[m,0].min(),co[m,0].max(),'y',co[m,1].min(),co[m,1].max(),'z',co[m,2].min(),co[m,2].max())
z=np.load('donor_cache/mh-giant-01.npz'); d=z['co']; J=z['J'].astype(int); W=z['W']
names=[b.name for b in rig.data.bones if b.name.startswith('mixamorig:') or '_twist_' in b.name]
for n in ('LeftHand','LeftHandMiddle1','LeftHandMiddle3'):
    i=names.index('mixamorig:'+n); sel=(J==i)&(W>0.5); r=np.where(sel.any(1))[0]
    print('DB donor giant',n,len(r),np.round(d[r].mean(0),3),'min',np.round(d[r].min(0),3),'max',np.round(d[r].max(0),3))
