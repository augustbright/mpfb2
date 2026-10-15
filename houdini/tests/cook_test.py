"""Cook the HDAs headless and check them against the numpy core.   hython houdini/tests/cook_test.py"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "python"))
import hou
import numpy as np
import mpfb_houdini as mh

hou.hda.installFile(os.path.join(HERE, "..", "otls", "mpfb.hda"))
geo = hou.node("/obj").createNode("geo", "test")
human = geo.createNode("mpfb::human::1.0", "human")
for k, v in {"gender": 1.0, "age": 0.56, "muscle": 1.0, "weight": 0.66, "height": 0.6, "proportions": 0.9,
             "african": 0.0, "asian": 0.0, "caucasian": 1.0}.items():
    human.parm(k).set(v)
human.parm("modifiers").set(2)
human.parm("modifier1").set("head/head-square"); human.parm("value1").set(0.75)
human.parm("modifier2").set("chin/chin-width-decr-incr"); human.parm("value2").set(0.55)
t = time.time(); g = human.geometry(); first = time.time() - t
if g is None:
    print("human ERRORS", human.errors()); sys.stdout.flush(); os._exit(1)
human.parm("muscle").set(0.8)
t = time.time(); g = human.geometry(); second = time.time() - t
pos = np.asarray(g.attribValue("mpfb_positions"), dtype=np.float32).reshape(-1, 3)
ref = mh.build_human({"gender": 1.0, "age": 0.56, "muscle": 0.8, "weight": 0.66, "height": 0.6, "proportions": 0.9},
                     {"caucasian": 1.0}, {"head/head-square": 0.75, "chin/chin-width-decr-incr": 0.55})
print("human: points %d prims %d groups %d, first cook %.2fs, recook %.2fs, max diff %.2e, height %.3f m" % (
    len(g.points()), len(g.prims()), len(g.pointGroups()), first, second, np.abs(pos - ref).max(), pos[:, 1].max()))
print("joints", sorted(g.attribValue("mpfb_joints"))[:6], "errors", human.errors())
for kind, name in (("clothes", "shoes03"), ("hair", "ponytail01"), ("eyes", "low-poly")):
    fit = geo.createNode("mpfb::fit_asset::1.0", name.replace("-", "_"))
    fit.setInput(0, human)
    fit.parm("kind").set(kind); fit.parm("asset").set(name)
    t = time.time(); fg = fit.geometry()
    if fg is None:
        print(name, "ERRORS", fit.errors()); continue
    ap = np.frombuffer(fg.pointFloatAttribValuesAsString("P"), dtype=np.float32).reshape(-1, 3)
    expect = mh.fit_mhclo(mh.read_mhclo(mh.find_asset(kind, name)), ref)
    print("%s: points %d prims %d, %.2fs, max diff %.2e, material %s, errors %s" % (
        name, len(fg.points()), len(fg.prims()), time.time() - t, np.abs(ap - expect).max(),
        fg.prims()[0].attribValue("shop_materialpath"), fit.errors()))
sys.stdout.flush()
os._exit(0)
