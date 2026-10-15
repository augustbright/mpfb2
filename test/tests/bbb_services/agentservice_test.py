import bpy, os, tempfile
import pytest
from .. import AgentService
from .. import AssetService
from .. import ObjectService


def _clear():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)


def test_agentservice_exists():
    """AgentService"""
    assert AgentService is not None, "AgentService can be imported"


def test_unknown_command():
    """AgentService.call() rejects unknown commands"""
    with pytest.raises(ValueError):
        AgentService.call("no_such_command")


def test_describe_commands():
    """AgentService.describe_commands() lists every command with documentation"""
    commands = AgentService.call("describe_commands")
    names = [c["name"] for c in commands]
    assert "create_human" in names
    assert all(c["doc"] for c in commands)
    assert names == AgentService.command_names()


def test_create_and_describe_human():
    """AgentService.create_human() applies macros and race"""
    _clear()
    info = AgentService.call("create_human", {"name": "agent_test", "macros": {"gender": 1.0, "height": 0.9},
                                              "race": {"caucasian": 1.0}, "basic_assets": False, "skin": None})
    assert info["name"] == "agent_test"
    assert info["macros"]["gender"] == pytest.approx(1.0)
    assert info["race"]["caucasian"] == pytest.approx(1.0)
    assert info["race"]["asian"] == pytest.approx(0.0)
    assert info["height_m"] > 1.8
    assert info["bounds"][0][2] == pytest.approx(0.0, abs=0.01), "Feet are on the floor"
    assert [h["name"] for h in AgentService.call("list_humans")] == ["agent_test"]
    _clear()


def test_create_human_rejects_unknown_macro():
    """AgentService.create_human() validates macro names"""
    with pytest.raises(ValueError):
        AgentService.call("create_human", {"macros": {"wings": 1.0}, "basic_assets": False, "skin": None})


def test_modifiers():
    """AgentService.set_modifiers() handles unipolar, bipolar and sided modifiers"""
    _clear()
    AgentService.call("create_human", {"name": "agent_mod", "basic_assets": False, "skin": None})
    listed = AgentService.call("list_modifiers", {"section": "chin"})
    assert any(m["modifier"] == "chin/chin-width-decr-incr" and m["bipolar"] for m in listed)
    targets = AgentService.call("set_modifiers", {"values": {"chin/chin-width-decr-incr": -0.4}})
    assert targets.get("chin-width-decr") == pytest.approx(0.4)
    targets = AgentService.call("set_modifiers", {"values": {"chin/chin-width-decr-incr": 0.6}})
    assert targets.get("chin-width-incr") == pytest.approx(0.6)
    assert "chin-width-decr" not in targets
    sided = next(m for m in AgentService.call("list_modifiers", {"section": "arms"}) if m["sided"])
    targets = AgentService.call("set_modifiers", {"values": {sided["modifier"] + "@left": 0.5}})
    assert any(name.startswith("l-") for name in targets)
    assert not any(name.startswith("r-") for name in targets)
    with pytest.raises(ValueError):
        AgentService.call("set_modifiers", {"values": {"chin/no-such-modifier": 1.0}})
    _clear()


@pytest.mark.skipif(not AssetService.system_assets_pack_is_installed(), reason="needs makehuman_system_assets")
def test_assets_rig_render_export():
    """AgentService equips assets, rigs, poses, renders and exports"""
    _clear()
    AgentService.call("create_human", {"name": "agent_full"})
    hair = AgentService.call("list_assets", {"kind": "hair"})
    assert hair, "There is hair in the library"
    AgentService.call("equip_asset", {"kind": "hair", "name": hair[0]["name"]})
    AgentService.call("equip_asset", {"kind": "hair", "name": hair[-1]["name"]})
    assets = AgentService.call("describe_human")["assets"]
    assert len([a for a in assets if a["type"] == "Hair"]) == 1, "New hair replaced the old one"
    rig = AgentService.call("add_rig", {"rig": "game_engine"})
    assert "upperarm_l" in rig["bones"]
    AgentService.call("set_pose", {"bones": {"upperarm_l": {"rotation": [0, 0, 30]}}})
    with pytest.raises(ValueError):
        AgentService.call("set_pose", {"bones": {"no_such_bone": {"rotation": [0, 0, 30]}}})
    out = tempfile.mkdtemp()
    paths = AgentService.call("render_views", {"views": ["front"], "size": 64, "out_dir": out})
    assert len(paths) == 1 and os.path.exists(paths[0])
    assert "mpfb_agent_camera" not in bpy.data.objects, "Render camera is cleaned up"
    count = len(bpy.data.objects)
    exported = AgentService.call("export_gltf", {"path": os.path.join(out, "agent_full.glb")})
    assert exported["bytes"] > 0
    assert len(bpy.data.objects) == count, "Export copy is cleaned up"
    _clear()


@pytest.mark.skipif(not AssetService.system_assets_pack_is_installed(), reason="needs makehuman_system_assets")
def test_meshes_have_a_render_uv_map():
    """Loaded meshes render with their UV map (the Blender 5.x OBJ importer leaves active_render off)"""
    _clear()
    AgentService.call("create_human", {"name": "agent_uv"})
    AgentService.call("equip_asset", {"kind": "hair", "name": AgentService.call("list_assets", {"kind": "hair"})[0]["name"]})
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.data.uv_layers:
            assert any(layer.active_render for layer in obj.data.uv_layers), obj.name
    _clear()


@pytest.mark.skipif(not AssetService.system_assets_pack_is_installed(), reason="needs makehuman_system_assets")
def test_pose_bones_world_space_and_ground():
    """AgentService.pose_bones aims bones in world space and keeps the feet on the floor"""
    from mathutils import Vector
    _clear()
    AgentService.call("create_human", {"name": "agent_pose", "basic_assets": False, "skin": None})
    rig = bpy.data.objects[AgentService.call("add_rig", {"rig": "game_engine"})["rig"]]
    aim = Vector((0.4, -0.2, -0.9)).normalized()
    AgentService.call("pose_bones", {"bones": {"thigh_l": {"aim": list(aim)}, "calf_l": {"aim": [0, 0.3, -1]},
                                               "foot_l": {"keep_rest": True}, "index_01_l": {"curl": 60}}})
    pb = rig.pose.bones["thigh_l"]
    along = ((rig.matrix_world @ pb.tail) - (rig.matrix_world @ pb.head)).normalized()
    assert along.dot(aim) > 0.999
    lo, _ = AgentService._world_bounds(AgentService._hierarchy(bpy.data.objects["agent_pose"]))
    assert lo.z == pytest.approx(0.0, abs=0.005), "Grounded"
    with pytest.raises(ValueError):
        AgentService.call("pose_bones", {"bones": {"no_such_bone": {"curl": 1}}})
    _clear()


def test_set_material_flat_is_makeskin(tmp_path=None):
    """set_material flat writes a color swatch and leaves a MakeSkin material (so MakeClothes keeps the color)"""
    from .. import MaterialService
    _clear()
    AgentService.call("create_human", {"name": "agent_mat", "basic_assets": False, "skin": None})
    AgentService.call("make_belt", {"name": "belt"})
    out = tempfile.mkdtemp()
    result = AgentService.call("set_material", {"object": "belt", "color": [0.1, 0.2, 0.3], "image_dir": out})
    assert os.path.exists(result["texture"])
    assert MaterialService.identify_material(bpy.data.objects["belt"].active_material) == "makeskin"
    _clear()
