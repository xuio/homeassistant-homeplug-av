from __future__ import annotations

"""Low-level network helpers built on *Scapy*.

This is *not* a full abstraction of the Ada `Packets.Network_Devices` API – it
only provides the basic functionality needed by the high-level command
implementation:

* open a specific interface (or guess the first active non-loopback one)
* craft an Ethernet frame that contains one of our payload buffers
* send the frame
* wait for a single reply (optional)

The original program uses libpcap in *immediate* mode and waits on a poll()able
file descriptor.  Scapy does all the heavy lifting for us.
"""

from typing import Optional, Any
import logging
import time
import platform
import os

from scapy.all import Ether, Raw, sendp, sniff, conf, get_if_hwaddr  # type: ignore
from scapy.sendrecv import AsyncSniffer  # type: ignore

from .messages import ether_type_for_payload

_LOG = logging.getLogger(__name__)


class NetworkError(RuntimeError):
    """Raised for unrecoverable network-level issues."""


DEFAULT_TIMEOUT = 0.5  # seconds – mirrors Ada default of 500 ms
BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"


__all__ = [
    "send_message",
    "DEFAULT_TIMEOUT",
    "BROADCAST_MAC",
    "NetworkError",
]


def _resolve_interface(preferred: Optional[str] = None) -> str:
    """Return *preferred* if given, otherwise guess a usable interface."""

    if preferred:
        return preferred

    # Best-effort pick the first *up* interface that is not the loopback.
    # When Scapy is initialised it populates `conf.ifaces`, which is a mapping
    # with rich metadata – we just iterate over it.
    for iface in conf.ifaces.values():
        if iface.is_up() and not iface.is_loopback:
            return iface.name

    raise NetworkError("Could not find a suitable network interface – use --interface")


def send_message(
    payload: bytes,
    interface: Optional[str] = None,
    dest_mac: str = BROADCAST_MAC,
    timeout: float = DEFAULT_TIMEOUT,
):
    """Send *payload* on *interface* to *dest_mac* and wait for a reply.

    Returns a Scapy packet instance or ``None`` when *timeout* expires.
    """

    iface = _resolve_interface(interface)
    ether_type = ether_type_for_payload(payload)
    src_mac = get_if_hwaddr(iface)

    frame = Ether(src=src_mac, dst=dest_mac, type=ether_type) / Raw(load=payload)

    _LOG.debug("Sending %d-byte frame on %s (0x%04x → %s)", len(payload), iface, ether_type, dest_mac)

    # The reply can come from *any* PLA, so we only filter on EtherType (and
    # destination MAC == our MAC).  If the caller specified a unicast dest MAC
    # we also accept that as the source in the reply.
    def _match(pkt):  # type: ignore[override]
        if Ether not in pkt:  # pragma: no cover – defensive
            return False
        if pkt.type != ether_type:
            return False
        if pkt[Ether].dst.lower() != src_mac.lower():
            return False
        # When we addressed a specific adapter, enforce that in the reply.
        if dest_mac != BROADCAST_MAC and pkt[Ether].src.lower() != dest_mac.lower():
            return False
        return True

    # Arm the sniffer *before* sending to avoid timing races where the reply
    # arrives faster than we can call sniff().  AsyncSniffer runs in a
    # separate thread and will stop automatically after *timeout* seconds or
    # after the *count* is reached (whichever comes first).
    sniffer = AsyncSniffer(iface=iface, timeout=timeout, lfilter=_match, count=1, store=True)
    sniffer.start()

    t_start = time.time()
    sendp(frame, iface=iface, verbose=False)

    sniffer.join(timeout + 0.1)  # small guard interval
    pkts = sniffer.results  # type: ignore[attr-defined]
    elapsed = time.time() - t_start

    if pkts:
        _LOG.debug("Received %d-byte reply after %.3f s", len(pkts[0]), elapsed)
        return pkts[0]

    _LOG.debug("No reply after %.3fs", timeout)
    return None


# ---------------------------------------------------------------------------
# Platform-specific tweak ------------------------------------------------------
# ---------------------------------------------------------------------------
# On Linux Scapy's libpcap backend buffers packets unless immediate mode is
# enabled – that makes our sub-second timeouts unreliable.  Scapy's raw-socket
# backend, on the other hand, delivers frames right away (similar to the BPF
# backend used by default on macOS).  We therefore disable libpcap when we're
# on Linux, unless the user forces it back via an environment variable.
if platform.system() == "Linux" and not bool(int(os.getenv("PLA_UTIL_PY_FORCE_PCAP", "0"))):
    conf.use_pcap = True
    _LOG.debug("Running on Linux – disabled libpcap backend (use raw sockets)")


# ---------------------------------------------------------------------------
# Helper that collects **all** replies for a short window
# ---------------------------------------------------------------------------


def send_message_collect(
    payload: bytes,
    interface: Optional[str] = None,
    dest_mac: str = BROADCAST_MAC,
    timeout: float = DEFAULT_TIMEOUT,
    window: float | None = None,
):
    """Send *payload* and return a list of reply packets.

    A first reply is waited for up to *timeout* seconds.  If one arrives, we
    continue capturing for *window* seconds (defaults to *timeout*) to collect
    additional confirmations that may come from other adapters.
    """

    if window is None:
        window = timeout

    iface = _resolve_interface(interface)
    ether_type = ether_type_for_payload(payload)
    src_mac = get_if_hwaddr(iface)

    frame = Ether(src=src_mac, dst=dest_mac, type=ether_type) / Raw(load=payload)

    _LOG.debug(
        "[collect] Sending %d-byte frame on %s (0x%04x → %s)", len(payload), iface, ether_type, dest_mac
    )

    # Arm a sniffer to capture *all* matching packets, first until the first
    # reply (or *timeout*), then for an additional *window* seconds so we can
    # gather confirmations from other adapters.

    # We cannot express this "wait X seconds *after* first match" logic with
    # a single AsyncSniffer, so we use a small helper below.

    def _match(pkt):  # type: ignore[override]
        if Ether not in pkt:
            return False
        if pkt.type != ether_type:
            return False
        if pkt[Ether].dst.lower() != src_mac.lower():
            return False
        if dest_mac != BROADCAST_MAC and pkt[Ether].src.lower() != dest_mac.lower():
            return False
        return True

    # ------------------------------------------------------------------
    # Capture helper ----------------------------------------------------
    # ------------------------------------------------------------------
    captured: list[Any] = []  # we will fill this from the sniffer callback

    def _store(pkt):  # type: ignore[override]
        captured.append(pkt)

    # Start sniffer *before* we transmit to avoid races.  We do not rely on
    # the "results" attribute (which is only populated once the sniffer is
    # stopped) but store packets on-the-fly via the callback above.

    sniffer = AsyncSniffer(iface=iface, lfilter=_match, prn=_store, store=False)
    sniffer.start()

    sendp(frame, iface=iface, verbose=False)

    # Wait for the first confirmation up to *timeout* seconds.
    deadline = time.time() + timeout
    while time.time() < deadline and not captured:
        time.sleep(0.01)

    if not captured:  # nothing at all
        sniffer.stop()
        return []

    # First reply seen – keep listening for *window* seconds more.
    time.sleep(window)

    sniffer.stop()

    return captured
