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


def _device_manufacturer(mac: str) -> str:
    """Best-effort device vendor for common powerline adapter MAC OUIs."""

    if mac.startswith("58:d6:1f"):
        return "Ubiquiti"
    return "Unknown"


def _empty_qca_network_info(source: str | None = None) -> Dict[str, Any]:
    """Return the empty QCA network-info shape used by callers."""

    return {
        "source": source,
        "sub_version": None,
        "networks": [],
        "stations": [],
    }


class PLAUtil:
    """High-level convenience wrapper around *pla-util-py* commands."""

    def __init__(
        self,
        interface: Optional[str] = None,
        pla_mac: Optional[str] = None,
        backend: Optional[str] = None,
    ):
        self.interface = interface
        self.pla_mac = pla_mac
        self.backend = backend

    # ---------------------------------------------------------------------
    # Read-only helpers returning structured data
    # ---------------------------------------------------------------------

    def discover(self, *, timeout: float | None = None) -> List[Dict[str, Any]]:
        adapters_by_mac: Dict[str, Dict[str, Any]] = {}
        cmd_timeout = timeout or commands.DEFAULT_TIMEOUT

        # Broadcom/MediaXtream discovery used by the original integration.
        broadcom_pkts = commands.discover(self.interface, timeout=cmd_timeout)
        for adapter in parsers.parse_discover(broadcom_pkts):
            mac = adapter["mac"].lower()
            adapters_by_mac[mac] = {
                **adapter,
                "backend": "broadcom",
                "manufacturer": "Broadcom",
            }

        # Standard HomePlug AV capability discovery catches Qualcomm/Atheros
        # devices such as the UniFi two-wire PoE extenders.
        capability_pkts = commands.discover_capabilities(self.interface, timeout=cmd_timeout)
        for pkt in capability_pkts:
            try:
                caps = parsers.parse_capabilities(pkt)
            except ValueError:
                continue

            mac = (caps.get("source_mac") or caps.get("mac_address", "")).lower()
            if not mac:
                continue

            adapter = adapters_by_mac.setdefault(mac, {"mac": mac, "interface": "PLC"})
            adapter.update(
                {
                    "mac": mac,
                    "interface": adapter.get("interface", "PLC"),
                    "av_version": caps.get("av_version"),
                    "homeplug_oui": caps.get("oui"),
                    "backup_cco": caps.get("backup_cco"),
                    "proxy": caps.get("proxy"),
                    "implementation_version": caps.get("implementation_version"),
                }
            )

            if caps.get("oui") == "00b052":
                adapter["backend"] = "qca"
                adapter["manufacturer"] = _device_manufacturer(mac)
                adapter["chipset_vendor"] = "Qualcomm Atheros"
                try:
                    version_pkt = commands.qca_get_sw_version(self.interface, mac, timeout=cmd_timeout)
                    version = parsers.parse_qca_sw_version(version_pkt)
                    adapter.update(version)
                    adapter["manufacturer"] = _device_manufacturer(mac)
                    adapter["chipset_vendor"] = version.get("vendor", "Qualcomm Atheros")
                    adapter["hfid"] = version.get("firmware", "Unknown")
                except Exception:
                    adapter.setdefault("hfid", "Unknown")
                try:
                    attributes_pkt = commands.qca_get_op_attributes(
                        self.interface,
                        mac,
                        timeout=cmd_timeout,
                    )
                    if attributes_pkt is not None:
                        attributes = parsers.parse_qca_op_attributes(attributes_pkt)
                        adapter.update(attributes)
                        if attributes.get("device_family") and not adapter.get("chipset"):
                            adapter["chipset"] = attributes["device_family"]
                        if attributes.get("op_firmware_version") and not adapter.get("firmware"):
                            adapter["firmware"] = attributes["op_firmware_version"]
                except Exception:
                    pass
            else:
                adapter.setdefault("backend", "homeplug")
                adapter.setdefault("manufacturer", "Unknown")
                adapter.setdefault("hfid", "Unknown")

        return list(adapters_by_mac.values())

    def capabilities(self, *, timeout: float | None = None) -> Dict[str, Any]:
        pkt = commands.get_capabilities(self.interface, self.pla_mac, timeout=timeout or commands.DEFAULT_TIMEOUT)
        return parsers.parse_capabilities(pkt)

    def discover_list(self, *, timeout: float | None = None) -> Dict[str, Any]:
        if self.backend == "qca":
            return self.qca_network_info(timeout=timeout)
        pkt = commands.get_discover_list(self.interface, self.pla_mac, timeout=timeout or commands.DEFAULT_TIMEOUT)
        if pkt is None:
            return {"stations": []}
        return parsers.parse_discover_list(pkt)

    def network_stats(self, *, timeout: float | None = None) -> List[Dict[str, Any]]:
        cmd_timeout = timeout or commands.DEFAULT_TIMEOUT
        if self.backend == "qca":
            pkt = self._qca_get_network_info_packet(cmd_timeout)
            if pkt is None:
                return []
            return parsers.parse_qca_network_stats(pkt)

        if self.backend is None and self.pla_mac:
            try:
                pkt = self._qca_get_network_info_packet(cmd_timeout)
                if pkt is not None:
                    return parsers.parse_qca_network_stats(pkt)
            except Exception:
                pass

        pkt = commands.get_network_stats(self.interface, self.pla_mac, timeout=cmd_timeout)
        return parsers.parse_network_stats(pkt)

    def hfid(self, *, timeout: float | None = None) -> str:
        pkt = commands.get_hfid(self.interface, self.pla_mac, timeout=timeout or commands.DEFAULT_TIMEOUT)
        return parsers.parse_hfid(pkt)

    def id_info(self, *, timeout: float | None = None) -> Dict[str, str]:
        pkt = commands.get_id_info(self.interface, self.pla_mac, timeout=timeout or commands.DEFAULT_TIMEOUT)
        if pkt is None:
            return {}
        return parsers.parse_id_info(pkt)

    def network_info(self, *, timeout: float | None = None) -> List[Dict[str, Any]]:
        pkt = commands.get_network_info(self.interface, self.pla_mac, timeout=timeout or commands.DEFAULT_TIMEOUT)
        if pkt is None:
            return []
        return parsers.parse_network_info(pkt)

    def station_info(self, *, timeout: float | None = None) -> Dict[str, Any]:
        pkt = commands.get_station_info(self.interface, self.pla_mac, timeout=timeout or commands.DEFAULT_TIMEOUT)
        if pkt is None:
            return {}
        return parsers.parse_station_info(pkt)

    def qca_sw_version(self, *, timeout: float | None = None) -> Dict[str, Any]:
        pkt = commands.qca_get_sw_version(self.interface, self.pla_mac, timeout=timeout or commands.DEFAULT_TIMEOUT)
        return parsers.parse_qca_sw_version(pkt)

    def qca_network_info(self, *, timeout: float | None = None) -> Dict[str, Any]:
        pkt = self._qca_get_network_info_packet(timeout or commands.DEFAULT_TIMEOUT)
        if pkt is None:
            return _empty_qca_network_info(self.pla_mac)
        return parsers.parse_qca_network_info(pkt)

    def qca_network_info_stats(self, *, timeout: float | None = None) -> Dict[str, Any]:
        pkt = commands.qca_get_network_info_stats(
            self.interface,
            self.pla_mac,
            timeout=timeout or commands.DEFAULT_TIMEOUT,
        )
        if pkt is None:
            return {
                **_empty_qca_network_info(self.pla_mac),
                "first_tei": None,
                "num_stations_reported": 0,
                "in_avln": 0,
            }
        return parsers.parse_qca_network_info_stats(pkt)

    def qca_op_attributes(self, *, timeout: float | None = None) -> Dict[str, Any]:
        pkt = commands.qca_get_op_attributes(self.interface, self.pla_mac, timeout=timeout or commands.DEFAULT_TIMEOUT)
        return parsers.parse_qca_op_attributes(pkt)

    def qca_link_stats(
        self,
        peer_mac: str,
        *,
        direction: int = 2,
        lid: int = 0xF8,
        timeout: float | None = None,
    ) -> Dict[str, Any]:
        pkt = commands.qca_get_link_stats(
            self.interface,
            self.pla_mac,
            peer_mac=peer_mac,
            direction=direction,
            lid=lid,
            timeout=timeout or commands.DEFAULT_TIMEOUT,
        )
        if pkt is None:
            return {
                "source": self.pla_mac,
                "peer": peer_mac.lower(),
                "status": None,
                "direction": direction,
                "lid": lid,
                "tei": None,
                "tx": {},
                "rx": {},
            }
        parsed = parsers.parse_qca_link_stats(pkt)
        parsed.setdefault("peer", peer_mac.lower())
        return parsed

    def _qca_get_network_info_packet(self, timeout: float):
        for _ in range(2):
            pkt = commands.qca_get_network_info(self.interface, self.pla_mac, timeout=timeout)
            if pkt is not None:
                return pkt
        return None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def reset(self):
        commands.reset(self.interface, self.pla_mac)

    def restart(self):
        if self.backend == "qca":
            commands.qca_restart(self.interface, self.pla_mac)
            return
        if self.backend is None and self.pla_mac:
            try:
                pkt = commands.qca_restart(self.interface, self.pla_mac)
                if pkt is not None:
                    return
            except Exception:
                pass
        commands.restart(self.interface, self.pla_mac) 
