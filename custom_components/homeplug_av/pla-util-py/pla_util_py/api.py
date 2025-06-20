from __future__ import annotations

"""Public Python API for pla-util-py.

Example:
    >>> from pla_util_py import PLAUtil
    >>> pla = PLAUtil(interface="eth0")
    >>> adapters = pla.discover()
    >>> print(adapters)
"""

from typing import Optional, List, Dict, Any

from . import commands
from . import parsers

__all__ = ["PLAUtil"]


class PLAUtil:
    """High-level convenience wrapper around *pla-util-py* commands."""

    def __init__(self, interface: Optional[str] = None, pla_mac: Optional[str] = None):
        self.interface = interface
        self.pla_mac = pla_mac

    # ---------------------------------------------------------------------
    # Read-only helpers returning structured data
    # ---------------------------------------------------------------------

    def discover(self) -> List[Dict[str, Any]]:
        pkts = commands.discover(self.interface)
        return parsers.parse_discover(pkts)

    def capabilities(self) -> Dict[str, Any]:
        pkt = commands.get_capabilities(self.interface, self.pla_mac)
        return parsers.parse_capabilities(pkt)

    def discover_list(self) -> Dict[str, Any]:
        pkt = commands.get_discover_list(self.interface, self.pla_mac)
        return parsers.parse_discover_list(pkt)

    def network_stats(self) -> List[Dict[str, int]]:
        pkt = commands.get_network_stats(self.interface, self.pla_mac)
        return parsers.parse_network_stats(pkt)

    def hfid(self) -> str:
        pkt = commands.get_hfid(self.interface, self.pla_mac)
        return parsers.parse_hfid(pkt)

    def id_info(self) -> Dict[str, str]:
        pkt = commands.get_id_info(self.interface, self.pla_mac)
        return parsers.parse_id_info(pkt)

    def network_info(self) -> List[Dict[str, Any]]:
        pkt = commands.get_network_info(self.interface, self.pla_mac)
        return parsers.parse_network_info(pkt)

    def station_info(self) -> Dict[str, Any]:
        pkt = commands.get_station_info(self.interface, self.pla_mac)
        return parsers.parse_station_info(pkt)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def reset(self):
        commands.reset(self.interface, self.pla_mac)

    def restart(self):
        commands.restart(self.interface, self.pla_mac) 
