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

from typing import Callable, Optional, Any
import logging
import threading
import time
import platform
import os

from scapy.all import Ether, Raw, sendp, conf, get_if_hwaddr  # type: ignore
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


def _reply_matcher(
    ether_type: int,
    src_mac: str,
    dest_mac: str,
    response_match: Callable[[Any], bool] | None,
) -> Callable[[Any], bool]:
    """Return an lfilter accepting only replies to the frame we are sending.

    The reply must carry the expected EtherType, be addressed to our own
    interface and (for unicast requests) come from the adapter we queried.
    Anything else seen on the wire - ordinary IP traffic in particular - is
    dropped before it reaches a parser.
    """

    src_mac = src_mac.lower()
    dest_mac = dest_mac.lower()

    def _match(pkt: Any) -> bool:
        try:
            if Ether not in pkt:
                return False
            eth = pkt[Ether]
            if eth.type != ether_type:
                return False
            if (eth.dst or "").lower() != src_mac:
                return False
            if dest_mac != BROADCAST_MAC and (eth.src or "").lower() != dest_mac:
                return False
            if response_match is not None and not response_match(pkt):
                return False
            return True
        except Exception:  # pragma: no cover - malformed frame
            return False

    return _match


def _stop_sniffer(sniffer: Any) -> None:
    """Stop an AsyncSniffer whether or not it has already finished."""

    try:
        if getattr(sniffer, "running", False):
            sniffer.stop()
        else:
            sniffer.join(1.0)
    except Exception as err:  # pragma: no cover - depends on Scapy internals
        _LOG.debug("Ignoring error while stopping sniffer: %s", err)


def _send_and_capture(
    frame: Any,
    iface: str,
    lfilter: Callable[[Any], bool],
    *,
    timeout: float,
    window: float = 0.0,
    first_only: bool = True,
) -> list[Any]:
    """Send *frame* and return matching replies.

    Waits up to *timeout* seconds for the first reply.  With
    ``first_only=False`` capture continues for *window* more seconds to gather
    replies from other adapters.

    ``AsyncSniffer`` is never given a ``timeout`` keyword: Scapy 2.7+ rejects
    it (``'timeout' isn't supported with AsyncSniffer``), and older versions
    do not need it because we bound the wait ourselves and call ``stop()``.
    """

    captured: list[Any] = []
    first_reply = threading.Event()
    started = threading.Event()

    def _store(pkt: Any) -> None:
        captured.append(pkt)
        first_reply.set()

    kwargs: dict[str, Any] = {
        "iface": iface,
        "lfilter": lfilter,
        "prn": _store,
        "store": False,
        "started_callback": started.set,
    }
    if first_only:
        kwargs["count"] = 1

    sniffer = AsyncSniffer(**kwargs)
    sniffer.start()
    # Arm the sniffer *before* sending so a fast reply is not missed.
    if not started.wait(1.0):
        _LOG.debug("Sniffer on %s did not report start within 1 s", iface)

    try:
        sendp(frame, iface=iface, verbose=False)
        if first_reply.wait(timeout) and not first_only and window > 0:
            time.sleep(window)
    finally:
        _stop_sniffer(sniffer)

    if sniffer.exception is not None:
        raise NetworkError(f"Sniffer failed on {iface}: {sniffer.exception}") from sniffer.exception

    return list(captured)


def send_message(
    payload: bytes,
    interface: Optional[str] = None,
    dest_mac: str = BROADCAST_MAC,
    timeout: float = DEFAULT_TIMEOUT,
    ether_type: int | None = None,
    response_match: Callable[[Any], bool] | None = None,
):
    """Send *payload* on *interface* to *dest_mac* and wait for a reply.

    Returns a Scapy packet instance or ``None`` when *timeout* expires.
    """

    iface = _resolve_interface(interface)
    if ether_type is None:
        ether_type = ether_type_for_payload(payload)
    src_mac = get_if_hwaddr(iface)

    frame = Ether(src=src_mac, dst=dest_mac, type=ether_type) / Raw(load=payload)

    _LOG.debug("Sending %d-byte frame on %s (0x%04x → %s)", len(payload), iface, ether_type, dest_mac)

    t_start = time.time()
    pkts = _send_and_capture(
        frame,
        iface,
        _reply_matcher(ether_type, src_mac, dest_mac, response_match),
        timeout=timeout,
    )
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
if platform.system() == "Linux":
    if bool(int(os.getenv("PLA_UTIL_PY_FORCE_PCAP", "0"))):
        conf.use_pcap = True
        _LOG.debug("Running on Linux – forced libpcap backend")
    else:
        conf.use_pcap = False
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
    ether_type: int | None = None,
    response_match: Callable[[Any], bool] | None = None,
):
    """Send *payload* and return a list of reply packets.

    A first reply is waited for up to *timeout* seconds.  If one arrives, we
    continue capturing for *window* seconds (defaults to *timeout*) to collect
    additional confirmations that may come from other adapters.
    """

    if window is None:
        window = timeout

    iface = _resolve_interface(interface)
    if ether_type is None:
        ether_type = ether_type_for_payload(payload)
    src_mac = get_if_hwaddr(iface)

    frame = Ether(src=src_mac, dst=dest_mac, type=ether_type) / Raw(load=payload)

    _LOG.debug(
        "[collect] Sending %d-byte frame on %s (0x%04x → %s)", len(payload), iface, ether_type, dest_mac
    )

    return _send_and_capture(
        frame,
        iface,
        _reply_matcher(ether_type, src_mac, dest_mac, response_match),
        timeout=timeout,
        window=window,
        first_only=False,
    )
