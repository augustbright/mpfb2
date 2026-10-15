"""Quick preview renders of a SOP (e.g. the merged character) with the OpenGL ROP, framed on its bounding box."""

import math
import os

import hou

VIEWS = {"front": (0, 0), "left": (90, 0), "back": (180, 0), "right": (-90, 0), "three_quarter": (35, -8)}


def render_views(sop_path, out_dir, views=("front", "left", "three_quarter"), size=800, framing="body"):
    """Render the SOP from each view (character faces +Z, Y up); framing "head" crops to the top 17 %.
    Returns the image paths. Temporary camera and ROP are removed afterwards."""
    sop = hou.node(sop_path)
    bbox = sop.geometry().boundingBox()
    centre, size_v = bbox.center(), bbox.sizevec()
    height = size_v[1]
    if framing == "head":
        centre = hou.Vector3(centre[0], bbox.maxvec()[1] - height * 0.085, centre[2])
        extent = height * 0.17
    else:
        extent = max(size_v[0], size_v[1], size_v[2]) * 1.08
    os.makedirs(out_dir, exist_ok=True)
    cam = hou.node("/obj").createNode("cam", "mpfb_preview_cam")
    rop = hou.node("/out").createNode("opengl", "mpfb_preview_rop")
    paths = []
    try:
        # A long lens from far away: close to orthographic, and the OpenGL ROP handles it reliably.
        cam.parm("resx").set(size)
        cam.parm("resy").set(size)
        cam.parm("focal").set(200.0)
        fov = 2 * math.atan(cam.parm("aperture").eval() / 2 / 200.0)
        distance = extent / 2 / math.tan(fov / 2)
        rop.parm("camera").set(cam.path())
        if rop.parm("tres"):
            rop.parm("tres").set(True)
            rop.parm("res1").set(size)
            rop.parm("res2").set(size)
        for name in views:
            yaw, pitch = VIEWS[name]
            ry, rx = math.radians(yaw), math.radians(pitch)
            direction = hou.Vector3(math.sin(ry) * math.cos(rx), -math.sin(rx), math.cos(ry) * math.cos(rx))
            cam.parmTuple("t").set(tuple(centre + direction * distance))
            cam.parmTuple("r").set((pitch, yaw, 0))
            path = os.path.join(out_dir, "%s_%s_%s.png" % (sop.name(), framing, name))
            if hou.isUIAvailable():
                _flipbook(cam, path, size)
            else:
                rop.parm("picture").set(path)
                rop.render()
            paths.append(path)
    finally:
        rop.destroy()
        cam.destroy()
    return paths


def _flipbook(cam, path, size):
    """Capture the scene viewer through cam (the OpenGL ROP of Houdini 22 renders the USD stage's camera)."""
    viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    if viewer is None:
        raise RuntimeError("No scene viewer to capture")
    viewport = viewer.curViewport()
    previous = viewport.camera()
    viewport.setCamera(cam)
    try:
        settings = viewer.flipbookSettings().stash()
        frame = hou.frame()
        settings.frameRange((frame, frame))
        settings.output(path.replace(".png", ".$F4.png"))
        settings.useResolution(True)
        settings.resolution((size, size))
        settings.outputToMPlay(False)
        viewer.flipbook(viewport, settings)
        produced = path.replace(".png", ".%04d.png" % int(frame))
        if os.path.exists(produced):
            os.replace(produced, path)
    finally:
        if previous is not None:
            viewport.setCamera(previous)
        else:
            viewport.homeAll()
