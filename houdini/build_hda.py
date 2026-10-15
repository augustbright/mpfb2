"""Build houdini/otls/mpfb.hda (mpfb::human, mpfb::fit_asset) from the Python code in houdini/python.

    hython houdini/build_hda.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "python"))

import hou  # noqa: E402

OTL = os.path.join(HERE, "otls", "mpfb.hda")

HUMAN_COOK = """import mpfb_houdini.sop as mpfb
mpfb.cook_human(hou.pwd().parent(), hou.pwd().geometry())
"""
FIT_COOK = """import mpfb_houdini.sop as mpfb
node = hou.pwd()
inputs = node.inputs()
mpfb.cook_fit_asset(node.parent(), node.geometry(), inputs[0].geometry() if inputs and inputs[0] else None)
"""


ASSEMBLE_COOK = """import mpfb_houdini.sop as mpfb
node = hou.pwd()
inputs = list(node.inputs()) + [None, None]
mpfb.cook_assemble(node.parent(), node.geometry(), inputs[0].geometry() if inputs[0] else None,
                   inputs[1].geometry() if inputs[1] else None)
"""


def assemble_parms():
    return [hou.ToggleParmTemplate("mask_body", "Hide Body Under Assets", default_value=True,
                                   help="Remove body faces listed in the assets' delete_verts (MPFB delete groups)")]


def slider(name, label, default, lo=0.0, hi=1.0, help=""):
    return hou.FloatParmTemplate(name, label, 1, default_value=(default,), min=lo, max=hi, min_is_strict=False,
                                 max_is_strict=False, help=help)


def human_parms():
    macros = hou.FolderParmTemplate("macros_folder", "Macros", folder_type=hou.folderType.Tabs, parm_templates=[
        slider("gender", "Gender", 0.5, help="0 female, 1 male"),
        slider("age", "Age", 0.5, help="0 = 1 year, 0.1875 = 11, 0.5 = 25, 1 = 90"),
        slider("muscle", "Muscle", 0.5), slider("weight", "Weight", 0.5),
        slider("proportions", "Proportions", 0.5, help="1 = idealised"), slider("height", "Height", 0.5),
        slider("cupsize", "Cup Size", 0.5), slider("firmness", "Firmness", 0.5),
        hou.SeparatorParmTemplate("sep_race"),
        slider("african", "African", 0.33), slider("asian", "Asian", 0.33), slider("caucasian", "Caucasian", 0.33),
    ])
    modifier = hou.StringParmTemplate(
        "modifier#", "Modifier", 1, menu_type=hou.menuType.StringReplace,
        item_generator_script="import mpfb_houdini.sop as s\nreturn s.modifier_menu()",
        item_generator_script_language=hou.scriptLanguage.Python,
        help="section/name from MPFB's Model panel, e.g. head/head-square or chin/chin-width-decr-incr")
    side = hou.MenuParmTemplate("side#", "Side", ("both", "left", "right"), ("Both", "Left", "Right"))
    value = slider("value#", "Value", 0.0, -1.0, 1.0, help="-1..1 for bipolar modifiers, 0..1 otherwise")
    modifiers = hou.FolderParmTemplate("modifiers", "Modifiers", folder_type=hou.folderType.MultiparmBlock,
                                       parm_templates=[modifier, side, value])
    modifiers_tab = hou.FolderParmTemplate("modifiers_folder", "Modifiers", folder_type=hou.folderType.Tabs,
                                           parm_templates=[modifiers])
    skin = hou.StringParmTemplate("skin", "Skin", 1, default_value=("young_caucasian_male",),
                                  menu_type=hou.menuType.StringReplace,
                                  item_generator_script="import mpfb_houdini.sop as s\nreturn s.skin_menu()",
                                  item_generator_script_language=hou.scriptLanguage.Python)
    make_skin = hou.ButtonParmTemplate("make_material", "Create Skin Material",
                                       script_callback="import mpfb_houdini.sop as s; s.create_human_material(kwargs['node'])",
                                       script_callback_language=hou.scriptLanguage.Python)
    output = hou.FolderParmTemplate("output_folder", "Output", folder_type=hou.folderType.Tabs, parm_templates=[
        skin, make_skin,
        hou.FloatParmTemplate("scale", "Scale", 1, default_value=(0.1,), min=0.001, max=1.0,
                              help="MakeHuman units are decimetres: 0.1 gives metres"),
        hou.ToggleParmTemplate("ground", "Feet on Ground", default_value=True),
        hou.ToggleParmTemplate("helpers", "Keep Helper Geometry", default_value=False,
                               help="Also output the helper geometry (tights, skirt, hair, joint cubes); point numbers "
                                    "then match MakeHuman's. Assets fit either way (detail attribute mpfb_positions)"),
    ])
    return [macros, modifiers_tab, output]


def fit_parms():
    kind = hou.MenuParmTemplate("kind", "Kind", ("clothes", "hair", "eyes", "eyebrows", "eyelashes", "teeth",
                                                 "tongue", "proxymeshes"),
                                ("Clothes", "Hair", "Eyes", "Eyebrows", "Eyelashes", "Teeth", "Tongue", "Proxy"))
    asset = hou.StringParmTemplate("asset", "Asset", 1, default_value=("shoes03",),
                                   menu_type=hou.menuType.StringReplace,
                                   item_generator_script="import mpfb_houdini.sop as s\nreturn s.asset_menu(hou.pwd())",
                                   item_generator_script_language=hou.scriptLanguage.Python)
    file = hou.StringParmTemplate("file", "MHCLO File", 1, string_type=hou.stringParmType.FileReference,
                                  help="Overrides Asset: an .mhclo or .proxy file")
    tint = hou.FloatParmTemplate("tint", "Tint", 3, default_value=(1.0, 1.0, 1.0), min=0.0, max=1.0,
                                 naming_scheme=hou.parmNamingScheme.RGBA, look=hou.parmLook.ColorSquare,
                                 help="Multiplies the diffuse texture")
    diffuse = hou.StringParmTemplate("diffuse", "Diffuse Override", 1, string_type=hou.stringParmType.FileReference,
                                     file_type=hou.fileType.Image, help="Use this texture instead of the asset's")
    make = hou.ButtonParmTemplate("make_material", "Create Material",
                                  script_callback="import mpfb_houdini.sop as s; s.create_asset_material(kwargs['node'])",
                                  script_callback_language=hou.scriptLanguage.Python)
    return [kind, asset, file, hou.SeparatorParmTemplate("sep_material"), tint, diffuse, make]


def build(name, label, cook, parms, inputs):
    geo = hou.node("/obj").createNode("geo", "mpfb_build_" + name)
    sub = geo.createNode("subnet", name)
    py = sub.createNode("python", "cook")
    py.parm("python").set(cook)
    for i in range(inputs):
        py.setInput(i, sub.indirectInputs()[i])
    out = sub.createNode("output", "output0")
    out.setInput(0, py)
    out.setDisplayFlag(True)
    sub.layoutChildren()
    hda = sub.createDigitalAsset(name="mpfb::%s::1.0" % name, hda_file_name=OTL, description=label,
                                 min_num_inputs=min(inputs, 1), max_num_inputs=inputs)
    definition = hda.type().definition()
    group = definition.parmTemplateGroup()
    for template in parms:
        group.append(template)
    definition.setParmTemplateGroup(group)
    definition.setIcon("SOP_python")
    definition.setExtraInfo("")
    definition.save(OTL)
    geo.destroy()


if os.path.exists(OTL):
    os.remove(OTL)
build("human", "MPFB Human", HUMAN_COOK, human_parms(), 0)
build("fit_asset", "MPFB Fit Asset", FIT_COOK, fit_parms(), 1)
build("assemble", "MPFB Assemble", ASSEMBLE_COOK, assemble_parms(), 2)
print("built", OTL, [d.nodeTypeName() for d in hou.hda.definitionsInFile(OTL)])
sys.stdout.flush()
os._exit(0)  # skip Houdini's teardown (the HoudiniMCP startup plugin crashes there)
