from __future__ import annotations

"""High-level command helpers (very thin wrappers at the moment).

Each helper sends the corresponding request *payload* and returns the raw reply
packet (or ``None``).  Proper parsing of the replies would require quite a bit
of additional work and is left for future iterations.
"""

from typing import Optional, Any
import logging

from .messages import PAYLOADS
from .network import send_message, BROADCAST_MAC, send_message_collect, DEFAULT_TIMEOUT

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helper with timeout support
# ---------------------------------------------------------------------------

def _run(cmd_name: str, interface: Optional[str], pla_mac: Optional[str], timeout: Optional[float] = None):
    payload = PAYLOADS[cmd_name]
    dest_mac = pla_mac or BROADCAST_MAC
    reply = send_message(payload, interface=interface, dest_mac=dest_mac, timeout=timeout or DEFAULT_TIMEOUT)

    if reply is not None:
        _LOG.info("Reply received (%d bytes)", len(reply))
    else:
        _LOG.warning("No reply received")

    return reply


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


def get_capabilities(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    return _run("get_capabilities", interface, pla_mac, timeout)


def get_network_stats(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    dest = pla_mac or BROADCAST_MAC
    payload = PAYLOADS["get_network_stats"]
    pkts = list(
        send_message_collect(payload, interface=interface, dest_mac=dest, timeout=timeout, window=timeout)
    )
    # Keep only packets whose payload matches the expected confirmation header 0x02 0x2D
    filtered: list[Any] = []
    for p in pkts:
        try:
            raw = bytes(p.load)
        except AttributeError:
            from scapy.packet import Raw  # type: ignore

            raw = bytes(p[Raw].load)
        if len(raw) >= 2 and raw[0] == 0x02 and raw[1] == 0x2D:
            filtered.append(p)

    return filtered


def reset(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    return _run("reset", interface, pla_mac, timeout)


def restart(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    return _run("restart", interface, pla_mac, timeout)


# ---------------------------------------------------------------------------
# Newly added commands
# ---------------------------------------------------------------------------


def get_discover_list(interface: Optional[str] = None, pla_mac: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT):
    """Request the discover list from adapter(s)."""
    return _run("get_discover_list", interface, pla_mac, timeout)


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


# Additional helpers can be wired in the same way… 
