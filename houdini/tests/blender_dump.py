# Run inside Blender with MPFB: dump basemesh (with helpers) and one fitted asset for compare_with_blender.py.
#   blender -b -P blender_dump.py -- OUT.npz
import bpy, sys, json, numpy as np, importlib
mod = [m for m in bpy.context.preferences.addons.keys() if m.split('.')[-1] == 'mpfb'][0]
svc = importlib.import_module(mod + ".services")
A = svc.AgentService
out = sys.argv[sys.argv.index("--") + 1]
CASE = json.loads(open(out + ".json").read())
A.call("reset_scene")
A.call("create_human", {"name": "H", "macros": CASE["macros"], "race": CASE["race"], "basic_assets": False, "skin": None})
if CASE["modifiers"]:
    A.call("set_modifiers", {"values": CASE["modifiers"]})
asset = A.call("equip_asset", {"kind": "clothes", "name": CASE["asset"]})["object"]
def world_coords(obj, disable_masks=True):
    mods = [m for m in obj.modifiers if m.type in ("MASK", "SUBSURF") and m.show_viewport]
    for m in mods: m.show_viewport = False
    dg = bpy.context.evaluated_depsgraph_get(); ev = obj.evaluated_get(dg); me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); ev.to_mesh_clear()
    for m in mods: m.show_viewport = True
    co = co.reshape(-1, 3); mw = np.array(obj.matrix_world)
    return co @ mw[:3, :3].T + mw[:3, 3]
body = world_coords(bpy.data.objects["H"])
clothes = world_coords(bpy.data.objects[asset])
extra = {}
if CASE.get("rig"):
    rig = bpy.data.objects[A.call("add_rig", {"rig": CASE["rig"]})["rig"]]
    names = [b.name for b in rig.data.bones]
    heads = np.array([list(b.head_local) for b in rig.data.bones])
    tails = np.array([list(b.tail_local) for b in rig.data.bones])
    mats = np.array([[list(r) for r in b.matrix_local.to_3x3()] for b in rig.data.bones])
    def weights(obj):
        w = np.zeros((len(obj.data.vertices), len(names)))
        gi = {g.index: g.name for g in obj.vertex_groups}
        for v in obj.data.vertices:
            for g in v.groups:
                name = gi.get(g.group)
                if name in names:
                    w[v.index, names.index(name)] = g.weight
        return w
    extra = dict(bone_names=np.array(names), heads=heads, tails=tails, mats=mats,
                 body_w=weights(bpy.data.objects["H"]), clothes_w=weights(bpy.data.objects[asset]))
np.savez(out, body=body, clothes=clothes, **extra)
print("DUMPED", body.shape, clothes.shape)
