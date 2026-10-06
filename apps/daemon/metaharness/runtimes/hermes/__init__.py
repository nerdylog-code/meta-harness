"""Hermes over ACP (M2, ADR-0016).

`docs/protocols/HERMES_ACP.md` is the observed protocol; this package is the code that speaks it.
The adapter is instantiated by the daemon and reached through the same `RuntimeAdapter` interface
Pi implements, which is the whole point: the control plane gains a second runtime without learning
a second vocabulary.
"""

from __future__ import annotations

from .adapter import DEFAULT_ARGV, PROTOCOL_NAME, HermesRuntimeAdapter
from .parse import AcpParser, ParsedEvent
from .transport import AcpProtocolError, AcpTransport, AcpTransportError, CloseReport

__all__ = [
    "DEFAULT_ARGV",
    "PROTOCOL_NAME",
    "AcpParser",
    "AcpProtocolError",
    "AcpTransport",
    "AcpTransportError",
    "CloseReport",
    "HermesRuntimeAdapter",
    "ParsedEvent",
]
