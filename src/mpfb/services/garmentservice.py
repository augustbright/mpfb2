"""Mesh editing for authoring clothes and accessories around a human, driven by declarative (JSON) specs.

The intended flow is to start from something that already fits (an equipped asset, a basemesh helper group or a
generated accessory), edit it with these functions while the human is in rest pose, and then turn it into an MPFB
asset with ClothesService.write_mhclo_asset (AgentService.write_clothes).

Positions in specs are world coordinates, or joint references: "joint:l-elbow" is the centre of the basemesh helper
group "joint-l-elbow". A list of three numbers is used as is. The character faces -Y and his left side is +X.

Region shapes (used to select vertices or boundary loops), all optionally with "invert": true:

    {"type": "box", "min": [x, y, z], "max": [x, y, z]}
    {"type": "sphere", "center": "joint:l-shoulder", "radius": 0.1}
    {"type": "half_space", "point": "joint:pelvis", "normal": [0, 0, -1]}       # the side the normal points to
    {"type": "limb", "from": "shoulder", "to": "elbow", "beyond": 0.07}          # both sides, past elbow + 7 cm,
                                                                                 # within max_radius (0.15) of the axis
    {"type": "opening", "y_max": -0.02, "z_top": 1.5, "z_bottom": 1.0,           # open front, |x| below a width
     "half_width_top": 0.06, "half_width_bottom": 0.12}                          # interpolated over z
    {"type": "all"}
"""

import math, os, typing  # pylint: disable=C0410
from typing import Any

import bmesh
import bpy
import numpy
from mathutils import Vector

from .logservice import LogService

_LOG = LogService.get_logger("services.garmentservice")

Spec = dict[str, Any]


class GarmentService():
    """See the module docstring."""

    def __init__(self) -> None:
        raise RuntimeError("You should not instance GarmentService. Use its static methods instead.")

    # ----------------------------------------------------------------------------------------------------------------
    # Measuring the human

    @staticmethod
    def basemesh_positions(basemesh: bpy.types.Object) -> numpy.ndarray:
        """World positions of all basemesh vertices with shape keys applied, helpers included."""
        masks = [m for m in basemesh.modifiers if m.type == "MASK" and m.show_viewport]
        armature = [m for m in basemesh.modifiers if m.type == "ARMATURE" and m.show_viewport]
        for m in masks + armature:
            m.show_viewport = False
        try:
            dg = bpy.context.evaluated_depsgraph_get()
            ev = basemesh.evaluated_get(dg)
            mesh = ev.to_mesh()
            co = numpy.empty(len(mesh.vertices) * 3, dtype=numpy.float64)
            mesh.vertices.foreach_get("co", co)
            ev.to_mesh_clear()
        finally:
            for m in masks + armature:
                m.show_viewport = True
        mw = numpy.array(basemesh.matrix_world)
        return co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]

    @staticmethod
    def joints(basemesh: bpy.types.Object) -> dict[str, Vector]:
        """Rest pose joint centres from the joint-* helper groups, keyed without the prefix (e.g. "l-elbow")."""
        pos = GarmentService.basemesh_positions(basemesh)
        index = {g.index: g.name for g in basemesh.vertex_groups if g.name.startswith("joint-")}
        members: dict[str, list[int]] = {name: [] for name in index.values()}
        for v in basemesh.data.vertices:
            for g in v.groups:
                if g.group in index:
                    members[index[g.group]].append(v.index)
        return {name[len("joint-"):]: Vector(pos[idx].mean(axis=0)) for name, idx in members.items() if idx}

    @staticmethod
    def resolve_point(value: Any, joints: dict[str, Vector]) -> Vector:
        if isinstance(value, str) and value.startswith("joint:"):
            name = value[len("joint:"):]
            if name not in joints:
                raise ValueError("Unknown joint '%s'. Known: %s" % (name, sorted(joints)))
            return joints[name].copy()
        return Vector([float(c) for c in value])

    # ----------------------------------------------------------------------------------------------------------------
    # Regions

    @staticmethod
    def region_test(spec: Spec, joints: dict[str, Vector]) -> typing.Callable[[Vector], bool]:
        """Turn a region spec (see the module docstring) into a predicate on world positions."""
        kind = spec.get("type")
        test: typing.Callable[[Vector], bool]
        if kind == "all":
            test = lambda p: True
        elif kind == "box":
            lo = Vector(spec.get("min", (-1e9,) * 3))
            hi = Vector(spec.get("max", (1e9,) * 3))
            test = lambda p: all(lo[i] <= p[i] <= hi[i] for i in range(3))
        elif kind == "sphere":
            c = GarmentService.resolve_point(spec["center"], joints) + Vector(spec.get("offset", (0, 0, 0)))
            r = float(spec["radius"])
            test = lambda p: (p - c).length <= r
        elif kind == "half_space":
            pt = GarmentService.resolve_point(spec["point"], joints) + Vector(spec.get("offset", (0, 0, 0)))
            n = Vector(spec["normal"]).normalized()
            test = lambda p: (p - pt).dot(n) > 0
        elif kind == "limb":
            start, end = spec["from"], spec["to"]
            beyond = float(spec.get("beyond", 0.0))
            sides = spec.get("sides", ["l", "r"])
            min_x = float(spec.get("min_abs_x", 0.08))
            max_radius = float(spec.get("max_radius", 0.15))

            def test(p: Vector) -> bool:
                if abs(p.x) < min_x:
                    return False
                side = "l" if p.x > 0 else "r"
                if side not in sides:
                    return False
                a, b = joints[side + "-" + start], joints[side + "-" + end]
                d = (b - a).normalized()
                along = (p - a).dot(d)
                # Only near the limb: the torso below a shoulder also lies "past" it along the arm direction.
                return along > (b - a).length + beyond and (p - a - d * along).length < max_radius
        elif kind == "opening":
            y_max = float(spec.get("y_max", 0.0))
            z_top, z_bottom = float(spec["z_top"]), float(spec["z_bottom"])
            w_top, w_bottom = float(spec["half_width_top"]), float(spec["half_width_bottom"])

            def test(p: Vector) -> bool:
                t = min(max((z_top - p.z) / max(z_top - z_bottom, 1e-6), 0.0), 1.0)
                return p.y < y_max and abs(p.x) < w_top + (w_bottom - w_top) * t
        else:
            raise ValueError("Unknown region type '%s'" % kind)
        if spec.get("invert"):
            return lambda p: not test(p)
        return test

    @staticmethod
    def any_region(specs: list[Spec], joints: dict[str, Vector]) -> typing.Callable[[Vector], bool]:
        tests = [GarmentService.region_test(s, joints) for s in specs]
        return lambda p: any(t(p) for t in tests)

    # ----------------------------------------------------------------------------------------------------------------
    # Editing

    @staticmethod
    def _edit(obj: bpy.types.Object, func: typing.Callable[[bmesh.types.BMesh, Any], Any]) -> Any:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        result = func(bm, obj.matrix_world)
        bm.normal_update()
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return result

    @staticmethod
    def duplicate(source: bpy.types.Object, name: str) -> bpy.types.Object:
        """Free-standing copy of a fitted mesh (no parent, modifiers or vertex groups, own material copies)."""
        dg = bpy.context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(source.evaluated_get(dg))
        mesh.name = name
        obj = bpy.data.objects.new(name, mesh)
        for coll in source.users_collection:
            coll.objects.link(obj)
        obj.matrix_world = source.matrix_world.copy()
        for i, slot in enumerate(obj.material_slots):
            if slot.material:
                obj.material_slots[i].material = slot.material.copy()
        obj.vertex_groups.clear()
        from .objectservice import ObjectService  # pylint: disable=C0415
        ObjectService.ensure_render_uv_layer(obj)
        return obj

    @staticmethod
    def delete_vertices(obj: bpy.types.Object, test: typing.Callable[[Vector], bool]) -> int:
        def f(bm, mw):
            doomed = [v for v in bm.verts if test(mw @ v.co)]
            bmesh.ops.delete(bm, geom=doomed, context="VERTS")
            return len(doomed)
        return GarmentService._edit(obj, f)

    @staticmethod
    def smooth_boundaries(obj: bpy.types.Object, iterations: int = 2, factor: float = 0.5) -> int:
        """Relax open edges (after cutting a coarse mesh the border zigzags): each boundary vertex moves toward
        the mean of its boundary neighbours."""
        def f(bm, mw):
            moved = 0
            for _ in range(max(0, int(iterations))):
                targets = {}
                for v in bm.verts:
                    if not v.is_boundary:
                        continue
                    nbrs = [e.other_vert(v) for e in v.link_edges if e.is_boundary]
                    if len(nbrs) == 2:
                        targets[v] = (nbrs[0].co + nbrs[1].co) / 2
                for v, t in targets.items():
                    v.co = v.co.lerp(t, factor)
                moved = len(targets)
            return moved
        return GarmentService._edit(obj, f)

    @staticmethod
    def keep_islands(obj: bpy.types.Object, count: int = 1,
                     test: typing.Callable[[Vector], bool] | None = None) -> int:
        """Keep the `count` largest connected parts whose centre passes `test`; returns the vertices left."""
        def f(bm, mw):
            bm.verts.ensure_lookup_table()
            seen: set[int] = set()
            islands = []
            for v in bm.verts:
                if v.index in seen:
                    continue
                stack, isl = [v], []
                seen.add(v.index)
                while stack:
                    x = stack.pop()
                    isl.append(x)
                    for e in x.link_edges:
                        w = e.other_vert(x)
                        if w.index not in seen:
                            seen.add(w.index)
                            stack.append(w)
                islands.append(isl)
            if test is not None:
                islands = [i for i in islands if test(mw @ (sum((v.co for v in i), Vector()) / len(i)))]
            islands.sort(key=len, reverse=True)
            keep = {v for isl in islands[:count] for v in isl}
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if v not in keep], context="VERTS")
            return len(keep)
        return GarmentService._edit(obj, f)

    @staticmethod
    def _boundary_loops(bm: bmesh.types.BMesh) -> list[list[bmesh.types.BMEdge]]:
        seen: set[bmesh.types.BMEdge] = set()
        loops = []
        for e in bm.edges:
            if not e.is_boundary or e in seen:
                continue
            loop, stack = [], [e]
            seen.add(e)
            while stack:
                x = stack.pop()
                loop.append(x)
                for v in x.verts:
                    for y in v.link_edges:
                        if y.is_boundary and y not in seen:
                            seen.add(y)
                            stack.append(y)
            loops.append(loop)
        return loops

    @staticmethod
    def roll_edges(obj: bpy.types.Object, test: typing.Callable[[Vector], bool], toward: list[Vector],
                   out: float = 0.01, back: float = 0.04) -> int:
        """Roll open garment ends (sleeves, trouser legs, hems): each boundary loop whose centre passes `test` is
        first straightened into a clean ring across the limb, then folded back as a cuff: out by `out` from the
        loop axis and back by `back` toward the nearest point in `toward`."""
        def f(bm, mw):
            inv = mw.inverted()
            rolled = 0
            for loop in GarmentService._boundary_loops(bm):
                verts = list({v for e in loop for v in e.verts})
                world = {v: mw @ v.co for v in verts}
                centre = sum(world.values(), Vector()) / len(verts)
                if not test(centre):
                    continue
                target = min(toward, key=lambda t: (t - centre).length) if toward else centre + Vector((0, 0, 1))
                axis = (target - centre).normalized()
                # Straighten the cut: put every loop vertex at the mean axial position.
                for v in verts:
                    w = world[v]
                    w = w - axis * (w - centre).dot(axis)
                    world[v] = w
                    v.co = inv @ w

                def extrude(edges, offset_fn):
                    ret = bmesh.ops.extrude_edge_only(bm, edges=edges)
                    new_verts = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMVert)]
                    new_edges = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMEdge)
                                 and all(v in new_verts for v in g.verts)]
                    new_faces = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMFace)]
                    for v in new_verts:
                        w = mw @ v.co
                        radial = w - centre
                        radial -= axis * radial.dot(axis)
                        v.co = inv @ (w + offset_fn(radial.normalized()))
                    return new_edges, new_faces

                edges, faces1 = extrude(loop, lambda r: r * out * 0.7)            # lip of the fold
                _, faces2 = extrude(edges, lambda r: r * out * 0.3 + axis * back)   # folded band
                bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
                rolled += 1
            return rolled
        return GarmentService._edit(obj, f)

    @staticmethod
    def inflate(obj: bpy.types.Object, distance: float, test: typing.Callable[[Vector], bool] | None = None) -> int:
        """Move vertices (those passing `test`, or all) along their normals."""
        def f(bm, mw):
            m3 = mw.to_3x3()
            inv3 = m3.inverted()
            moved = 0
            for v in bm.verts:
                if test is None or test(mw @ v.co):
                    v.co += inv3 @ ((m3 @ v.normal).normalized() * distance)
                    moved += 1
            return moved
        return GarmentService._edit(obj, f)

    @staticmethod
    def apply_modifier(obj: bpy.types.Object, kind: str, **props: Any) -> None:
        mod = obj.modifiers.new(kind.lower(), kind)
        for k, v in props.items():
            setattr(mod, k, v)
        dg = bpy.context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
        old = obj.data
        obj.modifiers.remove(mod)
        obj.data = mesh
        mesh.name = obj.name
        if old.users == 0:
            bpy.data.meshes.remove(old)

    @staticmethod
    def prepare_for_makeclothes(obj: bpy.types.Object, group: str = "body") -> None:
        """Apply modifiers and put every vertex in exactly one basemesh group, as MakeClothes requires."""
        if obj.modifiers:
            dg = bpy.context.evaluated_depsgraph_get()
            mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
            old = obj.data
            obj.modifiers.clear()
            obj.data = mesh
            if old.users == 0:
                bpy.data.meshes.remove(old)
        def drop_loose(bm, mw):
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
            bmesh.ops.delete(bm, geom=[e for e in bm.edges if not e.link_faces], context="EDGES")
        GarmentService._edit(obj, drop_loose)
        sizes = {len(p.vertices) for p in obj.data.polygons}
        if len(sizes) > 1:
            # MakeClothes matching needs a single face type.
            GarmentService.apply_modifier(obj, "TRIANGULATE", min_vertices=4)
        counts = [len(v.groups) for v in obj.data.vertices]
        if counts and all(c == 1 for c in counts):
            return
        obj.vertex_groups.clear()
        vg = obj.vertex_groups.new(name=group)
        vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")

    # ----------------------------------------------------------------------------------------------------------------
    # Textures

    @staticmethod
    def diffuse_image_node(material: bpy.types.Material) -> bpy.types.Node | None:
        """The image node feeding the base color: MakeSkin's diffuseTexture if there is one, else the first image
        found upstream of a Principled BSDF's Base Color that is not an AO, normal, bump or roughness map."""
        tree = material.node_tree
        if tree is None:
            return None
        node = tree.nodes.get("diffuseTexture")
        if node is not None and node.type == "TEX_IMAGE" and node.image is not None:
            return node
        skip = ("ao", "normal", "bump", "rough", "spec", "metal", "displace", "transmission", "emission")
        for bsdf in [n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"]:
            pending = [l.from_node for l in tree.links if l.to_socket == bsdf.inputs["Base Color"]]
            seen: set[str] = set()
            while pending:
                n = pending.pop(0)
                if n.name in seen:
                    continue
                seen.add(n.name)
                if n.type == "TEX_IMAGE" and n.image is not None and not any(s in n.name.lower() for s in skip):
                    return n
                pending.extend(l.from_node for l in tree.links if l.to_node == n)
        return None

    @staticmethod
    def paint_strokes(obj: bpy.types.Object, strokes: list[list[float]], color: list[float],
                      view: str = "front", jitter: float = 0.0) -> int:
        """Paint strokes into the diffuse texture of obj, as seen from a view. Each stroke is
        [u1, v1, u2, v2, width] in the view plane in world units: front/back use (x, z), left/right use (y, z).
        Only faces turned toward the view are painted. jitter roughens the edges (brush look). Returns texels."""
        material = obj.active_material
        node = GarmentService.diffuse_image_node(material) if material else None
        if node is None:
            raise ValueError("'%s' has no diffuse texture to paint on; tint it first" % obj.name)
        image = node.image
        axes = {"front": ((0, 2), (0, -1, 0)), "back": ((0, 2), (0, 1, 0)),
                "left": ((1, 2), (1, 0, 0)), "right": ((1, 2), (-1, 0, 0))}
        if view not in axes:
            raise ValueError("view must be one of %s" % list(axes))
        (ia, ib), facing = axes[view]
        facing_v = Vector(facing)
        segs = numpy.array([s[:5] for s in strokes], dtype=numpy.float64)
        col = numpy.array(color[:3], dtype=numpy.float32)

        def inside(points: numpy.ndarray) -> numpy.ndarray:
            q = points[:, [ia, ib]]
            hit = numpy.zeros(len(q), dtype=bool)
            for a1, b1, a2, b2, width in segs:
                a = numpy.array([a1, b1])
                d = numpy.array([a2 - a1, b2 - b1])
                t = numpy.clip(((q - a) @ d) / max(d @ d, 1e-12), 0, 1)
                dist = numpy.linalg.norm(q - (a + t[:, None] * d), axis=1)
                w = width / 2
                if jitter:
                    w = w * (1 + jitter * numpy.sin(q[:, 0] * 311.0 + q[:, 1] * 173.0))
                hit |= dist < w
            return hit

        w, h = image.size
        px = numpy.empty(w * h * 4, dtype=numpy.float32)
        image.pixels.foreach_get(px)
        px = px.reshape(h, w, 4)
        me = obj.data
        me.calc_loop_triangles()
        uv = me.uv_layers.active.data
        mw = obj.matrix_world
        m3 = mw.to_3x3()
        painted = 0
        for tri in me.loop_triangles:
            if (m3 @ tri.normal).normalized().dot(facing_v) < 0.2:
                continue
            P = numpy.array([list(mw @ me.vertices[i].co) for i in tri.vertices])
            U = numpy.array([uv[l].uv[:] for l in tri.loops]) * [w, h]
            x0, y0 = numpy.floor(U.min(axis=0)).astype(int)
            x1, y1 = numpy.ceil(U.max(axis=0)).astype(int)
            xs, ys = numpy.meshgrid(numpy.arange(max(x0, 0), min(x1 + 1, w)), numpy.arange(max(y0, 0), min(y1 + 1, h)))
            if xs.size == 0:
                continue
            m = numpy.array([[U[1, 0] - U[0, 0], U[2, 0] - U[0, 0]], [U[1, 1] - U[0, 1], U[2, 1] - U[0, 1]]])
            if abs(numpy.linalg.det(m)) < 1e-9:
                continue
            pts = numpy.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], axis=1)
            lam = numpy.linalg.solve(m, (pts - U[0]).T).T
            ok = (lam[:, 0] >= -0.02) & (lam[:, 1] >= -0.02) & (lam.sum(axis=1) <= 1.02)
            if not ok.any():
                continue
            world = P[0] + lam[:, :1] * (P[1] - P[0]) + lam[:, 1:] * (P[2] - P[0])
            hit = ok & inside(world)
            if hit.any():
                px[ys.ravel()[hit], xs.ravel()[hit], :3] = col
                painted += int(hit.sum())
        image.pixels.foreach_set(px.ravel())
        if image.filepath_raw:
            image.save()
            image.reload()  # keeps the GPU texture in sync with the file
        return painted

    # ----------------------------------------------------------------------------------------------------------------
    # Generated accessories

    @staticmethod
    def make_belt(basemesh: bpy.types.Object, name: str, z: float, width: float = 0.04, thickness: float = 0.006,
                  clearance: float = 0.012, buckle: bool = True, segments: int = 48) -> bpy.types.Object:
        """A belt hugging the body at height z: the body's cross-section at z is sampled around the waist and
        pushed out by clearance, extruded to width and given thickness. Adds a buckle plate at the front."""
        pos = GarmentService.basemesh_positions(basemesh)
        body = basemesh.vertex_groups["body"].index
        idx = [v.index for v in basemesh.data.vertices if any(g.group == body for g in v.groups)]
        ring = pos[idx]
        ring = ring[numpy.abs(ring[:, 2] - z) < 0.025]
        ring = ring[numpy.abs(ring[:, 0]) < 0.3]  # leave out the hands in A pose
        if len(ring) < 8:
            raise ValueError("No body found at z=%.3f" % z)
        cx, cy = float(ring[:, 0].mean()), float(ring[:, 1].mean())
        ang = numpy.arctan2(ring[:, 1] - cy, ring[:, 0] - cx)
        rad = numpy.hypot(ring[:, 0] - cx, ring[:, 1] - cy)
        bm = bmesh.new()
        rows = []
        for i in range(segments):
            a = -math.pi + 2 * math.pi * i / segments
            near = numpy.abs((ang - a + math.pi) % (2 * math.pi) - math.pi) < (2 * math.pi / segments) * 1.5
            r = float(rad[near].max() if near.any() else rad.max()) + clearance
            d = Vector((math.cos(a), math.sin(a), 0))
            base = Vector((cx, cy, 0)) + d * r
            rows.append([bm.verts.new(base + d * dt + Vector((0, 0, z + dz)))
                         for dt, dz in ((0, -width / 2), (0, width / 2), (thickness, width / 2), (thickness, -width / 2))])
        for i in range(segments):
            a, b = rows[i], rows[(i + 1) % segments]
            for k in range(4):
                bm.faces.new((a[k], b[k], b[(k + 1) % 4], a[(k + 1) % 4]))
        if buckle:
            front = min(range(segments), key=lambda i: abs(-math.pi / 2 - (-math.pi + 2 * math.pi * i / segments)))
            p = rows[front][2].co
            res = bmesh.ops.create_cube(bm, size=1.0)
            for v in res["verts"]:
                v.co = Vector((p.x + v.co.x * 0.075, p.y - 0.004 + v.co.y * 0.010, z + v.co.z * width * 1.25))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        GarmentService._box_uvs(obj)
        return obj

    @staticmethod
    def make_glasses(basemesh: bpy.types.Object, name: str, lens_radius: float = 0.021, rim: float = 0.0022,
                     forward: float = 0.03, shape: str = "round") -> bpy.types.Object:
        """Glasses in front of the eyes (joint-l-eye / joint-r-eye): rims, lenses, a bridge and temples reaching
        back toward the ears. shape: round or square. Two materials: 0 frame, 1 lenses."""
        j = GarmentService.joints(basemesh)
        le, re_ = j["l-eye"], j["r-eye"]
        head = j.get("head", (le + re_) / 2 + Vector((0, 0.08, 0)))
        bm = bmesh.new()
        frame_faces, lens_faces = [], []
        n = 24
        for eye in (le, re_):
            c = eye + Vector((0, -forward, 0))
            ring_outer, ring_inner, ring_back, lens = [], [], [], []
            for i in range(n):
                a = 2 * math.pi * i / n
                d = Vector((math.cos(a), 0, math.sin(a)))
                if shape == "square":
                    d = d / max(abs(d.x), abs(d.z))
                ring_outer.append(bm.verts.new(c + d * (lens_radius + rim)))
                ring_inner.append(bm.verts.new(c + d * lens_radius))
                ring_back.append(bm.verts.new(c + d * (lens_radius + rim * 0.5) + Vector((0, rim * 1.5, 0))))
                lens.append(bm.verts.new(c + d * lens_radius + Vector((0, rim * 0.6, 0))))
            for i in range(n):
                k = (i + 1) % n
                frame_faces.append(bm.faces.new((ring_inner[i], ring_inner[k], ring_outer[k], ring_outer[i])))
                frame_faces.append(bm.faces.new((ring_outer[i], ring_outer[k], ring_back[k], ring_back[i])))
                frame_faces.append(bm.faces.new((ring_back[i], ring_back[k], ring_inner[k], ring_inner[i])))
            lens_faces.append(bm.faces.new(lens))

        def bar(a: Vector, b: Vector, r: float) -> None:
            d = (b - a).normalized()
            u = d.orthogonal().normalized()
            w = d.cross(u)
            ra = [bm.verts.new(a + (u * math.cos(t) + w * math.sin(t)) * r) for t in (0, 2.1, 4.2)]
            rb = [bm.verts.new(b + (u * math.cos(t) + w * math.sin(t)) * r) for t in (0, 2.1, 4.2)]
            for i in range(3):
                k = (i + 1) % 3
                frame_faces.append(bm.faces.new((ra[i], ra[k], rb[k], rb[i])))
            frame_faces.append(bm.faces.new(ra[::-1]))
            frame_faces.append(bm.faces.new(rb))

        cl = le + Vector((-lens_radius - rim * 0.5, -forward, 0.004))
        cr = re_ + Vector((lens_radius + rim * 0.5, -forward, 0.004))
        bar(cl, cr, rim * 0.6)
        for eye, s in ((le, 1), (re_, -1)):
            hinge = eye + Vector((s * (lens_radius + rim), -forward + 0.002, 0.003))
            temple_side = abs(eye.x) + 0.045
            ear = Vector((s * temple_side, head.y + 0.005, eye.z - 0.004))
            mid = Vector((s * (temple_side - 0.004), (hinge.y + ear.y) / 2, eye.z + 0.001))
            bar(hinge, mid, rim * 0.55)
            bar(mid, ear, rim * 0.55)
        bmesh.ops.recalc_face_normals(bm, faces=frame_faces)
        for f in lens_faces:
            f.material_index = 1
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        GarmentService._box_uvs(obj)
        return obj

    @staticmethod
    def _box_uvs(obj: bpy.types.Object) -> None:
        """Simple planar UVs so generated meshes can carry materials through MakeClothes and glTF."""
        mesh = obj.data
        uv = mesh.uv_layers.new(name="UVMap")
        uv.active_render = True
        co = [v.co for v in mesh.vertices]
        lo = Vector((min(c.x for c in co), min(c.y for c in co), min(c.z for c in co)))
        size = max(max(c.x for c in co) - lo.x, max(c.y for c in co) - lo.y, max(c.z for c in co) - lo.z, 1e-6)
        for poly in mesh.polygons:
            for li in poly.loop_indices:
                c = mesh.vertices[mesh.loops[li].vertex_index].co
                uv.data[li].uv = ((c.x - lo.x) / size, (c.z - lo.z + (c.y - lo.y) * 0.5) / size)

    @staticmethod
    def make_tube(name: str, points: list[Vector], radii: list[float], sides: int = 12, closed: bool = False,
                  flatten: float = 1.0, caps: bool = False) -> bpy.types.Object:
        """Sweep a tube through points (radius per point) - collars, hood rolls, straps, wristbands, hair ties.
        flatten < 1 squashes the cross-section along the curve's normal plane (an oval band). caps closes the ends
        with n-gons (off by default: MakeClothes wants one face type, and taper the radii to 0 to close instead)."""
        if len(points) < 2 or len(points) != len(radii):
            raise ValueError("Need at least two points and one radius per point")
        bm = bmesh.new()
        count = len(points)
        tangents = []
        for i in range(count):
            a = points[i - 1] if (closed or i > 0) else points[i]
            b = points[(i + 1) % count] if (closed or i < count - 1) else points[i]
            tangents.append((b - a).normalized())
        normal = tangents[0].orthogonal().normalized()
        rings = []
        for i in range(count):
            t = tangents[i]
            normal = (normal - t * normal.dot(t)).normalized()  # parallel transport
            binormal = t.cross(normal)
            ring = []
            for k in range(sides):
                a = 2 * math.pi * k / sides
                ring.append(bm.verts.new(points[i] + (normal * math.cos(a) * flatten + binormal * math.sin(a)) * radii[i]))
            rings.append(ring)
        for i in range(count if closed else count - 1):
            r0, r1 = rings[i], rings[(i + 1) % count]
            for k in range(sides):
                bm.faces.new((r0[k], r0[(k + 1) % sides], r1[(k + 1) % sides], r1[k]))
        if caps and not closed:
            bm.faces.new(rings[0][::-1])
            bm.faces.new(rings[-1])
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        GarmentService._box_uvs(obj)
        return obj
