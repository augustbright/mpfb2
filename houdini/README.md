# MPFB for Houdini

MakeHuman characters as Houdini SOPs, built from MPFB's own data (base mesh, targets, macros, MHCLO assets) with
no Blender involved. The same settings give the same vertices as MPFB in Blender (`tests/compare_with_blender.py`
checks it to a micron).

| Node | What |
|---|---|
| `mpfb::human` | The body: macros (gender, age, muscle, weight, proportions, height, cup size, firmness), race, any number of modifiers (`section/name` as in MPFB's Model panel, e.g. `chin/chin-width-decr-incr`), skin. Outputs the body mesh with uv, point groups, `mpfb_index` (MakeHuman vertex number) and detail attributes `mpfb_positions` (all vertices, helpers included), `mpfb_joints` (joint centres) and `mpfb_settings` |
| `mpfb::fit_asset` | Fits an MPFB/MakeHuman asset (clothes, hair, eyes, eyebrows, eyelashes, teeth, tongue, proxy) to the human from its `.mhclo`: by name from the asset libraries or an explicit file. Tint and diffuse override for its material |
| `mpfb::assemble` | Human plus merged assets, without the body faces the assets hide (their delete groups) |

Each node's *Create Material* button makes a Principled shader in `/mat` named after the node from the asset's
`.mhmat` (diffuse, alpha cut-out for hair, brows and lashes, normal map); primitives point to it with
`shop_materialpath`.

Characters face +Z with Y up and the left side on +X, in metres (MakeHuman's decimetres times the *Scale* 0.1).

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

```bash
hython houdini/build_hda.py
python3 houdini/tests/compare_with_blender.py
hython houdini/tests/cook_test.py
```

Not there yet: rigging (KineFX skeleton and weights from MPFB's rigs), poses, export, proxies/topologies as a
replacement body, MakeClothes in Houdini.
