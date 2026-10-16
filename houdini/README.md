# MPFB for Houdini

MakeHuman characters as Houdini SOPs, built from MPFB's own data (base mesh, targets, macros, MHCLO assets) with
no Blender involved. The same settings give the same vertices as MPFB in Blender (`tests/compare_with_blender.py`
checks it to a micron).

| Node | What |
|---|---|
| `mpfb::human` | The body: macros (gender, age, muscle, weight, proportions, height, cup size, firmness), race, any number of modifiers (`section/name` as in MPFB's Model panel, e.g. `chin/chin-width-decr-incr`), skin. Outputs the body mesh with uv, point groups, `mpfb_index` (MakeHuman vertex number) and detail attributes `mpfb_positions` (all vertices, helpers included), `mpfb_joints` (joint centres) and `mpfb_settings` |
| `mpfb::fit_asset` | Fits an MPFB/MakeHuman asset (clothes, hair, eyes, eyebrows, eyelashes, teeth, tongue, proxy) to the human from its `.mhclo`: by name from the asset libraries or an explicit file. Tint and diffuse override for its material |
| `mpfb::assemble` | Human plus merged assets, without the body faces the assets hide (their delete groups) |
| `mpfb::rig` | Rigs the assembled character with one of MPFB's standard rigs (`game_engine` by default). Output 0: the character with KineFX `boneCapture` (body weights from the rig's weight file, asset weights interpolated from the body and overridden by the asset's `.mhw`, as MPFB does), output 1: the rest skeleton (joint points with `name` and `transform`, oriented exactly like MPFB's Blender bones) |

Each node's *Create Material* button makes a Principled shader in `/mat` named after the node from the asset's
`.mhmat` (diffuse, alpha cut-out for hair, brows and lashes, normal map); primitives point to it with
`shop_materialpath`.

Characters face +Z with Y up and the left side on +X, in metres (MakeHuman's decimetres times the *Scale* 0.1).

A typical network: `mpfb::human` → several `mpfb::fit_asset` → Merge → `mpfb::assemble` (human, merged assets)
→ `mpfb::rig`. Pose with KineFX (Rig Pose, animation) and deform with Bone Deform (rig output 0, output 1, posed
skeleton). Export with the *glTF Character* ROP (`kinefx::gltfcharacter`): *Skin* = the rig (output 0), *Capture
Pose* = output 1, *Animated Pose* = the posed skeleton; set *Clip Range* to *Render Current Frame* for a static
character and *Max Texture Resolution* (a menu: `"1024"`) for game textures. Keep the rig's *Remove MPFB Attributes*
on: the ROP stalls on the large `mpfb_*` bookkeeping attributes.

## Install

```bash
python3 houdini/install_package.py --houdini 22.0 --extra-assets /path/to/project/assets
```

This writes a Houdini package (`~/Library/Preferences/houdini/22.0/packages/mpfb.json`) that adds `otls/` and
`python/`. Data is found automatically:

- `MPFB_DATA`: MPFB system data, default `src/mpfb/data` of this repository.
- `MPFB_ASSETS`: asset libraries; default every MPFB user data folder of installed Blenders (MakeHuman system
  assets installed through MPFB).
- `MPFB_EXTRA_ASSETS`: project libraries searched first (`--extra-assets`), same layout as MPFB's data folder.

## Layout

| Path | What |
|---|---|
| `python/mpfb_houdini/core.py` | Pure numpy port: OBJ, targets, macro formula, modifiers, MHCLO parsing and fitting, MHMAT |
| `python/mpfb_houdini/sop.py` | Cook code of the HDAs, menus, materials |
| `python/mpfb_houdini/preview.py` | Preview captures framed on a SOP (viewport flipbook in the UI) |
| `build_hda.py` | Builds `otls/mpfb.hda` with `hython` |
| `tests/compare_with_blender.py` | Parity with MPFB in Blender (body and fitted assets for three characters) |
| `tests/cook_test.py` | Cooks the HDAs headless and checks them against the core |
| `tests/rig_test.py` | Rigs a character, poses it with KineFX and exports glTF |

```bash
hython houdini/build_hda.py
python3 houdini/tests/compare_with_blender.py
hython houdini/tests/cook_test.py
hython houdini/tests/rig_test.py /tmp/rigged.glb
```

`compare_with_blender.py` also checks rigs: bone heads, tails and orientations, body and asset weights for the
`game_engine` and `default` rigs.

Not there yet: MPFB's pose files and animations, asset sub-rigs (`.mpfbskel`), Rigify, proxies/topologies as a
replacement body, MakeClothes in Houdini.
