"""mesh_trim.py <in.glb> <out.glb> <frac_from_top> [axis=y]
Delete every face whose centroid is below a plane at <frac_from_top> of the mesh height (0 = top, 1 = bottom), keep UVs and the texture.
Used to cut the collar and shoulders off a head bust (they belong to the gear parts). CPU only; the source is never modified."""
import json, sys
import numpy as np, trimesh

src, dst, frac = sys.argv[1], sys.argv[2], float(sys.argv[3])
ax = {'x': 0, 'y': 1, 'z': 2}[sys.argv[4] if len(sys.argv) > 4 else 'y']
scene = trimesh.load(src)
g = list(scene.geometry.values())[0] if hasattr(scene, 'geometry') else scene
v = np.asarray(g.vertices)
lo, hi = v[:, ax].min(), v[:, ax].max()
cut = hi - frac * (hi - lo)
keep = g.triangles_center[:, ax] >= cut
before = len(g.faces)
g.update_faces(keep)
g.remove_unreferenced_vertices()
scene.export(dst)
stat = {'in': src, 'axis': ax, 'cut': float(cut), 'frac_from_top': frac, 'faces_before': before, 'faces_after': int(len(g.faces))}
json.dump(stat, open(dst + '.trim.json', 'w'), indent=1)
print(json.dumps(stat))
