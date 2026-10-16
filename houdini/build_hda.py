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


CAPTURE_COOK = """import mpfb_houdini.sop as mpfb
mpfb.cook_capture(hou.pwd().parent(), hou.pwd().geometry())
"""
SKELETON_COOK = """import mpfb_houdini.sop as mpfb
node = hou.pwd()
mpfb.cook_skeleton(node.parent(), node.geometry(), node.inputs()[0].geometry())
"""


def rig_parms():
    rig = hou.StringParmTemplate("rig", "Rig", 1, default_value=("game_engine",), menu_type=hou.menuType.Normal,
                                 item_generator_script="import mpfb_houdini.sop as s\nreturn s.rig_menu()",
                                 item_generator_script_language=hou.scriptLanguage.Python,
                                 help="MPFB's standard rigs; game_engine has Unreal-style names for game engines")
    influences = hou.IntParmTemplate("influences", "Max Influences", 1, default_value=(4,), min=0, max=16,
                                     help="Bones per point, renormalised (glTF uses 4). 0 keeps all")
    strip = hou.ToggleParmTemplate("strip", "Remove MPFB Attributes", default_value=True,
                                   help="Drop the mpfb_* bookkeeping attributes from the rigged character; the glTF "
                                        "Character ROP stalls on the large ones")
    return [rig, influences, strip]


def build_rig():
    """mpfb::rig: character in, captured character (output 0) and rest skeleton (output 1) out."""
    geo = hou.node("/obj").createNode("geo", "mpfb_build_rig")
    sub = geo.createNode("subnet", "rig")
    capture = sub.createNode("python", "capture")
    capture.parm("python").set(CAPTURE_COOK)
    capture.setInput(0, sub.indirectInputs()[0])
    pack = sub.createNode("captureattribpack", "pack")
    pack.setInput(0, capture)
    clean = sub.createNode("attribdelete", "clean")
    clean.setInput(0, pack)
    for parm in ("ptdel", "primdel", "dtldel"):
        clean.parm(parm).set("mpfb_*")
    switch = sub.createNode("switch", "strip")
    switch.setInput(0, pack)
    switch.setInput(1, clean)
    switch.parm("input").setExpression('ch("../strip")')
    skeleton = sub.createNode("python", "skeleton")
    skeleton.parm("python").set(SKELETON_COOK)
    skeleton.setInput(0, sub.indirectInputs()[0])
    out0 = sub.createNode("output", "output0")
    out0.parm("outputidx").set(0)
    out0.setInput(0, switch)
    out1 = sub.createNode("output", "output1")
    out1.parm("outputidx").set(1)
    out1.setInput(0, skeleton)
    out0.setDisplayFlag(True)
    sub.layoutChildren()
    hda = sub.createDigitalAsset(name="mpfb::rig::1.0", hda_file_name=OTL, description="MPFB Rig",
                                 min_num_inputs=1, max_num_inputs=1)
    definition = hda.type().definition()
    group = definition.parmTemplateGroup()
    for template in rig_parms():
        group.append(template)
    definition.setParmTemplateGroup(group)
    definition.setIcon("SOP_kinefx-rigdoctor")
    definition.save(OTL)
    geo.destroy()


def build_apex_character():
    """mpfb::apex_character: rigged character (skin, rest skeleton) in, an APEX character folder out, ready for
    APEX Scene Add Character / Scene Animate: FK controls on every joint, bone deformation and the full body IK
    configuration of the Animate state."""
    geo = hou.node("/obj").createNode("geo", "mpfb_build_apex")
    sub = geo.createNode("subnet", "apex_character")
    skin, skeleton = sub.indirectInputs()[0], sub.indirectInputs()[1]
    pack = sub.createNode("apex::packcharacter", "pack")
    pack.setInput(1, skin)
    pack.setInput(2, skeleton)
    pack.setInput(3, skeleton)
    pack.parm("addbaserig").set(True)
    mapping = sub.createNode("apex::mapcharacter", "map")
    mapping.setInput(0, pack)
    mapping.parm("rigpath").set("/Base.rig")
    mapping.parm("skeletonpath").set("/Base.skel")
    mapping.parm("mapbyname").set(True)
    configure = sub.createNode("apex::configurecharacter", "configure")
    configure.setInput(0, mapping)
    configure.parm("enablefbikconfig").setExpression('ch("../fbik")')
    out = sub.createNode("output", "output0")
    out.setInput(0, configure)
    out.setDisplayFlag(True)
    sub.layoutChildren()
    hda = sub.createDigitalAsset(name="mpfb::apex_character::1.0", hda_file_name=OTL,
                                 description="MPFB APEX Character", min_num_inputs=2, max_num_inputs=2)
    definition = hda.type().definition()
    group = definition.parmTemplateGroup()
    group.append(hou.ToggleParmTemplate("fbik", "Full Body IK", default_value=True,
                                        help="Configure the character for the Animate state's full body IK tools"))
    definition.setParmTemplateGroup(group)
    definition.setIcon("SOP_apex-packcharacter")
    definition.save(OTL)
    geo.destroy()


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
build_rig()
build_apex_character()
print("built", OTL, [d.nodeTypeName() for d in hou.hda.definitionsInFile(OTL)])
sys.stdout.flush()
os._exit(0)  # skip Houdini's teardown (the HoudiniMCP startup plugin crashes there)
