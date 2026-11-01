"""Render four stills (head crop, Idle, Walk, Attack) of a bound GLB with Blender in the background, plus a thumbnail, one receipt.
Usage: run_stills.py <asset_id> <bound.glb> [--turntable]
Default look is a LIT PBR presentation shot (config stills.lit): three-point area-light rig, dark graded cyclorama backdrop whose floor catches a
soft contact shadow, glTF materials as imported, Cycles CPU with OIDN denoise when available. stills.lit.enabled=false restores the flat sun look.
--turntable renders a 360 degree orbit (the character turns on a pivot; camera, lights and backdrop stay put) as N frames into <stills>/<frames_subdir>,
then encodes an mp4 if ffmpeg is on PATH (otherwise the frames are kept and a message says so). N, size and samples are in stills.lit.turntable.
Settings come from config/config.json ("stills") and config.local.json; nothing here is a literal path or number.
CPU only: Cycles on CPU with a low sample count from config (no EEVEE, no workbench, no GPU).
Clips are looked up by id as <mixamo_clips_dir>/<id><clip_ext>; a missing clip stops the run before Blender starts. Nothing is downloaded."""
import glob, json, os, shutil, subprocess, sys, time

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
IMPORTED_ROOTS = [o for o in bpy.data.objects if o.parent is None]
rigs = [o for o in bpy.data.objects if o.type == 'ARMATURE']
rig =bpy.data.objects.get(S['rig_name']) or rigs[0]
meshes = [o for o in bpy.data.objects if o.type == 'MESH']
# the glTF importer also adds hidden bone-shape helper meshes (Icosphere, 2 m wide); only the skinned character meshes may drive framing
skinned = [o for o in meshes if any(md.type == 'ARMATURE' for md in o.modifiers)]
meshes = skinned or [o for o in meshes if not o.hide_render and not o.hide_get()]
if not meshes: P('ERROR no mesh in bound glb'); sys.exit(3)
bpy.context.view_layer.update()
import numpy as np
def eval_bounds():
    # bounds of the EVALUATED (posed, skinned) meshes in world space: (min xyz, max xyz)
    bpy.context.view_layer.update(); dg = bpy.context.evaluated_depsgraph_get(); lo = None
    for m in meshes:
        e = m.evaluated_get(dg); me = e.to_mesh(); n = len(me.vertices)
        if n == 0: e.to_mesh_clear(); continue
        co = np.empty(n * 3, dtype=np.float32); me.vertices.foreach_get('co', co); co = co.reshape(n, 3)
        W = np.array(e.matrix_world); w = co @ W[:3, :3].T + W[:3, 3]; e.to_mesh_clear()
        a, b = w.min(axis=0), w.max(axis=0)
        lo, hi = (a, b) if lo is None else (np.minimum(lo, a), np.maximum(hi, b))
    return Vector(lo.tolist()), Vector(hi.tolist())
bones = rig.data.bones
hb = bones[S['head_bone']]
bpy.context.view_layer.update()
bmin, bmax = eval_bounds(); H = bmax.z - bmin.z
head_c = rig.matrix_world @ ((hb.head_local + hb.tail_local) * 0.5)
P('body height %.3f m, head centre z %.3f, bounds' % (H, head_c.z), tuple(bmin), tuple(bmax))

scn = bpy.context.scene
L = S.get('lit') or {}
LIT = bool(L.get('enabled'))
TURN = A.get('mode') == 'turntable'
TT = L.get('turntable', {})
scn.render.engine = S['engine']
scn.cycles.device = S['device']
scn.cycles.samples = TT['samples'] if TURN else (L['samples'] if LIT else S['samples'])
use_dn = bool(L['denoise']) if LIT else bool(S['denoise'])
scn.cycles.use_denoising = use_dn
if use_dn and LIT:
    try:
        scn.cycles.denoiser = L['denoiser']
    except Exception as e:
        P('denoiser', L['denoiser'], 'unavailable, denoise OFF:', e); scn.cycles.use_denoising = False
    try: scn.cycles.denoising_input_passes = 'RGB_ALBEDO_NORMAL'
    except Exception as e: P('denoise passes not set:', e)
if LIT:
    try: scn.cycles.sample_clamp_indirect = L['clamp_indirect']
    except Exception as e: P('clamp not set:', e)
    try: scn.view_settings.view_transform = L['view_transform']
    except Exception as e: P('view transform', L['view_transform'], 'unavailable, keeping default:', e)
    scn.view_settings.exposure = L['exposure']
if S['threads'] > 0:
    scn.render.threads_mode = 'FIXED'; scn.render.threads = S['threads']
RW, RH = (TT['width'], TT['height']) if TURN else ((L['width'], L['height']) if LIT else (S['width'], S['height']))
scn.render.resolution_x = RW; scn.render.resolution_y = RH; scn.render.resolution_percentage = 100
scn.render.image_settings.file_format = 'PNG'
w = bpy.data.worlds.new('w'); scn.world = w; w.use_nodes = True
bg = w.node_tree.nodes['Background']
if LIT:
    bg.inputs[0].default_value = tuple(L['world_color']) + (1.0,); bg.inputs[1].default_value = L['world_strength']
else:
    bg.inputs[0].default_value = tuple(S['world_color']) + (1.0,); bg.inputs[1].default_value = S['world_strength']
    sd = bpy.data.lights.new('sun', 'SUN'); sd.energy = S['sun_energy']
    sun = bpy.data.objects.new('sun', sd); scn.collection.objects.link(sun)
    sun.rotation_euler = tuple(math.radians(d) for d in S['sun_euler_deg'])

def kelvin_rgb(k):
    # Tanner Helland blackbody approximation, returned as linear rgb
    t = k / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * ((t - 60) ** -0.1332047592)
    g = (99.4708025861 * math.log(t) - 161.1195681661) if t <= 66 else 288.1221695283 * ((t - 60) ** -0.0755148492)
    b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    return tuple((max(0.0, min(255.0, c)) / 255.0) ** 2.2 for c in (r, g, b))

LIT_OBJS = []
def clear_lit():
    for o in LIT_OBJS:
        d = o.data; bpy.data.objects.remove(o, do_unlink=True)
        if isinstance(d, bpy.types.Light): bpy.data.lights.remove(d)
        elif isinstance(d, bpy.types.Mesh): bpy.data.meshes.remove(d)
    LIT_OBJS.clear()
def cyc_material(Bd, v_fig):
    m = bpy.data.materials.new('cyc'); m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
    out_ = nt.nodes.new('ShaderNodeOutputMaterial'); pr = nt.nodes.new('ShaderNodeBsdfPrincipled'); pr.inputs['Roughness'].default_value = Bd['roughness']
    uv = nt.nodes.new('ShaderNodeUVMap'); uv.uv_map = 'UVMap'
    sep = nt.nodes.new('ShaderNodeSeparateXYZ'); nt.links.new(uv.outputs[0], sep.inputs[0])
    ramp = nt.nodes.new('ShaderNodeValToRGB'); el = ramp.color_ramp.elements
    el[0].position = 0.0; el[0].color = tuple(Bd['color_bottom']) + (1.0,)
    el[1].position = 1.0; el[1].color = tuple(Bd['color_top']) + (1.0,)
    e = el.new(max(0.01, min(0.99, v_fig + Bd['mid_offset']))); e.color = tuple(Bd['color_mid']) + (1.0,)
    nt.links.new(sep.outputs['Y'], ramp.inputs[0])
    # side falloff (vignette across the width): 1 - k * (2|u-0.5|)^2
    sub = nt.nodes.new('ShaderNodeMath'); sub.operation = 'SUBTRACT'; sub.inputs[1].default_value = 0.5; nt.links.new(sep.outputs['X'], sub.inputs[0])
    ab = nt.nodes.new('ShaderNodeMath'); ab.operation = 'ABSOLUTE'; nt.links.new(sub.outputs[0], ab.inputs[0])
    dbl = nt.nodes.new('ShaderNodeMath'); dbl.operation = 'MULTIPLY'; dbl.inputs[1].default_value = 2.0; nt.links.new(ab.outputs[0], dbl.inputs[0])
    sq = nt.nodes.new('ShaderNodeMath'); sq.operation = 'POWER'; sq.inputs[1].default_value = 2.0; nt.links.new(dbl.outputs[0], sq.inputs[0])
    kk = nt.nodes.new('ShaderNodeMath'); kk.operation = 'MULTIPLY_ADD'; kk.inputs[1].default_value = -Bd['side_falloff']; kk.inputs[2].default_value = 1.0; nt.links.new(sq.outputs[0], kk.inputs[0])
    cb = nt.nodes.new('ShaderNodeCombineXYZ')
    for i in range(3): nt.links.new(kk.outputs[0], cb.inputs[i])
    mix = nt.nodes.new('ShaderNodeMix'); mix.data_type = 'RGBA'; mix.blend_type = 'MULTIPLY'; mix.inputs[0].default_value = 1.0
    nt.links.new(ramp.outputs[0], mix.inputs[6]); nt.links.new(cb.outputs[0], mix.inputs[7])
    nt.links.new(mix.outputs[2], pr.inputs['Base Color']); nt.links.new(pr.outputs[0], out_.inputs[0])
    return m
def setup_lit(lo, hi):
    # three-point area rig + cyclorama sized from the (posed) body bounds lo/hi; replaces any previous rig
    clear_lit()
    Hh = hi.z - lo.z; cx, cy = (lo.x + hi.x) / 2, (lo.y + hi.y) / 2; zf = lo.z
    aim = Vector((cx, cy, zf + Hh * L['aim_z_frac'])); esc = (Hh / L['ref_height_m']) ** 2
    for d in L['lights']:
        ld = bpy.data.lights.new(d['name'], 'AREA'); ld.shape = 'SQUARE'; ld.size = d['size'] * Hh; ld.energy = d['energy'] * esc; ld.color = kelvin_rgb(d['temp_k'])
        ob = bpy.data.objects.new(d['name'], ld); scn.collection.objects.link(ob)
        ob.location = Vector((cx + d['pos'][0] * Hh, cy + d['pos'][1] * Hh, zf + d['pos'][2] * Hh))
        ob.rotation_euler = (aim - ob.location).to_track_quat('-Z', 'Y').to_euler()
        ob.visible_camera = False
        LIT_OBJS.append(ob)
    Bd = L['backdrop']; Wd = Bd['width_h'] * Hh; R_ = Bd['curve_radius_h'] * Hh; fr = Bd['floor_front_h'] * Hh; bk = Bd['floor_back_h'] * Hh; wh = Bd['wall_height_h'] * Hh
    prof = [(-fr, 0.0), (bk - R_, 0.0)]
    nseg = 16
    for i in range(1, nseg + 1):
        t = (math.pi / 2) * i / nseg; prof.append((bk - R_ + R_ * math.sin(t), R_ - R_ * math.cos(t)))
    prof.append((bk, wh))
    acc = [0.0]
    for i in range(1, len(prof)): acc.append(acc[-1] + math.hypot(prof[i][0] - prof[i - 1][0], prof[i][1] - prof[i - 1][1]))
    vv = [a / acc[-1] for a in acc]; v_fig = fr / acc[-1]  # the figure stands at local y = 0, fr metres along the floor profile
    verts, uvs = [], []
    for (y, z), v in zip(prof, vv):
        verts.append((-Wd / 2, y, z)); uvs.append((0.0, v)); verts.append((Wd / 2, y, z)); uvs.append((1.0, v))
    faces = [(2 * i, 2 * i + 1, 2 * i + 3, 2 * i + 2) for i in range(len(prof) - 1)]  # normals face up on the floor and toward -Y (the camera) on the wall
    me = bpy.data.meshes.new('cyc'); me.from_pydata(verts, [], faces); me.update()
    for p_ in me.polygons: p_.use_smooth = True
    uvl = me.uv_layers.new(name='UVMap')
    for li, lp in enumerate(me.loops): uvl.data[li].uv = uvs[lp.vertex_index]
    me.materials.append(cyc_material(Bd, v_fig))
    cy_ob = bpy.data.objects.new('cyc', me); scn.collection.objects.link(cy_ob); cy_ob.location = (cx, cy, zf)
    LIT_OBJS.append(cy_ob)
cd =bpy.data.cameras.new('cam'); cd.lens = S['lens_mm']; cd.sensor_width = S['sensor_mm']; cd.sensor_height = S['sensor_mm']; cd.sensor_fit = 'VERTICAL'  # vertical fit reads sensor_height (default 24 mm), so set it too or the frame is 1.5x too tight
cam = bpy.data.objects.new('cam', cd); scn.collection.objects.link(cam); scn.camera = cam
fov = 2 * math.atan(S['sensor_mm'] / 2 / S['lens_mm'])
cdir = Vector(S['camera_dir']).normalized()
def place_cam(target, frame_h):
    dist = (frame_h / 2) / math.tan(fov / 2)
    cam.location = target + cdir * dist
    cam.rotation_euler = (target - cam.location).to_track_quat('-Z', 'Y').to_euler()
def shot(path, target, frame_h):
    place_cam(target, frame_h)
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

out = A['out']; os.makedirs(out, exist_ok=True); R = {'renders': {}}
if TURN:
    fdir = os.path.join(out, TT['frames_subdir']); os.makedirs(fdir, exist_ok=True)
    reset_pose(); R['pose'] = TT['pose']
    if TT['pose'] == 'idle': f, nm = pose_from_clip(A['clips']['idle']); R['clip_frame'] = f; R['matched_bones'] = nm
    lo, hi = eval_bounds(); c = (lo + hi) * 0.5
    span = max(hi.z - lo.z, math.hypot(hi.x - lo.x, hi.y - lo.y))  # a turning box stays inside its horizontal diagonal
    if LIT: setup_lit(lo, hi)
    pivot = bpy.data.objects.new('pivot', None); scn.collection.objects.link(pivot); pivot.location = (c.x, c.y, lo.z)
    bpy.context.view_layer.update(); inv = pivot.matrix_world.inverted()
    for o in IMPORTED_ROOTS:
        if o.name in bpy.data.objects: o.parent = pivot; o.matrix_parent_inverse = inv
    place_cam(c, span * S['body_margin'])
    N = TT['frames']; t0 = time.time()
    for i in range(N):
        pivot.rotation_euler = (0, 0, math.radians(TT['start_deg'] + 360.0 * i / N))
        scn.render.filepath = os.path.join(fdir, TT['frame_pattern'] % (i + 1))
        bpy.ops.render.render(write_still=True)
        if i % 6 == 0: P('turntable frame', i + 1, '/', N, round(time.time() - t0, 1), 's')
    R['frames'] = N; R['total_s'] = round(time.time() - T0, 1)
    json.dump(R, open(A['result'], 'w'), indent=1); P('DONE', R['total_s']); sys.exit(0)
reset_pose()
bmin, bmax = eval_bounds(); H = bmax.z - bmin.z
if LIT: setup_lit(bmin, bmax)
R['renders']['head'] = {'s': shot(os.path.join(out, 'head.png'), Vector(((bmin.x + bmax.x) / 2, (bmin.y + bmax.y) / 2, head_c.z)), H * S['head_height_frac'])}
for key in ('idle', 'walk', 'attack'):
    f, nm = pose_from_clip(A['clips'][key])
    lo, hi = eval_bounds(); size = max(hi.z - lo.z, hi.x - lo.x, hi.y - lo.y)  # posed bounds of this frame; square frame fits the larger span
    if LIT: setup_lit(lo, hi)
    R['renders'][key] = {'frame': f, 'matched_bones': nm, 'bounds': [list(lo), list(hi)], 's': shot(os.path.join(out, key + '.png'), (lo + hi) * 0.5, size * S['body_margin'])}
    P(key, R['renders'][key])
img = bpy.data.images.load(os.path.join(out, S['thumb_source'] + '.png'))
img.scale(S['thumb_px'], S['thumb_px']); img.filepath_raw = os.path.join(out, 'thumb.png'); img.file_format = 'PNG'; img.save()
R['total_s'] = round(time.time() - T0, 1)
json.dump(R, open(A['result'], 'w'), indent=1)
P('DONE', R['total_s'])
'''

args = [a for a in sys.argv[1:] if a != "--turntable"]
TURN = "--turntable" in sys.argv[1:]
if len(args) != 2:
    sys.exit(__doc__)
asset_id, bound = args[0], os.path.abspath(args[1])

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
if TURN:
    tt = st["lit"]["turntable"]
    frames_dir = os.path.join(out, tt["frames_subdir"])
    pngs = [os.path.join(frames_dir, tt["frame_pattern"] % (i + 1)) for i in range(tt["frames"])]
    mp4 = os.path.join(out, tt["mp4"])
    if tt["pose"] == "idle":
        clips = {"idle": clips["idle"]}
    else:
        clips = {}


def receipt(status, error="", timing=None, mem=None, extra=None):
    cf_common.write_receipt(cfg, asset_id, "turntable" if TURN else "stills", tool="run_stills.py (Blender Cycles CPU)", model={"id": "Blender " + os.path.basename(os.path.dirname(loc["blender_exe"]))},
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
    json.dump({"cfg": st, "mode": "turntable" if TURN else "stills", "bound": bound, "clips": clips, "out": out, "result": resf}, f)
for p in pngs + [resf] + ([mp4] if TURN else []):
    if os.path.exists(p):
        os.remove(p)

ram = cf_common.FreeRamWatch(); ram.start()
t0 = time.time()
try:
    with open(log, "w", encoding="utf-8") as lf:
        r = subprocess.run([loc["blender_exe"], "--background", "--python", script, "--", argf], stdout=lf, stderr=subprocess.STDOUT, timeout=st["lit"]["turntable"]["timeout_s"] if TURN else st["timeout_s"]).returncode
except subprocess.TimeoutExpired:
    r = "timeout"
res = json.load(open(resf, encoding="utf-8")) if os.path.exists(resf) else {}
ok = r == 0 and all(os.path.exists(p) for p in pngs)
note = ""
if TURN and ok:
    ff = shutil.which("ffmpeg")
    if not ff:
        note = "ffmpeg not found on PATH: frames kept in " + frames_dir + ", mp4 not encoded (install ffmpeg or encode them yourself)"
        print(note)
    else:
        cmd = [ff, "-y", "-framerate", str(tt["fps"]), "-start_number", "1", "-i", os.path.join(frames_dir, tt["frame_pattern"]), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(tt["crf"]), mp4]
        try:
            fr = subprocess.run(cmd, capture_output=True, text=True, timeout=tt["ffmpeg_timeout_s"])
            if fr.returncode != 0 or not os.path.exists(mp4):
                note = "ffmpeg failed (exit %s): %s" % (fr.returncode, fr.stderr[-300:]); print(note)
        except subprocess.TimeoutExpired:
            note = "ffmpeg timed out"; print(note)
        if TURN and os.path.exists(mp4):
            pngs = pngs + [mp4]
receipt("ok" if ok else "fail", "" if ok else f"blender exit {r}, see log", timing={"wall_s": round(time.time() - t0, 1)}, mem={"lowest_free_ram_gb": ram.stop()}, extra={"result": res, "note": note})
print("turntable" if TURN else "stills", "ok" if ok else "FAIL", "->", frames_dir if TURN else out)
sys.exit(0 if ok else 1)
