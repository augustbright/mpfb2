"""Start and stop the remote control server."""

import bpy
from .....services import LogService
from .....services import RemoteControlService
from ..... import ClassManager, get_preference
from ....mpfboperator import MpfbOperator

_LOG = LogService.get_logger("remotecontrol.operators.remotecontrol")


class MPFB_OT_Remote_Control_Start_Operator(MpfbOperator):
    """Start the local remote control server, so MCP clients can drive MPFB"""
    bl_idname = "mpfb.remote_control_start"
    bl_label = "Start remote control"
    bl_options = {'REGISTER'}

    def get_logger(self):
        return _LOG

    def hardened_execute(self, context):
        if not bpy.app.online_access:
            self.report({'ERROR'}, "Allow online access in Preferences > System > Network first")
            return {'CANCELLED'}
        port = get_preference("mpfb_remote_port") or 9877
        try:
            RemoteControlService.start(int(port))
        except OSError as err:
            self.report({'ERROR'}, "Could not listen on port %s: %s" % (port, err))
            return {'CANCELLED'}
        self.report({'INFO'}, "Remote control listening on 127.0.0.1:%s" % port)
        return {'FINISHED'}


class MPFB_OT_Remote_Control_Stop_Operator(MpfbOperator):
    """Stop the local remote control server"""
    bl_idname = "mpfb.remote_control_stop"
    bl_label = "Stop remote control"
    bl_options = {'REGISTER'}

    def get_logger(self):
        return _LOG

    def hardened_execute(self, context):
        RemoteControlService.stop()
        self.report({'INFO'}, "Remote control stopped")
        return {'FINISHED'}


ClassManager.add_class(MPFB_OT_Remote_Control_Start_Operator)
ClassManager.add_class(MPFB_OT_Remote_Control_Stop_Operator)
