# Waveshare Modbus POE ETH Relay (B) — Home Assistant Integration

A [HACS](https://hacs.xyz/) custom integration for the **Waveshare Modbus POE ETH Relay (B)** board. This board exposes 8 digital inputs and 8 relay outputs, communicated with over **Modbus TCP**.

This assumes the board has been setup using the [unfortunately Windows only software from Waveshare](https://www.waveshare.com/wiki/Modbus_POE_ETH_Relay_(B)?srsltid=AfmBOoorFDiB3OOl3PYBpNywF2-0d743o0mcKD34BLKNJPC2Z7j-8hCw). Configured to RTU over TCP on Port 502.

## Why This Integration?

Home Assistant's built-in Modbus integration polls each entity with a separate network request. With 8 inputs and 8 relays that means up to 16 round-trips per poll cycle.

This integration reads all 8 inputs in **one** Modbus request and all 8 relays in **one** Modbus request — two requests per poll cycle regardless of how many entities are in use. No MQTT broker, no external daemon, no additional services. It uses `local_polling` via [pymodbus](https://github.com/pymodbus-dev/pymodbus) and communicates directly with the board over your local network. When trying to use inputs for responsive interaction (PID, door open, etc.) polling at 1 s or faster is needed.

## Installation via HACS

1. Open HACS in Home Assistant.
2. Click the three-dot menu (⋮) in the top-right corner and choose **Custom repositories**.
3. Add the repository URL: `https://github.com/sacherjj/waveshare-modbus-eth-relay-b-HACS`
4. Set the category to **Integration** and click **Add**.
5. Find **Waveshare Modbus Relay B** in the HACS integration list and click **Download**.
6. Restart Home Assistant.

## Adding the Integration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Waveshare Modbus Relay B** and select it.
3. Fill in the configuration form:
   - **Host** — IP address or hostname of the board.
   - **Port** — Modbus TCP port (default: `502`).
   - **Scan Interval** — How often to poll the board, in seconds (default: `5`).
4. Click **Submit**. Home Assistant will create a new device with all 16 entities.

The scan interval can be changed later via **Settings → Devices & Services**, selecting the integration, and clicking **Configure**.

## Entity Model

Each configured board appears as a single **device** in Home Assistant with 16 entities:

| Platform | Entities | Naming |
|---|---|---|
| `binary_sensor` | Input 1 – Input 8 | 1-based channel number |
| `switch` | Relay 1 – Relay 8 | 1-based channel number |

Relay state is confirmed by a read-back after every write — the switch entity reflects the actual hardware state, not just the requested state.

The Modbus unit/slave ID is fixed at **1**, which matches the factory default for this board. There is no configuration field for it in v1.

## Out of Scope for v1

The following features are intentionally not implemented in this release:

- **Relay toggle/flash** — requires non-standard Modbus PDUs not supported by pymodbus out of the box.
- **Per-channel control mode / linkage** — board-specific register configuration.
- **Multiple boards per config entry** — add a separate integration entry for each board.
- **TLS / authentication** — the board does not support encrypted Modbus TCP.

## Requirements

- Home Assistant 2024.6.0 or later.
- The board must be reachable from the Home Assistant host over TCP port 502 (or your configured port).
- pymodbus 3.13.1 or later (installed automatically by Home Assistant).

## License

MIT — see [LICENSE](LICENSE).
