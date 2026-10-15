"""Register MPFB for Houdini as a Houdini package (adds otls/ and python/ of this folder).

    python3 houdini/install_package.py [--houdini 22.0] [--extra-assets DIR ...]

--extra-assets adds project asset libraries (same layout as MPFB's data folder: clothes/<name>/<name>.mhclo...).
"""
import argparse
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser()
parser.add_argument("--houdini", help="Houdini version folder, e.g. 22.0 (default: every installed one)")
parser.add_argument("--extra-assets", nargs="*", default=[])
args = parser.parse_args()

prefs = os.path.expanduser("~/Library/Preferences/houdini")
if not os.path.isdir(prefs):
    prefs = os.path.expanduser("~")  # Linux/Windows: ~/houdiniXX.Y
versions = [args.houdini] if args.houdini else sorted(
    os.path.basename(p) for p in glob.glob(os.path.join(prefs, "[0-9]*.[0-9]*")))
package = {
    "env": [{"MPFB_HOUDINI": HERE},
            {"PYTHONPATH": {"value": "$MPFB_HOUDINI/python", "method": "prepend"}}],
    "hpath": "$MPFB_HOUDINI",
}
if args.extra_assets:
    package["env"].append({"MPFB_EXTRA_ASSETS": os.pathsep.join(os.path.abspath(p) for p in args.extra_assets)})
for version in versions:
    folder = os.path.join(prefs, version, "packages")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "mpfb.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(package, f, indent=2)
    print("wrote", path)
