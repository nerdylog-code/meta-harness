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
import struct
import sys
import tempfile
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
        """An unbound CONNECT is exactly the tunnel this defends against."""

        async def scenario() -> None:
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
                writer.write(client_hello(None))
                await writer.drain()
                response = await asyncio.wait_for(reader.read(), timeout=5)
                writer.close()
                server.close()
                await server.wait_closed()
            self.assertIn(b"403", response)
            self.assertIn(b"no SNI", response)
            self.assertEqual(broker.tunnels, 0, "no tunnel was opened")

        asyncio.run(scenario())


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
