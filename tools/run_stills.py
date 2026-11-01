"""Render four stills (head crop, Idle, Walk, Attack) of a bound GLB with Blender in the background, plus a thumbnail, one receipt.
Usage: run_stills.py <asset_id> <bound.glb>
Settings come from config/config.json ("stills") and config.local.json; nothing here is a literal path or number.
CPU only: Cycles on CPU with a low sample count from config (no EEVEE, no workbench, no GPU).
Clips are looked up by id as <mixamo_clips_dir>/<id><clip_ext>; a missing clip stops the run before Blender starts. Nothing is downloaded."""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

# Runs inside Blender (--background). Reads a json of args, writes a json of results.
BLENDER_SCRIPT = r'''
import bpy, json, sys, os, math, struct, time
from mathutils import Vector, Matrix, Quaternion
A = json.load(open(sys.argv[sys.argv.index('--') + 1]))
S = A['cfg']
T0 = time.time()
def P(*a): print('ST>', *a, flush=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=A['bound'])
rigs = [o for o in bpy.data.objects if o.type == 'ARMATURE']
rig = bpy.data.objects.get(S['rig_name']) or rigs[0]
meshes = [o for o in bpy.data.objects if o.type == 'MESH']
if not meshes: P('ERROR no mesh in bound glb'); sys.exit(3)
bpy.context.view_layer.update()
pts = [m.matrix_world @ Vector(c) for m in meshes for c in m.bound_box]
zmin = min(p.z for p in pts); zmax = max(p.z for p in pts); H = zmax - zmin
cx = 0.5 * (min(p.x for p in pts) + max(p.x for p in pts)); cy = 0.5 * (min(p.y for p in pts) + max(p.y for p in pts))
bones = rig.data.bones
hb = bones[S['head_bone']]
head_c = rig.matrix_world @ ((hb.head_local + hb.tail_local) * 0.5)
P('body height %.3f m, head centre z %.3f' % (H, head_c.z))

scn = bpy.context.scene
scn.render.engine = S['engine']
scn.cycles.device = S['device']
scn.cycles.samples = S['samples']
scn.cycles.use_denoising = bool(S['denoise'])
if S['threads'] > 0:
    scn.render.threads_mode = 'FIXED'; scn.render.threads = S['threads']
scn.render.resolution_x = S['width']; scn.render.resolution_y = S['height']; scn.render.resolution_percentage = 100
scn.render.image_settings.file_format = 'PNG'
w = bpy.data.worlds.new('w'); scn.world = w; w.use_nodes = True
bg = w.node_tree.nodes['Background']; bg.inputs[0].default_value = tuple(S['world_color']) + (1.0,); bg.inputs[1].default_value = S['world_strength']
sd = bpy.data.lights.new('sun', 'SUN'); sd.energy = S['sun_energy']
sun = bpy.data.objects.new('sun', sd); scn.collection.objects.link(sun)
sun.rotation_euler = tuple(math.radians(d) for d in S['sun_euler_deg'])
cd = bpy.data.cameras.new('cam'); cd.lens = S['lens_mm']; cd.sensor_width = S['sensor_mm']; cd.sensor_fit = 'VERTICAL'
cam = bpy.data.objects.new('cam', cd); scn.collection.objects.link(cam); scn.camera = cam
fov = 2 * math.atan(S['sensor_mm'] / 2 / S['lens_mm'])
cdir = Vector(S['camera_dir']).normalized()
def shot(path, target, frame_h):
    dist = (frame_h / 2) / math.tan(fov / 2)
    cam.location = target + cdir * dist
    cam.rotation_euler = (target - cam.location).to_track_quat('-Z', 'Y').to_euler()
    scn.render.filepath = path
    t = time.time(); bpy.ops.render.render(write_still=True); return round(time.time() - t, 1)

# ---- retarget one clip glb (bone-named empties, no skin) onto the rig, same maths as bind/metrics2.py retarget
C = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
def gltf_rest(path):
    with open(path, 'rb') as f:
        f.read(12); n, _ = struct.unpack('<II', f.read(8)); j = json.loads(f.read(n))
    nodes = j['nodes']; parent = {}
    for i, nd in enumerate(nodes):
        for c in nd.get('children', []): parent[c] = i
    def local(nd):
        if 'matrix' in nd:
            m = nd['matrix']; return Matrix([[m[r + 4 * c] for c in range(4)] for r in range(4)])
        x, y, z, wq = nd.get('rotation', [0, 0, 0, 1]); s = nd.get('scale', [1, 1, 1])
        return Matrix.Translation(nd.get('translation', [0, 0, 0])) @ Quaternion((wq, x, y, z)).to_matrix().to_4x4() @ Matrix.Diagonal((s[0], s[1], s[2], 1))
    memo = {}
    def world(i):
        if i not in memo: memo[i] = local(nodes[i]) if i not in parent else world(parent[i]) @ local(nodes[i])
        return memo[i]
    return {nodes[i]['name']: C @ world(i) @ C.inverted() for i in range(len(nodes))}
r_rest = {b.name: rig.matrix_world @ b.matrix_local for b in bones}
def depth(b):
    d = 0
    while b.parent: b = b.parent; d += 1
    return d
order = sorted(bones, key=depth)
def reset_pose():
    for pb in rig.pose.bones: pb.rotation_mode = 'QUATERNION'; pb.rotation_quaternion = (1, 0, 0, 0); pb.location = (0, 0, 0)
    bpy.context.view_layer.update()
def pose_from_clip(path):
    s_rest = gltf_rest(path)
    before = set(bpy.data.objects); bpy.ops.import_scene.gltf(filepath=path)
    new_ = [o for o in bpy.data.objects if o not in before]
    emp = {o.name: o for o in new_ if o.animation_data and o.animation_data.action}
    if not emp: P('ERROR clip has no animated nodes:', path); sys.exit(4)
    act = next(iter(emp.values())).animation_data.action
    f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
    f = (f0 + f1) // 2 if S['clip_frame'] == 'mid' else int(S['clip_frame'])
    scn.frame_set(f)
    mapped = [n for n in emp if n in r_rest and n in s_rest]
    sp = {n: emp[n].matrix_world.copy() for n in mapped}
    hips = S['hips_bone']; hscale = r_rest[hips].translation.z / s_rest[hips].translation.z
    reset_pose(); pose_w = {}
    for b in order:
        n = b.name; par = b.parent
        chain = r_rest[n] if par is None else pose_w[par.name] @ r_rest[par.name].inverted() @ r_rest[n]
        if n in sp:
            dR = sp[n].to_3x3() @ s_rest[n].to_3x3().inverted(); tgt = (dR @ r_rest[n].to_3x3()).to_4x4(); tr = chain.translation.copy()
            if n == hips: tr = r_rest[n].translation + (sp[n].translation - s_rest[hips].translation) * hscale
            tgt.translation = tr
        else: tgt = chain
        pose_w[n] = tgt
        if n in sp:
            basis = (r_rest[n].inverted() @ tgt) if par is None else (pose_w[par.name] @ r_rest[par.name].inverted() @ r_rest[n]).inverted() @ tgt
            pb = rig.pose.bones[n]; pb.rotation_quaternion = basis.to_quaternion()
            if n == hips: pb.location = basis.to_translation()
    for o in new_: bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.view_layer.update()
    return f, len(mapped)

out = A['out']; os.makedirs(out, exist_ok=True); R = {'renders': {}, 'body_height_m': round(H, 3)}
body_t = Vector((cx, cy, zmin + H * S['cam_height_frac']))
reset_pose()
R['renders']['head'] = {'s': shot(os.path.join(out, 'head.png'), Vector((cx, cy, head_c.z)), H * S['head_height_frac'])}
for key in ('idle', 'walk', 'attack'):
    f, nm = pose_from_clip(A['clips'][key])
    R['renders'][key] = {'frame': f, 'matched_bones': nm, 's': shot(os.path.join(out, key + '.png'), body_t, H * S['body_margin'])}
    P(key, R['renders'][key])
img = bpy.data.images.load(os.path.join(out, S['thumb_source'] + '.png'))
img.scale(S['thumb_px'], S['thumb_px']); img.filepath_raw = os.path.join(out, 'thumb.png'); img.file_format = 'PNG'; img.save()
R['total_s'] = round(time.time() - T0, 1)
json.dump(R, open(A['result'], 'w'), indent=1)
P('DONE', R['total_s'])
'''

if len(sys.argv) != 3:
    sys.exit(__doc__)
asset_id, bound = sys.argv[1], os.path.abspath(sys.argv[2])

cfg = cf_common.load_config()
loc, st = cfg["local"], cfg["stills"]
work = os.path.join(loc["outputs_dir"], "3d", asset_id)
out = os.path.join(work, st["subdir"])
os.makedirs(out, exist_ok=True)
log = os.path.join(loc["logs_dir"], f"stills_{asset_id}.txt")
clips = {k: os.path.join(loc["mixamo_clips_dir"], v + st["clip_ext"]) for k, v in st["clips"].items()}
params = {k: v for k, v in st.items() if k not in ("clips", "subdir")}
params.update(clips=st["clips"], clips_dir=loc["mixamo_clips_dir"], blender=loc["blender_exe"], log=log)
names = ["head"] + list(st["clips"]) + ["thumb"]
pngs = [os.path.join(out, n + ".png") for n in names]


def receipt(status, error="", timing=None, mem=None, extra=None):
    cf_common.write_receipt(cfg, asset_id, "stills", tool="run_stills.py (Blender Cycles CPU)", model={"id": "Blender " + os.path.basename(os.path.dirname(loc["blender_exe"]))},
                            inputs=[bound] + list(clips.values()), params={**params, **(extra or {})}, timing=timing or {}, memory=mem or {}, outputs=pngs, status=status, error=error)


problems = [f"{k} clip {st['clips'][k]} not found at {p}" for k, p in clips.items() if not os.path.exists(p)]
for k in ("blender_exe", "mixamo_clips_dir"):
    if not os.path.exists(loc[k]):
        problems.append(f"{k} not found: {loc[k]}")
if not os.path.exists(bound):
    problems.append(f"bound glb not found: {bound}")
if problems:
    receipt("fail", "; ".join(problems))
    sys.exit("cannot render stills:\n  " + "\n  ".join(problems))

script = os.path.join(out, "_stills_blender.py")
argf = os.path.join(out, "_stills_args.json")
resf = os.path.join(out, "_stills_result.json")
with open(script, "w", encoding="utf-8") as f:
    f.write(BLENDER_SCRIPT)
with open(argf, "w", encoding="utf-8") as f:
    json.dump({"cfg": st, "bound": bound, "clips": clips, "out": out, "result": resf}, f)
for p in pngs + [resf]:
    if os.path.exists(p):
        os.remove(p)

ram = cf_common.FreeRamWatch(); ram.start()
t0 = time.time()
try:
    with open(log, "w", encoding="utf-8") as lf:
        r = subprocess.run([loc["blender_exe"], "--background", "--python", script, "--", argf], stdout=lf, stderr=subprocess.STDOUT, timeout=st["timeout_s"]).returncode
except subprocess.TimeoutExpired:
    r = "timeout"
res = json.load(open(resf, encoding="utf-8")) if os.path.exists(resf) else {}
ok = r == 0 and all(os.path.exists(p) for p in pngs)
receipt("ok" if ok else "fail", "" if ok else f"blender exit {r}, see log", timing={"wall_s": round(time.time() - t0, 1)}, mem={"lowest_free_ram_gb": ram.stop()}, extra={"result": res})
print("stills", "ok" if ok else "FAIL", "->", out)
sys.exit(0 if ok else 1)
