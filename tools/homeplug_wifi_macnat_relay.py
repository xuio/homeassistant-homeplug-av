#!/usr/bin/env python3
from __future__ import annotations

"""Relay HomePlug AV replies through macOS Wi-Fi MACNAT.

macOS vmnet bridging over Wi-Fi uses MACNAT. The VM can transmit HomePlug
management frames, but replies from adapters are addressed to the host Wi-Fi
MAC and never reach the guest. This relay copies only HomePlug AV frames
destined for the host MAC back onto the VM-side vmenet interface with the
destination rewritten to the VM MAC.

The guest still sees a normal Ethernet frame from the adapter MAC to its own
MAC, so pla-util/Home Assistant configuration remains identical to a later
wired bridged setup.
"""

import argparse
import json
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from scapy.all import Ether, Raw, get_if_hwaddr, sendp, sniff  # type: ignore


HOMEPLUG_ETHERTYPE = 0x88E1


def _normalize_mac(value: str) -> str:
    clean = value.strip().lower().replace("-", ":")
    if not re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", clean):
        raise argparse.ArgumentTypeError(f"Invalid MAC address: {value}")
    return clean


def _ifconfig(interface: str) -> str:
    return subprocess.check_output(["ifconfig", interface], text=True)


def _bridge_members(bridge: str) -> list[str]:
    try:
        output = _ifconfig(bridge)
    except subprocess.CalledProcessError:
        return []
    return re.findall(r"^\s*member:\s+(\S+)", output, flags=re.MULTILINE)


def _auto_vm_interface(wifi_interface: str, bridge: str) -> str:
    members = _bridge_members(bridge)
    candidates = [member for member in members if member != wifi_interface and member.startswith("vmenet")]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SystemExit(
            f"Could not find a vmenet member on {bridge}. Start the bridged VM first or pass --vm-if."
        )
    raise SystemExit(f"Multiple vmenet members on {bridge}: {', '.join(candidates)}. Pass --vm-if.")


@dataclass
class RelayStats:
    seen: int = 0
    forwarded: int = 0
    dropped: int = 0
    ignored: int = 0
    last_report: float = 0.0


@dataclass
class FaultConfig:
    drop_all: bool = False
    drop_sources: set[str] = field(default_factory=set)
    drop_percent: float = 0.0
    delay_ms: float = 0.0


class FaultController:
    """Runtime fault injection settings for relay hardening tests."""

    def __init__(
        self,
        *,
        static_config: FaultConfig,
        control_file: Path | None,
    ) -> None:
        self._static_config = static_config
        self._control_file = control_file
        self._control_mtime: float | None = None
        self._config = static_config

    def config(self) -> FaultConfig:
        if self._control_file is None:
            return self._static_config

        try:
            stat = self._control_file.stat()
        except FileNotFoundError:
            self._control_mtime = None
            self._config = self._static_config
            return self._config

        if self._control_mtime == stat.st_mtime:
            return self._config

        self._control_mtime = stat.st_mtime
        try:
            data = json.loads(self._control_file.read_text())
        except Exception as err:
            print(f"invalid control file {self._control_file}: {err}", file=sys.stderr, flush=True)
            self._config = self._static_config
            return self._config

        drop_sources = set(self._static_config.drop_sources)
        drop_sources.update(
            _normalize_mac(mac)
            for mac in data.get("drop_sources", [])
        )
        self._config = FaultConfig(
            drop_all=bool(data.get("drop_all", self._static_config.drop_all)),
            drop_sources=drop_sources,
            drop_percent=float(data.get("drop_percent", self._static_config.drop_percent)),
            delay_ms=float(data.get("delay_ms", self._static_config.delay_ms)),
        )
        print(f"fault config reloaded: {self._config}", flush=True)
        return self._config


def _drop_percent(value: str) -> float:
    percent = float(value)
    if percent < 0 or percent > 100:
        raise argparse.ArgumentTypeError("--drop-percent must be between 0 and 100")
    return percent


def run_relay(
    *,
    wifi_if: str,
    vm_if: str,
    host_mac: str,
    vm_mac: str,
    report_interval: float,
    fault_controller: FaultController,
) -> None:
    stats = RelayStats(last_report=time.monotonic())
    ignored_sources = {host_mac, vm_mac, "ff:ff:ff:ff:ff:ff"}

    print(
        f"Relaying HomePlug replies: wifi={wifi_if} host_mac={host_mac} "
        f"vm_if={vm_if} vm_mac={vm_mac}",
        flush=True,
    )

    def handle(pkt) -> None:  # type: ignore[no-untyped-def]
        if Ether not in pkt:
            return

        eth = pkt[Ether]
        if eth.type != HOMEPLUG_ETHERTYPE:
            return

        stats.seen += 1
        src = eth.src.lower()
        dst = eth.dst.lower()

        if dst != host_mac or src in ignored_sources:
            stats.ignored += 1
            return

        fault_config = fault_controller.config()
        if (
            fault_config.drop_all
            or src in fault_config.drop_sources
            or (
                fault_config.drop_percent > 0
                and random.random() * 100 < fault_config.drop_percent
            )
        ):
            stats.dropped += 1
            print(f"dropped {src} -> {host_mac} by fault injection", flush=True)
            return

        if fault_config.delay_ms > 0:
            time.sleep(fault_config.delay_ms / 1000)

        payload = bytes(eth.payload)
        frame = Ether(src=src, dst=vm_mac, type=HOMEPLUG_ETHERTYPE) / Raw(load=payload)
        sendp(frame, iface=vm_if, verbose=False)
        stats.forwarded += 1
        print(f"forwarded {src} -> {host_mac} as {src} -> {vm_mac} ({len(payload)} bytes)", flush=True)

        now = time.monotonic()
        if report_interval > 0 and now - stats.last_report >= report_interval:
            stats.last_report = now
            print(
                "stats "
                f"seen={stats.seen} forwarded={stats.forwarded} "
                f"dropped={stats.dropped} ignored={stats.ignored}",
                flush=True,
            )

    sniff(iface=wifi_if, store=False, prn=handle, filter=f"ether proto 0x{HOMEPLUG_ETHERTYPE:04x}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wifi-if", default="en1", help="macOS Wi-Fi interface used by vmnet bridging")
    parser.add_argument("--vm-if", help="VM-side vmenet interface. Defaults to the vmenet member of bridge100.")
    parser.add_argument("--bridge", default="bridge100", help="macOS vmnet bridge to inspect for --vm-if")
    parser.add_argument("--host-mac", type=_normalize_mac, help="Host Wi-Fi MAC. Defaults to --wifi-if MAC.")
    parser.add_argument("--vm-mac", required=True, type=_normalize_mac, help="Guest NIC MAC address")
    parser.add_argument("--report-interval", type=float, default=30.0)
    parser.add_argument("--drop-all", action="store_true", help="Drop all relayed HomePlug replies.")
    parser.add_argument(
        "--drop-source-mac",
        action="append",
        default=[],
        type=_normalize_mac,
        help="Drop relayed HomePlug replies from this adapter MAC. Can be passed multiple times.",
    )
    parser.add_argument(
        "--drop-percent",
        type=_drop_percent,
        default=0.0,
        help="Randomly drop this percentage of otherwise relayable HomePlug replies.",
    )
    parser.add_argument("--delay-ms", type=float, default=0.0, help="Delay forwarded replies by this many ms.")
    parser.add_argument(
        "--control-file",
        type=Path,
        help=(
            "Optional JSON file reloaded at runtime. Supports drop_all, "
            "drop_sources, drop_percent, and delay_ms."
        ),
    )
    args = parser.parse_args(argv)

    host_mac = args.host_mac or _normalize_mac(get_if_hwaddr(args.wifi_if))
    vm_if = args.vm_if or _auto_vm_interface(args.wifi_if, args.bridge)
    fault_controller = FaultController(
        static_config=FaultConfig(
            drop_all=args.drop_all,
            drop_sources=set(args.drop_source_mac),
            drop_percent=args.drop_percent,
            delay_ms=args.delay_ms,
        ),
        control_file=args.control_file,
    )

    try:
        run_relay(
            wifi_if=args.wifi_if,
            vm_if=vm_if,
            host_mac=host_mac,
            vm_mac=args.vm_mac,
            report_interval=args.report_interval,
            fault_controller=fault_controller,
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
