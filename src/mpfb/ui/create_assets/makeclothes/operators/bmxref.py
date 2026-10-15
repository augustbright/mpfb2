"""Operator for writing a MHCLO file."""

import bpy, os, shutil
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty
from .....services import LogService
from .....services import LocationService
from .....services import ObjectService
from .....services import ClothesService
from .....entities.meshcrossref import MeshCrossRef
from ..... import ClassManager
from ....mpfboperator import MpfbOperator
from ....pollstrategy import pollstrategy, PollStrategy

_LOG = LogService.get_logger("makeclothes.basemesh_xref")

@pollstrategy(PollStrategy.ANY_OBJECT_ACTIVE)
class MPFB_OT_BasemeshXrefOperator(MpfbOperator):
    """Create a cache with cross-reference tables for the base mesh. This is generic for all base meshes, irregardless of shape and normally only needs to be done once. Note that this can take a long time, up towards 30 seconds"""
    bl_idname = "mpfb.basemesh_xref"
    bl_label = "Create xref cache"
    bl_options = {'REGISTER'}

    def get_logger(self):
        return _LOG

    def hardened_execute(self, context):

        basemesh = None
        for obj in context.selected_objects:
            if ObjectService.object_is_basemesh(obj):
                basemesh = obj

        if not basemesh:
            self.report({'ERROR'}, "No base mesh selected.")
            return {'CANCELLED'}

        cache_dir = ClothesService.build_basemesh_xref_cache(basemesh)
        _LOG.debug("Cache dir", cache_dir)

        self.report({'INFO'}, "The xref cache was created.")
        return {'FINISHED'}

ClassManager.add_class(MPFB_OT_BasemeshXrefOperator)
