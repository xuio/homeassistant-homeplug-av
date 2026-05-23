from __future__ import annotations

"""HomePlug AV / Broadcom MediaXtreme request payloads.

This module mirrors the hard-coded message constructors that live in the Ada
source (`pla/src/messages-constructors.adb`).  The original implementation
creates *payload* buffers (without the Ethernet header) and then decides which
EtherType to use based on the 3rd payload byte (0xa0 = MediaXtream → 0x8912,
otherwise HomePlug → 0x88e1).

In Python we keep the same payload bytes as immutable ``bytes`` objects and
provide a helper :func:`ether_type_for_payload` that returns the correct 16-bit
EtherType value as an ``int``.
"""

from typing import Dict

__all__ = [
    "PAYLOADS",
    "QCA_OUI",
    "qca_payload",
    "ether_type_for_payload",
]

# Minimum payload length used by the Ada implementation
_MIN_LEN = 46

QCA_OUI = bytes([0x00, 0xB0, 0x52])

_QCA_MMV0 = 0x00
_QCA_MMV1 = 0x01
_QCA_VS_SW_VER = 0xA000
_QCA_VS_LNK_STATS = 0xA030
_QCA_VS_RS_DEV = 0xA01C
_QCA_VS_NW_INFO = 0xA038
_QCA_VS_OP_ATTRIBUTES = 0xA068
_QCA_VS_NW_INFO_STATS = 0xA074
_MMTYPE_REQ = 0x0000

# fmt: off
PAYLOADS: Dict[str, bytes] = {
    # Device Access Key checks (four-step handshake)
    "check_dak_1": bytes([
        0x02, 0x5C, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x02, 0x09,
        *([0x00] * (_MIN_LEN - 10)),
    ]),
    "check_dak_2": bytes([
        0x02, 0x5C, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x03, 0x0A,
        *([0x00] * (_MIN_LEN - 10)),
    ]),
    "check_dak_3": bytes([
        0x02, 0x5C, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x04, 0x0B,
        *([0x00] * (_MIN_LEN - 10)),
    ]),
    "check_dak_4": bytes([
        0x02, 0x5C, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x05, 0x0C,
        *([0x00] * (_MIN_LEN - 10)),
    ]),
    # Network Membership Key check
    "check_nmk": bytes([
        0x02, 0x5C, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x02, 0x24,
        *([0x00] * (_MIN_LEN - 10)),
    ]),
    # Discovery (broadcast)
    "discover": bytes([
        0x01, 0x70, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x01,
        0xA3, 0x97, 0xA2, 0x55, 0x53, 0xBE, 0xF1, 0xFC, 0xF9, 0x79, 0x6B,
        0x52, 0x14, 0x13, 0xE9, 0xE2,
        *([0x00] * (_MIN_LEN - 9 - 16)),  # 9 bytes already + 16 above
    ]),
    # Various information requests
    "get_any_network_info": bytes([
        0x02, 0x28, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x02, 0x00, 0x01,
        *([0x00] * (_MIN_LEN - 11)),
    ]),
    "get_capabilities": bytes([
        0x01, 0x34, 0x60,
        *([0x00] * (_MIN_LEN - 3)),
    ]),
    "get_discover_list": bytes([
        0x01, 0x14,
        *([0x00] * (_MIN_LEN - 2)),
    ]),
    "get_id_info": bytes([
        0x01, 0x60, 0x60,
        *([0x00] * (_MIN_LEN - 3)),
    ]),
    "get_manufacturer_hfid": bytes([
        0x02, 0x5C, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x02, 0x1B,
        *([0x00] * (_MIN_LEN - 10)),
    ]),
    "get_member_network_info": bytes([
        0x02, 0x28, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x02,
        *([0x00] * (_MIN_LEN - 9)),
    ]),
    "get_network_stats": bytes([
        0x02, 0x2C, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x02, 0x00,
        0xB0, 0xF2, 0xE6, 0x95, 0x66, 0x6B, 0x03,
        *([0x00] * (_MIN_LEN - 17)),
    ]),
    "get_station_info": bytes([
        0x02, 0x4C, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x02,
        *([0x00] * (_MIN_LEN - 9)),
    ]),
    "get_user_hfid": bytes([
        0x02, 0x5C, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x02, 0x25,
        *([0x00] * (_MIN_LEN - 10)),
    ]),
    # Adapter reset/restart
    "reset": bytes([
        0x02, 0x54, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x02, 0x01,
        *([0x00] * (_MIN_LEN - 10)),
    ]),
    "restart": bytes([
        0x02, 0x20, 0xA0, 0x00, 0x00, 0x00, 0x1F, 0x84, 0x02,
        *([0x00] * (_MIN_LEN - 9)),
    ]),
}
# fmt: on


def _qca_header(mmv: int, mmtype: int, *, fmi: bool = False) -> bytes:
    """Build a Qualcomm/Atheros vendor-specific HomePlug header."""

    header = bytes([mmv]) + mmtype.to_bytes(2, "little")
    if fmi:
        header += b"\x00\x00"
    return header + QCA_OUI


def _pad(payload: bytes) -> bytes:
    """Pad a management payload to the minimum Ethernet payload length."""

    return payload + (b"\x00" * max(0, _MIN_LEN - len(payload)))


def _mac_to_bytes(mac: str | None) -> bytes:
    """Convert a colon or hyphen separated MAC address to bytes."""

    if not mac:
        return b"\x00" * 6
    clean = mac.replace(":", "").replace("-", "").lower()
    if len(clean) != 12:
        raise ValueError(f"Invalid MAC address: {mac}")
    return bytes.fromhex(clean)


def qca_payload(
    name: str,
    *,
    cookie: int = 0,
    peer_mac: str | None = None,
    direction: int = 2,
    lid: int = 0xF8,
    mcontrol: int = 0,
) -> bytes:
    """Return a Qualcomm/Atheros management payload by name.

    These messages are standard HomePlug AV frames using EtherType 0x88e1,
    even though their MMTYPE values are vendor-specific and start with 0xa0.
    """

    if name == "sw_version":
        payload = _qca_header(_QCA_MMV0, _QCA_VS_SW_VER | _MMTYPE_REQ)
        payload += cookie.to_bytes(4, "little")
        return _pad(payload)

    if name == "network_info":
        return _pad(_qca_header(_QCA_MMV1, _QCA_VS_NW_INFO | _MMTYPE_REQ, fmi=True))

    if name == "network_info_stats":
        payload = _qca_header(_QCA_MMV1, _QCA_VS_NW_INFO_STATS | _MMTYPE_REQ, fmi=True)
        payload += b"\x00"  # MME_SUBVER
        payload += b"\x00\x00\x00"  # reserved
        payload += b"\x00"  # FIRST_TEI
        payload += b"\x00" * 6  # NUM_STAS / reserved query range
        return _pad(payload)

    if name == "op_attributes":
        payload = _qca_header(_QCA_MMV0, _QCA_VS_OP_ATTRIBUTES | _MMTYPE_REQ)
        payload += cookie.to_bytes(4, "little")
        payload += b"\x00"
        return _pad(payload)

    if name == "link_stats":
        payload = _qca_header(_QCA_MMV0, _QCA_VS_LNK_STATS | _MMTYPE_REQ)
        payload += bytes([mcontrol & 0xFF, direction & 0xFF, lid & 0xFF])
        payload += _mac_to_bytes(peer_mac)
        return _pad(payload)

    if name == "restart":
        return _pad(_qca_header(_QCA_MMV0, _QCA_VS_RS_DEV | _MMTYPE_REQ))

    raise KeyError(f"Unknown Qualcomm/Atheros payload: {name}")

for _name, _payload in PAYLOADS.items():
    if len(_payload) < _MIN_LEN:
        raise ValueError(f"Payload '{_name}' length should be at least {_MIN_LEN} bytes, got {len(_payload)}")


def ether_type_for_payload(payload: bytes) -> int:
    """Return the numerical EtherType to use for *payload*.

    The original Ada code infers the EtherType by inspecting the 3rd byte
    (index 2).  We replicate the same logic here.
    """

    return 0x8912 if payload[2] == 0xA0 else 0x88E1
