"""S2B: the broker decides where a sandbox may connect, and says how strong that is.

Deterministic and offline: the resolver is injected, so address classification, DNS rebinding and the
allowlist boundary are all testable without a network. The ClientHello is crafted by hand, so the SNI
check is exercised for real rather than mocked.

The wildcard boundary is the test that matters most here: `*.example.com` must match
`api.example.com` and must never match `evil-example.com`. A suffix comparison that forgets the dot is
how an allowlist silently becomes an open proxy.
"""

from __future__ import annotations

import asyncio
import os
import socket
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
for extra in (REPO_ROOT / "apps" / "daemon", REPO_ROOT / "packages" / "contracts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from metaharness.egress.broker import (  # noqa: E402
    ALLOWED_PORT,
    EgressBroker,
    HostAllowlist,
    enforcement_level,
    forbidden_reason,
    resolve_hostname,
    sni_from_client_hello,
)


def client_hello(sni: str | None) -> bytes:
    """A minimal but structurally real TLS ClientHello, optionally carrying an SNI."""
    extensions = b""
    if sni is not None:
        name = sni.encode()
        # The extension body is a ServerNameList: a 2-byte list length, then the entry (type, name
        # length, name). Omitting the list length is a malformed hello, and the parser is right to
        # refuse it.
        entry = b"\x00" + struct.pack("!H", len(name)) + name
        server_name = struct.pack("!H", len(entry)) + entry
        extension = b"\x00\x00" + struct.pack("!H", len(server_name)) + server_name
        extensions = struct.pack("!H", len(extension)) + extension
    body = (
        b"\x03\x03"
        + b"\x00" * 32  # random
        + b"\x00"  # session id length
        + struct.pack("!H", 2)
        + b"\x13\x01"  # one cipher suite
        + b"\x01\x00"  # compression methods
        + extensions
    )
    # The handshake length is 3 bytes: [1:] of a big-endian 4-byte int, not [3:] which is one byte.
    handshake = b"\x01" + struct.pack("!I", len(body))[1:] + body
    return b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake


def resolver_for(mapping: dict[str, list[str]]):
    def resolve(hostname: str, port: int, **kwargs):
        if hostname not in mapping:
            raise OSError(f"unknown host {hostname}")
        return [(2, 1, 6, "", (address, port)) for address in mapping[hostname]]

    return resolve


class AllowlistTest(unittest.TestCase):
    def test_exact_hosts_match(self) -> None:
        allowlist = HostAllowlist(["api.example.com"])
        self.assertTrue(allowlist.matches("api.example.com"))
        self.assertTrue(allowlist.matches("API.Example.COM"))
        self.assertFalse(allowlist.matches("other.example.com"))

    def test_a_wildcard_respects_the_suffix_boundary(self) -> None:
        """The failure this defends against: `evil-example.com` is not inside `*.example.com`."""
        allowlist = HostAllowlist(["*.example.com"])
        self.assertTrue(allowlist.matches("api.example.com"))
        self.assertTrue(allowlist.matches("a.b.example.com"))
        self.assertFalse(allowlist.matches("evil-example.com"), "a missing dot boundary would allow this")
        self.assertFalse(allowlist.matches("example.com.attacker.net"))
        self.assertFalse(allowlist.matches("notexample.com"))

    def test_an_allow_everything_entry_is_refused(self) -> None:
        for entry in ("*", "*.com", "*.org"):
            with self.assertRaises(ValueError, msg=entry):
                HostAllowlist([entry])

    def test_the_allowlist_reports_itself(self) -> None:
        allowlist = HostAllowlist(["api.example.com", "*.provider.net"])
        self.assertEqual(allowlist.as_list(), ["api.example.com", "*.provider.net"])


class AddressTest(unittest.TestCase):
    def test_forbidden_ranges_are_refused(self) -> None:
        for address in (
            "127.0.0.1",
            "::1",
            "10.1.2.3",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "0.0.0.0",
            "224.0.0.1",
            "fd00::1",
            "fe80::1",
        ):
            self.assertIsNotNone(forbidden_reason(address), address)

    def test_public_addresses_are_allowed(self) -> None:
        for address in ("1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"):
            self.assertIsNone(forbidden_reason(address), address)

    def test_a_hostname_resolving_to_a_private_address_is_refused(self) -> None:
        """A provider allowlist must never accidentally authorise metadata or localhost."""
        addresses, reason = resolve_hostname(
            "provider.example", resolver=resolver_for({"provider.example": ["169.254.169.254"]})
        )
        self.assertIsNotNone(reason)
        self.assertEqual(addresses, ["169.254.169.254"])

    def test_one_forbidden_address_among_many_refuses_the_whole_name(self) -> None:
        addresses, reason = resolve_hostname(
            "rebind.example", resolver=resolver_for({"rebind.example": ["1.1.1.1", "127.0.0.1"]})
        )
        self.assertIsNotNone(reason, "every returned address is inspected, not just the first")
        self.assertEqual(sorted(addresses), ["1.1.1.1", "127.0.0.1"])


class SniTest(unittest.TestCase):
    def test_a_client_hello_with_sni_parses(self) -> None:
        self.assertEqual(sni_from_client_hello(client_hello("api.example.com")), "api.example.com")

    def test_a_client_hello_without_sni_is_none(self) -> None:
        self.assertIsNone(sni_from_client_hello(client_hello(None)))

    def test_garbage_is_none_not_a_crash(self) -> None:
        for blob in (b"", b"\x16\x03\x01", b"not tls at all", b"\x17\x03\x03\x00\x10" + b"x" * 16):
            self.assertIsNone(sni_from_client_hello(blob))


class BrokerDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = EgressBroker(
            allowlist=HostAllowlist(["api.provider.com"]),
            resolver=resolver_for({"api.provider.com": ["93.184.216.34"]}),
        )

    def test_an_allowed_host_is_allowed(self) -> None:
        decision = self.broker.decide("api.provider.com", ALLOWED_PORT)
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(decision.resolved, ("93.184.216.34",))

    def test_an_unlisted_host_is_refused(self) -> None:
        decision = self.broker.decide("evil.example.com", ALLOWED_PORT)
        self.assertFalse(decision.allowed)
        self.assertIn("not on the allowlist", decision.reason)

    def test_a_port_other_than_443_is_refused(self) -> None:
        decision = self.broker.decide("api.provider.com", 22)
        self.assertFalse(decision.allowed)
        self.assertIn("only port 443", decision.reason)

    def test_an_allowed_name_resolving_private_is_refused(self) -> None:
        broker = EgressBroker(
            allowlist=HostAllowlist(["sneaky.example.com"]),
            resolver=resolver_for({"sneaky.example.com": ["10.0.0.5"]}),
        )
        decision = broker.decide("sneaky.example.com", ALLOWED_PORT)
        self.assertFalse(decision.allowed)
        self.assertIn("inside", decision.reason)

    def test_sni_must_match_the_allowed_host(self) -> None:
        decision = self.broker.decide("api.provider.com", ALLOWED_PORT)
        self.assertIsNone(self.broker.check_sni(decision, "api.provider.com"))
        self.assertIsNotNone(self.broker.check_sni(decision, "co-hosted.internal"))
        self.assertIsNotNone(self.broker.check_sni(decision, None), "no SNI means no binding")


@unittest.skipUnless(
    os.name == "posix",
    "the egress transport is a unix socket: asyncio.start_unix_server does not exist on Windows, so "
    "the broker's decision layer is verified there while its transport is honestly unsupported",
)
class BrokerTransportTest(unittest.TestCase):
    """The transport is POSIX-only in this milestone, and says so rather than faking parity."""
    def test_a_refused_connect_answers_403_and_records_no_content(self) -> None:
        async def scenario() -> None:
            broker = EgressBroker(
                allowlist=HostAllowlist(["api.provider.com"]),
                resolver=resolver_for({"api.provider.com": ["93.184.216.34"]}),
            )
            with tempfile.TemporaryDirectory() as tmp:
                socket_path = str(Path(tmp) / "egress.sock")
                server = await broker.serve_unix(socket_path)
                reader, writer = await asyncio.open_unix_connection(socket_path)
                writer.write(b"CONNECT evil.example.com:443 HTTP/1.1\r\nHost: evil.example.com:443\r\n\r\n")
                await writer.drain()
                response = await asyncio.wait_for(reader.read(), timeout=5)
                writer.close()
                server.close()
                await server.wait_closed()
            self.assertIn(b"403", response)
            self.assertEqual(broker.refusals, 1)
            self.assertEqual(broker.tunnels, 0)
            record = broker.records[-1]
            self.assertEqual(record.hostname, "evil.example.com")
            self.assertEqual(record.decision, "refused")
            # The record holds the decision and the reason, never a body or a credential.
            payload = record.as_dict()
            for forbidden in ("authorization", "cookie", "body", "query", "key"):
                self.assertNotIn(forbidden, str(payload).lower())

        asyncio.run(scenario())

    def test_a_non_connect_method_is_refused(self) -> None:
        async def scenario() -> None:
            broker = EgressBroker(allowlist=HostAllowlist(["api.provider.com"]))
            with tempfile.TemporaryDirectory() as tmp:
                socket_path = str(Path(tmp) / "egress.sock")
                server = await broker.serve_unix(socket_path)
                reader, writer = await asyncio.open_unix_connection(socket_path)
                writer.write(b"GET http://evil.example.com/ HTTP/1.1\r\nHost: evil.example.com\r\n\r\n")
                await writer.drain()
                response = await asyncio.wait_for(reader.read(), timeout=5)
                writer.close()
                server.close()
                await server.wait_closed()
            self.assertIn(b"403", response)
            self.assertIn(b"CONNECT only", response)

        asyncio.run(scenario())

    def test_a_connect_without_sni_is_refused_before_any_tunnel(self) -> None:
        """An unbound CONNECT is exactly the tunnel this defends against.

        The refusal now comes after the ``200``, because that is the order a proxy must speak: a
        standard client waits for 200 before it will send TLS, so reading the ClientHello first would
        simply deadlock it. What must hold is the security property, not the shape of the refusal:
        answering 200 promises to carry the TLS, it does not open anything upstream. So the assertions
        that matter are that no tunnel was opened and that the refusal names the missing binding.
        """

        async def scenario() -> tuple[bytes, int]:
            broker = EgressBroker(
                allowlist=HostAllowlist(["api.provider.com"]),
                resolver=resolver_for({"api.provider.com": ["93.184.216.34"]}),
            )
            with tempfile.TemporaryDirectory() as tmp:
                socket_path = str(Path(tmp) / "egress.sock")
                server = await broker.serve_unix(socket_path)
                reader, writer = await asyncio.open_unix_connection(socket_path)
                writer.write(b"CONNECT api.provider.com:443 HTTP/1.1\r\n\r\n")
                await writer.drain()
                established = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
                writer.write(client_hello(None))
                await writer.drain()
                await asyncio.sleep(0.05)
                tunnels = broker.tunnels
                refused = broker.records[-1].reason if broker.records else ""
                writer.close()
                server.close()
                await server.wait_closed()
            return established, tunnels, refused

        established, tunnels, refused = asyncio.run(scenario())
        self.assertIn(b"200 Connection Established", established, "the proxy answers after the CONNECT")
        self.assertEqual(tunnels, 0, "no tunnel was opened for a CONNECT that never bound to a host")
        self.assertIn("no SNI", refused, "and the refusal names the missing binding")

    def test_both_allowed_hosts_still_bind_one_tunnel_to_one_them(self) -> None:
        """The mandatory negative: two allowed hosts must not become interchangeable.

        `CONNECT a` with `SNI b` used to pass, because the check asked whether the SNI was *somewhere*
        on the allowlist. That turns a CONNECT to one approved host into a tunnel to another co-hosted
        approved virtual host, which is authorisation the operator never gave.
        """
        broker = EgressBroker(
            allowlist=HostAllowlist(["a.provider.com", "b.provider.com"]),
            resolver=resolver_for({"a.provider.com": ["93.184.216.34"], "b.provider.com": ["93.184.216.35"]}),
        )
        decision = broker.decide("a.provider.com", 443)
        self.assertTrue(decision.allowed, "the CONNECT target itself is allowed")
        self.assertIsNone(broker.check_sni(decision, "a.provider.com"), "the TLS intends the same host")
        mismatch = broker.check_sni(decision, "b.provider.com")
        self.assertIsNotNone(mismatch, "a co-hosted allowed host is still a different host")
        self.assertIn("not the host that was CONNECTed", mismatch or "")
        # Canonicalization must not widen this: the same name in other clothes still matches, and a
        # different name never does.
        self.assertIsNone(broker.check_sni(decision, "A.Provider.COM."), "case and trailing dot are the same name")
        self.assertIsNotNone(broker.check_sni(decision, "a.provider.com.evil.test"), "a longer name is not this name")

    def test_standard_client_sequencing_works(self) -> None:
        """A standard HTTPS proxy client sends CONNECT, waits for 200, and only then starts TLS.

        Reading the ClientHello before answering would deadlock exactly this client, so the ordering is
        a protocol requirement rather than a style choice.
        """

        async def scenario() -> tuple[bytes, list[str]]:
            broker = EgressBroker(
                allowlist=HostAllowlist(["api.provider.com"]),
                resolver=resolver_for({"api.provider.com": ["93.184.216.34"]}),
            )
            with tempfile.TemporaryDirectory() as tmp:
                socket_path = str(Path(tmp) / "egress.sock")
                server = await broker.serve_unix(socket_path)
                reader, writer = await asyncio.open_unix_connection(socket_path)
                writer.write(b"CONNECT api.provider.com:443 HTTP/1.1\r\n\r\n")
                await writer.drain()
                # The client now WAITS, exactly as a real one does. If the broker were waiting for the
                # ClientHello first, this read would time out and the test would fail.
                established = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
                # The ordering is what this proves: the 200 arrived while the broker had not yet seen
                # any TLS, so a standard client's wait is satisfied. Reaching an upstream is a separate
                # concern and cannot be asserted offline, so the connection is simply closed.
                writer.write(client_hello("api.provider.com"))
                await writer.drain()
                await asyncio.sleep(0.05)
                reasons = [r.reason for r in broker.records]
                writer.close()
                server.close()
                await server.wait_closed()
            return established, reasons

        established, reasons = asyncio.run(scenario())
        self.assertIn(b"200 Connection Established", established)
        self.assertFalse(
            any("SNI" in reason for reason in reasons),
            f"the SNI stage accepted the bound hello, so no SNI refusal may appear: {reasons}",
        )

class EnforcementLevelTest(unittest.TestCase):
    def test_the_levels_are_honest(self) -> None:
        self.assertEqual(enforcement_level(policy="deny", direct_route_closed=True, measured=True)[0], "strong")
        self.assertEqual(enforcement_level(policy="unrestricted", direct_route_closed=False, measured=True)[0], "weak")
        level, why = enforcement_level(policy="allowlist", direct_route_closed=False, measured=True)
        self.assertEqual(level, "moderate", "a proxy that can be bypassed is not strong")
        self.assertIn("direct route", why)
        level, why = enforcement_level(policy="allowlist", direct_route_closed=True, measured=True)
        self.assertEqual(level, "strong")
        self.assertEqual(enforcement_level(policy="allowlist", direct_route_closed=True, measured=False)[0], "unknown")

    def test_an_unmeasured_policy_is_never_strong(self) -> None:
        for policy in ("deny", "allowlist", "unrestricted"):
            level, _ = enforcement_level(policy=policy, direct_route_closed=True, measured=False)
            self.assertNotEqual(level, "strong", f"{policy} claimed strong without being measured")


if __name__ == "__main__":
    unittest.main()
class PeerReader:
    """A tunnel end whose bytes arrive on a schedule the test owns.

    The pump is the unit under test, so it is driven directly: a real upstream is refused by the
    broker's own policy (loopback is never allowed), and mocking the tunnel away would leave the idle
    logic unexercised. This shape was verified by hand -- it forwarded 96 one-way bytes while the
    opposite peer stayed silent -- so the harness is known to work before a single assertion is written.
    """

    def __init__(self, script=(), eof_after: bool = False) -> None:
        self.script = list(script)
        self.eof_after = eof_after
        self.reads = 0

    async def read(self, _n: int) -> bytes:
        self.reads += 1
        if not self.script:
            if self.eof_after:
                return b""  # a real EOF: this forwarding loop ends normally
            # No EOF. The peer stays open and silent, so only the idle clock can end this side -- which
            # is what keeps "went quiet" and "was closed" distinguishable.
            await asyncio.sleep(3600.0)
            return b""
        delay, chunk = self.script.pop(0)
        await asyncio.sleep(delay)
        return chunk


class PeerWriter:
    """Collects what the pump forwarded: the buffer is evidence that bytes reached the peer."""

    def __init__(self) -> None:
        self.bytes = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.bytes.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def can_write_eof(self) -> bool:
        # A fake has no transport-level half-close, so the pump must cope with its absence. The
        # ownership rule still has to hold: returning from a forward must not close anything.
        return False

    def write_eof(self) -> None:  # pragma: no cover - only called when can_write_eof() is True
        self.closed = True

    def close(self) -> None:
        self.closed = True


class IdleTimeoutTest(unittest.TestCase):
    """Idle means silence in BOTH directions: a byte either way resets ONE shared clock.

    Each case injects a small timeout and is run under a hard outer bound, so a broken implementation
    becomes a red test rather than a hung suite.
    """

    IDLE = 0.20

    def new_broker(self) -> EgressBroker:
        return EgressBroker(allowlist=HostAllowlist(["api.provider.com"]), idle_timeout_s=self.IDLE)

    def drive(self, client: PeerReader, upstream: PeerReader):
        broker = self.new_broker()
        client_out, upstream_out = PeerWriter(), PeerWriter()

        async def scenario():
            return await asyncio.wait_for(
                broker._pump(client, client_out, upstream, upstream_out), timeout=3.0
            )

        up, down, idle = asyncio.run(scenario())
        return broker, up, down, idle, client_out, upstream_out

    def test_c1_a_completely_idle_tunnel_closes_once(self) -> None:
        started = time.monotonic()
        broker, up, down, idle, client_out, upstream_out = self.drive(PeerReader(), PeerReader())
        elapsed = time.monotonic() - started
        self.assertTrue(idle, "an idle tunnel must close")
        self.assertEqual(broker.idle_closures, 1, "counted exactly once")
        self.assertEqual((up, down), (0, 0))
        self.assertGreaterEqual(elapsed, self.IDLE * 0.5, f"closed too early: {elapsed:.3f}s")
        self.assertLess(elapsed, 2.0, f"did not close on the idle clock: {elapsed:.3f}s")
        self.assertTrue(client_out.closed and upstream_out.closed, "both sides are closed")

    def test_c2_download_only_survives_a_silent_client(self) -> None:
        """The authoritative anti-per-direction-timeout proof.

        The client sends nothing for the whole run while the server streams for longer than one idle
        window. A timeout per direction would kill this -- and a long response with a quiet client is
        exactly what that breaks in production.
        """
        broker, up, down, idle, _, _ = self.drive(PeerReader(), PeerReader([(0.07, b"B" * 24)] * 4))
        self.assertEqual(down, 96, f"the whole download was forwarded: {down}")
        self.assertEqual(up, 0, "the client really sent nothing")
        self.assertTrue(idle, "silence after the chunks still ends the tunnel")
        self.assertEqual(broker.idle_closures, 1)

    def test_c3_upload_only_survives_a_silent_server(self) -> None:
        broker, up, down, idle, _, _ = self.drive(PeerReader([(0.07, b"A" * 24)] * 4), PeerReader())
        self.assertEqual(up, 96, f"the whole upload was forwarded: {up}")
        self.assertEqual(down, 0, "the server really sent nothing")
        self.assertTrue(idle)

    def test_c4_intermittent_traffic_both_ways_then_silence(self) -> None:
        broker, up, down, idle, _, _ = self.drive(
            PeerReader([(0.05, b"U" * 10)] * 4), PeerReader([(0.05, b"D" * 10)] * 4)
        )
        self.assertEqual(up, 40, f"upstream traffic forwarded: {up}")
        self.assertEqual(down, 40, f"client traffic forwarded: {down}")
        self.assertTrue(idle, "once both sides stop, the tunnel closes")
        self.assertEqual(broker.idle_closures, 1, "counted once, not once per direction")

    def test_c5_a_full_normal_close_is_not_an_idle_timeout(self) -> None:
        """Both loops reach EOF before the deadline, which is what normal completion looks like.

        One-sided EOF is deliberately not asserted: TCP half-close is legitimate, and if one side closes
        while the other stays open and silent, an eventual idle timeout is correct.
        """
        broker, _, _, idle, _, _ = self.drive(PeerReader(eof_after=True), PeerReader(eof_after=True))
        self.assertFalse(idle, "both peers closing is a normal ending, not a timeout")
        self.assertEqual(broker.idle_closures, 0, "a clean close counts no idle closure")

    def test_c6_byte_accounting_survives_the_timeout(self) -> None:
        _, up, down, idle, _, _ = self.drive(PeerReader([(0.0, b"A" * 37)]), PeerReader([(0.0, b"B" * 53)]))
        self.assertEqual(up, 37, f"upload bytes preserved: {up}")
        self.assertEqual(down, 53, f"download bytes preserved: {down}")
        self.assertTrue(idle)

    def test_c7_no_pump_or_watchdog_task_outlives_the_tunnel(self) -> None:
        async def scenario() -> list[str]:
            broker = EgressBroker(allowlist=HostAllowlist(["api.provider.com"]), idle_timeout_s=0.15)
            baseline = set(asyncio.all_tasks())
            await asyncio.wait_for(
                broker._pump(PeerReader(), PeerWriter(), PeerReader(), PeerWriter()), timeout=3.0
            )
            await asyncio.sleep(0)
            return [t.get_name() for t in asyncio.all_tasks() if t not in baseline and not t.done()]

        leftover = asyncio.run(scenario())
        self.assertEqual(leftover, [], f"tasks outlived the tunnel: {leftover}")
