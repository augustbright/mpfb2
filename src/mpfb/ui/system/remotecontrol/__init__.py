"""Panel and operators for the remote control server used by MCP clients."""

from ....services import LogService
_LOG = LogService.get_logger("remotecontrol.init")
_LOG.trace("initializing remote control module")

from .remotecontrolpanel import MPFB_PT_Remote_Control_Panel
from .operators import *

__all__ = [
    "MPFB_PT_Remote_Control_Panel",
    "MPFB_OT_Remote_Control_Start_Operator",
    "MPFB_OT_Remote_Control_Stop_Operator"
    ]
