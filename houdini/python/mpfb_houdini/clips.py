"""Animation clips for games: a clip table, sprite references laid out on the timeline, and export of every clip
into one glTF file.

A clip table is a JSON list, one entry per animation:

    [{"name": "stance", "reference": "stand", "loop": true},
     {"name": "punch", "reference": "attack_0"},
     {"name": "run", "frames": 30, "loop": true}]

``reference`` names a sprite sequence folder (``<sprites>/<reference>/0000.png ...`` with ``timing.json``: ticks per
picture at 60 ticks a second, 0 for held). ``start``/``end`` place a clip explicitly; otherwise clips follow each
other with a gap, as long as their reference (or ``frames``). Looping clips are exported with a ``_loop`` suffix,
which Godot's importer turns into looping animations.
"""

import json
import math
import os
import struct
import tempfile

TICKS_PER_SECOND = 60
HELD_TICKS = 12  # how long a held (0 tick) picture shows in a reference


# ----------------------------------------------------------------------------------------------------------------
# Clip table and timeline layout (no Houdini needed)

def load_clips(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def sequence_timing(sequence_dir):
    """Ticks per picture and the sequence's end mode ("hold", "loop", "continue")."""
    with open(os.path.join(sequence_dir, "timing.json"), "r", encoding="utf-8") as f:
        timing = json.load(f)
    ticks = [int(t) or HELD_TICKS for t in timing["ticks"]]
    return ticks, timing.get("end", "hold"), timing.get("ticks_per_second", TICKS_PER_SECOND)


def reference_frames(sequence_dir, fps):
    ticks, _, tps = sequence_timing(sequence_dir)
    return max(1, int(math.ceil(sum(ticks) * fps / tps)))


def layout(clips, sprites_dir=None, fps=60.0, first=1, gap=10):
    """Copies of the clips with start and end frames filled in."""
    out, frame = [], first
    for clip in clips:
        clip = dict(clip)
        if "start" not in clip:
            clip["start"] = frame
        if "end" not in clip:
            if "frames" in clip:
                length = int(clip["frames"])
            elif clip.get("reference") and sprites_dir:
                length = reference_frames(os.path.join(sprites_dir, clip["reference"]), fps)
            else:
                length = int(fps)
            clip["end"] = clip["start"] + length - 1
        frame = clip["end"] + 1 + gap
        out.append(clip)
    return out


def export_name(clip):
    name = clip["name"]
    if clip.get("loop") and not name.endswith("_loop"):
        name += "_loop"
    return name


def build_reference_track(clips, sprites_dir, out_dir, fps=60.0):
    """Fill out_dir with one picture per timeline frame (<frame:04d>.png, symbolic links to the sprite pictures),
    timed by each clip's reference so the sprite plays back at the game's speed under the animation."""
    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(out_dir):
        if name.endswith(".png"):
            os.remove(os.path.join(out_dir, name))
    written = 0
    for clip in clips:
        if not clip.get("reference"):
            continue
        seq = os.path.join(sprites_dir, clip["reference"])
        ticks, end_mode, tps = sequence_timing(seq)
        pictures = sorted(f for f in os.listdir(seq) if f.endswith(".png"))
        total = sum(ticks)
        bounds = [sum(ticks[:i + 1]) for i in range(len(ticks))]
        for frame in range(int(clip["start"]), int(clip["end"]) + 1):
            tick = (frame - clip["start"]) * tps / fps
            if clip.get("loop") or end_mode == "loop":
                tick %= total
            index = next((i for i, b in enumerate(bounds) if tick < b), len(ticks) - 1)
            source = os.path.join(seq, pictures[min(index, len(pictures) - 1)])
            target = os.path.join(out_dir, "%04d.png" % frame)
            os.symlink(os.path.realpath(source), target)
            written += 1
    return written


# ----------------------------------------------------------------------------------------------------------------
# glb merging (no Houdini needed)

_JSON, _BIN = 0x4E4F534A, 0x004E4942
_SIZES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_glb(path):
    with open(path, "rb") as f:
        data = f.read()
    magic, _, length = struct.unpack("<4sII", data[:12])
    if magic != b"glTF":
        raise ValueError("%s is not a .glb file" % path)
    offset, doc, binary = 12, None, b""
    while offset < length:
        size, kind = struct.unpack("<II", data[offset:offset + 8])
        chunk = data[offset + 8:offset + 8 + size]
        if kind == _JSON:
            doc = json.loads(chunk.decode("utf-8"))
        elif kind == _BIN:
            binary = chunk
        offset += 8 + size
    return doc, bytearray(binary)


def write_glb(path, doc, binary):
    text = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    text += b" " * ((4 - len(text) % 4) % 4)
    binary = bytes(binary) + b"\0" * ((4 - len(binary) % 4) % 4)
    total = 12 + 8 + len(text) + (8 + len(binary) if binary else 0)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(text), _JSON) + text)
        if binary:
            f.write(struct.pack("<II", len(binary), _BIN) + binary)


def _accessor_bytes(doc, binary, index):
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    element = _SIZES[acc["componentType"]] * _COMPONENTS[acc["type"]]
    stride = view.get("byteStride", element)
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    return b"".join(bytes(binary[start + i * stride:start + i * stride + element]) for i in range(acc["count"]))


def _append_accessor(doc, binary, acc, data):
    while len(binary) % 4:
        binary.append(0)
    doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
    binary.extend(data)
    acc = dict(acc, bufferView=len(doc["bufferViews"]) - 1)
    acc.pop("byteOffset", None)
    doc["accessors"].append(acc)
    return len(doc["accessors"]) - 1


def _rebased_times(doc, binary, index):
    acc = doc["accessors"][index]
    times = struct.unpack("<%df" % acc["count"], _accessor_bytes(doc, binary, index))
    t0 = min(times) if times else 0.0
    times = [t - t0 for t in times]
    return dict(acc, min=[min(times)], max=[max(times)]), struct.pack("<%df" % len(times), *times)


def merge_clips(clip_files, out_path):
    """One glb from per-clip exports: the first file's scene, mesh and skeleton, plus the animation of every file,
    each starting at time 0. Channels are matched to nodes by name."""
    doc, binary = read_glb(clip_files[0][1])
    by_name = {node.get("name"): i for i, node in enumerate(doc.get("nodes", []))}
    animations = []
    for name, path in clip_files:
        src, src_bin = (doc, binary) if path == clip_files[0][1] else read_glb(path)
        for anim in src.get("animations", []):
            samplers = []
            for sampler in anim["samplers"]:
                acc, data = _rebased_times(src, src_bin, sampler["input"])
                inp = _append_accessor(doc, binary, acc, data)
                out = _append_accessor(doc, binary, src["accessors"][sampler["output"]],
                                       _accessor_bytes(src, src_bin, sampler["output"]))
                samplers.append(dict(sampler, input=inp, output=out))
            channels = []
            for channel in anim["channels"]:
                node = src["nodes"][channel["target"]["node"]].get("name")
                if node in by_name:
                    channels.append({"sampler": channel["sampler"],
                                     "target": dict(channel["target"], node=by_name[node])})
            animations.append({"name": name, "samplers": samplers, "channels": channels})
    doc["animations"] = animations
    doc["buffers"] = [{"byteLength": len(binary)}]
    write_glb(out_path, doc, binary)
    return out_path


# ----------------------------------------------------------------------------------------------------------------
# Houdini side

def setup_reference(track_dir, sprite_canvas, sprite_anchor, scale, name="sprite_reference", depth=1.0,
                    facing="right"):
    """A card showing the reference track (one picture per frame) behind the character, seen from his right side
    so the character's forward (+Z) points right like the sprites, plus an orthographic camera looking at it.
    Pixel (u, v) of the canvas lands at z = (u - anchor_u) * scale, y = (anchor_v - v) * scale."""
    import hou
    w, h = sprite_canvas
    au, av = sprite_anchor
    sign = 1.0 if facing == "right" else -1.0
    obj = hou.node("/obj")
    for old in (name, name + "_cam"):
        if obj.node(old):
            obj.node(old).destroy()
    geo = obj.createNode("geo", name)
    card = geo.createNode("python", "card")
    corners = [((0 - au) * scale * sign, (av - h) * scale, 0.0, 0.0), ((w - au) * scale * sign, (av - h) * scale, 1.0, 0.0),
               ((w - au) * scale * sign, av * scale, 1.0, 1.0), ((0 - au) * scale * sign, av * scale, 0.0, 1.0)]
    card.parm("python").set(
        "geo = hou.pwd().geometry()\n"
        "geo.addAttrib(hou.attribType.Vertex, 'uv', (0.0, 0.0, 0.0))\n"
        "geo.addAttrib(hou.attribType.Prim, 'shop_materialpath', '')\n"
        "corners = %r\n"
        "points = geo.createPoints([hou.Vector3(%r, y, z) for z, y, u, v in corners])\n"
        "poly = geo.createPolygon()\n"
        "for point, (z, y, u, v) in reversed(list(zip(points, corners))):\n"
        "    poly.addVertex(point).setAttribValue('uv', (u, v, 0.0))\n"
        "poly.setAttribValue('shop_materialpath', %r)\n" % (corners, depth, "/mat/" + name))
    card.setDisplayFlag(True)
    card.setRenderFlag(True)
    mat = hou.node("/mat/" + name) or hou.node("/mat").createNode("principledshader::2.0", name)
    mat.parm("basecolor_useTexture").set(True)
    mat.parm("basecolor_texture").set(os.path.join(track_dir, "$F4.png"))
    mat.parm("basecolor_useTextureAlpha").set(True)
    mat.parmTuple("basecolor").set((1.0, 1.0, 1.0))
    if mat.parm("emitcolor_useTexture"):
        mat.parm("emitcolor_useTexture").set(True)
        mat.parm("emitcolor_texture").set(os.path.join(track_dir, "$F4.png"))
        mat.parm("emitint").set(1.0)
    cam = obj.createNode("cam", name + "_cam")
    cam.parm("projection").set("ortho")
    cam.parm("orthowidth").set(max(w, h) * scale * 1.05)
    cam.parm("resx").set(w * 4)
    cam.parm("resy").set(h * 4)
    centre_z = (w / 2.0 - au) * scale * sign
    centre_y = (av - h / 2.0) * scale
    cam.parmTuple("t").set((-10.0, centre_y, centre_z))
    cam.parmTuple("r").set((0.0, -90.0, 0.0))
    return geo, cam


def set_timeline(clips, fps):
    import hou
    hou.setFps(fps)
    start = min(c["start"] for c in clips)
    end = max(c["end"] for c in clips)
    hou.playbar.setFrameRange(start, end)
    hou.playbar.setPlaybackRange(start, end)


def export_clips(rop_path, clips, out_path):
    """Render the glTF Character ROP once per clip over the clip's frames, then merge the results into out_path."""
    import hou
    rop = hou.node(rop_path)
    saved = hou.playbar.frameRange(), hou.playbar.playbackRange(), hou.frame(), rop.parm("filepath").unexpandedString()
    tmp = tempfile.mkdtemp(prefix="mpfb_clips_")
    files = []
    try:
        rop.parm("animclipexport").set("singleclip")
        rop.parm("cliprangemode").set("normal")
        for clip in clips:
            start, end = int(clip["start"]), int(clip["end"])
            hou.playbar.setFrameRange(start, end)
            hou.playbar.setPlaybackRange(start, end)
            hou.setFrame(start)
            path = os.path.join(tmp, "%s.glb" % clip["name"])
            rop.parm("filepath").set(path)
            rop.parm("clipname").set(export_name(clip))
            rop.render(frame_range=(start, end, 1))
            files.append((export_name(clip), path))
    finally:
        (frame_range, playback, frame, filepath) = saved
        hou.playbar.setFrameRange(*frame_range)
        hou.playbar.setPlaybackRange(*playback)
        hou.setFrame(frame)
        rop.parm("filepath").set(filepath)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    return merge_clips(files, out_path)
