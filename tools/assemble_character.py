"""Assemble one character: the bound body plus every gear part of the asset under ONE armature (the 106 skeleton), exported as <asset_id>.glb, one receipt.
Usage: assemble_character.py <asset_id>
Settings come from config/config.json ("assemble", plus "gear" and "bind" for file names) and config.local.json; nothing here is a literal path or number.
CPU only: Blender runs --background, no render. Inputs are <outputs_dir>/3d/<asset_id>/<bound_glb_name> and every <glb_prefix>*.glb beside it.
Export keeps skinning and avoids Draco so gltf-transform can apply meshopt afterwards; keyword names come from assemble.export (unknown ones for this Blender are skipped and recorded)."""
import glob, json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_common

# Runs inside Blender (--background). Reads a json of args, writes a json of results.
BLENDER_SCRIPT = r'''
import bpy, json, sys, os, time
A = json.load(open(sys.argv[sys.argv.index('--') + 1], encoding='utf-8'))
B, X = A['bind'], A['assemble']
R = {'parts': {}, 'notes': []}
T0 = time.time()
def P(*a): print('ASM>', *a, flush=True)
def finish(code):
    R['total_s'] = round(time.time() - T0, 1)
    json.dump(R, open(A['result'], 'w', encoding='utf-8'), indent=1); sys.exit(code)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=A['body'])
rig = bpy.data.objects.get(B['rig_name']) or next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
bodies = [o for o in bpy.data.objects if o.type == 'MESH']
if rig is None or not bodies:
    R['error'] = 'bound body glb has no armature or no mesh'; finish(3)
bone_names = {b.name for b in rig.data.bones}
R['bones'] = len(bone_names)
R['parts']['body'] = {'meshes': [o.name for o in bodies], 'verts': sum(len(o.data.vertices) for o in bodies)}
used_by_gear = set()
for path in A['gears']:
    part = os.path.splitext(os.path.basename(path))[0]
    before = set(bpy.data.objects); bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == 'MESH']; arms = [o for o in new if o.type == 'ARMATURE']
    info = {'meshes': len(meshes), 'verts': 0, 'groups_not_in_rig': []}
    for m in meshes:
        mw = m.matrix_world.copy()
        m.parent = None; m.matrix_world = mw
        m.modifiers.clear()
        m.parent = rig; m.matrix_parent_inverse = rig.matrix_world.inverted()
        md = m.modifiers.new('Armature', 'ARMATURE'); md.object = rig
        m.name = part if len(meshes) == 1 else part + '.' + m.name
        info['verts'] += len(m.data.vertices)
        for vg in m.vertex_groups:
            if vg.name in bone_names: used_by_gear.add(vg.name)
            else: info['groups_not_in_rig'].append(vg.name)
    for a in arms: bpy.data.objects.remove(a, do_unlink=True)
    R['parts'][part] = info
for n in used_by_gear: rig.data.bones[n].use_deform = True
R['gear_bones_used'] = sorted(used_by_gear)
bpy.context.view_layer.update()

# one armature, no stray objects, no orphan skins
arm_n = len([o for o in bpy.data.objects if o.type == 'ARMATURE'])
meshes_all = [o for o in bpy.data.objects if o.type == 'MESH']
unskinned = [o.name for o in meshes_all if not any(m.type == 'ARMATURE' and m.object == rig for m in o.modifiers)]
R['pre_export'] = {'armatures': arm_n, 'meshes': len(meshes_all), 'unskinned_meshes': unskinned}

# export with config keywords the running Blender knows
known = set(bpy.ops.export_scene.gltf.get_rna_type().properties.keys())
kw = {k: v for k, v in X['export'].items() if k in known}
R['export_skipped_keys'] = sorted(k for k in X['export'] if k not in known)
if os.path.exists(A['out']): os.remove(A['out'])
bpy.ops.object.select_all(action='DESELECT')
rig.select_set(True)
for o in meshes_all: o.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.export_scene.gltf(filepath=A['out'], **kw)
R['exported'] = os.path.exists(A['out'])
R['size_bytes'] = os.path.getsize(A['out']) if R['exported'] else 0

# re-import the result and count what a game engine will see
if R['exported']:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=A['out'])
    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    ms = [o for o in bpy.data.objects if o.type == 'MESH']
    R['reimport'] = {'armatures': len(arms), 'bones': len(arms[0].data.bones) if arms else 0, 'meshes': len(ms),
                     'skinned_meshes': len([o for o in ms if any(m.type == 'ARMATURE' for m in o.modifiers)]), 'verts': sum(len(o.data.vertices) for o in ms)}
P('DONE', json.dumps({k: R[k] for k in ('pre_export', 'exported', 'reimport') if k in R}))
finish(0)
'''

if len(sys.argv) != 2:
    sys.exit(__doc__)
asset_id = sys.argv[1]

cfg = cf_common.load_config()
loc, bd, gg, ax = cfg["local"], cfg["bind"], cfg["gear"], cfg["assemble"]
work = os.path.join(loc["outputs_dir"], "3d", asset_id)
awork = os.path.join(work, ax["work_subdir"])
os.makedirs(awork, exist_ok=True)
body_glb = os.path.join(work, bd["bound_glb_name"])
gears = sorted(glob.glob(os.path.join(work, gg["glb_prefix"] + "*.glb")))
out_glb = os.path.join(work, ax["output_name"].format(asset_id=asset_id))
script = os.path.join(awork, "_assemble_blender.py")
argf = os.path.join(awork, "_assemble_args.json")
resf = os.path.join(awork, "_assemble_result.json")
log = os.path.join(loc["logs_dir"], f"assemble_{asset_id}.txt")
params = {"assembly": True, "parts": [os.path.basename(g) for g in gears], "expected_bones": ax["expected_bones"], "export": ax["export"], "blender": loc["blender_exe"], "log": log}


def receipt(status, error="", outs=(), timing=None, mem=None, extra=None):
    cf_common.write_receipt(cfg, asset_id, "bind", tool=os.path.basename(__file__), model={"id": "single-armature assembly of bound body and gear parts"},
                            inputs=[body_glb] + gears, params={**params, **(extra or {})}, timing=timing or {}, memory=mem or {}, outputs=list(outs), status=status, error=error)


problems = []
if not os.path.exists(loc["blender_exe"]):
    problems.append(f"blender_exe not found: {loc['blender_exe']}")
if not os.path.exists(body_glb):
    problems.append(f"bound body not found: {body_glb}")
if not gears:
    problems.append(f"no {gg['glb_prefix']}*.glb gear parts in {work}")
if problems:
    receipt("fail", "; ".join(problems))
    sys.exit("cannot assemble:\n  " + "\n  ".join(problems))

with open(script, "w", encoding="utf-8") as f:
    f.write(BLENDER_SCRIPT)
with open(argf, "w", encoding="utf-8") as f:
    json.dump({"bind": bd, "assemble": ax, "body": body_glb, "gears": gears, "out": out_glb, "result": resf}, f)
for p in (resf, out_glb):
    if os.path.exists(p):
        os.remove(p)

ram = cf_common.FreeRamWatch(); ram.start()
t0 = time.time()
try:
    with open(log, "w", encoding="utf-8") as lf:
        r = subprocess.run([loc["blender_exe"], "--background", "--python", script, "--", argf], stdout=lf, stderr=subprocess.STDOUT, timeout=ax["timeout_s"]).returncode
except subprocess.TimeoutExpired:
    r = "timeout"
res = json.load(open(resf, encoding="utf-8")) if os.path.exists(resf) else {}
timing, mem = {"wall_s": round(time.time() - t0, 1)}, {"lowest_free_ram_gb": ram.stop()}
if r != 0 or not os.path.exists(out_glb):
    receipt("fail", f"blender exit {r}{': ' + res['error'] if res.get('error') else ''}, see log", timing=timing, mem=mem, extra={"result": res})
    sys.exit(f"assemble failed: {r}")

pre, re_ = res.get("pre_export", {}), res.get("reimport", {})
checks = {
    "one_armature_before_export": pre.get("armatures") == 1,
    "all_meshes_skinned": not pre.get("unskinned_meshes", ["?"]),
    "expected_bones": res.get("bones") == ax["expected_bones"] and re_.get("bones") == ax["expected_bones"],
    "gear_groups_in_rig": all(not v.get("groups_not_in_rig") for k, v in res.get("parts", {}).items() if k != "body"),
    "reimport_one_armature": re_.get("armatures") == 1,
    "reimport_all_skinned": re_.get("meshes", 0) > 0 and re_.get("skinned_meshes") == re_.get("meshes"),
    "all_parts_present": re_.get("meshes", 0) >= len(gears) + 1,
}
passed = all(checks.values())
err = "" if passed else "assembly checks failed: " + ",".join(k for k, v in checks.items() if not v)
receipt("ok" if passed else "fail", err, outs=[out_glb], timing=timing, mem=mem, extra={"result": res, "checks": checks})
print("assemble", "ok" if passed else "FAIL", len(gears), "gear parts ->", out_glb)
sys.exit(0 if passed else 1)
