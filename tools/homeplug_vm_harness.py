#!/usr/bin/env python3
from __future__ import annotations

"""Run repeatable Homeplug AV hardening tests inside the development VM.

The harness keeps Home Assistant configured exactly like the later Ethernet
setup: the integration runs inside the guest against one interface, usually
``enp0s1``. Host-side Wi-Fi relay fault injection is optional and controlled by
writing JSON to the relay control file.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_REMOTE = "codex@192.168.178.176"
DEFAULT_REMOTE_REPO = "/home/codex/homeassistant-powerline-stats"
DEFAULT_HA_CONFIG = "/home/codex/ha-homeplug-soak"
DEFAULT_HA_STDOUT = "/home/codex/ha-homeplug-soak/ha-stdout.log"


@dataclass
class HarnessConfig:
    remote: str
    remote_repo: str
    ha_config: str
    interface: str
    scan_interval_seconds: int
    adapter_retention_seconds: int
    link_retention_seconds: int
    qca_diagnostic_interval_seconds: int
    duration_seconds: int
    sample_interval_seconds: int
    restart_interval_seconds: int
    sync: bool
    local_repo: Path
    relay_control_file: Path | None
    fault_scenario: str
    ssh_extra_args: list[str]


def _run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        result.check_returncode()
    return result


def _run_stdin(
    cmd: list[str],
    stdin: str,
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        input=stdin,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        result.check_returncode()
    return result


def _remote_quote(value: str) -> str:
    return shlex.quote(value)


def sync_repo(config: HarnessConfig) -> None:
    if not config.sync:
        return
    excludes = [
        ".git/",
        ".venv/",
        ".pytest_cache/",
        "__pycache__/",
        "*.pyc",
        ".DS_Store",
        "._*",
    ]
    cmd = ["rsync", "-az", "--delete"]
    for exclude in excludes:
        cmd.extend(["--exclude", exclude])
    ssh_cmd = "ssh " + " ".join(shlex.quote(arg) for arg in config.ssh_extra_args)
    cmd.extend(["-e", ssh_cmd, f"{config.local_repo}/", f"{config.remote}:{config.remote_repo}/"])
    env = dict(os.environ)
    env["COPYFILE_DISABLE"] = "1"
    subprocess.run(cmd, cwd=config.local_repo, env=env, check=True)

    _run_stdin(
        ["ssh", *config.ssh_extra_args, config.remote, "bash -s"],
        f"""
set -euo pipefail
cd {_remote_quote(config.remote_repo)}
sudo find . -path ./.venv -prune -o \\( -name '._*' -o -name '.DS_Store' -o -name '__pycache__' -o -name '*.pyc' -o -name '.pytest_cache' \\) -exec rm -rf {{}} +
""",
    )


def setup_homeassistant(config: HarnessConfig) -> None:
    remote_repo = _remote_quote(config.remote_repo)
    ha_config = _remote_quote(config.ha_config)
    interface = _remote_quote(config.interface)
    _run_stdin(
        ["ssh", *config.ssh_extra_args, config.remote, "bash -s"],
        f"""
set -euo pipefail
sudo rm -rf {ha_config}
mkdir -p {ha_config}/.storage
ln -s {remote_repo}/custom_components {ha_config}/custom_components
cat > {ha_config}/configuration.yaml <<'YAML'
homeassistant:
  name: HomePlug Soak
  latitude: 0
  longitude: 0
  elevation: 0
  unit_system: metric
  time_zone: Europe/Berlin

logger:
  default: warning
  logs:
    custom_components.homeplug_av: debug
    pla_util_py: warning
    homeassistant.config_entries: info
    homeassistant.helpers.entity_registry: debug

recorder:
  purge_keep_days: 1
YAML
cd {remote_repo}
.venv/bin/python - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from homeassistant.config_entries import ConfigEntry

config_dir = Path({config.ha_config!r})
now = datetime.now(timezone.utc)
entry = ConfigEntry(
    version=2,
    minor_version=1,
    domain="homeplug_av",
    title="Homeplug AV ({config.interface})",
    data={{
        "interface": {config.interface!r},
        "scan_interval": {config.scan_interval_seconds},
    }},
    options={{
        "scan_interval": {config.scan_interval_seconds},
        "adapter_retention_seconds": {config.adapter_retention_seconds},
        "link_retention_seconds": {config.link_retention_seconds},
        "qca_diagnostic_interval_seconds": {config.qca_diagnostic_interval_seconds},
    }},
    source="user",
    unique_id={config.interface!r},
    discovery_keys=MappingProxyType({{}}),
    entry_id="homeplug_av_{config.interface.replace('-', '_')}",
    created_at=now,
    modified_at=now,
    disabled_by=None,
    pref_disable_new_entities=False,
    pref_disable_polling=False,
)
(config_dir / ".storage" / "core.config_entries").write_text(json.dumps({{
    "version": 1,
    "minor_version": 1,
    "key": "core.config_entries",
    "data": {{"entries": [entry.as_dict()]}},
}}, indent=2))
PY
""",
    )


def stop_ha(config: HarnessConfig) -> None:
    _run_stdin(
        ["ssh", *config.ssh_extra_args, config.remote, "bash -s"],
        f"""
set -euo pipefail
pattern='[h]ass -c {config.ha_config}'
pids=$(pgrep -f "$pattern" || true)
if [ -n "$pids" ]; then
  sudo kill -INT $pids || true
fi
for _ in $(seq 1 60); do
  sleep 1
  pids=$(pgrep -f "$pattern" || true)
  [ -z "$pids" ] && exit 0
done
sudo kill $pids || true
""",
        check=False,
    )


def start_ha(config: HarnessConfig) -> None:
    remote_repo = _remote_quote(config.remote_repo)
    ha_config = _remote_quote(config.ha_config)
    stdout = _remote_quote(DEFAULT_HA_STDOUT)
    _run_stdin(
        ["ssh", *config.ssh_extra_args, config.remote, "bash -s"],
        f"""
set -euo pipefail
mkdir -p {ha_config}
cd {remote_repo}
nohup sudo .venv/bin/hass -c {ha_config} --debug >>{stdout} 2>&1 </dev/null &
echo $! >/tmp/ha-homeplug.pid
for _ in $(seq 1 120); do
  sleep 1
  if pgrep -f '[h]ass -c {config.ha_config}' >/dev/null && grep -q 'Collected mesh data' {stdout}; then
    exit 0
  fi
done
tail -120 {stdout} >&2
exit 1
""",
    )


def sample(config: HarnessConfig) -> dict[str, Any]:
    stdout = DEFAULT_HA_STDOUT
    result = _run_stdin(
        ["ssh", *config.ssh_extra_args, config.remote, "bash -s"],
        f"""
sudo python3 - <<'PY'
import json
import re
import sqlite3
import subprocess
from pathlib import Path

ha_config = Path({config.ha_config!r})
stdout = Path({stdout!r})
registry_path = ha_config / ".storage" / "core.entity_registry"
db_path = ha_config / "home-assistant_v2.db"

registry_entities = 0
registry_unique_ids = 0
registry_macs = []
if registry_path.exists():
    data = json.loads(registry_path.read_text())
    ents = [
        ent
        for ent in data.get("data", {{}}).get("entities", [])
        if ent.get("platform") == "homeplug_av"
    ]
    registry_entities = len(ents)
    registry_unique_ids = len({{ent.get("unique_id") for ent in ents}})
    registry_macs = sorted(
        {{
            match.group(1)
            for ent in ents
            if (match := re.match(r"powerline_([0-9a-f]{{2}}(?::[0-9a-f]{{2}}){{5}})_", ent.get("unique_id") or ""))
        }}
    )

rate_states = []
zero_rate_rows = 0
state_rows = 0
if db_path.exists():
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    state_rows = cur.execute("select count(*) from states").fetchone()[0]
    rate_states = cur.execute('''
        select sm.entity_id, s.state, datetime(s.last_updated_ts, 'unixepoch')
        from states s join states_meta sm on s.metadata_id=sm.metadata_id
        where (sm.entity_id like 'sensor.adapter_%_tx' or sm.entity_id like 'sensor.adapter_%_rx')
          and s.state_id in (
            select max(s2.state_id)
            from states s2 join states_meta sm2 on s2.metadata_id=sm2.metadata_id
            where sm2.entity_id=sm.entity_id
        )
        order by sm.entity_id
    ''').fetchall()
    zero_rate_rows = cur.execute('''
        select count(*)
        from states s join states_meta sm on s.metadata_id=sm.metadata_id
        where (sm.entity_id like 'sensor.adapter_%_tx' or sm.entity_id like 'sensor.adapter_%_rx')
          and s.state='0'
    ''').fetchone()[0]
    con.close()

log_text = stdout.read_text(errors="replace") if stdout.exists() else ""
process = subprocess.run(
    ["pgrep", "-f", "[h]ass -c {config.ha_config}"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
).returncode == 0

print(json.dumps({{
    "process_running": process,
    "registered_new": sum(
        1
        for line in log_text.splitlines()
        if "Registered new " in line and "homeplug_av entity" in line
    ),
    "collected_mesh": log_text.count("Collected mesh data"),
    "no_reply": log_text.count("No reply received"),
    "errors": sum(log_text.count(token) for token in ("ERROR", "Traceback", "UpdateFailed")),
    "registry_entities": registry_entities,
    "registry_macs": registry_macs,
    "registry_unique_ids": registry_unique_ids,
    "state_rows": state_rows,
    "zero_rate_rows": zero_rate_rows,
    "rate_states": rate_states,
}}, sort_keys=True))
PY
""",
    )
    return json.loads(result.stdout.strip())


def write_fault(config: HarnessConfig, payload: dict[str, Any]) -> None:
    if config.relay_control_file is None:
        return
    config.relay_control_file.write_text(json.dumps(payload, indent=2))


def fault_payload(config: HarnessConfig, elapsed: float) -> dict[str, Any]:
    if config.fault_scenario == "none":
        return {}
    if config.fault_scenario == "drop-all-2m":
        return {"drop_all": 60 <= elapsed < 180}
    if config.fault_scenario == "loss-30":
        return {"drop_percent": 30}
    raise ValueError(f"Unknown fault scenario: {config.fault_scenario}")


def run_harness(config: HarnessConfig) -> int:
    sync_repo(config)
    stop_ha(config)
    setup_homeassistant(config)
    write_fault(config, {})
    start_ha(config)

    start = time.monotonic()
    next_restart = start + config.restart_interval_seconds if config.restart_interval_seconds else None
    failures: list[str] = []
    samples: list[dict[str, Any]] = []
    registered_new_baseline: int | None = None
    registry_entity_baseline: int | None = None
    registry_macs_baseline: set[str] = set()
    index = 0

    try:
        while True:
            elapsed = time.monotonic() - start
            write_fault(config, fault_payload(config, elapsed))

            if next_restart is not None and elapsed >= next_restart - start:
                stop_ha(config)
                start_ha(config)
                next_restart += config.restart_interval_seconds

            current = sample(config)
            current["sample"] = index
            current["elapsed_seconds"] = round(elapsed, 1)
            samples.append(current)
            print(json.dumps(current, sort_keys=True), flush=True)

            if registered_new_baseline is None:
                registered_new_baseline = current["registered_new"]
            if current["registry_entities"] > 0 and registry_entity_baseline is None:
                registry_entity_baseline = current["registry_entities"]
                registry_macs_baseline = set(current["registry_macs"])
                registered_new_baseline = current["registered_new"]

            registry_macs = set(current["registry_macs"])
            new_registry_macs = registry_macs - registry_macs_baseline
            accepted_new_registry_macs = bool(new_registry_macs)

            if not current["process_running"]:
                failures.append("homeassistant stopped")
            if current["registry_entities"] != current["registry_unique_ids"]:
                failures.append("duplicate entity unique ids")
            if (
                registry_entity_baseline is not None
                and current["registered_new"] > registered_new_baseline
                and not accepted_new_registry_macs
            ):
                failures.append("new entity registrations after startup")
            if (
                current["registry_entities"] > 0
                and registered_new_baseline is not None
                and current["registry_entities"] != registered_new_baseline
                and not accepted_new_registry_macs
            ):
                failures.append("entity registry count differs from startup registrations")
            if (
                current["registry_entities"] > 0
                and registry_entity_baseline is not None
                and current["registry_entities"] != registry_entity_baseline
                and not accepted_new_registry_macs
            ):
                failures.append("entity registry count changed after startup")
            if current["zero_rate_rows"] != 0:
                failures.append("zero rate rows recorded")
            if current["errors"] != 0:
                failures.append("homeassistant errors logged")

            if accepted_new_registry_macs:
                registered_new_baseline = current["registered_new"]
                registry_entity_baseline = current["registry_entities"]
                registry_macs_baseline = registry_macs

            index += 1
            if elapsed >= config.duration_seconds:
                break
            time.sleep(config.sample_interval_seconds)
    finally:
        write_fault(config, {})

    summary = {
        "event": "summary",
        "samples": len(samples),
        "duration_seconds": round(time.monotonic() - start, 1),
        "failures": sorted(set(failures)),
        "final": samples[-1] if samples else None,
    }
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 1 if failures else 0


def parse_args(argv: list[str] | None = None) -> HarnessConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--remote-repo", default=DEFAULT_REMOTE_REPO)
    parser.add_argument("--ha-config", default=DEFAULT_HA_CONFIG)
    parser.add_argument("--interface", default="enp0s1")
    parser.add_argument("--scan-interval-seconds", type=int, default=10)
    parser.add_argument("--adapter-retention-seconds", type=int, default=3600)
    parser.add_argument("--link-retention-seconds", type=int, default=300)
    parser.add_argument("--qca-diagnostic-interval-seconds", type=int, default=300)
    parser.add_argument("--duration-minutes", type=float, default=30)
    parser.add_argument("--sample-interval-seconds", type=int, default=60)
    parser.add_argument("--restart-interval-minutes", type=float, default=0)
    parser.add_argument("--no-sync", action="store_true")
    parser.add_argument("--relay-control-file", type=Path)
    parser.add_argument(
        "--fault-scenario",
        choices=("none", "drop-all-2m", "loss-30"),
        default="none",
    )
    parser.add_argument(
        "--ssh-known-hosts",
        default="/tmp/homeplug-direct-known_hosts",
        help="Known-hosts file used for the development VM SSH connection.",
    )
    args = parser.parse_args(argv)

    return HarnessConfig(
        remote=args.remote,
        remote_repo=args.remote_repo,
        ha_config=args.ha_config,
        interface=args.interface,
        scan_interval_seconds=args.scan_interval_seconds,
        adapter_retention_seconds=args.adapter_retention_seconds,
        link_retention_seconds=args.link_retention_seconds,
        qca_diagnostic_interval_seconds=args.qca_diagnostic_interval_seconds,
        duration_seconds=int(args.duration_minutes * 60),
        sample_interval_seconds=args.sample_interval_seconds,
        restart_interval_seconds=int(args.restart_interval_minutes * 60),
        sync=not args.no_sync,
        local_repo=Path.cwd(),
        relay_control_file=args.relay_control_file,
        fault_scenario=args.fault_scenario,
        ssh_extra_args=[
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            f"UserKnownHostsFile={args.ssh_known_hosts}",
        ],
    )


def main(argv: list[str] | None = None) -> int:
    return run_harness(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
