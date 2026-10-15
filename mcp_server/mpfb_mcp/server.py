"""MCP tools for MPFB. Each tool is a thin wrapper over an AgentService command running inside Blender.

Typical flow: create_human -> set_modifiers / set_macros -> equip_asset (hair, clothes...) -> add_rig -> set_pose
-> render_views to look at the result -> export_gltf / save_blend. New clothes: extract_clothes (or model a mesh
around the body with another tool) -> write_clothes.
"""

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP, Image

from .client import MpfbClient

mcp = FastMCP("mpfb", instructions=__doc__)
_client = MpfbClient()

AssetKind = Literal["clothes", "hair", "eyebrows", "eyelashes", "eyes", "teeth", "tongue", "proxymeshes", "skins"]
View = Literal["front", "back", "left", "right", "three_quarter", "three_quarter_right"]


def _call(command: str, **args: Any) -> Any:
    return _client.call(command, args)


@mcp.tool()
def info() -> dict:
    """MPFB/Blender versions, current .blend, asset library location, installed asset counts, humans in the scene."""
    return _call("info")


@mcp.tool()
def reset_scene() -> dict:
    """Start over with an empty scene (no objects; preferences and add-ons are untouched)."""
    return _call("reset_scene")


@mcp.tool()
def list_humans() -> list:
    """Describe every human in the scene (macros, modifiers, assets, skin, rig, height)."""
    return _call("list_humans")


@mcp.tool()
def describe_human(human: str | None = None) -> dict:
    """Macros, race, non-zero targets, equipped assets (object names), skin, rig and height of a human.
    human: name of any object of the character; optional when there is only one human."""
    return _call("describe_human", human=human)


@mcp.tool()
def create_human(name: str = "Human", macros: dict[str, float] | None = None, race: dict[str, float] | None = None,
                 basic_assets: bool = True, skin: str | None = "young_caucasian_male",
                 skin_type: str = "GAMEENGINE") -> dict:
    """Create a human standing on the floor at the origin, facing -Y.
    macros (all 0..1, default 0.5): gender (0 female, 1 male), age (0 = 1 year, 0.1875 = 11, 0.5 = 25, 1 = 90),
    muscle, weight, proportions (1 = idealised), height, cupsize, firmness.
    race: weights for african, asian, caucasian.
    basic_assets: add eyes, eyebrows, eyelashes, teeth, tongue. skin: a skins asset name or null.
    skin_type: GAMEENGINE (best for export), ENHANCED_SSS, MAKESKIN."""
    return _call("create_human", name=name, macros=macros, race=race, basic_assets=basic_assets, skin=skin,
                 skin_type=skin_type)


@mcp.tool()
def set_macros(macros: dict[str, float] | None = None, race: dict[str, float] | None = None,
               human: str | None = None) -> dict:
    """Change macros and/or race (see create_human); assets and rig are refitted, feet stay on the floor."""
    return _call("set_macros", macros=macros, race=race, human=human)


@mcp.tool()
def list_modifiers(section: str | None = None, query: str | None = None) -> list:
    """Modeling modifiers (Model panel sliders) as "section/name". Sections: arms, asym, breast, buttocks, cheek,
    chin, ears, eyebrows, eyes, feet, forehead, genitals, hands, head, hip, legs, measure, mouth, neck, nose, pelvis,
    stomach, torso. bipolar modifiers take -1..1, others 0..1; sided ones affect both sides unless keyed
    "name@left" / "name@right"."""
    return _call("list_modifiers", section=section, query=query)


@mcp.tool()
def set_modifiers(values: dict[str, float], human: str | None = None) -> dict:
    """Set modifiers, e.g. {"head/head-square": 0.8, "chin/chin-width-decr-incr": 0.5,
    "arms/upperarm-muscle-decr-incr": 0.6}. Returns all non-zero targets afterwards."""
    return _call("set_modifiers", values=values, human=human)


@mcp.tool()
def list_assets(kind: AssetKind = "clothes", query: str | None = None) -> list:
    """Installed assets of a kind with names, file paths and thumbnail paths."""
    return _call("list_assets", kind=kind, query=query)


@mcp.tool()
def show_asset_thumbnails(kind: AssetKind = "clothes", query: str | None = None, limit: int = 12) -> list:
    """Like list_assets, but returns the thumbnails as images so they can be looked at."""
    out: list = []
    for asset in _call("list_assets", kind=kind, query=query)[:limit]:
        out.append(asset["name"])
        if asset.get("thumbnail"):
            out.append(Image(path=asset["thumbnail"], format="png"))
    return out


@mcp.tool()
def equip_asset(kind: AssetKind, name: str, human: str | None = None, material_type: str = "MAKESKIN",
                replace: bool = True, subdiv_levels: int = 0) -> dict:
    """Fit an asset to the human (name from list_assets, or an absolute .mhclo path). Hair, eyes, eyebrows,
    eyelashes, teeth, tongue and proxies replace the current one when replace is true. material_type: MAKESKIN,
    GAMEENGINE, PROCEDURAL_EYES (eyes only)."""
    return _call("equip_asset", kind=kind, name=name, human=human, material_type=material_type, replace=replace,
                 subdiv_levels=subdiv_levels)


@mcp.tool()
def unequip_asset(asset: str) -> dict:
    """Remove an equipped asset by object name (see describe_human)."""
    return _call("unequip_asset", asset=asset)


@mcp.tool()
def set_skin(name: str, human: str | None = None, skin_type: str = "GAMEENGINE") -> dict:
    """Apply a skins asset (see list_assets kind=skins)."""
    return _call("set_skin", name=name, human=human, skin_type=skin_type)


@mcp.tool()
def set_material(object: str, color: list[float], mode: Literal["flat", "tint"] = "flat",
                 roughness: float | None = None, metallic: float | None = None, image_dir: str | None = None,
                 contrast: float = 1.0, remove_maps: list[str] | None = None) -> dict:
    """Recolor a mesh (equipped clothes, hair, body). color: [r, g, b] sRGB 0..1. flat: plain MakeSkin material.
    tint: keep the maps and write a recolored copy of the diffuse texture (keeps folds and seams) to image_dir."""
    return _call("set_material", object=object, color=color, mode=mode, roughness=roughness, metallic=metallic,
                 image_dir=image_dir, contrast=contrast, remove_maps=remove_maps)


@mcp.tool()
def add_rig(rig: str = "game_engine", human: str | None = None) -> dict:
    """Add a built-in rig: game_engine (Godot/Unity/Unreal), default, default_no_toes, mixamo, mixamo_unity,
    cmu_mb, openpose, game_engine_with_breast. Returns the bone names."""
    return _call("add_rig", rig=rig, human=human)


@mcp.tool()
def list_poses(human: str | None = None) -> list:
    """Pose names available for the human's rig."""
    return _call("list_poses", human=human)


@mcp.tool()
def set_pose(bones: dict[str, dict[str, list[float]]] | None = None, pose: str | None = None, reset: bool = True,
             human: str | None = None) -> dict:
    """Pose the rig. pose: name from list_poses applied first. bones: {"upperarm_l": {"rotation": [x, y, z]}}
    with XYZ euler degrees in bone space, optional "location". reset clears the previous pose."""
    return _call("set_pose", bones=bones, pose=pose, reset=reset, human=human)


@mcp.tool()
def pose_bones(bones: dict[str, dict], reset: bool = True, ground: bool = True, human: str | None = None) -> dict:
    """Pose in world terms (character faces -Y, his left is +X): per bone {"aim": [x, y, z]} points it,
    {"rotate": [ax, ay, az, degrees]} turns about a world axis, {"keep_rest": true} keeps its rest orientation
    (flat feet), {"curl": degrees} bends fingers about their own X axis. ground puts the lowest point at z = 0.
    game_engine bones: pelvis, spine_01..03, neck_01, head, clavicle_l, upperarm_l, lowerarm_l, hand_l,
    thigh_l, calf_l, foot_l, ball_l, index/middle/ring/pinky/thumb_01..03_l (and _r)."""
    return _call("pose_bones", bones=bones, reset=reset, ground=ground, human=human)


@mcp.tool()
def keyframe_pose(action: str, frame: int = 1, human: str | None = None) -> dict:
    """Key the current pose into the named action, so it is exported as an animation."""
    return _call("keyframe_pose", action=action, frame=frame, human=human)


@mcp.tool()
def list_vertex_groups(human: str | None = None, query: str | None = None) -> list:
    """Basemesh vertex groups; helper-tights, helper-skirt, helper-hair... are starting shapes for new clothes."""
    return _call("list_vertex_groups", human=human, query=query)


@mcp.tool()
def extract_clothes(vertex_group: str, name: str, human: str | None = None) -> dict:
    """Copy a basemesh vertex group (e.g. helper-tights) into a new mesh to shape into clothes."""
    return _call("extract_clothes", vertex_group=vertex_group, name=name, human=human)


@mcp.tool()
def write_clothes(clothes: str, name: str, human: str | None = None, kind: str = "clothes", author: str = "",
                  license: str = "CC0", description: str = "", delete_group: str | None = None,
                  file_path: str | None = None, equip: bool = True, remove_source: bool = True) -> dict:
    """MakeClothes: turn a mesh modeled around the human into an MPFB asset in the user library (or file_path),
    then equip it. The mesh must have no modifiers and one basemesh vertex group per vertex (extract_clothes gives
    that); the human must be in rest pose. kind: clothes, hair, eyebrows, eyelashes, proxymeshes."""
    return _call("write_clothes", clothes=clothes, name=name, human=human, kind=kind, author=author,
                 license=license, description=description, delete_group=delete_group, file_path=file_path,
                 equip=equip, remove_source=remove_source)


@mcp.tool()
def get_joints(human: str | None = None, query: str | None = None) -> dict:
    """Rest pose joint centres (world metres): l-/r-shoulder, elbow, hand, upper-leg, knee, ankle, neck, head,
    l-/r-eye, pelvis, spine-1..5, fingers, toes. Reference them as "joint:<name>" in garment specs."""
    return _call("get_joints", human=human, query=query)


@mcp.tool()
def derive_clothes(source: str, name: str, part: Literal["all", "upper", "lower"] = "all", human: str | None = None,
                   hide_source: bool = True) -> dict:
    """Editable copy of an equipped asset (e.g. the shirt or trousers of a suit) to cut and reshape."""
    return _call("derive_clothes", source=source, name=name, part=part, human=human, hide_source=hide_source)


@mcp.tool()
def cut_clothes(object: str, regions: list[dict], human: str | None = None, keep_largest: int = 0) -> dict:
    """Delete vertices inside any region. Shapes: {"type": "box", "min", "max"}, {"type": "sphere", "center",
    "radius"}, {"type": "half_space", "point", "normal"} (deletes the side the normal points to), {"type": "limb",
    "from": "shoulder", "to": "elbow", "beyond": 0.07} (both sides; also upper-leg/knee/ankle), {"type":
    "opening", "y_max", "z_top", "z_bottom", "half_width_top", "half_width_bottom"} (open front). Points may be
    "joint:<name>". Any shape takes "invert": true. keep_largest keeps that many connected pieces."""
    return _call("cut_clothes", object=object, regions=regions, human=human, keep_largest=keep_largest)


@mcp.tool()
def roll_edges(object: str, region: dict, toward: list, out: float = 0.01, back: float = 0.04,
               human: str | None = None) -> dict:
    """Roll open ends (sleeves, trouser legs) whose centre is in region: out from the axis, back toward the
    nearest of toward (e.g. ["joint:l-shoulder", "joint:r-shoulder"])."""
    return _call("roll_edges", object=object, region=region, toward=toward, out=out, back=back, human=human)


@mcp.tool()
def inflate(object: str, distance: float, regions: list[dict] | None = None, human: str | None = None) -> dict:
    """Push vertices out along normals (looser fit, outer layer such as a vest over a shirt)."""
    return _call("inflate", object=object, distance=distance, regions=regions, human=human)


@mcp.tool()
def solidify(object: str, thickness: float = 0.004, offset: float = -1.0) -> dict:
    """Give a sheet garment thickness (applied)."""
    return _call("solidify", object=object, thickness=thickness, offset=offset)


@mcp.tool()
def join_objects(target: str, sources: list[str]) -> dict:
    """Join meshes into target (sources are removed)."""
    return _call("join_objects", target=target, sources=sources)


@mcp.tool()
def set_visibility(object: str, visible: bool) -> dict:
    """Show or hide an object in viewport and renders."""
    return _call("set_visibility", object=object, visible=visible)


@mcp.tool()
def delete_object(object: str) -> dict:
    """Delete a free object (equipped assets: unequip_asset)."""
    return _call("delete_object", object=object)


@mcp.tool()
def paint_strokes(object: str, strokes: list[list[float]], color: list[float], view: str = "front",
                  jitter: float = 0.0) -> dict:
    """Paint strokes [u1, v1, u2, v2, width] (metres in the view plane: front/back x,z; left/right y,z) into the
    object's diffuse texture; tint the object first so it owns a PNG. jitter roughens edges like a brush."""
    return _call("paint_strokes", object=object, strokes=strokes, color=color, view=view, jitter=jitter)


@mcp.tool()
def make_belt(name: str, z: str | float = "joint:pelvis", offset: float = 0.0, width: float = 0.04,
              thickness: float = 0.006, clearance: float = 0.012, buckle: bool = True,
              human: str | None = None) -> dict:
    """Generate a belt hugging the body at height z (+ offset) with a buckle at the front."""
    return _call("make_belt", name=name, z=z, offset=offset, width=width, thickness=thickness, clearance=clearance,
                 buckle=buckle, human=human)


@mcp.tool()
def make_glasses(name: str, lens_radius: float = 0.021, forward: float = 0.03,
                 shape: Literal["round", "square"] = "round", human: str | None = None) -> dict:
    """Generate glasses in front of the eyes (material slot 0 frame, 1 lenses)."""
    return _call("make_glasses", name=name, lens_radius=lens_radius, forward=forward, shape=shape, human=human)


@mcp.tool()
def make_tube(name: str, points: list, radii: list[float], sides: int = 12, closed: bool = False,
              flatten: float = 1.0, human: str | None = None) -> dict:
    """Sweep a tube through points (world or "joint:" refs, one radius each): collars, hood rolls, straps."""
    return _call("make_tube", name=name, points=points, radii=radii, sides=sides, closed=closed, flatten=flatten,
                 human=human)


@mcp.tool()
def create_delete_group(clothes: str, group_name: str = "Delete", human: str | None = None) -> dict:
    """Rough basemesh vertex group of the body under the clothes, for write_clothes' delete_group."""
    return _call("create_delete_group", clothes=clothes, group_name=group_name, human=human)


@mcp.tool()
def render_views(human: str | None = None, views: list[View] | None = None, framing: Literal["body", "head"] = "body",
                 size: int = 768, engine: Literal["EEVEE", "WORKBENCH"] = "EEVEE") -> list:
    """Render orthographic previews of the human and return them as images (default: front, left,
    three_quarter, back). Use this to compare against reference art after every change."""
    paths = _call("render_views", human=human, views=views, framing=framing, size=size, engine=engine)
    out: list = []
    for path in paths:
        out.append(path)
        out.append(Image(path=path, format="png"))
    return out


@mcp.tool()
def save_blend(path: str) -> dict:
    """Save the scene to a .blend file."""
    return _call("save_blend", path=path)


@mcp.tool()
def open_blend(path: str) -> dict:
    """Open a .blend file, replacing the current scene."""
    return _call("open_blend", path=path)


@mcp.tool()
def export_gltf(path: str, human: str | None = None, bake_subdiv: bool = False, animations: bool = True,
                texture_size: int | None = None) -> dict:
    """Export the human (rig + meshes, helpers removed) to .glb or .gltf for a game engine such as Godot.
    texture_size caps exported textures (e.g. 1024) without touching the scene."""
    return _call("export_gltf", path=path, human=human, bake_subdiv=bake_subdiv, animations=animations,
                 texture_size=texture_size)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
