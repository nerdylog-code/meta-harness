"""The Pi runtime: transport, parser and adapter.

Split on purpose. `transport` speaks JSONL and owns the process; `parse` turns records into
canonical events and usage samples and knows nothing about processes or I/O; `adapter`
implements `RuntimeAdapter` v2 over both. A bug in framing never needs the parser open, and a
parser test never needs a subprocess.
"""

from __future__ import annotations

from .transport import (
    CloseReport,
    PiCommandError,
    PiProtocolError,
    PiTransport,
    PiTransportError,
    TransportStats,
)

__all__ = [
    "CloseReport",
    "PiCommandError",
    "PiProtocolError",
    "PiTransport",
    "PiTransportError",
    "TransportStats",
]
