"""Operators for starting and stopping the remote control server."""

from .....services import LogService
_LOG = LogService.get_logger("remotecontrol.operators.init")
_LOG.trace("initializing remote control operators")

from .remotecontrol import MPFB_OT_Remote_Control_Start_Operator, MPFB_OT_Remote_Control_Stop_Operator

__all__ = [
    "MPFB_OT_Remote_Control_Start_Operator",
    "MPFB_OT_Remote_Control_Stop_Operator"
    ]
