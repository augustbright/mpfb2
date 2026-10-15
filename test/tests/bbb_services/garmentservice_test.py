import bpy
import pytest
from .. import AgentService
from .. import GarmentService
from .. import AssetService


def _clear():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)


def test_garmentservice_exists():
    """GarmentService"""
    assert GarmentService is not None


def test_joints_and_regions():
    """GarmentService.joints() and region specs"""
    _clear()
    AgentService.call("create_human", {"name": "garment_test", "basic_assets": False, "skin": None})
    joints = AgentService.call("get_joints")
    for name in ("l-shoulder", "l-elbow", "r-knee", "pelvis", "neck"):
        assert name in joints
    assert joints["l-shoulder"][0] > 0 > joints["r-shoulder"][0], "Left is +X"
    j = GarmentService.joints(bpy.data.objects["garment_test"])
    limb = GarmentService.region_test({"type": "limb", "from": "shoulder", "to": "elbow", "beyond": 0.05}, j)
    past = j["l-elbow"] + (j["l-elbow"] - j["l-shoulder"]).normalized() * 0.1
    assert limb(past)
    assert not limb(j["l-shoulder"])
    assert not limb(j["pelvis"]), "Torso below the shoulder is not part of the arm"
    with pytest.raises(ValueError):
        GarmentService.region_test({"type": "nope"}, j)
    _clear()


def test_generated_accessories_are_valid_clothes():
    """make_belt / make_glasses / make_tube produce meshes MakeClothes accepts"""
    _clear()
    AgentService.call("create_human", {"name": "garment_acc", "basic_assets": False, "skin": None})
    basemesh = bpy.data.objects["garment_acc"]
    from .. import ClothesService
    for name, command, args in (("belt", "make_belt", {}), ("glasses", "make_glasses", {}),
                                ("strap", "make_tube", {"points": ["joint:l-elbow", "joint:l-hand"], "radii": [0.04, 0.03]})):
        AgentService.call(command, dict(args, name=name))
        obj = bpy.data.objects[name]
        GarmentService.prepare_for_makeclothes(obj)
        assert ClothesService.mesh_is_valid_as_clothes(obj, basemesh)["all_checks_ok"], name
    _clear()


@pytest.mark.skipif(not AssetService.system_assets_pack_is_installed(), reason="needs makehuman_system_assets")
def test_derive_cut_roll():
    """derive_clothes, cut_clothes and roll_edges reshape an equipped suit"""
    _clear()
    AgentService.call("create_human", {"name": "garment_suit", "basic_assets": False, "skin": None})
    suit = AgentService.call("equip_asset", {"kind": "clothes", "name": "male_casualsuit02"})["object"]
    shirt = AgentService.call("derive_clothes", {"source": suit, "name": "shirt", "part": "upper"})
    before = shirt["vertices"]
    sleeve = {"type": "limb", "from": "shoulder", "to": "elbow", "beyond": 0.05}
    cut = AgentService.call("cut_clothes", {"object": "shirt", "regions": [sleeve], "keep_largest": 1})
    assert 0 < cut["vertices"] < before
    rolled = AgentService.call("roll_edges", {"object": "shirt", "region": dict(sleeve, beyond=0.0),
                                              "toward": ["joint:l-shoulder", "joint:r-shoulder"]})
    assert rolled["rolled_loops"] == 2
    _clear()
