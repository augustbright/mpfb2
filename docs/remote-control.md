# Remote control and MCP

MPFB can be driven from outside Blender, for example by an AI assistant through the
[MCP server](../mcp_server/README.md).

## AgentService

[`services/agentservice.py`](../src/mpfb/services/agentservice.py) is a facade over the other services where every
command takes and returns JSON-compatible values:

```python
from mpfb.services import AgentService  # in practice: dynamic_import, see script_samples
AgentService.call("create_human", {"name": "Hero", "macros": {"gender": 1.0, "muscle": 0.8}})
AgentService.call("set_modifiers", {"values": {"chin/chin-width-decr-incr": 0.5}})
AgentService.call("equip_asset", {"kind": "hair", "name": "ponytail01"})
AgentService.call("add_rig", {"rig": "game_engine"})
AgentService.call("render_views", {"views": ["front", "left"]})
AgentService.call("export_gltf", {"path": "/tmp/hero.glb"})
```

`AgentService.call("describe_commands")` lists every command with its parameters and documentation. Humans are
referred to by the name of any object of the character; with one human in the scene the name can be left out.

Modifiers are the Model panel sliders, keyed `section/name` as in `data/targets/target.json`. Bipolar modifiers take
-1..1, the others 0..1. Sided modifiers change both sides unless the key ends in `@left` or `@right`.

New clothes go through MakeClothes: `extract_clothes` copies a basemesh helper group (`helper-tights`,
`helper-skirt`...) into a mesh to shape, and `write_clothes` matches it against the basemesh, writes the `.mhclo`
into the user library and equips it. `ClothesService.write_mhclo_asset()` and
`ClothesService.build_basemesh_xref_cache()` are the service level versions of the MakeClothes operators.

## Making clothes, accessories and poses

[`services/garmentservice.py`](../src/mpfb/services/garmentservice.py) edits meshes around the human from JSON
specs, so an agent can tailor garments without running code in Blender:

- `get_joints` - rest pose joint centres (`l-elbow`, `r-knee`, `neck`, `l-eye`...), usable as `"joint:<name>"`.
- `derive_clothes` - an editable copy of an equipped asset, optionally only its upper or lower part.
- `cut_clothes` - delete inside regions: `box`, `sphere`, `half_space`, `limb` (e.g. sleeves past the elbow,
  only near the limb axis), `opening` (open front of a jacket); the border is smoothed afterwards.
- `roll_edges` - straighten open ends and fold them back into cuffs.
- `inflate`, `solidify`, `join_objects`, `make_tube` (hood rolls, straps, collars), `make_belt`, `make_glasses`.
- `set_material` - `flat` (MakeSkin material with a color swatch texture) or `tint` (recolor the diffuse texture,
  `contrast` to fade prints, `remove_maps` to drop AO/normal maps); results are PNG files, so they survive
  MakeClothes and glTF export.
- `paint_strokes` - paint brush strokes into the texture as seen from a view (logos, marks, a red X).
- `write_clothes` - prepares the mesh (applies modifiers, one vertex group, one face type, no loose geometry) and
  writes the MHCLO asset.

`add_rig` also binds assets that were equipped before the rig. `pose_bones` poses in world terms (`aim` a bone,
`rotate` about a world axis, `keep_rest`, finger `curl`) and puts the soles on the floor; `keyframe_pose` stores
the pose as an action, which `export_gltf` exports (`texture_size` caps texture resolution).

## RemoteControlService

[`services/remotecontrolservice.py`](../src/mpfb/services/remotecontrolservice.py) serves AgentService over TCP on
`127.0.0.1` (port 9877 by default, see the preferences). One JSON object per line in each direction:

```
-> {"id": 1, "command": "create_human", "args": {"name": "Hero"}}
<- {"id": 1, "ok": true, "result": {...}}
<- {"id": 2, "ok": false, "error": "ValueError: ...", "traceback": "..."}
```

`ping` returns versions and the command list; `reload_agent` re-imports ClothesService, GarmentService and
AgentService from disk (for developing commands without restarting Blender). In the Blender UI, requests run on the main thread through a timer
and each one is an undo step. With `blender -b` there are no timers, so
`RemoteControlService.serve_blocking(port, parent_pid=None)` serves on the main thread until a `shutdown` request
(or until `parent_pid` exits).

The server never starts by itself: the user starts it in *System > Remote control (MCP)*, enables
`mpfb_remote_autostart` in the preferences, or a script calls `RemoteControlService.start()`.
