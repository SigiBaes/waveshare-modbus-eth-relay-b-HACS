# Waveshare Modbus POE ETH Relay (B) — Home Assistant Integration

A [HACS](https://hacs.xyz/) custom integration for the **Waveshare Modbus POE ETH Relay (B)** board. This board exposes 8 digital inputs and 8 relay outputs, communicated with over **Modbus TCP**.

This assumes the board has been setup using the [Windows Vircom software from Waveshare](https://www.waveshare.com/wiki/Modbus_POE_ETH_Relay_(B)). Required board settings:

- Working mode: **TCP Server**
- Transfer Protocol: **Modbus TCP protocol** (device port 502). Do not leave the factory default “None” (Modbus RTU on a raw TCP socket) — this integration speaks Modbus TCP MBAP.
- Modbus Gateway Type: **Multi-host non-storage**. The factory storage gateway injects extra queries and the MCU can stop answering.
- All eight relay control modes: **Normal**. Linkage mode ignores Modbus writes (commands ACK, coils do not change).
- Slave / unit ID: **1** (the board is identified by IP).

## Why This Integration?

Home Assistant's built-in Modbus integration polls each entity with a separate network request. With 8 inputs and 8 relays that means up to 16 round-trips per poll cycle.

This integration reads all 8 inputs in **one** Modbus request and all 8 relays in **one** Modbus request — two requests per poll cycle regardless of how many entities are in use. Writes use Modbus function 15 (write multiple coils) for all 8 relays in **one** packet, then function 01 to confirm. No MQTT broker, no external daemon, no additional services. It uses `local_polling` via [pymodbus](https://github.com/pymodbus-dev/pymodbus) and communicates directly with the board over your local network.

## Installation via HACS

1. Open HACS in Home Assistant.
2. Click the three-dot menu (⋮) in the top-right corner and choose **Custom repositories**.
3. Add the repository URL: `https://github.com/SigiBaes/waveshare-modbus-eth-relay-b-HACS`
4. Set the category to **Integration** and click **Add**.
5. Find **Waveshare Modbus Relay B** in the HACS integration list and click **Download**.
6. Restart Home Assistant.

Do not also add the stock `sacherjj` repository for the same integration — a HACS update from stock would drop these write changes.

## Adding the Integration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Waveshare Modbus Relay B** and select it.
3. Fill in the configuration form:
   - **Host** — IP address or hostname of the board.
   - **Port** — Modbus TCP port (default: `502`).
   - **Scan Interval** — How often to poll the board, in **milliseconds** (default: `300`, minimum `50`).
4. Click **Submit**. Home Assistant will create a new device with all 16 entities.

Options (Configure on the integration entry):

- **Scan interval (milliseconds)** — default `300`.
- **Restore commanded relays if the board disagrees** — default on. After a reconnect this always happens. On a normal poll mismatch, turning this off publishes hardware and does **not** write.
- **Optimistic dashboard switches** — default off. When on, Lovelace may flip immediately; the coordinator still waits for the board confirm and snaps back on failure.

### Scan interval migration

Config entries created by stock HACS `0.1.x` used seconds (default `5`). On first load this fork migrates `1`–`60` as seconds × 1000 (`5` → `5000` ms). Values already above `60` are treated as milliseconds, so an EVW-patched `300` stays `300` ms. After that the form is milliseconds only — typing `20` means 20 ms and is rejected (below 50).

## Entity Model

Each configured board appears as a single **device** in Home Assistant with 16 entities:

| Platform | Entities | Naming |
|---|---|---|
| `binary_sensor` | Input 1 – Input 8 | 1-based channel number |
| `switch` | Relay 1 – Relay 8 | 1-based channel number |

Relay `is_on` is the last confirmed coil read, not an optimistic guess (unless the optimistic option is on). A Lovelace toggle still writes **all eight** coils so unused channels cannot drift.

The Modbus unit/slave ID is fixed at **1**. There is no configuration field for it.

## Writes

Every relay change is:

1. Update the per-board command image (one bit from a switch, or all eight from `set_relays`).
2. If the image already matches the last confirm, **do not** hit the wire.
3. Otherwise `write_coils` (FC15) of exactly 8 bits at address 0, then `read_coils` (FC01).
4. Publish entity state from that confirm. Do not wait for the next poll.

Single-coil `write_coil` (FC05) is not used.

Action **Set all relays** (`waveshare_relay_b.set_relays`): eight checkboxes in Developer Tools → Actions, or a `states` list of 8 booleans for automations/plants. **0.2.1:** targeting uses `device_id` from service data (Home Assistant merges websocket `target` into `call.data`; `ServiceCall` has no `.target`). Actions UI `target.device` still works. **0.2.2:** after idle, a half-open TCP socket is closed and reconnected on the first IO retry so entities update from the write confirm instead of a later poll. On success the action returns:

```json
{"relays": [{"device_id": "<ha-device-id>", "states": [false, false, true, false, false, false, false, false]}]}
```

## Timeout, retry, many boards

- pymodbus socket timeout is **1 second**, `retries=0`, no library auto-reconnect.
- IO / connection errors are retried **once** in the integration. A confirm mismatch is **not** retried.
- One config entry per board. Twenty boards are twenty TCP clients and may write in parallel.
- The first poll of each board is staggered with `zlib.crc32(host)` (not Python `hash()`), outside the IO lock.
- A poll cycle is skipped while a command is in flight (after the first successful poll).
- Do **not** add Home Assistant’s core `modbus:` hub on the same board IPs. Two masters on one slave will fight.
- Plant callers must set `return_response: true` (websocket) or the REST equivalent, or there is no `relays[].states` payload.

## Plant contract (EVW simulator — follow-up in that repo, not this one)

1. Call `waveshare_relay_b.set_relays` once per island with the **full 8-bit word** (unused channels = last desired / off, never omitted).
2. Fan out islands with `Promise.all` (or equivalent). Never 20 sequential awaits.
3. Prefer `data.states: [8 booleans]` plus `target.device_id` (or `device_id` in the payload).
4. On success, use the action **response** `relays[].states` as confirm. Do **not** `waitForHaState` / `get_states` in a 20 ms loop.
5. Do not use `switch.turn_on` / `switch.turn_off` for Hardwaretest aux.
6. Complementary Gesloten/Geopend are two bits in the **same** word.

## Out of Scope

- **Relay toggle/flash** — requires non-standard Modbus PDUs not supported by pymodbus out of the box.
- **Per-channel control mode / linkage** — board-specific register configuration.
- **Multiple boards per config entry** — add a separate integration entry for each board.
- **TLS / authentication** — the board does not support encrypted Modbus TCP.
- **Bitmask sensor** — eight switches remain the UI; a bitmask is not faster for the plant.
- **FC05 hybrid** — mixing single-coil writes with FC15 reintroduces clobber without a mailbox.

## Requirements

- Home Assistant 2024.6.0 or later.
- The board must be reachable from the Home Assistant host over TCP port 502 (or your configured port).
- pymodbus 3.11.2 or later (installed automatically by Home Assistant).

## License

MIT — see [LICENSE](LICENSE).
