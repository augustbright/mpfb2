"""Mode independent functionality for saving a MHCLO file."""

import bpy, os
from .....services import LogService
from .....services import LocationService
from .....services import ObjectService
from .....services import ClothesService
from .....services import MaterialService
from ...makeclothes import MakeClothesObjectProperties
from .....entities.objectproperties import GeneralObjectProperties
from .....entities.material.makeskinmaterial import MakeSkinMaterial

_LOG = LogService.get_logger("makeclothes.clothescommon")

class ClothesCommon:
    """Common functionality for saving a MHCLO file."""

    def generic_execute(self, context, file_name):
        """Write mhclo and obj"""

        basemesh = None
        clothes = None
        for obj in context.selected_objects:
            if ObjectService.object_is_basemesh(obj):
                basemesh = obj
            else:
                ot = ObjectService.get_object_type(obj)
                if ot and ot != "Skeleton":
                    clothes = obj

        if not basemesh:
            self.report({'ERROR'}, "No basemesh selected")
            return {'CANCELLED'}

        if not clothes:
            self.report({'ERROR'}, "No clothes selected")
            return {'CANCELLED'}

        _LOG.debug("basemesh, clothes", (basemesh, clothes))

        props_dict = {}
        for prop in ["author", "description", "name", "homepage", "license"]:
            props_dict[prop] = MakeClothesObjectProperties.get_value(prop, entity_reference=clothes)
        for prop in ["uuid"]:
            props_dict[prop] = GeneralObjectProperties.get_value(prop, entity_reference=clothes)

        delete_group = MakeClothesObjectProperties.get_value("delete_group", entity_reference=clothes)

        from ...makeclothes.makeclothespanel import MAKECLOTHES_PROPERTIES
        disable_exact_strategy = MAKECLOTHES_PROPERTIES.get_value("disable_exact_strategy", entity_reference=context.scene)
        save_material = MAKECLOTHES_PROPERTIES.get_value("save_material", entity_reference=context.scene)

        try:
            file_name = ClothesService.write_mhclo_asset(basemesh, clothes, file_name, properties_dict=props_dict,
                                                         delete_group=delete_group,
                                                         allow_exact=not disable_exact_strategy,
                                                         save_material=save_material)
        except ValueError as e:
            _LOG.error("Error writing MHCLO", e)
            self.report({'ERROR'}, str(e) + ". Do a clothes check and/or check the log for details.")
            return {'CANCELLED'}

        self.report({'INFO'}, "The asset file was written as " + file_name)
        return {'FINISHED'}
