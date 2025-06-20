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
    "ether_type_for_payload",
]

# Minimum payload length used by the Ada implementation
_MIN_LEN = 46

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

for _name, _payload in PAYLOADS.items():
    if len(_payload) < _MIN_LEN:
        raise ValueError(f"Payload '{_name}' length should be at least {_MIN_LEN} bytes, got {len(_payload)}")


def ether_type_for_payload(payload: bytes) -> int:
    """Return the numerical EtherType to use for *payload*.

    The original Ada code infers the EtherType by inspecting the 3rd byte
    (index 2).  We replicate the same logic here.
    """

    return 0x8912 if payload[2] == 0xA0 else 0x88E1
