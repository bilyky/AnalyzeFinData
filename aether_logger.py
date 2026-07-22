# Root shim — aether_logger is now aether.logger
import sys as _sys
from aether import logger as _mod
_sys.modules[__name__] = _mod
