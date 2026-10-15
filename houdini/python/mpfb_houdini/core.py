"""MPFB human generation without Blender: base mesh, targets, macros, modifiers and MHCLO fitting.

Everything works in MakeHuman's own space, which is Houdini's: Y up, the character faces +Z, his left side is +X.
Distances in the data files are decimetres; ``scale`` (0.1 by default) turns them into metres.

The macro formula and the MHCLO fitting follow MPFB's TargetService and ClothesService, so the same settings give
the same vertices as MPFB in Blender (see tests/compare_with_blender.py).
"""

import functools
import glob
import gzip
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

MACRO_KEYS = ["gender", "age", "muscle", "weight", "proportions", "height", "cupsize", "firmness"]
RACE_KEYS = ["african", "asian", "caucasian"]
DEFAULT_MACROS = {"gender": 0.5, "age": 0.5, "muscle": 0.5, "weight": 0.5, "proportions": 0.5, "height": 0.5,
                  "cupsize": 0.5, "firmness": 0.5}
DEFAULT_RACE = {"african": 0.33, "asian": 0.33, "caucasian": 0.33}


# ----------------------------------------------------------------------------------------------------------------
# Locations

def data_root():
    """MPFB's system data (base mesh, targets, rigs): $MPFB_DATA, else src/mpfb/data of this repository."""
    root = os.environ.get("MPFB_DATA") or os.path.normpath(os.path.join(HERE, "..", "..", "..", "src", "mpfb", "data"))
    if not os.path.isdir(os.path.join(root, "targets")):
        raise IOError("MPFB data not found at %s; set MPFB_DATA" % root)
    return root


def asset_roots():
    """Asset libraries (clothes, hair, eyes, skins...): $MPFB_EXTRA_ASSETS first, then $MPFB_ASSETS, else MPFB's
    user data folders of every Blender version found, newest first (both os.pathsep separated)."""
    env = os.environ.get("MPFB_ASSETS")
    if env:
        roots = [p for p in env.split(os.pathsep) if p]
    else:
        pattern = os.path.expanduser("~/Library/Application Support/Blender/*/extensions/.user/*/mpfb/data")
        pattern_linux = os.path.expanduser("~/.config/blender/*/extensions/.user/*/mpfb/data")
        roots = sorted(glob.glob(pattern) + glob.glob(pattern_linux), reverse=True)
    # Project libraries (like MPFB's secondary asset root), searched first.
    extra = [p for p in os.environ.get("MPFB_EXTRA_ASSETS", "").split(os.pathsep) if p]
    return list(dict.fromkeys(os.path.realpath(os.path.expanduser(p)) for p in extra + roots))


def find_asset(kind, name):
    """Absolute path of an asset by name or file (kind: clothes, hair, eyes, eyebrows, eyelashes, teeth, tongue,
    proxymeshes, skins)."""
    if os.path.isabs(name) and os.path.exists(name):
        return name
    ext = {"skins": ".mhmat", "proxymeshes": ".proxy"}.get(kind, ".mhclo")
    stem = os.path.splitext(name)[0]
    for root in asset_roots():
        for candidate in (os.path.join(root, kind, stem, stem + ext), os.path.join(root, kind, name)):
            if os.path.exists(candidate):
                return candidate
        hits = glob.glob(os.path.join(root, kind, "**", stem + ext), recursive=True)
        if hits:
            return hits[0]
    raise IOError("No %s asset '%s' in %s" % (kind, name, asset_roots()))


def list_assets(kind):
    ext = {"skins": ".mhmat", "proxymeshes": ".proxy"}.get(kind, ".mhclo")
    names = set()
    for root in asset_roots():
        for path in glob.glob(os.path.join(root, kind, "**", "*" + ext), recursive=True):
            names.add(os.path.splitext(os.path.basename(path))[0])
    return sorted(names)


# ----------------------------------------------------------------------------------------------------------------
# Wavefront OBJ

def read_obj(path):
    """Positions (N, 3), faces (list of vertex index lists), face UV indices, UVs (M, 2) and face groups."""
    positions, uvs, faces, face_uvs = [], [], [], []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            tag = parts[0]
            if tag == "v":
                positions.append([float(parts[1]), float(parts[2]), float(parts[3])])
            elif tag == "vt":
                uvs.append([float(parts[1]), float(parts[2])])
            elif tag == "f":
                vs, ts = [], []
                for corner in parts[1:]:
                    bits = corner.split("/")
                    vs.append(int(bits[0]) - 1)
                    ts.append(int(bits[1]) - 1 if len(bits) > 1 and bits[1] else -1)
                faces.append(vs)
                face_uvs.append(ts)
    return {"positions": np.asarray(positions, dtype=np.float64), "uvs": np.asarray(uvs, dtype=np.float64),
            "faces": faces, "face_uvs": face_uvs}


@functools.lru_cache(maxsize=1)
def basemesh():
    """The MakeHuman hm08 base mesh, unscaled, with its vertex groups (name -> sorted index array)."""
    root = data_root()
    mesh = read_obj(os.path.join(root, "3dobjs", "base.obj"))
    with open(os.path.join(root, "mesh_metadata", "basemesh_vertex_groups.json"), "r", encoding="utf-8") as f:
        ranges = json.load(f)
    groups = {}
    for name, spans in ranges.items():
        groups[name] = np.concatenate([np.arange(a, b + 1) for a, b in spans]).astype(np.int32)
    mesh["groups"] = groups
    return mesh


# ----------------------------------------------------------------------------------------------------------------
# Targets

@functools.lru_cache(maxsize=4096)
def load_target(path):
    """A target file as (vertex indices, offsets (k, 3)) in MakeHuman space."""
    opener = gzip.open if path.endswith(".gz") else open
    idx, off = [], []
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line[0] in "#\"":
                continue
            parts = line.split()
            idx.append(int(parts[0]))
            off.append([float(parts[1]), float(parts[2]), float(parts[3])])
    return np.asarray(idx, dtype=np.int64), np.asarray(off, dtype=np.float64).reshape(-1, 3)


def target_path(name):
    """Path of a system target given as "section/name" (as in macro stacks and target.json)."""
    path = os.path.join(data_root(), "targets", name + ".target.gz")
    if not os.path.exists(path):
        alt = os.path.join(data_root(), "targets", name + ".target")
        if os.path.exists(alt):
            return alt
    return path


# ----------------------------------------------------------------------------------------------------------------
# Macros (port of TargetService._interpolate_macro_components / calculate_target_stack_from_macro_info_dict)

@functools.lru_cache(maxsize=1)
def _macro_config():
    with open(os.path.join(data_root(), "targets", "macrodetails", "macro.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def _components(macro, value):
    out = []
    for part in _macro_config()["macrotargets"][macro]["parts"]:
        lowest, highest = part["lowest"], part["highest"]
        if lowest < value < highest:
            pct = (value - lowest) / (highest - lowest)
            if part["low"]:
                out.append((part["low"], round(1 - pct, 4)))
            if part["high"]:
                out.append((part["high"], round(pct, 4)))
    return out


def macro_targets(macros=None, race=None, cutoff=0.01):
    """[(target name, weight)] for the given macro values (0..1) and race weights."""
    m = dict(DEFAULT_MACROS, **(macros or {}))
    r = dict(DEFAULT_RACE) if race is None else {k: float(race.get(k, 0.0)) for k in RACE_KEYS}
    c = {k: _components(k, float(m[k])) for k in MACRO_KEYS}
    targets = []
    for rname, rweight in r.items():
        if rweight > 0.0001:
            for age, aw in c["age"]:
                for gender, gw in c["gender"]:
                    if gender != "universal":
                        w = rweight * gw * aw
                        if w > cutoff:
                            targets.append(("macrodetails/%s-%s-%s" % (rname, gender, age), w))
    for gender, gw in c["gender"]:
        for age, aw in c["age"]:
            for muscle, mw in c["muscle"]:
                for weight, ww in c["weight"]:
                    w = gw * aw * mw * ww
                    if w > cutoff:
                        targets.append(("macrodetails/universal-%s-%s-%s-%s" % (gender, age, muscle, weight), w))
                    for height, hw in c["height"]:
                        wh = w * hw
                        if wh > cutoff:
                            targets.append(("macrodetails/height/%s-%s-%s-%s-%s" % (gender, age, muscle, weight,
                                                                                   height), wh))
    for gender, gw in c["gender"]:
        if gender != "female":
            continue
        for age, aw in c["age"]:
            for muscle, mw in c["muscle"]:
                for weight, ww in c["weight"]:
                    for cup, cw in c["cupsize"]:
                        for firm, fw in c["firmness"]:
                            w = aw * mw * ww * cw * fw  # no male counterpart, so no gender weight
                            name = "breast/%s-%s-%s-%s-%s-%s" % (gender, age, muscle, weight, cup, firm)
                            if w > cutoff and "averagecup-averagefirmness" not in name and "-baby-" not in name:
                                targets.append((name, w))
    for gender, gw in c["gender"]:
        for age, aw in c["age"]:
            for muscle, mw in c["muscle"]:
                for weight, ww in c["weight"]:
                    for prop, pw in c["proportions"]:
                        w = gw * aw * mw * ww * pw
                        name = "macrodetails/proportions/%s-%s-%s-%s-%s" % (gender, age, muscle, weight, prop)
                        if w > cutoff and "-baby-" not in name:
                            targets.append((name, w))
    return targets


# ----------------------------------------------------------------------------------------------------------------
# Modifiers (the Model panel sliders, keyed "section/name" as in data/targets/target.json)

@functools.lru_cache(maxsize=1)
def modifier_sections():
    with open(os.path.join(data_root(), "targets", "target.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def list_modifiers():
    out = []
    for section, data in modifier_sections().items():
        for cat in data["categories"]:
            out.append({"modifier": section + "/" + cat["name"], "bipolar": "opposites" in cat,
                        "sided": cat["has_left_and_right"]})
    return out


def modifier_targets(values):
    """[(target name, weight)] for {"section/name[@left|@right]": value}; bipolar modifiers take -1..1."""
    sections = modifier_sections()
    out = []
    for key, value in (values or {}).items():
        side_req = None
        if "@" in key:
            key, side_req = key.split("@", 1)
        section, _, name = key.partition("/")
        if section not in sections:
            raise ValueError("Unknown modifier section '%s'" % section)
        cat = next((c for c in sections[section]["categories"] if c["name"] == name), None)
        if cat is None:
            raise ValueError("Unknown modifier '%s'" % key)
        value = float(value)
        sides = ["unsided"]
        if cat["has_left_and_right"]:
            sides = [side_req] if side_req in ("left", "right") else ["left", "right"]
        for side in sides:
            if "opposites" in cat:
                if value > 0:
                    out.append((section + "/" + cat["opposites"]["positive-" + side], value))
                elif value < 0:
                    out.append((section + "/" + cat["opposites"]["negative-" + side], -value))
            elif value > 0:
                prefix = {"left": "l-", "right": "r-"}.get(side, "")
                out.append((section + "/" + prefix + cat["name"], min(value, 1.0)))
    return out


# ----------------------------------------------------------------------------------------------------------------
# The human

def build_human(macros=None, race=None, modifiers=None, scale=0.1, ground=True, extra_targets=None):
    """Point positions (N, 3) of the base mesh with macros, modifiers and extra (path, weight) targets applied,
    in metres; with ground the lowest body vertex is put at y = 0."""
    mesh = basemesh()
    pos = mesh["positions"].copy()
    stack = [(target_path(n), w) for n, w in macro_targets(macros, race)]
    stack += [(target_path(n), w) for n, w in modifier_targets(modifiers)]
    stack += list(extra_targets or [])
    for path, weight in stack:
        idx, off = load_target(path)
        np.add.at(pos, idx, off * weight)
    pos *= scale
    if ground:
        pos[:, 1] -= pos[mesh["groups"]["body"], 1].min()
    return pos


def joint_centres(positions):
    """{"l-elbow": (x, y, z), ...} from the joint-* helper groups."""
    groups = basemesh()["groups"]
    return {name[6:]: positions[idx].mean(axis=0) for name, idx in groups.items() if name.startswith("joint-")}


# ----------------------------------------------------------------------------------------------------------------
# MHCLO assets

@functools.lru_cache(maxsize=256)
def read_mhclo(path):
    """Parse an .mhclo/.proxy file: binding per asset vertex, reference scales, delete list, material, obj."""
    folder = os.path.dirname(os.path.realpath(path))
    info = {"path": path, "name": os.path.splitext(os.path.basename(path))[0], "obj_file": None, "material": None,
            "x_scale": None, "y_scale": None, "z_scale": None, "delete_verts": [], "z_depth": 50, "uuid": None, "weights_file": None}
    verts, weights, offsets = [], [], []
    status = ""
    with open(path, "r", encoding="utf8", errors="surrogateescape") as f:
        for line in f:
            words = line.split()
            if not words:
                status = ""
                continue
            if words[0] == "#":
                continue
            # As in MPFB: these may appear inside the vertex block without ending it.
            if words[0] == "material":
                info["material"] = os.path.join(folder, words[1])
                continue
            if words[0].startswith("vertexboneweights"):
                info["weights_file"] = os.path.join(folder, words[1])
                continue
            if status == "v":
                if words[0].isnumeric():
                    if len(words) == 1:
                        v = int(words[0])
                        verts.append((v, v, v))
                        weights.append((1.0, 0.0, 0.0))
                        offsets.append((0.0, 0.0, 0.0))
                    else:
                        verts.append((int(words[0]), int(words[1]), int(words[2])))
                        weights.append((float(words[3]), float(words[4]), float(words[5])))
                        offsets.append((float(words[6]), float(words[7]), float(words[8])))
                    continue
                status = ""
            elif status == "d":
                if words[0].isnumeric():
                    seq, prev = False, None
                    for w in words:
                        if w == "-":
                            seq = True
                            continue
                        v = int(w)
                        if seq and prev is not None:
                            info["delete_verts"].extend(range(prev + 1, v + 1))
                            seq = False
                        else:
                            info["delete_verts"].append(v)
                        prev = v
                    continue
                status = ""
            key = words[0]
            if key == "verts":
                status = "v"
            elif key == "delete_verts":
                status = "d"
            elif key == "obj_file":
                info["obj_file"] = os.path.join(folder, words[1])
            elif key in ("x_scale", "y_scale", "z_scale"):
                info[key] = (int(words[1]), int(words[2]), float(words[3]))
            elif key == "z_depth":
                info["z_depth"] = int(words[1])
            elif key == "uuid":
                info["uuid"] = words[1]
            elif key == "name":
                info["name"] = words[1]
    info["verts"] = np.asarray(verts, dtype=np.int64).reshape(-1, 3)
    info["weights"] = np.asarray(weights, dtype=np.float64).reshape(-1, 3)
    info["offsets"] = np.asarray(offsets, dtype=np.float64).reshape(-1, 3)
    return info


def fit_mhclo(info, human_positions, scale=0.1):
    """Asset vertex positions for the human: barycentric mix of three body vertices plus an offset scaled by how
    much the body grew along each reference pair (ClothesService.fit_clothes_to_human)."""
    h = human_positions
    sizes = np.full(3, scale)
    if info["x_scale"]:
        for axis, key in enumerate(("x_scale", "y_scale", "z_scale")):
            a, b, ref = info[key]
            sizes[axis] = abs(h[a, axis] - h[b, axis]) / ref
    v, w = info["verts"], info["weights"]
    return (h[v[:, 0]] * w[:, :1] + h[v[:, 1]] * w[:, 1:2] + h[v[:, 2]] * w[:, 2:3]
            + info["offsets"] * sizes[None, :])


def read_mhmat(path):
    """Key/value settings of an .mhmat material; texture paths made absolute."""
    folder = os.path.dirname(os.path.realpath(path))
    out = {}
    with open(path, "r", encoding="utf8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            key, _, value = line.partition(" ")
            value = value.strip()
            if key.lower().endswith("texture") and value:
                candidate = os.path.join(folder, value)
                value = candidate if os.path.exists(candidate) else value
            out[key] = value
    return out
