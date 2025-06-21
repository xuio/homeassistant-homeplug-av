# Homeplug AV Integration for Home Assistant

This custom integration discovers HomePlug-AV / AV2 adapters on your network, polls them for statistics, and exposes sensors, binary sensors and buttons in Home Assistant.

## Installation

1. Copy the `custom_components/homeplug_av` directory into your Home Assistant `custom_components` folder (or install via HACS using the _Custom Repository_ URL).
2. Restart Home Assistant.
3. Navigate to **Settings → Devices & Services → Add Integration** and search for **Homeplug AV**.
4. Select the network interface that connects to your power-line adapters and click **Submit**.

A device is created for each adapter it finds. Entities update every 30 seconds by default.

## Entities

| Entity Type | Example ID | Description |
|-------------|-----------|-------------|
| Online | `binary_sensor.powerline_adapter_1_online` | Connectivity status |
| Restart button | `button.powerline_adapter_1_restart` | Reboot the adapter |
| Interface | `sensor.powerline_adapter_1_interface` | Connection interface (MII0, PLC…) |
| HFID | `sensor.powerline_adapter_1_hfid` | Firmware string |
| MAC address | `sensor.powerline_adapter_1_mac` | Adapter MAC |
| TEI / SNID / CCo … | `sensor.powerline_adapter_1_tei` | Extended diagnostics |
| Mesh rate | `sensor.powerline_adapter_1_to_2_tx` | TX rate from Adapter 1 → Adapter 2 |

## Options

* **Scan interval** – seconds between polls (default 30). Adjustable in the integration options.
