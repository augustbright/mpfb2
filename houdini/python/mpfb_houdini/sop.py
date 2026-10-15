"""Cook code for the MPFB SOP digital assets (mpfb::human, mpfb::fit_asset). Each HDA holds a Python SOP that
calls cook_human / cook_fit_asset with its own geometry; parameters are read from the HDA node."""

import json
import os

import hou
import numpy as np

from . import core

_TOPOLOGY = {}

KINDS = ["clothes", "hair", "eyes", "eyebrows", "eyelashes", "teeth", "tongue", "proxymeshes"]


def _group_name(name):
    return name.replace("-", "_").replace(".", "_")


def _float_bytes(array):
    return np.ascontiguousarray(array, dtype=np.float32).tobytes()


def _mesh_geometry(mesh, faces_mask=None, groups=None):
    """hou.Geometry with points, polygons, uv (vertex) and point groups for an OBJ-like mesh dict."""
    geo = hou.Geometry()
    pos = mesh["positions"]
    geo.createPoints([hou.Vector3(0, 0, 0)] * len(pos))
    faces = [f for i, f in enumerate(mesh["faces"]) if faces_mask is None or faces_mask[i]]
    face_uvs = [f for i, f in enumerate(mesh["face_uvs"]) if faces_mask is None or faces_mask[i]]
    # OBJ winding is counter-clockwise, Houdini's is clockwise: reverse each face.
    geo.createPolygons(tuple(tuple(reversed(f)) for f in faces), True)
    if len(mesh["uvs"]):
        uv = mesh["uvs"]
        corner = np.array([t for f in face_uvs for t in reversed(f)], dtype=np.int64)
        values = np.zeros((len(corner), 3), dtype=np.float32)
        ok = corner >= 0
        values[ok, :2] = uv[corner[ok]]
        geo.addAttrib(hou.attribType.Vertex, "uv", (0.0, 0.0, 0.0))
        geo.setVertexFloatAttribValuesFromString("uv", values.tobytes())
    if groups:
        points = geo.points()
        for name, idx in groups.items():
            grp = geo.createPointGroup(_group_name(name))
            grp.add([points[i] for i in idx])
    return geo


def _body_points():
    """Indices of the base mesh points kept without helpers (those used by body faces), in order."""
    if "body_points" not in _TOPOLOGY:
        mesh = core.basemesh()
        body = np.zeros(len(mesh["positions"]), dtype=bool)
        body[mesh["groups"]["body"]] = True
        faces = [f for f in mesh["faces"] if all(body[v] for v in f)]
        _TOPOLOGY["body_points"] = np.unique(np.concatenate([np.asarray(f) for f in faces]))
    return _TOPOLOGY["body_points"]


def _human_topology(with_helpers):
    key = ("human", with_helpers)
    if key not in _TOPOLOGY:
        mesh = core.basemesh()
        groups = mesh["groups"]
        if not with_helpers:
            keep = _body_points()
            remap = np.full(len(mesh["positions"]), -1, dtype=np.int64)
            remap[keep] = np.arange(len(keep))
            inside = [all(remap[v] >= 0 for v in f) for f in mesh["faces"]]
            mesh = {"positions": mesh["positions"][keep], "uvs": mesh["uvs"],
                    "faces": [[int(remap[v]) for v in f] for f, ok in zip(mesh["faces"], inside) if ok],
                    "face_uvs": [t for t, ok in zip(mesh["face_uvs"], inside) if ok]}
            groups = {name: remap[idx][remap[idx] >= 0] for name, idx in groups.items()}
            groups = {name: idx for name, idx in groups.items() if len(idx)}
        geo = _mesh_geometry(mesh, None, groups)
        geo.addAttrib(hou.attribType.Prim, "shop_materialpath", "")
        geo.addAttrib(hou.attribType.Prim, "name", "")
        _TOPOLOGY[key] = geo
    return _TOPOLOGY[key]


def _asset_topology(path):
    key = ("asset", path)
    if key not in _TOPOLOGY:
        info = core.read_mhclo(path)
        mesh = core.read_obj(info["obj_file"])
        geo = _mesh_geometry(mesh)
        geo.addAttrib(hou.attribType.Prim, "shop_materialpath", "")
        geo.addAttrib(hou.attribType.Prim, "name", "")
        _TOPOLOGY[key] = geo
    return _TOPOLOGY[key]


def _set_string_prims(geo, attrib, value):
    geo.setPrimStringAttribValues(attrib, (value,) * len(geo.prims()))


def material_path(node):
    """Each MPFB node owns one material: /mat/mpfb_<node name>."""
    return "/mat/mpfb_" + _group_name(node.name())


# ----------------------------------------------------------------------------------------------------------------

def human_settings(node):
    macros = {k: node.evalParm(k) for k in core.MACRO_KEYS}
    race = {k: node.evalParm(k) for k in core.RACE_KEYS}
    modifiers = {}
    for i in range(1, node.evalParm("modifiers") + 1):
        name = node.evalParm("modifier%d" % i).strip()
        if name:
            side = node.parm("side%d" % i).evalAsString()
            key = name + ("" if side == "both" else "@" + side)
            modifiers[key] = modifiers.get(key, 0.0) + node.evalParm("value%d" % i)
    return macros, race, modifiers


def cook_human(node, geo):
    macros, race, modifiers = human_settings(node)
    scale = node.evalParm("scale")
    pos = core.build_human(macros, race, modifiers, scale=scale, ground=bool(node.evalParm("ground")))
    helpers = bool(node.evalParm("helpers"))
    geo.merge(_human_topology(helpers))
    geo.setPointFloatAttribValuesFromString("P", _float_bytes(pos if helpers else pos[_body_points()]))
    index = np.arange(len(pos), dtype=np.int32) if helpers else _body_points().astype(np.int32)
    geo.addAttrib(hou.attribType.Point, "mpfb_index", 0)
    geo.setPointIntAttribValuesFromString("mpfb_index", index.tobytes())
    # Every base mesh vertex (helpers included), so fit_asset works whether or not helpers are output.
    geo.addArrayAttrib(hou.attribType.Global, "mpfb_positions", hou.attribData.Float)
    geo.setGlobalAttribValue("mpfb_positions", pos.astype(np.float32).ravel().tolist())
    _set_string_prims(geo, "name", "body")
    if node.evalParm("skin").strip():
        _set_string_prims(geo, "shop_materialpath", material_path(node))
    joints = core.joint_centres(pos)
    geo.addAttrib(hou.attribType.Global, "mpfb_joints", {})
    geo.setGlobalAttribValue("mpfb_joints", {k: [float(c) for c in v] for k, v in joints.items()})
    geo.addAttrib(hou.attribType.Global, "mpfb_settings", "")
    geo.setGlobalAttribValue("mpfb_settings", json.dumps({"macros": macros, "race": race, "modifiers": modifiers,
                                                          "scale": scale}))


def asset_file(node):
    override = node.evalParm("file").strip()
    if override:
        return hou.text.expandString(override)
    return core.find_asset(node.parm("kind").evalAsString(), node.evalParm("asset"))


def cook_fit_asset(node, geo, human):
    if human is None:
        raise hou.NodeError("Connect an mpfb::human to the input")
    count = len(core.basemesh()["positions"])
    if human.findGlobalAttrib("mpfb_positions") is not None:
        raw = np.asarray(human.attribValue("mpfb_positions"), dtype=np.float32).reshape(-1, 3)
    elif len(human.points()) == count:
        raw = np.frombuffer(human.pointFloatAttribValuesAsString("P"), dtype=np.float32).reshape(-1, 3)
    else:
        raise hou.NodeError("Connect an mpfb::human (or geometry with its %d points) to the input" % count)
    path = asset_file(node)
    info = core.read_mhclo(path)
    settings = json.loads(human.attribValue("mpfb_settings") or "{}")
    pos = core.fit_mhclo(info, raw.astype(np.float64), scale=settings.get("scale", 0.1))
    geo.clear()  # a Python SOP starts with a copy of its input
    geo.merge(_asset_topology(path))
    geo.setPointFloatAttribValuesFromString("P", _float_bytes(pos))
    _set_string_prims(geo, "name", info["name"])
    _set_string_prims(geo, "shop_materialpath", material_path(node))
    geo.addAttrib(hou.attribType.Global, "mpfb_asset", "")
    geo.setGlobalAttribValue("mpfb_asset", path)
    # Body vertices this asset hides (MHCLO delete_verts), for mpfb::assemble. Kept on the first primitive so it
    # survives merging several assets.
    geo.addArrayAttrib(hou.attribType.Prim, "mpfb_delete_verts", hou.attribData.Int)
    if geo.prims() and info["delete_verts"]:
        geo.prims()[0].setAttribValue("mpfb_delete_verts", [int(v) for v in info["delete_verts"]])


# ----------------------------------------------------------------------------------------------------------------
# Menus and buttons

def modifier_menu():
    out = []
    for m in core.list_modifiers():
        out += [m["modifier"], m["modifier"] + ("  (-1..1)" if m["bipolar"] else "")]
    return out


def asset_menu(node):
    out = []
    for name in core.list_assets(node.parm("kind").evalAsString()):
        out += [name, name]
    return out


def skin_menu():
    out = ["", "(none)"]
    for name in core.list_assets("skins"):
        out += [name, name]
    return out


def _set(node, parm, value):
    p = node.parm(parm) or (node.parmTuple(parm) if node.parmTuple(parm) else None)
    if p is not None:
        p.set(value)


def make_material(node, mhmat_path, tint=(1.0, 1.0, 1.0), diffuse_override=""):
    """Create (or update) the node's Principled shader in /mat from an .mhmat: diffuse texture (or an override
    file), multiplied by tint; alpha cut-outs (hair, brows, lashes) use the texture's alpha; normal map."""
    mat = hou.node("/mat")
    path = material_path(node)
    shader = hou.node(path) or mat.createNode("principledshader::2.0", path.rsplit("/", 1)[1])
    settings = core.read_mhmat(mhmat_path) if mhmat_path and os.path.exists(mhmat_path) else {}
    color = tuple(float(c) for c in settings.get("diffuseColor", "0.8 0.8 0.8").split()[:3])
    diffuse = hou.text.expandString(diffuse_override) if diffuse_override else settings.get("diffuseTexture")
    if diffuse and os.path.exists(diffuse):
        _set(shader, "basecolor", tuple(tint))
        _set(shader, "basecolor_useTexture", True)
        _set(shader, "basecolor_texture", diffuse)
        cutout = settings.get("transparent", "").lower() in ("true", "1") or any(
            k in os.path.basename(mhmat_path or "") for k in ("hair", "eyebrow", "eyelash", "ponytail", "bob",
                                                               "braid", "afro", "short0", "long0"))
        _set(shader, "basecolor_useTextureAlpha", cutout)
    else:
        _set(shader, "basecolor", tuple(c * t for c, t in zip(color, tint)))
        _set(shader, "basecolor_useTexture", False)
    normal = settings.get("normalmapTexture")
    if normal and os.path.exists(normal):
        _set(shader, "baseBumpAndNormal_enable", True)
        _set(shader, "baseBumpAndNormal_type", "normal")
        _set(shader, "baseNormal_texture", normal)
    _set(shader, "rough", float(settings.get("roughness", 0.6)))
    return shader


def create_human_material(node):
    skin = node.evalParm("skin").strip()
    if skin:
        make_material(node, core.find_asset("skins", skin))


def create_asset_material(node):
    info = core.read_mhclo(asset_file(node))
    make_material(node, info["material"], node.parmTuple("tint").eval(), node.evalParm("diffuse"))


def cook_assemble(node, geo, human, assets):
    """Human (first input) plus merged fitted assets (second input), without the body faces the assets hide
    (their MHCLO delete_verts, as MPFB's delete groups do)."""
    geo.clear()
    if human is None or human.findPointAttrib("mpfb_index") is None:
        raise hou.NodeError("The first input must be an mpfb::human")
    hidden = set()
    if assets is not None and node.evalParm("mask_body") and assets.findPrimAttrib("mpfb_delete_verts") is not None:
        for prim in assets.prims():
            values = prim.attribValue("mpfb_delete_verts")
            if values:
                hidden.update(values)
    body = hou.Geometry()
    body.merge(human)
    if hidden:
        index = np.frombuffer(body.pointIntAttribValuesAsString("mpfb_index"), dtype=np.int32)
        hide = np.isin(index, np.fromiter(hidden, dtype=np.int32))
        body.deletePrims([prim for prim in body.prims() if any(hide[v.point().number()] for v in prim.vertices())])
    geo.merge(body)
    if assets is not None:
        geo.merge(assets)
