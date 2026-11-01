import os
import bpy, sys, numpy as np
bpy.ops.wm.open_mainfile(filepath=sys.argv[sys.argv.index('--')+1])
body=bpy.data.objects['Body']; rig=bpy.data.objects['Rig']; gn={g.index:g.name for g in body.vertex_groups}
co=np.array([v.co[:] for v in body.data.vertices]); dom=np.array([gn[max(v.groups,key=lambda g:g.weight).group] for v in body.data.vertices])
for s in ('Left',):
    for a,b in (('Arm','ForeArm'),('ForeArm','Hand')):
        xa=co[dom=='mixamorig:%s%s'%(s,a)][:,0]; xb=co[dom=='mixamorig:%s%s'%(s,b)][:,0]
        j=rig.data.bones['mixamorig:%s%s'%(s,b)].head_local.x
        print('DJ>',os.path.basename(sys.argv[-1]),a,'->',b,'rig joint x=%.3f'%j,'| dominant %s x p5-p95 %.3f..%.3f | %s %.3f..%.3f'%(a,np.percentile(xa,5),np.percentile(xa,95),b,np.percentile(xb,5),np.percentile(xb,95)))
