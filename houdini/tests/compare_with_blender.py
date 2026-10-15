# Compare mpfb_houdini against MPFB in Blender for the same settings (plain python3 with numpy, no Houdini needed).
#   python3 compare_with_blender.py [BLENDER]
import json, os, subprocess, sys, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "python"))
import mpfb_houdini as mh

BLENDER = sys.argv[1] if len(sys.argv) > 1 else "/Applications/Blender.app/Contents/MacOS/blender"
CASES = [
    {"macros": {}, "race": {"african": 0.33, "asian": 0.33, "caucasian": 0.33}, "modifiers": {}, "asset": "shoes03"},
    {"macros": {"gender": 1.0, "age": 0.56, "muscle": 1.0, "weight": 0.66, "height": 0.6, "proportions": 0.9},
     "race": {"caucasian": 1.0}, "asset": "male_casualsuit02",
     "modifiers": {"head/head-square": 0.75, "chin/chin-width-decr-incr": 0.55, "nose/nose-hump-decr-incr": 0.3,
                   "cheek/cheek-bones-decr-incr": 0.45, "torso/torso-vshape-decr-incr": 0.6,
                   "arms/upperarm-scale-horiz-decr-incr@left": -0.3}},
    {"macros": {"gender": 0.0, "age": 0.3, "weight": 0.2, "cupsize": 0.8, "firmness": 0.3},
     "race": {"asian": 0.7, "african": 0.3}, "modifiers": {}, "asset": "female_casualsuit01"},
]


def to_mh(blender_coords):
    """Blender (Z up, -Y forward) back to MakeHuman/Houdini (Y up, +Z forward)."""
    x, y, z = blender_coords[:, 0], blender_coords[:, 1], blender_coords[:, 2]
    return np.stack([x, z, -y], axis=1)


worst = 0.0
for i, case in enumerate(CASES):
    out = os.path.join(tempfile.mkdtemp(), "case%d" % i)
    open(out + ".json", "w").write(json.dumps(case))
    subprocess.run([BLENDER, "-b", "-P", os.path.join(HERE, "blender_dump.py"), "--", out], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ref = np.load(out + ".npz")
    body = mh.build_human(case["macros"], case["race"], case["modifiers"])
    clothes = mh.fit_mhclo(mh.read_mhclo(mh.find_asset("clothes", case["asset"])), body)
    db = np.abs(body - to_mh(ref["body"])).max()
    dc = np.abs(clothes - to_mh(ref["clothes"])).max()
    worst = max(worst, db, dc)
    print("case %d: body max error %.6f m, %s max error %.6f m" % (i, db, case["asset"], dc))
print("PASS" if worst < 1e-4 else "FAIL", "worst %.6f m" % worst)
sys.exit(0 if worst < 1e-4 else 1)
