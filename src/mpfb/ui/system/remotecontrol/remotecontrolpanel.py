"""This file contains the remote control panel."""

from .... import ClassManager
from ....services import LogService
from ....services import RemoteControlService
from ....services import UiService
from ...abstractpanel import Abstract_Panel

_LOG = LogService.get_logger("remotecontrol.remotecontrolpanel")


class MPFB_PT_Remote_Control_Panel(Abstract_Panel):
    """Start or stop the remote control server for MCP clients."""
    bl_label = "Remote control (MCP)"
    bl_category = UiService.get_value("DEVELOPERCATEGORY")
    bl_options = {'DEFAULT_CLOSED'}
    bl_parent_id = "MPFB_PT_System_Panel"

    def draw(self, context):
        _LOG.enter()
        layout = self.layout
        if RemoteControlService.is_running():
            layout.label(text="Listening on 127.0.0.1:%s" % RemoteControlService.get_port())
            layout.operator("mpfb.remote_control_stop")
        else:
            layout.label(text="Not running")
            layout.operator("mpfb.remote_control_start")


ClassManager.add_class(MPFB_PT_Remote_Control_Panel)
