"""Matched lit turntable of ONE GLB, run inside headless Blender (plugin 0.1.14; UNRUN: written offline, never executed on the laptop's Blender 5.2 yet).
  blender.exe --background --python tools/ab/turntable_blender.py -- --config <render_config.json>
The config is built by tools/run_ab.py from config/config.json ab.render and is IDENTICAL for both meshes except glb, out_dir and label: same angles and camera, same three-point rig
(key, fill, rim), one fixed neutral world, same resolution and samples, Cycles on CPU. The mesh is normalised the same way for both (uniform scale to a fixed height, feet on z=0,
centred on x/y), the lights stay fixed and the subject turns on a pivot, so the lighting on every frame is comparable. Assumes an upright subject facing -Y after the glTF import.
Writes turn_<angle>.png frames, face.png (head close-up, front), hair.png (same head camera from behind) and turntable.json (the manifest run_ab.py reads).
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector

SUBJECT_HEIGHT = 1.8   # metres: both meshes are scaled to this so one light rig fits both


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return json.load(open(argv[argv.index("--config") + 1], encoding="utf-8"))


def clear():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def mesh_objects():
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def bbox(objs):
    lo, hi = Vector((1e9,) * 3), Vector((-1e9,) * 3)
    for o in objs:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return lo, hi


def area_light(name, spec, target):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.size = spec["energy"], spec["size"]
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = spec["location"]
    track = obj.constraints.new("TRACK_TO")
    track.target, track.track_axis, track.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
    return obj


def look_at(cam, loc, target):
    cam.location = loc
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def main(post_import=None):
    """post_import(objs, lo, hi, cfg), plugin 0.1.17: an optional hook (tools/materials/apply_materials_blender.py) that runs right after the GLB import, before the
    subject is normalised to SUBJECT_HEIGHT; the camera, lights and world below are the ONE rig for both the baked and the scanned-material render."""
    cfg = args()
    clear()
    bpy.ops.import_scene.gltf(filepath=cfg["glb"])
    for extra in cfg.get("extra_glbs", []):   # 0.1.18: optional parts rendered with the main GLB under the same rig (the cloth proof: bound body + draped cloak)
        bpy.ops.import_scene.gltf(filepath=extra)
    objs = mesh_objects()
    if not objs:
        raise SystemExit("no mesh in " + cfg["glb"])
    pivot = bpy.data.objects.new("pivot", None)
    bpy.context.scene.collection.objects.link(pivot)
    roots = [o for o in bpy.context.scene.objects if o.parent is None and o is not pivot]
    bpy.context.view_layer.update()
    lo, hi = bbox(objs)
    if post_import:
        post_import(objs, lo, hi, cfg)
    scale = SUBJECT_HEIGHT / max(hi.z - lo.z, 1e-6)
    cx, cy = (lo.x + hi.x) / 2, (lo.y + hi.y) / 2
    for r in roots:
        r.parent = pivot
    pivot.scale = (scale, scale, scale)
    pivot.location = (-cx * scale, -cy * scale, -lo.z * scale)
    bpy.context.view_layer.update()
    # the pivot turns about the vertical axis through the subject centre: move the origin there
    holder = bpy.data.objects.new("turn", None)
    bpy.context.scene.collection.objects.link(holder)
    pivot.parent = holder

    sc = bpy.context.scene
    sc.render.engine = cfg["engine"]
    sc.cycles.device = cfg["device"]
    sc.cycles.samples = cfg["samples"]
    sc.cycles.use_denoising = False
    sc.render.resolution_x, sc.render.resolution_y = cfg["resolution"]
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("neutral")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (*cfg["world"]["color"], 1.0)
    bg.inputs[1].default_value = cfg["world"]["strength"]
    sc.world = world
    target = bpy.data.objects.new("target", None)
    target.location = (0, 0, SUBJECT_HEIGHT * 0.5)
    sc.collection.objects.link(target)
    for name in ("key", "fill", "rim"):
        area_light(name, cfg["lights"][name], target)

    cam_data = bpy.data.cameras.new("cam")
    cam_data.sensor_fit, cam_data.sensor_height = "VERTICAL", 36.0
    cam = bpy.data.objects.new("cam", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    out = cfg["out_dir"]
    os.makedirs(out, exist_ok=True)

    def shoot(name, lens, centre_z, frame_h, azimuth_deg=0.0):
        cam_data.lens = lens
        dist = frame_h * lens / 36.0
        look_at(cam, (0, -dist, centre_z), (0, 0, centre_z))
        holder.rotation_euler = (0, 0, math.radians(azimuth_deg))
        path = os.path.join(out, name)
        sc.render.filepath = path
        bpy.ops.render.render(write_still=True)
        return path

    c = cfg["camera"]
    frames = [shoot(f"turn_{int(a):03d}.png", c["lens_mm"], SUBJECT_HEIGHT * c["height_frac"], SUBJECT_HEIGHT * c["frame_margin"], a) for a in cfg["angles_deg"]]
    if cfg.get("head_crops", True) is False:   # 0.1.17: gear parts have no head
        json.dump({"status": "ok", "label": cfg["label"], "frames": frames, "face_crop": None, "hair_crop": None, "config_hash": cfg["config_hash"], "glb_sha256": cfg["glb_sha256"],
                   "scale_applied": scale, "subject_height_m": SUBJECT_HEIGHT, "blender": bpy.app.version_string}, open(os.path.join(out, "turntable.json"), "w", encoding="utf-8"), indent=2)
        return
    f = cfg["face_crop"]
    head_h = SUBJECT_HEIGHT * f["height_frac"]
    head_z = SUBJECT_HEIGHT - head_h / 2
    face = shoot("face.png", f["lens_mm"], head_z, head_h * f["frame_margin"], 0.0)
    hair = shoot("hair.png", f["lens_mm"], head_z, head_h * f["frame_margin"], 180.0)
    json.dump({"status": "ok", "label": cfg["label"], "frames": frames, "face_crop": face, "hair_crop": hair, "config_hash": cfg["config_hash"], "glb_sha256": cfg["glb_sha256"],
               "scale_applied": scale, "subject_height_m": SUBJECT_HEIGHT, "blender": bpy.app.version_string}, open(os.path.join(out, "turntable.json"), "w", encoding="utf-8"), indent=2)


if not globals().get("CF_NO_AUTORUN"):   # 0.1.17: apply_materials_blender.py execs this file with CF_NO_AUTORUN set and calls main(post_import=...)
    main()
