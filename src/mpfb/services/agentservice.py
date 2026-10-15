"""High level, JSON in / JSON out operations on MPFB characters, for scripts and remote control (MCP).

Every command takes and returns plain JSON-compatible values (str, int, float, bool, list, dict, None), so it can be
called from RemoteControlService over a socket as well as directly from Python:

    from mpfb.services import AgentService
    AgentService.call("create_human", {"name": "Hero", "macros": {"gender": 1.0, "muscle": 0.8}})

Humans are referred to by the name of any object in their hierarchy (basemesh, rig or an equipped asset). When a
command takes an optional ``human`` and there is exactly one human in the scene, it is used.
"""

import inspect, json, math, os, re, tempfile, typing, uuid  # pylint: disable=C0410
import numpy
from collections.abc import Callable
from typing import Any

import bmesh
import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from .logservice import LogService
from .locationservice import LocationService
from .objectservice import ObjectService
from .assetservice import AssetService
from .targetservice import TargetService
from .rigservice import RigService
from .exportservice import ExportService
from .clothesservice import ClothesService
from .humanservice import HumanService
from .garmentservice import GarmentService
from ..entities.objectproperties import HumanObjectProperties, GeneralObjectProperties

_LOG = LogService.get_logger("services.agentservice")

JsonDict = dict[str, Any]

MACRO_KEYS = ["gender", "age", "muscle", "weight", "proportions", "height", "cupsize", "firmness"]
RACE_KEYS = ["african", "asian", "caucasian"]

# Asset kind (as used by commands) -> (asset subdir, file type, MPFB object type)
ASSET_KINDS: dict[str, tuple[str, str, str]] = {
    "clothes": ("clothes", "mhclo", "Clothes"),
    "hair": ("hair", "mhclo", "Hair"),
    "eyebrows": ("eyebrows", "mhclo", "Eyebrows"),
    "eyelashes": ("eyelashes", "mhclo", "Eyelashes"),
    "eyes": ("eyes", "mhclo", "Eyes"),
    "teeth": ("teeth", "mhclo", "Teeth"),
    "tongue": ("tongue", "mhclo", "Tongue"),
    "proxymeshes": ("proxymeshes", "proxy", "Proxymeshes"),
    "skins": ("skins", "mhmat", "Material"),
}

# Body parts a human has at most one of: equipping a new one replaces the old one.
SINGLE_SLOT_TYPES = ["Hair", "Eyebrows", "Eyelashes", "Eyes", "Teeth", "Tongue", "Proxymeshes"]

BASIC_ASSETS = [("eyes", "low-poly"), ("eyebrows", "eyebrow001"), ("eyelashes", "eyelashes01"),
                ("teeth", "teeth_base"), ("tongue", "tongue01")]

# View name -> camera direction from the subject (character faces -Y, his left side is +X)
VIEWS: dict[str, tuple[float, float, float]] = {
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (1.0, 0.0, 0.0),
    "right": (-1.0, 0.0, 0.0),
    "three_quarter": (0.7071, -0.7071, 0.0),
    "three_quarter_right": (-0.7071, -0.7071, 0.0),
}

_COMMANDS: dict[str, Callable[..., Any]] = {}


def _command(func: Callable[..., Any]) -> Callable[..., Any]:
    _COMMANDS[func.__name__] = func
    return func


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float):
        return round(value, 5)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if hasattr(value, "__len__") and hasattr(value, "__getitem__"):  # mathutils vectors, colors
        return [_jsonable(v) for v in value]
    return str(value)


class AgentService():
    """JSON-friendly facade over the MPFB services, see the module docstring."""

    def __init__(self) -> None:
        raise RuntimeError("You should not instance AgentService. Use its static methods instead.")

    # ----------------------------------------------------------------------------------------------------------------
    # Dispatch

    @staticmethod
    def call(command: str, args: JsonDict | None = None) -> Any:
        """Run a command by name with keyword arguments and return its JSON-compatible result."""
        if command not in _COMMANDS:
            raise ValueError("Unknown command '%s'. Known commands: %s" % (command, ", ".join(sorted(_COMMANDS))))
        _LOG.debug("Agent command", (command, args))
        return _jsonable(_COMMANDS[command](**(args or {})))

    @staticmethod
    def command_names() -> list[str]:
        return sorted(_COMMANDS)

    # ----------------------------------------------------------------------------------------------------------------
    # Lookup helpers

    @staticmethod
    def _humans() -> list[bpy.types.Object]:
        return [obj for obj in bpy.context.scene.objects if ObjectService.object_is_basemesh(obj)]

    @staticmethod
    def _basemesh(human: str | None = None) -> bpy.types.Object:
        if not human:
            humans = AgentService._humans()
            if len(humans) == 1:
                return humans[0]
            raise ValueError("Specify which human: %s" % ([h.name for h in humans] or "there are no humans"))
        obj = bpy.data.objects.get(human)
        if obj is None:
            raise ValueError("No object named '%s'" % human)
        if ObjectService.object_is_basemesh(obj):
            return obj
        basemesh = ObjectService.find_object_of_type_amongst_nearest_relatives(obj, "Basemesh")
        if basemesh is None:
            raise ValueError("'%s' is not part of a human" % human)
        return basemesh

    @staticmethod
    def _rig(basemesh: bpy.types.Object) -> bpy.types.Object | None:
        return ObjectService.find_object_of_type_amongst_nearest_relatives(basemesh, "Skeleton")

    @staticmethod
    def _root(basemesh: bpy.types.Object) -> bpy.types.Object:
        return AgentService._rig(basemesh) or basemesh

    @staticmethod
    def _hierarchy(basemesh: bpy.types.Object) -> list[bpy.types.Object]:
        """The root (rig or basemesh) and all its descendants."""
        out = [AgentService._root(basemesh)]
        for obj in out:
            out.extend(ObjectService.get_list_of_children(obj))
        return out

    @staticmethod
    def _object(name: str) -> bpy.types.Object:
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise ValueError("No object named '%s'" % name)
        return obj

    @staticmethod
    def _asset_path(kind: str, name: str) -> str:
        if kind not in ASSET_KINDS:
            raise ValueError("Unknown asset kind '%s', use one of %s" % (kind, ", ".join(ASSET_KINDS)))
        subdir, ftype, _ = ASSET_KINDS[kind]
        if os.path.isabs(name) and os.path.exists(name):
            return name
        assets = AssetService.get_asset_list(subdir, ftype)
        for key, info in assets.items():
            if name in (key, info.get("name_without_ext"), info.get("label"), os.path.basename(info["full_path"])):
                return info["full_path"]
        lowered = name.lower()
        for key, info in assets.items():
            if lowered == str(key).lower() or lowered == str(info.get("label", "")).lower():
                return info["full_path"]
        raise ValueError("No %s asset named '%s'. Use list_assets to see what is installed." % (kind, name))

    @staticmethod
    def _world_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
        dg = bpy.context.evaluated_depsgraph_get()
        lo = Vector((math.inf,) * 3)
        hi = Vector((-math.inf,) * 3)
        for obj in objects:
            if obj.type != "MESH" or obj.hide_render:
                continue
            # Vertices of the evaluated mesh: bound_box lags behind armature deformation.
            ev = obj.evaluated_get(dg)
            mesh = ev.to_mesh()
            count = len(mesh.vertices)
            if count:
                co = numpy.empty(count * 3, dtype=numpy.float32)
                mesh.vertices.foreach_get("co", co)
                world = numpy.asarray(ev.matrix_world, dtype=numpy.float32)
                pts = co.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]
                lo = Vector(numpy.minimum(lo, pts.min(axis=0)))
                hi = Vector(numpy.maximum(hi, pts.max(axis=0)))
            ev.to_mesh_clear()
        if lo.x == math.inf:
            raise ValueError("Nothing renderable to measure")
        return lo, hi

    @staticmethod
    def _ground(basemesh: bpy.types.Object) -> None:
        """Move the character so the lowest point of the body is at z=0."""
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        ev = basemesh.evaluated_get(dg)
        mesh = ev.to_mesh()
        lowest = min((ev.matrix_world @ v.co).z for v in mesh.vertices)
        ev.to_mesh_clear()
        root = AgentService._root(basemesh)
        root.location.z -= lowest
        bpy.context.view_layer.update()

    # ----------------------------------------------------------------------------------------------------------------
    # Introspection

    @staticmethod
    @_command
    def describe_commands() -> list[JsonDict]:
        """List the available commands with their parameters and documentation."""
        out = []
        for name in sorted(_COMMANDS):
            func = _COMMANDS[name]
            sig = inspect.signature(func)
            out.append({"name": name, "parameters": [str(p) for p in sig.parameters.values()],
                        "doc": inspect.getdoc(func) or ""})
        return out

    @staticmethod
    @_command
    def info() -> JsonDict:
        """MPFB and Blender versions, data locations, installed asset counts and the humans in the scene."""
        from .. import VERSION  # pylint: disable=C0415
        counts = {kind: len(AssetService.get_asset_list(subdir, ftype)) for kind, (subdir, ftype, _) in ASSET_KINDS.items()}
        return {
            "mpfb_version": VERSION, "blender_version": bpy.app.version_string,
            "blend_file": bpy.data.filepath, "user_data": LocationService.get_user_data(),
            "system_assets_installed": AssetService.system_assets_pack_is_installed(),
            "asset_counts": counts, "humans": [h.name for h in AgentService._humans()],
        }

    @staticmethod
    @_command
    def reset_scene() -> JsonDict:
        """Start over with an empty scene (no objects; preferences and add-ons are untouched)."""
        bpy.ops.wm.read_homefile(use_empty=True)
        return {"objects": len(bpy.data.objects)}

    @staticmethod
    @_command
    def list_humans() -> list[JsonDict]:
        """Describe every human in the scene."""
        return [AgentService.describe_human(h.name) for h in AgentService._humans()]

    @staticmethod
    @_command
    def describe_human(human: str | None = None) -> JsonDict:
        """Macros, race, non-zero modifiers (shape key name -> weight), equipped assets, skin, rig and size."""
        basemesh = AgentService._basemesh(human)
        macros = {k: HumanObjectProperties.get_value(k, entity_reference=basemesh) for k in MACRO_KEYS}
        race = {k: HumanObjectProperties.get_value(k, entity_reference=basemesh) for k in RACE_KEYS}
        targets = {}
        keys = basemesh.data.shape_keys
        if keys:
            for kb in keys.key_blocks[1:]:
                if not kb.name.startswith("$") and abs(kb.value) > 1e-4:
                    targets[kb.name] = kb.value
        assets = []
        for obj in AgentService._hierarchy(basemesh):
            otype = ObjectService.get_object_type(obj)
            if obj is basemesh or otype in ("Skeleton", "Basemesh", ""):
                continue
            assets.append({"object": obj.name, "type": otype,
                           "source": GeneralObjectProperties.get_value("asset_source", entity_reference=obj)})
        rig = AgentService._rig(basemesh)
        lo, hi = AgentService._world_bounds([basemesh])
        return {
            "name": basemesh.name, "macros": macros, "race": race, "targets": targets, "assets": assets,
            "skin": basemesh.active_material.name if basemesh.active_material else None,
            "rig": {"name": rig.name, "bones": len(rig.data.bones)} if rig else None,
            "height_m": hi.z - lo.z, "bounds": [list(lo), list(hi)],
        }

    # ----------------------------------------------------------------------------------------------------------------
    # Creating and shaping

    @staticmethod
    @_command
    def create_human(name: str = "Human", macros: JsonDict | None = None, race: JsonDict | None = None,
                     basic_assets: bool = True, skin: str | None = "young_caucasian_male",
                     skin_type: str = "GAMEENGINE") -> JsonDict:
        """Create a human. macros: gender (0 female..1 male), age (0.5 = 25 y), muscle, weight, proportions,
        height, cupsize, firmness, all 0..1 (0.5 default). race: african/asian/caucasian weights. basic_assets adds
        eyes, eyebrows, eyelashes, teeth and tongue. skin is a skins asset name (None for no skin)."""
        info = TargetService.get_default_macro_info_dict()
        for key, value in (macros or {}).items():
            if key not in MACRO_KEYS:
                raise ValueError("Unknown macro '%s', use %s" % (key, MACRO_KEYS))
            info[key] = float(value)  # type: ignore[literal-required]
        if race:
            info["race"] = {k: float(race.get(k, 0.0)) for k in RACE_KEYS}  # type: ignore[typeddict-item]
        basemesh = HumanService.create_human(macro_detail_dict=info)
        basemesh.name = name
        if basic_assets:
            for kind, asset in BASIC_ASSETS:
                try:
                    AgentService.equip_asset(kind, asset, human=basemesh.name)
                except ValueError as err:
                    _LOG.warn("Skipping basic asset", err)
        if skin:
            AgentService.set_skin(skin, human=basemesh.name, skin_type=skin_type)
        return AgentService.describe_human(basemesh.name)

    @staticmethod
    @_command
    def set_macros(macros: JsonDict | None = None, race: JsonDict | None = None, human: str | None = None) -> JsonDict:
        """Change macros (see create_human) and/or race weights, refit assets and rig, keep the feet on the floor."""
        basemesh = AgentService._basemesh(human)
        for key, value in (macros or {}).items():
            if key not in MACRO_KEYS:
                raise ValueError("Unknown macro '%s', use %s" % (key, MACRO_KEYS))
            HumanObjectProperties.set_value(key, float(value), entity_reference=basemesh)
        for key, value in (race or {}).items():
            if key not in RACE_KEYS:
                raise ValueError("Unknown race '%s', use %s" % (key, RACE_KEYS))
            HumanObjectProperties.set_value(key, float(value), entity_reference=basemesh)
        TargetService.reapply_macro_details(basemesh)
        HumanService.refit(basemesh)
        AgentService._ground(basemesh)
        return AgentService.describe_human(basemesh.name)

    @staticmethod
    def _modifier_sections() -> JsonDict:
        with open(LocationService.get_mpfb_data(os.path.join("targets", "target.json")), "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    @_command
    def list_modifiers(section: str | None = None, query: str | None = None) -> list[JsonDict]:
        """Modeling modifiers (the sliders of the Model panel) as "section/name". bipolar ones take -1..1 (negative
        and positive targets), the others 0..1. sided ones apply to both sides unless suffixed with @left/@right."""
        out = []
        for sec, data in AgentService._modifier_sections().items():
            if section and sec != section:
                continue
            for cat in data["categories"]:
                key = sec + "/" + cat["name"]
                if query and query.lower() not in key.lower():
                    continue
                entry: JsonDict = {"modifier": key, "bipolar": "opposites" in cat, "sided": cat["has_left_and_right"]}
                if "opposites" in cat:
                    opp = cat["opposites"]
                    entry["negative"] = opp.get("negative-unsided") or opp.get("negative-left")
                    entry["positive"] = opp.get("positive-unsided") or opp.get("positive-left")
                out.append(entry)
        return out

    @staticmethod
    def _apply_target(basemesh: bpy.types.Object, section: str, target: str, weight: float) -> None:
        if TargetService.has_target(basemesh, target):
            TargetService.set_target_value(basemesh, target, weight, delete_target_on_zero=True)
        elif weight > 0.0:
            path = LocationService.get_mpfb_data(os.path.join("targets", section, target + ".target.gz"))
            if not os.path.exists(path):
                raise ValueError("Target file is missing: " + path)
            TargetService.load_target(basemesh, path, weight=weight, name=target)

    @staticmethod
    @_command
    def set_modifiers(values: JsonDict, human: str | None = None) -> JsonDict:
        """Set modeling modifiers, e.g. {"head/head-square": 0.8, "chin/chin-width-decr-incr": 0.4,
        "arms/upperarm-muscle-decr-incr@right": 0.2}. Keys come from list_modifiers. Refits assets once."""
        basemesh = AgentService._basemesh(human)
        sections = AgentService._modifier_sections()
        for key, value in values.items():
            side_req = None
            if "@" in key:
                key, side_req = key.split("@", 1)
            section, _, name = key.partition("/")
            if section not in sections:
                raise ValueError("Unknown modifier section '%s'" % section)
            cat = next((c for c in sections[section]["categories"] if c["name"] == name), None)
            if cat is None:
                raise ValueError("Unknown modifier '%s', see list_modifiers" % key)
            value = float(value)
            sides = ["unsided"]
            if cat["has_left_and_right"]:
                sides = [side_req] if side_req in ("left", "right") else ["left", "right"]
            for side in sides:
                if "opposites" in cat:
                    pos = cat["opposites"]["positive-" + side]
                    neg = cat["opposites"]["negative-" + side]
                    AgentService._apply_target(basemesh, section, pos, max(value, 0.0))
                    AgentService._apply_target(basemesh, section, neg, max(-value, 0.0))
                else:
                    target = {"left": "l-", "right": "r-"}.get(side, "") + cat["name"]
                    AgentService._apply_target(basemesh, section, target, min(max(value, 0.0), 1.0))
        HumanService.refit(basemesh)
        AgentService._ground(basemesh)
        return AgentService.describe_human(basemesh.name)["targets"]

    # ----------------------------------------------------------------------------------------------------------------
    # Assets

    @staticmethod
    @_command
    def list_assets(kind: str = "clothes", query: str | None = None) -> list[JsonDict]:
        """Installed assets of a kind: clothes, hair, eyebrows, eyelashes, eyes, teeth, tongue, proxymeshes, skins."""
        if kind not in ASSET_KINDS:
            raise ValueError("Unknown asset kind '%s', use one of %s" % (kind, ", ".join(ASSET_KINDS)))
        subdir, ftype, _ = ASSET_KINDS[kind]
        out = []
        for key, info in sorted(AssetService.get_asset_list(subdir, ftype).items()):
            label = str(info.get("label") or key)
            if query and query.lower() not in (label + " " + str(key)).lower():
                continue
            thumb = info.get("thumb_path")
            out.append({"name": info.get("name_without_ext") or key, "label": label, "path": info["full_path"],
                        "thumbnail": thumb if thumb and os.path.exists(str(thumb)) else None})
        return out

    @staticmethod
    @_command
    def equip_asset(kind: str, name: str, human: str | None = None, material_type: str = "MAKESKIN",
                    replace: bool = True, subdiv_levels: int = 0) -> JsonDict:
        """Put an asset on the human (kind as in list_assets; name or absolute file path). Hair, eyes, eyebrows,
        eyelashes, teeth, tongue and proxy replace the one already equipped when replace is true. skins go through
        set_skin. material_type: MAKESKIN, GAMEENGINE or PROCEDURAL_EYES (eyes)."""
        if kind == "skins":
            return AgentService.set_skin(name, human=human)
        basemesh = AgentService._basemesh(human)
        path = AgentService._asset_path(kind, name)
        object_type = ASSET_KINDS[kind][2]
        if replace and object_type in SINGLE_SLOT_TYPES:
            for obj in AgentService._hierarchy(basemesh):
                if obj is not basemesh and ObjectService.get_object_type(obj) == object_type:
                    HumanService.unload_mhclo_asset(basemesh, obj)
        before = set(bpy.data.objects)
        asset = HumanService.add_mhclo_asset(path, basemesh, asset_type=object_type, subdiv_levels=subdiv_levels,
                                             material_type=material_type)
        if asset is None:
            asset = next(iter(set(bpy.data.objects) - before), None)
        if asset is not None:
            asset["mpfb_asset_path"] = path  # lets add_rig rig it later, wherever the file lives
        return {"object": asset.name if asset else None, "type": object_type, "source": path}

    @staticmethod
    @_command
    def unequip_asset(asset: str) -> JsonDict:
        """Remove an equipped asset by its object name (see describe_human)."""
        obj = AgentService._object(asset)
        basemesh = AgentService._basemesh(asset)
        HumanService.unload_mhclo_asset(basemesh, obj)
        return {"removed": asset}

    @staticmethod
    @_command
    def set_skin(name: str, human: str | None = None, skin_type: str = "GAMEENGINE") -> JsonDict:
        """Apply a skins asset. skin_type: GAMEENGINE (exports cleanly to game engines), ENHANCED_SSS or MAKESKIN."""
        basemesh = AgentService._basemesh(human)
        path = AgentService._asset_path("skins", name)
        HumanService.set_character_skin(path, basemesh, skin_type=skin_type)
        return {"skin": path, "skin_type": skin_type}

    @staticmethod
    def _srgb_to_linear(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    @staticmethod
    @_command
    def set_material(object: str, color: list[float], mode: str = "flat",  # pylint: disable=W0622
                     roughness: float | None = None, metallic: float | None = None,
                     image_dir: str | None = None, contrast: float = 1.0,
                     remove_maps: list[str] | None = None) -> JsonDict:
        """Recolor a mesh (equipped clothes, hair, the body...). color: [r, g, b] sRGB 0..1.
        mode "flat": replace the material with a MakeSkin material whose diffuse texture is a small PNG of that
        color (written to image_dir), so the color survives MakeClothes (.mhmat) and glTF export.
        mode "tint": keep the material and its maps, and write a recolored copy of the diffuse texture (luminance
        detail such as folds and seams is kept) as PNG to image_dir (default: next to the .blend file, else temp),
        so it still exports as a plain image texture. contrast scales that detail (0.5 = half: fades printed logos
        while keeping soft folds). remove_maps drops MakeSkin maps from the material, e.g. ["aomap", "normalmap"]
        when the source garment has prints or seams baked into them."""
        obj = AgentService._object(object)
        lin = [AgentService._srgb_to_linear(float(c)) for c in color[:3]]
        result: JsonDict = {"object": obj.name, "mode": mode}
        if mode == "flat":
            from .materialservice import MaterialService  # pylint: disable=C0415
            from ..entities.material.makeskinmaterial import MakeSkinMaterial, _TEXTURE_NAMES  # pylint: disable=C0415
            if MaterialService.has_materials(obj):
                MaterialService.delete_all_materials(obj)
            swatch = AgentService._swatch(color, obj.name, image_dir)
            template: dict[str, str] = {}
            for part in _TEXTURE_NAMES:
                template["has_" + part] = "true" if part == "diffuse" else "false"
                template[part + "_filename"] = json.dumps(swatch) if part == "diffuse" else "\"\""
            material = MaterialService.create_empty_material(obj.name + "_material", obj)
            MakeSkinMaterial().apply_node_tree(material, template)
            result["texture"] = swatch
        elif mode == "tint":
            material = obj.active_material
            if material is None:
                raise ValueError("'%s' has no material to tint" % obj.name)
            material = material.copy()
            obj.active_material = material
        else:
            raise ValueError("mode must be flat or tint")
        material.diffuse_color = (*lin, 1.0)
        tree = material.node_tree
        for node in (tree.nodes if tree else []):
            if node.type != "BSDF_PRINCIPLED":
                continue
            base = node.inputs["Base Color"]
            image_node = GarmentService.diffuse_image_node(material) if mode == "tint" else None
            if image_node is not None and image_node.image is not None:
                result["texture"] = AgentService._tint_image(image_node, color, obj.name, image_dir, contrast)
            else:
                for link in [l for l in tree.links if l.to_socket == base]:
                    tree.links.remove(link)
                base.default_value = (*lin, 1.0)
            if roughness is not None:
                node.inputs["Roughness"].default_value = float(roughness)
            if metallic is not None:
                node.inputs["Metallic"].default_value = float(metallic)
        for part in remove_maps or []:
            node = tree.nodes.get(part + "Texture") if tree else None
            if node is None:
                continue
            for link in list(tree.links):
                if link.from_node == node:
                    to = link.to_node
                    tree.links.remove(link)
                    if to.type == "MIX_RGB":
                        to.inputs["Fac"].default_value = 0.0  # e.g. aoMix: pass the diffuse through unchanged
                    elif to.type in ("NORMAL_MAP", "BUMP"):
                        to.inputs["Strength"].default_value = 0.0
            tree.nodes.remove(node)
            result.setdefault("removed_maps", []).append(part)
        result["material"] = material.name
        return result

    @staticmethod
    def _texture_dir(image_dir: str | None) -> str:
        image_dir = image_dir or (os.path.join(os.path.dirname(bpy.data.filepath), "textures") if bpy.data.filepath
                                  else os.path.join(tempfile.gettempdir(), "mpfb_agent_textures"))
        os.makedirs(image_dir, exist_ok=True)
        return image_dir

    @staticmethod
    def _swatch(color: list[float], name: str, image_dir: str | None) -> str:
        """Write an 8x8 PNG of one sRGB color and return its path."""
        path = os.path.join(AgentService._texture_dir(image_dir),
                            "%s_color.png" % re.sub(r"[^A-Za-z0-9_.-]", "_", name))
        image = bpy.data.images.new(name + "_color", 8, 8, alpha=True)
        image.pixels.foreach_set(numpy.tile(numpy.array([*color[:3], 1.0], dtype=numpy.float32), 64))
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
        bpy.data.images.remove(image)
        return path

    @staticmethod
    def _tint_image(image_node: bpy.types.Node, color: list[float], name: str, image_dir: str | None,
                    contrast: float = 1.0) -> str:
        src = image_node.image
        width, height = src.size
        px = numpy.empty(width * height * 4, dtype=numpy.float32)
        src.pixels.foreach_get(px)
        px = px.reshape(-1, 4)
        lum = px[:, :3] @ numpy.array([0.2126, 0.7152, 0.0722], dtype=numpy.float32)
        opaque = px[:, 3] > 0.5
        mean = float(lum[opaque].mean()) if opaque.any() else float(lum.mean())
        detail = numpy.clip(1.0 + (lum / max(mean, 1e-3) - 1.0) * float(contrast), 0.0, 2.0)
        target = numpy.asarray(color[:3], dtype=numpy.float32)
        # Scale the color by the detail, plus a little additive detail so dark targets keep their folds.
        px[:, :3] = numpy.clip(target[None, :] * detail[:, None] + (detail[:, None] - 1.0) * 0.06, 0.0, 1.0)
        path = os.path.join(AgentService._texture_dir(image_dir),
                            "%s_diffuse.png" % re.sub(r"[^A-Za-z0-9_.-]", "_", name))
        scratch = bpy.data.images.new(name + "_diffuse_scratch", width, height, alpha=True)
        scratch.pixels.foreach_set(px.ravel())
        scratch.filepath_raw = path
        scratch.file_format = "PNG"
        scratch.save()
        bpy.data.images.remove(scratch)
        # Load it back from disk: a generated image whose pixels were set from Python renders as an empty GPU
        # texture in EEVEE and the viewport (Blender 5.x), even after update().
        image = bpy.data.images.load(path, check_existing=False)
        image.name = name + "_diffuse"
        image.alpha_mode = src.alpha_mode
        image_node.image = image
        return path

    # ----------------------------------------------------------------------------------------------------------------
    # Rig and pose

    @staticmethod
    @_command
    def add_rig(rig: str = "game_engine", human: str | None = None) -> JsonDict:
        """Add a built-in rig: game_engine (recommended for Godot/Unity/Unreal), default, default_no_toes, mixamo,
        mixamo_unity, cmu_mb, openpose, game_engine_with_breast. Assets equipped before the rig are bound to it
        too (weights interpolated from the body, sub-rigs imported)."""
        basemesh = AgentService._basemesh(human)
        if AgentService._rig(basemesh) is not None:
            raise ValueError("The human already has a rig")
        arm = HumanService.add_builtin_rig(basemesh, rig)
        if arm is None:
            raise ValueError("Could not add rig '%s'" % rig)
        # add_builtin_rig only rigs the body; bind the assets that were equipped before the rig existed.
        from ..entities.clothes.mhclo import Mhclo  # pylint: disable=C0415
        rigged, skipped = [], []
        for asset in list(ObjectService.find_related_mesh_assets(basemesh)):
            if any(m.type == "ARMATURE" for m in asset.modifiers):
                continue
            path = AgentService._equipped_asset_path(asset)
            if path is None:
                skipped.append(asset.name)
                continue
            mhclo = Mhclo()
            mhclo.load(path)  # pylint: disable=E1101
            asset.location = (0.0, 0.0, 0.0)
            ClothesService.set_up_rigging(basemesh, asset, arm, mhclo)
            rigged.append(asset.name)
        return {"rig": arm.name, "bones": [b.name for b in arm.data.bones], "rigged_assets": rigged,
                "not_rigged": skipped}

    @staticmethod
    def _equipped_asset_path(asset: bpy.types.Object) -> str | None:
        path = asset.get("mpfb_asset_path")
        if path and os.path.exists(path):
            return str(path)
        source = GeneralObjectProperties.get_value("asset_source", entity_reference=asset)
        if not source:
            return None
        if os.path.isabs(source) and os.path.exists(source):
            return source
        object_type = ObjectService.get_object_type(asset)
        for subdir, ftype, otype in ASSET_KINDS.values():
            if otype == object_type and ftype != "mhmat":
                return AssetService.find_asset_absolute_path(source, asset_subdir=subdir)
        return None

    @staticmethod
    @_command
    def list_poses(human: str | None = None) -> list[str]:
        """Pose names available for the human's rig type."""
        basemesh = AgentService._basemesh(human)
        rig = AgentService._rig(basemesh)
        if rig is None:
            raise ValueError("The human has no rig")
        RigService.ensure_global_poses_are_available()
        rig_type = RigService.identify_rig(rig)
        pose_dir = LocationService.get_user_data(os.path.join("poses", str(rig_type) + "_fk"))
        if not os.path.exists(pose_dir):
            return []
        return sorted(f[:-5] for f in os.listdir(pose_dir) if f.endswith(".json"))

    @staticmethod
    @_command
    def set_pose(bones: JsonDict | None = None, pose: str | None = None, reset: bool = True,
                 human: str | None = None) -> JsonDict:
        """Pose the rig. pose: a name from list_poses, applied first. bones: {bone: {"rotation": [x, y, z] degrees
        (XYZ euler in bone space), "location": [x, y, z]}}. reset clears the previous pose first."""
        basemesh = AgentService._basemesh(human)
        rig = AgentService._rig(basemesh)
        if rig is None:
            raise ValueError("The human has no rig")
        if reset:
            for pb in rig.pose.bones:
                pb.rotation_mode = "XYZ"
                pb.rotation_euler = (0, 0, 0)
                pb.rotation_quaternion = (1, 0, 0, 0)
                pb.location = (0, 0, 0)
        if pose:
            rig_type = RigService.identify_rig(rig)
            path = LocationService.get_user_data(os.path.join("poses", str(rig_type) + "_fk", pose + ".json"))
            if not os.path.exists(path):
                raise ValueError("No pose '%s' for rig type %s" % (pose, rig_type))
            with open(path, "r", encoding="utf-8") as f:
                RigService.set_pose_from_dict(rig, json.load(f))
        missing = []
        for bone, spec in (bones or {}).items():
            pb = rig.pose.bones.get(bone)
            if pb is None:
                missing.append(bone)
                continue
            if "rotation" in spec:
                pb.rotation_mode = "XYZ"
                pb.rotation_euler = Euler([math.radians(a) for a in spec["rotation"]], "XYZ")
            if "location" in spec:
                pb.location = spec["location"]
        bpy.context.view_layer.update()
        if missing:
            raise ValueError("Unknown bones %s; the rig has: %s" % (missing, [b.name for b in rig.data.bones]))
        return {"rig": rig.name, "posed_bones": sorted((bones or {}).keys()), "pose": pose}

    @staticmethod
    @_command
    def pose_bones(bones: JsonDict, reset: bool = True, ground: bool = True, human: str | None = None) -> JsonDict:
        """Pose in world terms (character faces -Y, his left is +X), bones handled parent first:
        {"thigh_l": {"aim": [0.3, -0.2, -0.93]}}            point the bone (head to tail) in a world direction
        {"spine_02": {"rotate": [0, 0, 1, 15]}}              rotate about a world axis by degrees, after aim
        {"foot_l": {"keep_rest": true}}                      keep the rest pose world orientation (flat feet)
        {"index_01_l": {"curl": 70}}                         bend about the bone's own X axis (fingers)
        ground moves the root so the lowest point of the character (soles included) touches z = 0. reset starts from the rest pose."""
        basemesh = AgentService._basemesh(human)
        rig = AgentService._rig(basemesh)
        if rig is None:
            raise ValueError("The human has no rig")
        unknown = [name for name in bones if name not in rig.pose.bones]
        if unknown:
            raise ValueError("Unknown bones %s; the rig has: %s" % (unknown, [b.name for b in rig.data.bones]))
        if reset:
            for pb in rig.pose.bones:
                pb.matrix_basis = Matrix()
        world = rig.matrix_world
        for bone in rig.data.bones:  # parents come before children
            spec = bones.get(bone.name)
            if not spec:
                continue
            pb = rig.pose.bones[bone.name]
            bpy.context.view_layer.update()
            current = world @ pb.matrix
            rot = (world @ bone.matrix_local).to_quaternion() if spec.get("keep_rest") else current.to_quaternion()
            if "aim" in spec:
                along = rot @ Vector((0, 1, 0))
                rot = along.rotation_difference(Vector(spec["aim"]).normalized()) @ rot
            if "rotate" in spec:
                ax, ay, az, deg = spec["rotate"]
                rot = Quaternion(Vector((ax, ay, az)).normalized(), math.radians(deg)) @ rot
            if "curl" in spec:
                rot = rot @ Quaternion(Vector((1, 0, 0)), math.radians(float(spec["curl"])))
            target = Matrix.Translation(current.translation) @ rot.to_matrix().to_4x4()
            pb.matrix = world.inverted() @ target
        bpy.context.view_layer.update()
        drop = 0.0
        if ground:
            lo, _ = AgentService._world_bounds(AgentService._hierarchy(basemesh))
            root = rig.pose.bones[rig.data.bones[0].name]
            for pb in rig.pose.bones:
                if pb.parent is None:
                    root = pb
                    break
            drop = -lo.z
            target = world @ root.matrix
            target.translation.z += drop
            root.matrix = world.inverted() @ target
            bpy.context.view_layer.update()
        return {"posed": sorted(bones), "grounded_by": drop}

    @staticmethod
    @_command
    def keyframe_pose(action: str, frame: int = 1, human: str | None = None) -> JsonDict:
        """Store the current pose as a keyframe of the named action (created if needed) on the rig."""
        basemesh = AgentService._basemesh(human)
        rig = AgentService._rig(basemesh)
        if rig is None:
            raise ValueError("The human has no rig")
        rig.animation_data_create()
        act = bpy.data.actions.get(action) or bpy.data.actions.new(action)
        act.use_fake_user = True
        rig.animation_data.action = act
        for pb in rig.pose.bones:
            pb.keyframe_insert("rotation_euler" if pb.rotation_mode == "XYZ" else "rotation_quaternion", frame=frame)
            pb.keyframe_insert("location", frame=frame)
        return {"action": act.name, "frame": frame}

    # ----------------------------------------------------------------------------------------------------------------
    # Clothes creation (MakeClothes)

    @staticmethod
    @_command
    def list_vertex_groups(human: str | None = None, query: str | None = None) -> list[str]:
        """Basemesh vertex groups, including the helper groups (e.g. helper-tights, helper-skirt, helper-hair)
        that extract_clothes can turn into a starting mesh for new clothes."""
        basemesh = AgentService._basemesh(human)
        return [g.name for g in basemesh.vertex_groups if not query or query.lower() in g.name.lower()]

    @staticmethod
    @_command
    def extract_clothes(vertex_group: str, name: str, human: str | None = None) -> JsonDict:
        """Copy a basemesh vertex group (e.g. helper-tights) into a new mesh object to model clothes from."""
        basemesh = AgentService._basemesh(human)
        if vertex_group not in basemesh.vertex_groups:
            raise ValueError("No vertex group '%s'" % vertex_group)
        before = set(bpy.data.objects)
        ObjectService.extract_vertex_group_to_new_object(basemesh, vertex_group)
        new = [o for o in bpy.data.objects if o not in before]
        if not new:
            raise ValueError("Extraction did not create an object")
        obj = new[0]
        obj.name = name
        GeneralObjectProperties.set_value("object_type", "Clothes", entity_reference=obj)
        return {"object": obj.name, "vertices": len(obj.data.vertices)}

    @staticmethod
    @_command
    def write_clothes(clothes: str, name: str, human: str | None = None, kind: str = "clothes",
                      author: str = "", license: str = "CC0", description: str = "",  # pylint: disable=W0622
                      delete_group: str | None = None, file_path: str | None = None, equip: bool = True,
                      remove_source: bool = True) -> JsonDict:
        """Turn a mesh modeled around the human into an MPFB asset (MakeClothes): writes .mhclo/.obj/.mhmat into the
        user library (or file_path), then equips it on the human and removes the source mesh if asked. The mesh must
        have no modifiers, one vertex group per vertex matching basemesh groups (extract_clothes gives that), and the
        human must be in rest pose with no rig modifications. kind: clothes, hair, eyebrows, eyelashes, proxymeshes."""
        basemesh = AgentService._basemesh(human)
        obj = AgentService._object(clothes)
        if kind not in ASSET_KINDS or ASSET_KINDS[kind][1] == "mhmat":
            raise ValueError("kind must be one of clothes, hair, eyebrows, eyelashes, eyes, teeth, tongue, proxymeshes")
        subdir, ftype, object_type = ASSET_KINDS[kind]
        GeneralObjectProperties.set_value("object_type", object_type, entity_reference=obj)
        if not GeneralObjectProperties.get_value("uuid", entity_reference=obj):
            GeneralObjectProperties.set_value("uuid", str(uuid.uuid4()), entity_reference=obj)
        cleaned = re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9_]", "_", name.strip().replace(" ", "_")))
        if not file_path:
            file_path = os.path.join(LocationService.get_user_data(os.path.join(subdir, cleaned)), cleaned + "." + ftype)
        file_path = os.path.abspath(os.path.expanduser(file_path))
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        GarmentService.prepare_for_makeclothes(obj)
        ClothesService.build_basemesh_xref_cache(basemesh)
        props = {"author": author, "description": description, "name": cleaned, "homepage": "", "license": license,
                 "uuid": GeneralObjectProperties.get_value("uuid", entity_reference=obj)}
        written = ClothesService.write_mhclo_asset(basemesh, obj, file_path, properties_dict=props,
                                                   delete_group=delete_group)
        AssetService.update_all_asset_lists()
        result: JsonDict = {"file": written}
        if equip:
            if remove_source:
                ObjectService.delete_object(obj)
            result["equipped"] = AgentService.equip_asset(kind, written, human=basemesh.name)
        return result

    # ----------------------------------------------------------------------------------------------------------------
    # Garment editing (GarmentService); positions may be "joint:<name>" references, see get_joints

    @staticmethod
    def _garment(obj_name: str, human: str | None) -> tuple[bpy.types.Object, dict[str, Vector]]:
        obj = AgentService._object(obj_name)
        basemesh = AgentService._basemesh(human)
        return obj, GarmentService.joints(basemesh)

    @staticmethod
    @_command
    def get_joints(human: str | None = None, query: str | None = None) -> JsonDict:
        """Rest pose joint centres (world, metres) from the basemesh joint helpers: shoulders, elbows, hands,
        knees, ankles, neck, head, eyes, pelvis, spine-1..5, fingers, toes... Use them as "joint:<name>" in specs."""
        joints = GarmentService.joints(AgentService._basemesh(human))
        return {k: list(v) for k, v in sorted(joints.items()) if not query or query in k}

    @staticmethod
    @_command
    def derive_clothes(source: str, name: str, part: str = "all", human: str | None = None,
                       hide_source: bool = True) -> JsonDict:
        """Make an editable copy of an equipped asset (e.g. take the shirt or the trousers of a suit) to cut and
        reshape into new clothes. part: all, upper (parts above the pelvis) or lower (parts below)."""
        src, joints = AgentService._garment(source, human)
        obj = GarmentService.duplicate(src, name)
        pelvis = joints["pelvis"].z
        if part == "upper":
            GarmentService.keep_islands(obj, 1, lambda c: c.z > pelvis + 0.08)
        elif part == "lower":
            GarmentService.keep_islands(obj, 1, lambda c: c.z < pelvis + 0.08)
        elif part != "all":
            raise ValueError("part must be all, upper or lower")
        if hide_source:
            src.hide_viewport = src.hide_render = True
        return {"object": obj.name, "vertices": len(obj.data.vertices)}

    @staticmethod
    @_command
    def cut_clothes(object: str, regions: list[JsonDict], human: str | None = None,  # pylint: disable=W0622
                    keep_largest: int = 0, smooth_edges: int = 3) -> JsonDict:
        """Delete the vertices inside any of the regions (see GarmentService for the shapes: box, sphere,
        half_space, limb, opening). E.g. sleeves past the elbow: {"type": "limb", "from": "shoulder", "to":
        "elbow", "beyond": 0.07}. keep_largest > 0 then drops all but that many connected pieces. smooth_edges
        relaxes the zigzag border left by the cut (iterations)."""
        obj, joints = AgentService._garment(object, human)
        deleted = GarmentService.delete_vertices(obj, GarmentService.any_region(regions, joints))
        if keep_largest:
            GarmentService.keep_islands(obj, keep_largest)
        if smooth_edges:
            GarmentService.smooth_boundaries(obj, smooth_edges)
        return {"deleted": deleted, "vertices": len(obj.data.vertices)}

    @staticmethod
    @_command
    def roll_edges(object: str, region: JsonDict, toward: list[Any], out: float = 0.01, back: float = 0.04,  # pylint: disable=W0622
                   human: str | None = None) -> JsonDict:
        """Roll the open ends whose centre lies in region (e.g. sleeve ends: {"type": "limb", "from": "shoulder",
        "to": "elbow", "beyond": 0}) out by `out` and back by `back` toward the nearest of `toward` (points or
        "joint:" refs, e.g. ["joint:l-shoulder", "joint:r-shoulder"])."""
        obj, joints = AgentService._garment(object, human)
        targets = [GarmentService.resolve_point(t, joints) for t in toward]
        rolled = GarmentService.roll_edges(obj, GarmentService.region_test(region, joints), targets, out, back)
        return {"rolled_loops": rolled}

    @staticmethod
    @_command
    def inflate(object: str, distance: float, regions: list[JsonDict] | None = None,  # pylint: disable=W0622
                human: str | None = None) -> JsonDict:
        """Push vertices out along their normals (loosen a garment, make an outer layer); only those in regions
        if given."""
        obj, joints = AgentService._garment(object, human)
        test = GarmentService.any_region(regions, joints) if regions else None
        return {"moved": GarmentService.inflate(obj, float(distance), test)}

    @staticmethod
    @_command
    def solidify(object: str, thickness: float = 0.004, offset: float = -1.0) -> JsonDict:  # pylint: disable=W0622
        """Give a sheet garment thickness (applied, so the mesh stays valid for MakeClothes)."""
        obj = AgentService._object(object)
        GarmentService.apply_modifier(obj, "SOLIDIFY", thickness=float(thickness), offset=float(offset),
                                      use_even_offset=True)
        return {"vertices": len(obj.data.vertices)}

    @staticmethod
    @_command
    def join_objects(target: str, sources: list[str]) -> JsonDict:
        """Join meshes into target (e.g. a generated hood roll into a vest). Sources are removed."""
        obj = AgentService._object(target)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        inv = obj.matrix_world.inverted()
        for name in sources:
            src = AgentService._object(name)
            mesh = src.data.copy()
            mesh.transform(inv @ src.matrix_world)
            offset = len(obj.data.materials)
            for mat in src.data.materials:
                obj.data.materials.append(mat)
            tmp = bmesh.new()
            tmp.from_mesh(mesh)
            for f in tmp.faces:
                f.material_index += offset
            tmp.to_mesh(mesh)
            tmp.free()
            bm.from_mesh(mesh)
            bpy.data.meshes.remove(mesh)
            ObjectService.delete_object(src)
        bm.to_mesh(obj.data)
        bm.free()
        return {"object": obj.name, "vertices": len(obj.data.vertices)}

    @staticmethod
    @_command
    def set_visibility(object: str, visible: bool) -> JsonDict:  # pylint: disable=W0622
        """Show or hide an object in the viewport and in renders/previews."""
        obj = AgentService._object(object)
        obj.hide_viewport = obj.hide_render = not visible
        return {"object": obj.name, "visible": visible}

    @staticmethod
    @_command
    def delete_object(object: str) -> JsonDict:  # pylint: disable=W0622
        """Delete an object that is not an equipped asset (use unequip_asset for those)."""
        ObjectService.delete_object(AgentService._object(object))
        return {"deleted": object}

    @staticmethod
    @_command
    def paint_strokes(object: str, strokes: list[list[float]], color: list[float], view: str = "front",  # pylint: disable=W0622
                      jitter: float = 0.0) -> JsonDict:
        """Paint into the object's diffuse texture (tint it first so it has its own PNG): strokes are
        [u1, v1, u2, v2, width] in metres in the view plane (front/back: x, z; left/right: y, z). jitter 0..0.5
        roughens the edges like a brush. E.g. a red X on a trouser leg."""
        obj = AgentService._object(object)
        return {"texels": GarmentService.paint_strokes(obj, strokes, color, view, float(jitter))}

    @staticmethod
    @_command
    def make_belt(name: str, z: Any = "joint:pelvis", offset: float = 0.0, width: float = 0.04,
                  thickness: float = 0.006, clearance: float = 0.012, buckle: bool = True,
                  human: str | None = None) -> JsonDict:
        """Generate a belt hugging the body at height z (number or joint ref, plus offset), with a front buckle."""
        basemesh = AgentService._basemesh(human)
        joints = GarmentService.joints(basemesh)
        height = GarmentService.resolve_point(z, joints).z if isinstance(z, str) else float(z)
        obj = GarmentService.make_belt(basemesh, name, height + float(offset), width, thickness, clearance, buckle)
        return {"object": obj.name, "z": height + float(offset)}

    @staticmethod
    @_command
    def make_glasses(name: str, lens_radius: float = 0.021, forward: float = 0.03, shape: str = "round",
                     human: str | None = None) -> JsonDict:
        """Generate glasses in front of the eyes (material 0 frame, 1 lenses); shape round or square."""
        obj = GarmentService.make_glasses(AgentService._basemesh(human), name, lens_radius, 0.0022, forward, shape)
        return {"object": obj.name}

    @staticmethod
    @_command
    def make_tube(name: str, points: list[Any], radii: list[float], sides: int = 12, closed: bool = False,
                  flatten: float = 1.0, human: str | None = None) -> JsonDict:
        """Sweep a tube through points (world or "joint:" refs, one radius each): collars, hood rolls, straps,
        wristbands. flatten < 1 makes an oval band."""
        joints = GarmentService.joints(AgentService._basemesh(human))
        pts = [GarmentService.resolve_point(p, joints) for p in points]
        obj = GarmentService.make_tube(name, pts, [float(r) for r in radii], int(sides), bool(closed), float(flatten))
        return {"object": obj.name, "vertices": len(obj.data.vertices)}

    @staticmethod
    @_command
    def create_delete_group(clothes: str, group_name: str = "Delete", human: str | None = None) -> JsonDict:
        """Make a rough basemesh vertex group of the body hidden under the given clothes (for write_clothes'
        delete_group), so skin does not poke through tight clothes."""
        basemesh = AgentService._basemesh(human)
        obj = AgentService._object(clothes)
        if obj.modifiers:
            raise ValueError("The clothes must not have modifiers")
        copy = obj.copy()
        copy.data = obj.data.copy()
        bpy.context.scene.collection.objects.link(copy)
        for group in list(copy.vertex_groups):
            copy.vertex_groups.remove(group)
        body = copy.vertex_groups.new(name="body")
        body.add(list(range(len(copy.data.vertices))), 1.0, "REPLACE")
        mhclo = ClothesService.create_mhclo_from_clothes_matching(basemesh, copy)
        ClothesService.create_new_delete_group(basemesh, copy, mhclo, group_name=group_name)
        ObjectService.delete_object(copy)
        return {"group": group_name, "vertices": sum(1 for v in basemesh.data.vertices
                                                      if any(g.group == basemesh.vertex_groups[group_name].index
                                                             for g in v.groups))}

    # ----------------------------------------------------------------------------------------------------------------
    # Output

    @staticmethod
    @_command
    def render_views(human: str | None = None, views: list[str] | None = None, framing: str = "body",
                     size: int = 768, engine: str = "EEVEE", out_dir: str | None = None) -> list[str]:
        """Render orthographic previews and return the PNG paths. views: front, back, left, right, three_quarter,
        three_quarter_right. framing: body or head. engine: EEVEE (textures and materials, studio lights) or
        WORKBENCH (fastest, shape only: material colors without textures)."""
        basemesh = AgentService._basemesh(human)
        views = views or ["front", "left", "three_quarter", "back"]
        lo, hi = AgentService._world_bounds(AgentService._hierarchy(basemesh))
        center = (lo + hi) / 2
        extent = max(hi.z - lo.z, hi.x - lo.x, hi.y - lo.y) * 1.08
        if framing == "head":
            extent = (hi.z - lo.z) * 0.17
            rig = AgentService._rig(basemesh)
            head = rig.pose.bones.get("head") or rig.pose.bones.get("Head") if rig else None
            if head is not None:
                center = rig.matrix_world @ ((head.head + head.tail) / 2)
            else:
                center = Vector((center.x, center.y, hi.z - extent * 0.5))
        out_dir = out_dir or os.path.join(tempfile.gettempdir(), "mpfb_agent_renders")
        os.makedirs(out_dir, exist_ok=True)

        scene = bpy.context.scene
        saved = (scene.render.engine, scene.camera, scene.render.resolution_x, scene.render.resolution_y,
                 scene.render.resolution_percentage, scene.render.film_transparent, scene.world)
        cam = bpy.data.objects.new("mpfb_agent_camera", bpy.data.cameras.new("mpfb_agent_camera"))
        scene.collection.objects.link(cam)
        temporary: list[bpy.types.Object] = [cam]
        world = bpy.data.worlds.new("mpfb_agent_world")
        world.color = (0.05, 0.05, 0.055)
        if world.node_tree and world.node_tree.nodes.get("Background"):
            world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.05, 0.05, 0.055, 1)
        scene.world = world
        scene.render.film_transparent = False
        if engine.upper() != "WORKBENCH":
            # Studio lights that follow the camera: key above the camera, fill from the side, rim behind.
            # Directions from the subject in camera space: x right, y up, z toward the camera.
            for name, energy, offset in (("key", 4.0, (0.6, 0.8, 1.0)), ("fill", 1.5, (-1.0, 0.2, 0.6)),
                                         ("rim", 3.0, (0.0, 0.6, -1.0))):
                light = bpy.data.objects.new("mpfb_agent_" + name, bpy.data.lights.new("mpfb_agent_" + name, "SUN"))
                light.data.energy = energy
                light["offset"] = offset
                scene.collection.objects.link(light)
                temporary.append(light)
        paths = []
        try:
            cam.data.type = "ORTHO"
            cam.data.ortho_scale = extent
            cam.data.clip_end = 100
            scene.camera = cam
            scene.render.engine = "BLENDER_WORKBENCH" if engine.upper() == "WORKBENCH" else "BLENDER_EEVEE"
            if engine.upper() == "WORKBENCH":
                shading = scene.display.shading
                shading.light = "STUDIO"
                shading.color_type = "MATERIAL"
                shading.show_cavity = True
            scene.render.resolution_x = scene.render.resolution_y = int(size)
            scene.render.resolution_percentage = 100
            for view in views:
                if view not in VIEWS:
                    raise ValueError("Unknown view '%s', use %s" % (view, list(VIEWS)))
                direction = Vector(VIEWS[view])
                cam.location = center + direction * 10
                cam.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
                for light in temporary[1:]:
                    toward = cam.rotation_euler.to_matrix() @ Vector(light["offset"]).normalized()
                    light.rotation_euler = (-toward).to_track_quat("-Z", "Y").to_euler()
                bpy.ops.render.render()
                path = os.path.join(out_dir, "%s_%s_%s.png" % (basemesh.name, framing, view))
                # Saving the render result works in every Blender version, also where write_still does not.
                bpy.data.images["Render Result"].save_render(path)
                paths.append(path)
        finally:
            (scene.render.engine, scene.camera, scene.render.resolution_x, scene.render.resolution_y,
             scene.render.resolution_percentage, scene.render.film_transparent, scene.world) = saved
            for obj in temporary:
                data = obj.data
                bpy.data.objects.remove(obj)
                if isinstance(data, bpy.types.Camera):
                    bpy.data.cameras.remove(data)
                elif isinstance(data, bpy.types.Light):
                    bpy.data.lights.remove(data)
            bpy.data.worlds.remove(world)
        return paths

    @staticmethod
    @_command
    def save_blend(path: str) -> JsonDict:
        """Save the scene as a .blend file (becomes the current file)."""
        path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return {"saved": path}

    @staticmethod
    @_command
    def open_blend(path: str) -> JsonDict:
        """Open a .blend file, replacing the current scene."""
        bpy.ops.wm.open_mainfile(filepath=os.path.abspath(os.path.expanduser(path)))
        return {"opened": bpy.data.filepath, "humans": [h.name for h in AgentService._humans()]}

    @staticmethod
    @_command
    def export_gltf(path: str, human: str | None = None, bake_subdiv: bool = False, animations: bool = True,
                    texture_size: int | None = None) -> JsonDict:
        """Export the human (rig, body and assets, helpers removed, masks baked) to .glb/.gltf for game engines.
        texture_size caps the longest side of every exported texture (e.g. 1024); the scene keeps the originals."""
        basemesh = AgentService._basemesh(human)
        path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        export_root = ExportService.create_character_copy(basemesh, name_suffix="_export")
        try:
            export_basemesh = ObjectService.find_object_of_type_amongst_nearest_relatives(export_root, "Basemesh")
            ExportService.bake_modifiers_remove_helpers(typing.cast(bpy.types.Object, export_basemesh),
                                                        bake_masks=True, bake_subdiv=bake_subdiv,
                                                        remove_helpers=True, also_proxy=True)
            temporary_images: list[bpy.types.Image] = []
            temporary_materials: list[bpy.types.Material] = []
            if texture_size:
                temporary_materials, temporary_images = AgentService._shrink_textures(export_root, int(texture_size))
            ObjectService.deselect_and_deactivate_all()
            objs = [export_root] + list(ObjectService.get_list_of_children(export_root))
            for obj in objs:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = export_root
            bpy.ops.export_scene.gltf(filepath=path, use_selection=True, export_format="GLB" if path.endswith(".glb")
                                      else "GLTF_SEPARATE", export_animations=animations, export_apply=True,
                                      export_yup=True)
        finally:
            for obj in [export_root] + list(ObjectService.get_list_of_children(export_root)):
                ObjectService.delete_object(obj)
            for mat in locals().get("temporary_materials", []):
                bpy.data.materials.remove(mat)
            for img in locals().get("temporary_images", []):
                bpy.data.images.remove(img)
        return {"exported": path, "bytes": os.path.getsize(path)}

    @staticmethod
    def _shrink_textures(root: bpy.types.Object, size: int) -> tuple[list[bpy.types.Material], list[bpy.types.Image]]:
        """Give the export copy its own materials whose images are downscaled to at most size pixels."""
        materials, images = [], []
        scaled: dict[str, bpy.types.Image] = {}
        objs = [root]
        for obj in objs:
            objs.extend(ObjectService.get_list_of_children(obj))
        for obj in objs:
            for slot in getattr(obj, "material_slots", []):
                if slot.material is None or slot.material.node_tree is None:
                    continue
                mat = slot.material.copy()
                materials.append(mat)
                slot.material = mat
                for node in mat.node_tree.nodes:
                    img = getattr(node, "image", None) if node.type == "TEX_IMAGE" else None
                    if img is None or max(img.size) <= size:
                        continue
                    if img.name not in scaled:
                        small = img.copy()
                        factor = size / max(img.size)
                        small.scale(max(1, int(img.size[0] * factor)), max(1, int(img.size[1] * factor)))
                        scaled[img.name] = small
                        images.append(small)
                    node.image = scaled[img.name]
        return materials, images
