"""Selective egress: the only connected path a sandbox may use.

Setting `HTTPS_PROXY` is not enforcement. If the runtime can ignore the variable and open a raw
socket, the network dimension is `weak`, and no amount of configuration changes that. So the broker
is built to be a *path*, not a suggestion:

* it is reachable over a **unix socket** the sandbox has mounted, while the sandbox itself runs with
  no network at all -- no direct route exists to ignore;
* it accepts only `CONNECT host:443`, so arbitrary protocols are not on offer;
* it resolves the approved hostname **itself**, inspects every returned address, and refuses private,
  loopback, link-local, multicast, unspecified and special-purpose ranges unconditionally -- a
  provider allowlist must never accidentally authorise metadata or localhost;
* it reads the TLS ClientHello and requires the SNI to match the host that was allowed, so an allowed
  `CONNECT` cannot become a tunnel to an arbitrary co-hosted TLS service. No interception, no fake CA,
  no decryption: the tunnel stays opaque and the credentials stay inside TLS;
* it records the decision, the address class, bytes and duration -- and never an authorization header,
  an API key, a body, a cookie, a query string or any plaintext.

`allowlist` is called **strong** only when the direct route is genuinely closed. That is measured, not
asserted, and this module is written so that the measurement has something honest to measure.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

#: V1 speaks HTTPS/WSS over CONNECT on 443 only. That covers normal provider APIs and is far safer
#: than pretending to support arbitrary protocols.
ALLOWED_PORT = 443

#: Explicit bounds, because a runtime chooses what it sends us and asyncio's defaults are not a
#: policy. Oversized or malformed input is refused with bounded memory, never buffered.
MAX_CONNECT_HEADER_BYTES = 8 * 1024
#: A real ClientHello is a couple of kilobytes; 16 KiB is generous and, unlike a 64 KiB bound, it can
#: actually be violated -- a TLS record length is a 16-bit field, so a limit at or above 65536 would
#: be a check that can never fire.
MAX_CLIENT_HELLO_BYTES = 16 * 1024
CONNECT_TIMEOUT_S = 10.0
HANDSHAKE_TIMEOUT_S = 10.0
IDLE_TIMEOUT_S = 300.0

#: Ranges a provider allowlist must never reach, whatever it says. Unconditional.
FORBIDDEN_V4 = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, including the metadata service
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
)

FORBIDDEN_V6 = (
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("2001:db8::/32"),
)


class EgressRefused(Exception):
    """A connection the broker refused. The reason is safe to record and to show."""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    hostname: str = ""
    port: int = 0
    resolved: tuple[str, ...] = ()


@dataclass
class EgressRecord:
    """What the broker may remember about a connection. Never content."""

    hostname: str
    port: int
    decision: str
    reason: str
    resolved: tuple[str, ...] = ()
    sni: str | None = None
    bytes_up: int = 0
    bytes_down: int = 0
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "hostname": self.hostname,
            "port": self.port,
            "decision": self.decision,
            "reason": self.reason,
            "resolved": list(self.resolved),
            "sni": self.sni,
            "bytes_up": self.bytes_up,
            "bytes_down": self.bytes_down,
            "duration_s": round(self.duration_s, 4),
        }


def canonical_hostname(hostname: str) -> str:
    """Lower-case, strip a trailing dot, and leave the rest alone.

    Canonicalization exists so that ``Provider.Example``, ``provider.example`` and
    ``provider.example.`` are one name -- which is what a comparison between a CONNECT target and a
    TLS SNI needs to be meaningful.
    """
    return hostname.strip().lower().rstrip(".")


def forbidden_reason(address: str) -> str | None:
    """Why this address may not be reached, or ``None`` when it is ordinary public space."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return f"not an address: {address}"
    networks = FORBIDDEN_V4 if ip.version == 4 else FORBIDDEN_V6
    for network in networks:
        if ip in network:
            return f"{address} is inside {network}"
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return f"{address} is a private, loopback, link-local, reserved or multicast address"
    return None


class HostAllowlist:
    """Allow by hostname, with suffix-boundary matching.

    `*.example.com` matches `api.example.com` and not `evil-example.com`: the boundary is checked, so
    a wildcard cannot be widened by a hostile name. `*` and `*.com` are refused as entries outright --
    they are not selective egress, whatever they are called.
    """

    def __init__(self, entries: Iterable[str] = ()) -> None:
        self.exact: set[str] = set()
        self.suffixes: set[str] = set()
        for raw in entries:
            entry = raw.strip().lower().rstrip(".")
            if not entry:
                continue
            if entry in {"*", "*.com", "*.net", "*.org", "*.io"} or entry.startswith("*.") and entry.count(".") < 2:
                raise ValueError(f"{raw!r} is not a selective allowlist entry: it allows everything")
            if entry.startswith("*."):
                self.suffixes.add(entry[2:])
            else:
                self.exact.add(entry)

    def matches(self, hostname: str) -> bool:
        name = hostname.strip().lower().rstrip(".")
        if name in self.exact:
            return True
        return any(name.endswith(f".{suffix}") for suffix in self.suffixes)

    def __bool__(self) -> bool:
        return bool(self.exact or self.suffixes)

    def as_list(self) -> list[str]:
        return sorted(self.exact) + sorted(f"*.{suffix}" for suffix in self.suffixes)


def sni_from_client_hello(data: bytes) -> str | None:
    """Extract the SNI host name from a TLS ClientHello, without decrypting anything.

    Returns ``None`` when the record is not a complete ClientHello or carries no SNI -- and the broker
    treats that as a refusal, because an unbound CONNECT is exactly the tunnel this defends against.
    """
    try:
        if len(data) < 5 or data[0] != 0x16:  # handshake
            return None
        if data[5] != 0x01:  # client_hello
            return None
        body = data[9:]
        index = 2 + 32  # session id length field is at 34
        if len(body) < index + 1:
            return None
        session_len = body[34]
        index = 35 + session_len
        if len(body) < index + 2:
            return None
        cipher_len = struct.unpack("!H", body[index : index + 2])[0]
        index += 2 + cipher_len
        if len(body) < index + 1:
            return None
        compression_len = body[index]
        index += 1 + compression_len
        if len(body) < index + 2:
            return None
        extensions_len = struct.unpack("!H", body[index : index + 2])[0]
        index += 2
        end = min(index + extensions_len, len(body))
        while index + 4 <= end:
            ext_type, ext_len = struct.unpack("!HH", body[index : index + 4])
            index += 4
            if ext_type == 0x00:  # server_name
                block = body[index : index + ext_len]
                if len(block) < 5:
                    return None
                name_type = block[2]
                name_len = struct.unpack("!H", block[3:5])[0]
                if name_type != 0 or len(block) < 5 + name_len:
                    return None
                # An SNI host is ASCII (punycode when it is not): decoding as idna here would reject the
                # error handling it is given, and a hostile name must never crash the parser.
                return block[5 : 5 + name_len].decode("ascii", errors="replace").lower()
            index += ext_len
        return None
    except (IndexError, struct.error, UnicodeDecodeError):
        return None


def resolve_hostname(hostname: str, *, resolver: Any = None) -> tuple[list[str], str | None]:
    """Resolve a hostname and classify **every** returned address.

    A stale classification is not trusted forever and a single address is not enough: the broker asks
    again, inspects all of them, and refuses if any one of them is forbidden.
    """
    try:
        infos = (resolver or socket.getaddrinfo)(hostname, ALLOWED_PORT, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return [], f"resolution failed: {exc}"
    addresses = sorted({info[4][0] for info in infos})
    for address in addresses:
        reason = forbidden_reason(address)
        if reason:
            return addresses, reason
    if not addresses:
        return [], "the hostname resolved to no address"
    return addresses, None


class EgressBroker:
    """The only connected path. It decides, it tunnels, and it records without content."""

    def __init__(
        self,
        *,
        allowlist: HostAllowlist,
        port: int = ALLOWED_PORT,
        resolver: Any = None,
    ) -> None:
        self.allowlist = allowlist
        self.port = port
        self.resolver = resolver
        self.records: list[EgressRecord] = []
        self.refusals = 0
        self.tunnels = 0

    # ------------------------------------------------------------------ decision

    def decide(self, hostname: str, port: int) -> Decision:
        """Allow or refuse, with a reason safe to record and to show an operator."""
        if port != self.port:
            return Decision(False, f"only port {self.port} may be reached through the broker", hostname, port)
        if not self.allowlist.matches(hostname):
            return Decision(False, f"{hostname} is not on the allowlist", hostname, port)
        addresses, reason = resolve_hostname(hostname, resolver=self.resolver)
        if reason:
            return Decision(False, reason, hostname, port, tuple(addresses))
        return Decision(True, "allowed", hostname, port, tuple(addresses))

    def check_sni(self, decision: Decision, sni: str | None) -> str | None:
        """Bind the tunnel to the hostname that was CONNECTed -- exactly that one.

        Two different questions are asked of two different things, and conflating them was a real
        bug: the allowlist decides whether the CONNECT target may be attempted, and this decides
        whether TLS actually intends that same host. Asking only "is this SNI somewhere on the
        allowlist?" would let ``CONNECT a.provider.com`` be used as a tunnel to ``b.provider.com``,
        which is a co-hosted virtual host the operator never authorised for that connection.
        """
        if not sni:
            return "the TLS ClientHello carried no SNI, so the tunnel cannot be bound to the CONNECTed host"
        if canonical_hostname(sni) != canonical_hostname(decision.hostname):
            return (
                f"the TLS SNI {sni!r} is not the host that was CONNECTed "
                f"({decision.hostname}); a tunnel is bound to one host, not to the allowlist"
            )
        return None

    # ------------------------------------------------------------------ tunnelling

    async def read_client_hello(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read one bounded ClientHello record, or ``None`` when it cannot be read safely."""
        try:
            head = await asyncio.wait_for(reader.readexactly(5), timeout=HANDSHAKE_TIMEOUT_S)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError):
            return None
        length = struct.unpack("!H", head[3:5])[0]
        if length <= 0 or length > MAX_CLIENT_HELLO_BYTES:
            return None
        try:
            body = await asyncio.wait_for(reader.readexactly(length), timeout=HANDSHAKE_TIMEOUT_S)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError):
            return None
        return head + body

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one CONNECT request from the sandbox and tunnel it if it passes every check.

        The ordering is the protocol, and getting it wrong deadlocks a standard client: a proxy is
        expected to answer ``200 Connection Established`` first and only then receive TLS. What must
        NOT happen before the SNI check is opening the upstream -- answering 200 means "we will
        carry your TLS", not "we have already connected somewhere". If the SNI turns out not to bind,
        the tunnel is closed rather than answered with an HTTP error, because a 403 written into an
        established tunnel is noise to a TLS client.
        """
        record: EgressRecord | None = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=CONNECT_TIMEOUT_S)
            if len(header) > MAX_CONNECT_HEADER_BYTES:
                await self._refuse(writer, "the CONNECT header is too large", "oversized")
                return
            request_line = header.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
            parts = request_line.split()
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                await self._refuse(writer, "the broker accepts CONNECT only", request_line)
                return
            target = parts[1]
            hostname, _, port_text = target.rpartition(":")
            port = int(port_text) if port_text.isdigit() else 0
            decision = self.decide(hostname, port)
            record = EgressRecord(
                hostname, port, "allowed" if decision.allowed else "refused", decision.reason, decision.resolved
            )
            if not decision.allowed:
                self.refusals += 1
                self.records.append(record)
                await self._refuse(writer, decision.reason, target)
                return

            # 200 first, as a proxy must. The upstream is still unopened at this point.
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            client_hello = await self.read_client_hello(reader)
            if client_hello is None:
                self.refusals += 1
                record.decision = "refused"
                record.reason = "the TLS ClientHello was absent, oversized or malformed"
                self.records.append(record)
                return
            sni = sni_from_client_hello(client_hello)
            record.sni = sni
            mismatch = self.check_sni(decision, sni)
            if mismatch:
                self.refusals += 1
                record.decision = "refused"
                record.reason = mismatch
                self.records.append(record)
                return

            upstream = await asyncio.open_connection(decision.resolved[0], port)
            self.tunnels += 1
            upstream[1].write(client_hello)
            await upstream[1].drain()
            up, down = await self._pump(reader, writer, upstream[0], upstream[1])
            record.bytes_up, record.bytes_down = up, down
            record.duration_s = time.time() - record.started_at
            self.records.append(record)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            if record is not None:
                record.decision = record.decision if record.decision == "refused" else "failed"
                record.reason = record.reason if record.decision == "refused" else str(exc)[:160]
                record.duration_s = time.time() - record.started_at
                self.records.append(record)
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001 - closing a broken socket is not an error worth raising
                pass

    async def _pump(self, reader, writer, up_reader, up_writer) -> tuple[int, int]:
        async def forward(source, sink) -> int:
            total = 0
            try:
                while True:
                    chunk = await source.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    sink.write(chunk)
                    await sink.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                try:
                    sink.close()
                except Exception:  # noqa: BLE001
                    pass
            return total

        up_task = asyncio.create_task(forward(reader, up_writer))
        down_task = asyncio.create_task(forward(up_reader, writer))
        up, down = await asyncio.gather(up_task, down_task)
        return up, down

    @staticmethod
    async def _refuse(writer, reason: str, target: str) -> None:
        body = f"refused: {reason}".encode()
        writer.write(
            b"HTTP/1.1 403 Forbidden\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()

    # ------------------------------------------------------------------ serving

    async def serve_unix(self, path: str) -> asyncio.AbstractServer:
        """Listen on a unix socket, which is the only thing a sandboxed runtime can reach.

        A unix socket rather than a TCP port is deliberate: the sandbox runs with no network at all,
        and a mounted socket gives it one narrow door instead of a route it could ignore.
        """
        return await asyncio.start_unix_server(self.handle, path=path)

    # ------------------------------------------------------------------ reporting

    def summary(self) -> dict[str, Any]:
        return {
            "allowlist": self.allowlist.as_list(),
            "port": self.port,
            "tunnels": self.tunnels,
            "refusals": self.refusals,
            "records": [record.as_dict() for record in self.records[-50:]],
        }


def enforcement_level(*, policy: str, direct_route_closed: bool, measured: bool) -> tuple[str, str]:
    """The honest level for the network dimension, and why.

    ``deny`` is strong when egress is genuinely unavailable. ``unrestricted`` is weak. ``allowlist`` is
    strong **only** when the direct route is closed and that was measured -- a proxy environment
    variable by itself never is.
    """
    if policy == "deny":
        return ("strong", "no network is available inside the boundary") if measured else ("unknown", "not measured")
    if policy == "unrestricted":
        return "weak", "arbitrary network is reachable"
    if policy == "allowlist":
        if not measured:
            return "unknown", "the allowlist was configured but never measured from inside"
        if direct_route_closed:
            return "strong", "the only connected path is the broker, and the direct route is closed"
        return "moderate", "the broker is available, but a direct route also exists and was reachable"
    return "unknown", f"unknown policy {policy!r}"
