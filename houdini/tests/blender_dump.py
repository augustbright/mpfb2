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
np.savez(out, body=body, clothes=clothes)
print("DUMPED", body.shape, clothes.shape)
