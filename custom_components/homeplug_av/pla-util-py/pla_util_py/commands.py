from __future__ import annotations

"""High-level command helpers (very thin wrappers at the moment).

Each helper sends the corresponding request *payload* and returns the raw reply
packet (or ``None``).  Proper parsing of the replies would require quite a bit
of additional work and is left for future iterations.
"""

from typing import Optional, Any
import logging

from .messages import PAYLOADS, qca_payload
from .network import send_message, BROADCAST_MAC, send_message_collect, DEFAULT_TIMEOUT

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helper with timeout support
# ---------------------------------------------------------------------------

def _run(
    cmd_name: str,
    interface: Optional[str],
    pla_mac: Optional[str],
    timeout: Optional[float] = None,
    *,
    warn_on_no_reply: bool = True,
):
    payload = PAYLOADS[cmd_name]
    dest_mac = pla_mac or BROADCAST_MAC
    reply = send_message(payload, interface=interface, dest_mac=dest_mac, timeout=timeout or DEFAULT_TIMEOUT)

    if reply is not None:
        _LOG.info("%s reply received (%d bytes)", cmd_name, len(reply))
    elif warn_on_no_reply:
        _LOG.warning("%s no reply received", cmd_name)
    else:
        _LOG.debug("%s no reply received", cmd_name)

    return reply


def _run_payload(
    payload: bytes,
    interface: Optional[str],
    pla_mac: Optional[str],
    timeout: Optional[float] = None,
    *,
    ether_type: int | None = None,
    response_prefix: bytes | None = None,
    command_name: str = "command",
    warn_on_no_reply: bool = True,
):
    dest_mac = pla_mac or BROADCAST_MAC
    reply = send_message(
        payload,
        interface=interface,
        dest_mac=dest_mac,
        timeout=timeout or DEFAULT_TIMEOUT,
        ether_type=ether_type,
        response_match=_payload_prefix_match(response_prefix),
    )

    if reply is not None:
        _LOG.info("%s reply received (%d bytes)", command_name, len(reply))
    elif warn_on_no_reply:
        _LOG.warning("%s no reply received", command_name)
    else:
        _LOG.debug("%s no reply received", command_name)

    return reply


def _payload_prefix_match(prefix: bytes | None):
    if prefix is None:
        return None

    def _match(pkt: Any) -> bool:
        from scapy.packet import Raw  # type: ignore

        return Raw in pkt and bytes(pkt[Raw].load).startswith(prefix)

    return _match


# ---------------------------------------------------------------------------
# Individual command entry points (exported)
# ---------------------------------------------------------------------------


def discover(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    """Broadcast a *discover* request and return **all** replies as a list."""

    payload = PAYLOADS["discover"]
    packets = list(
        send_message_collect(payload, interface=interface, dest_mac=BROADCAST_MAC, timeout=timeout, window=timeout)
    )
    return packets


def discover_capabilities(
    interface: Optional[str] = None,
    pla_mac: Optional[str] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
):
    """Broadcast standard HomePlug AV capabilities and return all replies."""

    payload = PAYLOADS["get_capabilities"]
    dest = pla_mac or BROADCAST_MAC
    if pla_mac:
        pkt = send_message(
            payload,
            interface=interface,
            dest_mac=dest,
            timeout=timeout,
            ether_type=0x88E1,
            response_match=_payload_prefix_match(b"\x01\x35\x60"),
        )
        return [] if pkt is None else [pkt]

    return list(
        send_message_collect(
            payload,
            interface=interface,
            dest_mac=dest,
            timeout=timeout,
            window=timeout,
            ether_type=0x88E1,
            response_match=_payload_prefix_match(b"\x01\x35\x60"),
        )
    )


def get_capabilities(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    return _run("get_capabilities", interface, pla_mac, timeout)


def get_network_stats(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    dest = pla_mac or BROADCAST_MAC
    payload = PAYLOADS["get_network_stats"]
    p = send_message(payload, interface=interface, dest_mac=dest, timeout=timeout)
    if p is None:
        _LOG.debug("No reply packet – returning empty list")
        return []

    _LOG.debug(f"p: {p}")

    return p


def reset(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    return _run("reset", interface, pla_mac, timeout)


def restart(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    return _run("restart", interface, pla_mac, timeout)


# ---------------------------------------------------------------------------
# Newly added commands
# ---------------------------------------------------------------------------


def get_discover_list(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    """Request the discover list from adapter(s)."""
    return _run(
        "get_discover_list",
        interface,
        pla_mac,
        timeout,
        warn_on_no_reply=False,
    )


def get_hfid(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    dest = pla_mac or BROADCAST_MAC
    payload = PAYLOADS["get_user_hfid"]
    return send_message(payload, interface=interface, dest_mac=dest, timeout=timeout)


def get_id_info(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    dest = pla_mac or BROADCAST_MAC
    payload = PAYLOADS["get_id_info"]
    return send_message(payload, interface=interface, dest_mac=dest, timeout=timeout)


def get_network_info(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    dest = pla_mac or BROADCAST_MAC
    payload = PAYLOADS["get_member_network_info"]
    return send_message(payload, interface=interface, dest_mac=dest, timeout=timeout)


def get_station_info(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    dest = pla_mac or BROADCAST_MAC
    payload = PAYLOADS["get_station_info"]
    return send_message(payload, interface=interface, dest_mac=dest, timeout=timeout)


def qca_get_sw_version(
    interface: Optional[str] = None,
    pla_mac: Optional[str] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
):
    """Request Qualcomm/Atheros hardware and firmware version information."""

    return _run_payload(
        qca_payload("sw_version"),
        interface,
        pla_mac,
        timeout,
        ether_type=0x88E1,
        response_prefix=b"\x00\x01\xA0\x00\xB0\x52",
        command_name="qca_get_sw_version",
        warn_on_no_reply=False,
    )


def qca_get_network_info(
    interface: Optional[str] = None,
    pla_mac: Optional[str] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
):
    """Request Qualcomm/Atheros network membership and PHY rate information."""

    return _run_payload(
        qca_payload("network_info"),
        interface,
        pla_mac,
        timeout,
        ether_type=0x88E1,
        response_prefix=b"\x01\x39\xA0\x00\x00\x00\xB0\x52",
        command_name="qca_get_network_info",
        warn_on_no_reply=False,
    )


def qca_get_network_info_stats(
    interface: Optional[str] = None,
    pla_mac: Optional[str] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
):
    """Request Qualcomm/Atheros network membership and PHY stats."""

    return _run_payload(
        qca_payload("network_info_stats"),
        interface,
        pla_mac,
        timeout,
        ether_type=0x88E1,
        response_prefix=b"\x01\x75\xA0\x00\x00\x00\xB0\x52",
        command_name="qca_get_network_info_stats",
        warn_on_no_reply=False,
    )


def qca_get_op_attributes(
    interface: Optional[str] = None,
    pla_mac: Optional[str] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
):
    """Request Qualcomm/Atheros device attributes using VS_OP_ATTRIBUTES."""

    return _run_payload(
        qca_payload("op_attributes"),
        interface,
        pla_mac,
        timeout,
        ether_type=0x88E1,
        response_prefix=b"\x00\x69\xA0\x00\xB0\x52",
        command_name="qca_get_op_attributes",
        warn_on_no_reply=False,
    )


def qca_get_link_stats(
    interface: Optional[str] = None,
    pla_mac: Optional[str] = None,
    *,
    peer_mac: str,
    direction: int = 2,
    lid: int = 0xF8,
    timeout: float = DEFAULT_TIMEOUT,
):
    """Request Qualcomm/Atheros per-link counters using VS_LNK_STATS."""

    return _run_payload(
        qca_payload("link_stats", peer_mac=peer_mac, direction=direction, lid=lid),
        interface,
        pla_mac,
        timeout,
        ether_type=0x88E1,
        response_prefix=b"\x00\x31\xA0\x00\xB0\x52",
        command_name="qca_get_link_stats",
        warn_on_no_reply=False,
    )


def qca_restart(
    interface: Optional[str] = None,
    pla_mac: Optional[str] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
):
    """Restart a Qualcomm/Atheros adapter using VS_RS_DEV."""

    return _run_payload(
        qca_payload("restart"),
        interface,
        pla_mac,
        timeout,
        ether_type=0x88E1,
        response_prefix=b"\x00\x1D\xA0\x00\xB0\x52",
        command_name="qca_restart",
    )


# Additional helpers can be wired in the same way… 
