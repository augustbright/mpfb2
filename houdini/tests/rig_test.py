"""Rig a character with mpfb::rig, pose it with KineFX and export glTF.   hython houdini/tests/rig_test.py OUT.glb"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "python"))
import hou
import numpy as np

hou.hda.installFile(os.path.join(HERE, "..", "otls", "mpfb.hda"))
out_glb = sys.argv[-1] if sys.argv[-1].endswith(".glb") else os.path.join("/tmp", "mpfb_rig_test.glb")
geo = hou.node("/obj").createNode("geo", "rigtest")
human = geo.createNode("mpfb::human::1.0", "human")
human.parm("gender").set(1.0); human.parm("muscle").set(0.8)
assets = geo.createNode("merge", "assets")
for i, (kind, name) in enumerate((("clothes", "male_casualsuit02"), ("clothes", "shoes03"), ("hair", "short02"))):
    fit = geo.createNode("mpfb::fit_asset::1.0", name)
    fit.setInput(0, human); fit.parm("kind").set(kind); fit.parm("asset").set(name)
    assets.setInput(i, fit)
character = geo.createNode("mpfb::assemble::1.0", "character")
character.setInput(0, human); character.setInput(1, assets)
rig = geo.createNode("mpfb::rig::1.0", "rig")
rig.setInput(0, character)
t = time.time()
skin = rig.geometry(0)
print("rig errors", rig.errors())
skel = rig.geometry(1) if hasattr(rig, "geometry") else None
print("capture cooked in %.2fs: points %d, attribs %s" % (time.time() - t, len(skin.points()), [a.name() for a in skin.pointAttribs()]))
skeleton_out = geo.createNode("null", "skeleton"); skeleton_out.setInput(0, rig, 1)
sk = skeleton_out.geometry()
print("skeleton joints %d, edges %d, names %s" % (len(sk.points()), len(sk.prims()), [p.attribValue("name") for p in sk.points()][:8]))

pose = geo.createNode("kinefx::rigpose", "pose")
pose.setInput(0, skeleton_out)
pose.parm("transformations").set(1)
pose.parm("group0").set("@name=upperarm_l")
pose.parmTuple("r0").set((0, 0, -60))
deform = geo.createNode("bonedeform", "deform")
deform.setInput(0, rig, 0); deform.setInput(1, skeleton_out); deform.setInput(2, pose)
d = deform.geometry()
print("deform errors", deform.errors())
rest = np.frombuffer(skin.pointFloatAttribValuesAsString("P"), dtype=np.float32).reshape(-1, 3)
posed = np.frombuffer(d.pointFloatAttribValuesAsString("P"), dtype=np.float32).reshape(-1, 3)
moved = np.linalg.norm(posed - rest, axis=1)
print("points moved > 1cm: %d, max %.3f m, left side mean x moved %.3f" % ((moved > 0.01).sum(), moved.max(),
      moved[rest[:, 0] > 0.3].mean() if (rest[:, 0] > 0.3).any() else 0))

rop = hou.node("/out").createNode("kinefx::gltfcharacter", "gltf")
rop.parm("skingeosop").set(rig.path())  # output 0: the captured character in rest pose
rop.parm("captureposesop").set(skeleton_out.path())
rop.parm("animatedposesop").set(pose.path())
rop.parm("filepath").set(out_glb)
rop.parm("cliprangemode").set("off")  # current frame: the pose is static
rop.parm("customattribs").set(False)  # mpfb_* bookkeeping attributes are not for game engines
t = time.time()
rop.render()
print("exported %s (%d bytes) in %.1fs, errors %s" % (out_glb, os.path.getsize(out_glb) if os.path.exists(out_glb) else -1,
      time.time() - t, rop.errors()))
sys.stdout.flush()
os._exit(0)
