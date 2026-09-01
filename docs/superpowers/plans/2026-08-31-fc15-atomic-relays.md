# Atomic FC15 Relay Writes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Do not start implementation until the user explicitly asks to execute this plan.**

**Goal:** Make every relay write on a Waveshare board a single Modbus function-15 packet of all 8 coils, confirmed by a coil read-back, so Lovelace, the Actions UI, and external callers (the EVW plant) cannot tear, desync, or race — including with 20+ boards.

**Architecture:** Keep one config entry = one board = one TCP client. Put the command image, skip-if-unchanged, FC15+FC01 confirm, IO retry, poll/restore policy, and payload parse in a Home-Assistant-free module (`relay_write.py`). The coordinator holds a **per-board** `asyncio.Lock`, skips a poll when a write is waiting, publishes entity state only from confirmed hardware (unless the user enables optimistic Lovelace), and never takes a process-wide lock. Eight `switch` entities stay the day-to-day UI. `waveshare_relay_b.set_relays` is the atomic full-word UI and the plant API; it **returns the confirmed bits**.

**Tech Stack:** Home Assistant custom integration (`waveshare_relay_b`), pymodbus `AsyncModbusTcpClient` (`write_coils` = FC 0x0F, `read_coils` = FC 0x01, `timeout=1`, `retries=0`, `reconnect_delay=0`), pytest logic tests (no HA install), existing `helpers.coalesce_bits` / `any_error`.

## Global Constraints

- Reliability before latency. Never skip the FC01 read-back on a real write. Never read-modify-write from a poll snapshot. Default Lovelace is **not** optimistic (`assumed_state` false unless the option is on).
- Every relay write is `write_coils(0, exactly_8_bools, device_id=1)`. `write_coil` (FC05) is forbidden after this change.
- One `asyncio.Lock` **per coordinator / board**. No global lock. 20 boards = 20 independent sockets that may FC15 in parallel.
- Entity `is_on` comes from `coordinator.data["relays"]` after a successful read. Optimistic option only changes the **frontend** (`_attr_assumed_state`); the coordinator still publishes from confirm and snaps back on failure.
- Scan interval is **milliseconds**. New default `300`. Clamp `50`–`60000`. Config entry **version 2**: migrate v1 once with the legacy heuristic (`1`–`60` → seconds × 1000; `> 60` already ms). After migration, values are ms only — never multiply again.
- Skip the Modbus write when desired bits already equal last confirmed bits.
- Do not call `async_request_refresh()` after a write. Update coordinator data from the confirm read. `always_update=False` so unchanged bits emit no `state_changed`.
- Do not read discrete inputs on the write path (write = FC15 + FC01 only).
- Sleep for poll stagger **outside** the IO lock. Stagger uses `zlib.crc32(host)` (stable), not `hash()` (salted per process).
- If a command is in flight and the coordinator already has data, **skip that poll cycle** (do not wait on the lock). First refresh must still poll.
- Restore commanded coils **always** after a reconnect. On a normal poll mismatch, restore only when option `restore_on_mismatch` is true (default **true** for this HIL fork). When false: publish hardware, keep desired, do not FC15 on that poll.
- Clear `_force_restore` **only after a successful restore** (or after deciding not to restore). If `_flush()` fails, leave the flag set so the next poll retries.
- One extra attempt (`attempts=2`) on `RelayIoError` / connection / pymodbus errors, in **`retry_io` only**. Do not wrap `flush_mailbox` in a second `retry_io` in the coordinator. **Never retry `RelayMismatchError`.**
- pymodbus client: `timeout=1`, `retries=0`, `reconnect_delay=0`. The library defaults (`retries=3`, auto-reconnect backoff up to 300 s) would stack with our retry and hide dead boards. Fail fast; our layer retries once.
- After push, tag GitHub **`v0.2.0`** so HACS can pin/roll back. Do not rely on `main` alone.
- `set_relays` uses a **simple** voluptuous schema (optional `device_id` + eight booleans **or** `states`). Do not use `make_entity_service_schema`. Device selection uses `device_ids_from_service_call`: Home Assistant **does not** put a `.target` on `ServiceCall` (Bugbot's 2026-08-31 note was wrong about the API). Websocket `target.device_id` is merged into `call.data`; read that, and `getattr(call, "target", None)` if a future HA exposes it. Register with `supports_response=SupportsResponse.OPTIONAL` and return confirmed bits.
- Lovelace switches remain the primary UI for single-channel toggles. Developer Tools → Actions is the full-word UI. Both paths use the same mailbox + FC15 flush.
- Tests stay HA-free. Do not import `coordinator.py` or `__init__.py` from pytest.
- pymodbus requirement stays `pymodbus>=3.11.2`. HA floor stays `2024.6.0`.
- Domain, `unique_id` pattern `{entry_id}_relay_{n}` / `{entry_id}_input_{n}`, device identifier `(waveshare_relay_b, entry_id)`, unit id `1`, and eight+eight entities do not change.
- English identifiers and log messages. Do not rename entities.
- Keyword-only helpers stay keyword-only in tests (`should_skip_poll(...)`, `should_restore_after_poll(..., mismatch=...)`). Do not call them positionally.
- Commit after each task with a focused message. Do not force-push. Do not change git config.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `custom_components/waveshare_relay_b/relay_write.py` | **New.** Mailbox, FC15+FC01, IO retry, skip-poll predicate, restore policy helper, payload parse, v1 scan migrate + ms clamp, poll stagger. |
| `tests/test_relay_write.py` | **New.** Fake board + tests for every HA-free scenario. |
| `pytest.ini` | **New.** `asyncio_mode = auto` so `@pytest.mark.asyncio` tests run. |
| `custom_components/waveshare_relay_b/helpers.py` | Unchanged. |
| `custom_components/waveshare_relay_b/const.py` | Defaults, option keys, service name, timeout, config version. |
| `custom_components/waveshare_relay_b/coordinator.py` | Lock, command-in-flight counter, skip poll, restore flags, `always_update=False`, `async_set_relays` → `list[bool]`. |
| `custom_components/waveshare_relay_b/switch.py` | `_attr_assumed_state` from coordinator `optimistic`. |
| `custom_components/waveshare_relay_b/__init__.py` | `async_migrate_entry`; simple `set_relays` schema; response payload; `AsyncModbusTcpClient(..., timeout=1)`. |
| `custom_components/waveshare_relay_b/config_flow.py` | `VERSION = 2`; options: scan ms, restore_on_mismatch, optimistic. |
| `custom_components/waveshare_relay_b/services.yaml` | Device target + `relay_1`…`relay_8`. |
| `custom_components/waveshare_relay_b/strings.json` | Milliseconds; options; service fields. |
| `custom_components/waveshare_relay_b/translations/en.json` | Same as strings. |
| `custom_components/waveshare_relay_b/manifest.json` | Version `0.2.0`; fork URLs. |
| `README.md` | FC15, UI, options, 20+ boards, plant contract, HACS URL of this fork. |
| `requirements-dev.txt` | Add `pytest-asyncio`. |

`binary_sensor.py` is untouched.

---

## Scenarios the design must survive

| # | Scenario | Anticipation |
| --- | --- | --- |
| S1 | Lovelace toggles Relay 3 | `set_bit(2, True)` then FC15 of **all 8** desired bits. |
| S2 | Two switches clicked on the same board | Per-board lock serializes. Second applies its bit on top, then FC15. |
| S3 | Plant `set_relays` while a click is in flight | Click waits. Plant `set_all` + flush returns confirmed bits. Click then mutates that word. |
| S4 | Poll scheduled during a write | `should_skip_poll(command_in_flight=True, has_data=True)` → return last `data`, **do not** take the lock. First refresh (`has_data=False`) still waits and polls. |
| S5 | FC15 ack but coils read back wrong | `RelayMismatchError`. No publish. **No retry.** Desired stays. |
| S6 | TCP drop / `RelayIoError` on write | Retry **once**. If still failing, no publish. Timeout is 1 s per attempt. |
| S7 | HA restart / first connect | First poll adopts hardware. No slam of all-off. |
| S8 | Reconnect after a blip, hardware ≠ desired | `_force_restore=True` after `connect()`. Next poll always `flush_mailbox` if mismatch. |
| S9 | Unused channels | Word is always 8. Unspecified bits are last desired. |
| S10 | Complementary aux | Both bits in one `set_relays` word. |
| S11 | `set_relays` same as confirmed | No Modbus. Return current bits in the action response. |
| S12 | 20 boards in one plant step | Parallel `set_relays` per island. Integration never queues board A behind B. |
| S13 | 20 boards polling | First poll sleeps `poll_stagger_ms(host, interval)` **outside** the lock (`crc32`). |
| S14a | Poll mismatch, `restore_on_mismatch=True` (default) | `flush_mailbox` restores command. Publish the **repaired** word (do not flash Lovelace with the mismatched poll). Log a warning. |
| S14b | Poll mismatch, `restore_on_mismatch=False` | Publish hardware. Keep desired. **No** FC15 on that poll. |
| S15 | Rapid identical `set_relays` | S11. Polls continue. |
| S16 | Actions UI eight checkboxes | `parse_set_relays_payload` from `relay_1`…`relay_8`. |
| S17 | Missing a relay field (and no `states`) | Validation error. No write. |
| S18 | v1 entry `scan_interval: 5` vs `300` | `migrate_scan_interval_from_v1(5)==5000`, `(300)==300`. Stored as ms in v2. |
| S19 | User types `20` meaning ms on a **v2** form | Clamp only → `20` is below min 50 → `ValueError`. Legacy `20` **only** during v1 migrate → `20000`. |
| S20 | Second HA `modbus:` hub on the same IP | README: do not. |
| S21 | Action return | `{"relays": [{"device_id": "…", "states": [8 bools]}, …]}`. Plant must not `get_states` to confirm. |
| S22 | Optimistic option on | Switch `_attr_assumed_state is True`. Coordinator still confirms; failed write leaves last confirmed `is_on`. |
| S23 | Unchanged coordinator data | `always_update=False` → no eight `state_changed` spam. |

Latency that **is** allowed:

- Skip write when unchanged (S11).
- Write path is 2 PDUs (no DI, no extra refresh poll).
- Publish from confirm (~40–50 ms), not the next 300 ms poll.
- Skip poll while a command is in flight (S4).
- Parallelism across boards (S12).
- Poll stagger (S13).
- 1 s socket timeout; one IO retry only.

Latency that is **forbidden**: skipping FC01 on a real write; retrying mismatches; global lock; RMW; fire-and-forget; waiting 5 s per dead board; stacking pymodbus library retries on top of `retry_io`.

---

## Board prerequisites (Waveshare wiki)

Source: [Modbus POE ETH Relay (B)](https://www.waveshare.com/wiki/Modbus_POE_ETH_Relay_(B)). These are **Vircom / board settings**, not Python. The integration cannot fix a misconfigured gateway.

Required for this fork (pymodbus `AsyncModbusTcpClient` = Modbus TCP MBAP, TCP client):

| Setting | Value | Why |
| --- | --- | --- |
| Working mode | **TCP Server** | HA connects out. TCP Client / UDP modes will not work. |
| Transfer Protocol | **Modbus TCP protocol** (port becomes 502) | Stock README saying “RTU over TCP” is wrong for this client. Default “None” is RTU-with-CRC on a raw TCP socket. |
| Modbus Gateway Type | **Multi-host non-storage** | Factory default is storage: the ETH chip injects extra queries and the MCU can stop answering. That looks like timeouts, busy (exception 0x06), and confirm mismatches. |
| Device / slave ID | **1** (not configurable in HA) | Wiki FAQ: the board is identified by IP; slave address is 1. |
| Per-channel control mode | **Normal (0x0000)** on all 8 | Linkage (0x0001) **ignores commands**. FC15 can ACK while coils stay DI-driven → `RelayMismatchError`. Toggle/edge (0x0002/0x0003) fight the mailbox. |

Wiki facts that **confirm** the plan (no extra features):

- FC **0x0F** write multiple coils from address 0 with quantity 8 is the documented all-relay write (`01 0F 00 00 00 08 01 FF …`). Bit0 = relay 1, same as `coalesce_bits`.
- FC **0x01** read coils / **0x02** read discrete inputs with quantity 8 is the documented poll.
- After power-on, “confirm or reset relay status” — restore-on-reconnect is the right HIL policy.
- Green Ethernet LED = TCP session up (debug for “is HA connected”).

Wiki facts that stay **out of scope** (already listed below): FC05 `0x00FF` all-on/all-off (not a bit mask), `0x5500` toggle, flash registers `0x0200`/`0x0400`, holding registers `0x1000` control-mode writes, MQTT/HTTP/cloud.

Do not add a second HA `modbus:` master on the same IPs. The ETH side is still one slave; two TCP clients will interleave PDUs.

---

### Task 1: Confirmed FC15 write + IO retry (HA-free)

**Files:**
- Create: `custom_components/waveshare_relay_b/relay_write.py`
- Create: `tests/test_relay_write.py`
- Create: `pytest.ini`
- Modify: `requirements-dev.txt`

**Interfaces:**
- Consumes: `coalesce_bits`, `any_error` from `helpers.py`
- Produces: `RelayIoError`, `RelayMismatchError`, `require_channel_bits`, `write_coils_confirmed`, `retry_io(op, *, attempts: int = 2)`

- [ ] **Step 1: Write the failing tests**

Create `pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
```

Replace `requirements-dev.txt` with:

```
pymodbus==3.11.2
pytest
pytest-asyncio
```

Create `tests/test_relay_write.py`:

```python
from __future__ import annotations

import pytest

from custom_components.waveshare_relay_b.relay_write import (
    RelayIoError,
    RelayMismatchError,
    require_channel_bits,
    retry_io,
    write_coils_confirmed,
)


class FakePdu:
    def __init__(self, bits: list[bool] | None = None, *, error: bool = False) -> None:
        self.bits = bits or []
        self._error = error

    def isError(self) -> bool:
        return self._error


class FakeBoard:
    """In-memory Modbus stand-in. `write_coil` must never be called."""

    def __init__(self) -> None:
        self.coils = [False] * 8
        self.inputs = [False] * 8
        self.calls: list[tuple] = []
        self.write_error = False
        self.read_error = False
        self.confirm_override: list[bool] | None = None

    async def write_coils(self, address: int, values, *, device_id: int = 1):
        self.calls.append(("write_coils", address, list(values), device_id))
        if self.write_error:
            return FakePdu(error=True)
        self.coils = list(values)
        return FakePdu()

    async def write_coil(self, *args, **kwargs):
        raise AssertionError("write_coil (FC05) must not be used")

    async def read_coils(self, address: int, count: int = 8, *, device_id: int = 1):
        self.calls.append(("read_coils", address, count, device_id))
        if self.read_error:
            return FakePdu(error=True)
        bits = self.confirm_override if self.confirm_override is not None else self.coils
        return FakePdu(bits=list(bits) + [False] * 8)

    async def read_discrete_inputs(
        self, address: int, count: int = 8, *, device_id: int = 1
    ):
        self.calls.append(("read_discrete_inputs", address, count, device_id))
        return FakePdu(bits=list(self.inputs) + [False] * 8)


def test_require_channel_bits_copies_eight_bools():
    bits = [True, False, True, False, False, False, False, True]
    out = require_channel_bits(bits)
    assert out == bits
    assert out is not bits


def test_require_channel_bits_rejects_wrong_length():
    with pytest.raises(ValueError, match="expected 8"):
        require_channel_bits([True, False])


def test_require_channel_bits_rejects_non_bool():
    with pytest.raises(ValueError, match="booleans"):
        require_channel_bits([1, 0, 0, 0, 0, 0, 0, 0])  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_write_coils_confirmed_sends_fc15_then_fc01():
    board = FakeBoard()
    bits = [True, False, True, False, False, False, False, True]
    actual = await write_coils_confirmed(board, bits)
    assert actual == bits
    assert board.coils == bits
    assert [c[0] for c in board.calls] == ["write_coils", "read_coils"]
    assert board.calls[0][1] == 0
    assert board.calls[0][2] == bits
    assert board.calls[0][3] == 1


@pytest.mark.asyncio
async def test_write_coils_confirmed_raises_on_mismatch():
    board = FakeBoard()
    board.confirm_override = [True] * 8
    with pytest.raises(RelayMismatchError):
        await write_coils_confirmed(board, [False] * 8)
    assert [c[0] for c in board.calls] == ["write_coils", "read_coils"]


@pytest.mark.asyncio
async def test_write_coils_confirmed_raises_on_write_error():
    board = FakeBoard()
    board.write_error = True
    with pytest.raises(RelayIoError, match="write"):
        await write_coils_confirmed(board, [False] * 8)
    assert [c[0] for c in board.calls] == ["write_coils"]


@pytest.mark.asyncio
async def test_write_coils_confirmed_raises_on_read_error():
    board = FakeBoard()
    board.read_error = True
    with pytest.raises(RelayIoError, match="confirm read"):
        await write_coils_confirmed(board, [False] * 8)


@pytest.mark.asyncio
async def test_retry_io_retries_io_error_once_then_succeeds():
    attempts = {"n": 0}

    async def op():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RelayIoError("boom")
        return "ok"

    assert await retry_io(op, attempts=2) == "ok"
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_retry_io_does_not_retry_mismatch():
    async def op():
        raise RelayMismatchError([True] + [False] * 7, [False] * 8)

    with pytest.raises(RelayMismatchError):
        await retry_io(op, attempts=2)


@pytest.mark.asyncio
async def test_write_coils_confirmed_wraps_connection_error():
    class Boom:
        async def write_coils(self, *args, **kwargs):
            raise ConnectionError("down")

    with pytest.raises(RelayIoError, match="down"):
        await write_coils_confirmed(Boom(), [False] * 8)


@pytest.mark.asyncio
async def test_write_coils_confirmed_wraps_unexpected_client_error():
    class Boom:
        async def write_coils(self, *args, **kwargs):
            raise RuntimeError("pymodbus boom")

    with pytest.raises(RelayIoError, match="pymodbus boom"):
        await write_coils_confirmed(Boom(), [False] * 8)


@pytest.mark.asyncio
async def test_retry_io_exhausts_attempts_and_reraises():
    attempts = {"n": 0}

    async def op():
        attempts["n"] += 1
        raise RelayIoError("still down")

    with pytest.raises(RelayIoError, match="still down"):
        await retry_io(op, attempts=2)
    assert attempts["n"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pip install -q pytest pytest-asyncio
python3 -m pytest tests/test_relay_write.py -v
```

Expected: FAIL because `relay_write` cannot be imported (module missing).

- [ ] **Step 3: Write minimal implementation**

Create `custom_components/waveshare_relay_b/relay_write.py`:

```python
"""Home-Assistant-free relay command image and FC15 confirm path."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from .helpers import any_error, coalesce_bits

DEFAULT_CHANNELS = 8
DEFAULT_IO_ATTEMPTS = 2

T = TypeVar("T")


class RelayIoError(Exception):
    """Modbus write or read reported an error."""


class RelayMismatchError(RelayIoError):
    """FC15 succeeded but FC01 did not return the commanded bits."""

    def __init__(self, expected: list[bool], actual: list[bool]) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"relay read-back mismatch: expected {expected}, got {actual}")


def require_channel_bits(values: Sequence[bool], count: int = DEFAULT_CHANNELS) -> list[bool]:
    bits = list(values)
    if len(bits) != count:
        raise ValueError(f"expected {count} coil values, got {len(bits)}")
    if not all(isinstance(bit, bool) for bit in bits):
        raise ValueError("coil values must be booleans")
    return bits


async def retry_io(op: Callable[[], Awaitable[T]], *, attempts: int = DEFAULT_IO_ATTEMPTS) -> T:
    last: BaseException | None = None
    for _ in range(attempts):
        try:
            return await op()
        except RelayMismatchError:
            raise
        except RelayIoError as err:
            last = err
    assert last is not None
    raise last


async def write_coils_confirmed(
    client,
    bits: Sequence[bool],
    *,
    device_id: int = 1,
    count: int = DEFAULT_CHANNELS,
) -> list[bool]:
    payload = require_channel_bits(bits, count)
    try:
        written = await client.write_coils(0, payload, device_id=device_id)
        if written is None or any_error(written):
            raise RelayIoError("Modbus write reported an error")
        read = await client.read_coils(0, count=count, device_id=device_id)
        if read is None or any_error(read):
            raise RelayIoError("Modbus confirm read reported an error")
    except RelayIoError:
        raise
    except Exception as err:
        # pymodbus ModbusException is not imported here (HA-free). Any client
        # failure must become RelayIoError so retry_io can retry it.
        raise RelayIoError(str(err)) from err
    actual = coalesce_bits(read.bits, count)
    if actual != payload:
        raise RelayMismatchError(payload, actual)
    return actual
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_relay_write.py tests/test_helpers.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/waveshare_relay_b/relay_write.py tests/test_relay_write.py requirements-dev.txt pytest.ini
git commit -m "$(cat <<'EOF'
feat: confirm all-eight coil writes with function 15 then a read-back

EOF
)"
```

---

### Task 2: Mailbox, skip-if-unchanged, poll, migrate, skip-poll, restore policy

**Files:**
- Modify: `custom_components/waveshare_relay_b/relay_write.py`
- Modify: `tests/test_relay_write.py`
- Modify: `custom_components/waveshare_relay_b/const.py`

**Interfaces:**
- Consumes: Task 1 (`require_channel_bits`, `write_coils_confirmed`, `retry_io`, `RelayIoError`, `DEFAULT_CHANNELS`)
- Produces: `RelayMailbox`, `flush_mailbox` → `list[bool] | None`, `poll_board` → `dict[str, list[bool]]`, `migrate_scan_interval_from_v1`, `clamp_scan_interval_ms`, `poll_stagger_ms`, `parse_set_relays_payload`, `should_skip_poll(*, command_in_flight, has_data)`, `should_restore_after_poll(*, force_restore, restore_on_mismatch, mismatch)`, `RELAY_FIELD_NAMES`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_relay_write.py`)

Add these imports to the existing import block:

```python
import zlib

from custom_components.waveshare_relay_b.relay_write import (
    RelayIoError,
    RelayMailbox,
    RelayMismatchError,
    clamp_scan_interval_ms,
    flush_mailbox,
    migrate_scan_interval_from_v1,
    parse_set_relays_payload,
    poll_board,
    poll_stagger_ms,
    require_channel_bits,
    retry_io,
    should_restore_after_poll,
    should_skip_poll,
    write_coils_confirmed,
)
```

Append:

```python
def test_mailbox_adopt_if_empty_does_not_overwrite():
    box = RelayMailbox()
    bits = [True] + [False] * 7
    box.adopt_if_empty(bits)
    assert box.desired_copy() == bits
    box.adopt_if_empty([False] * 8)
    assert box.desired_copy() == bits


def test_mailbox_set_bit_requires_baseline():
    box = RelayMailbox()
    with pytest.raises(RuntimeError, match="baseline"):
        box.set_bit(0, True)


def test_mailbox_set_all_replaces_desired():
    box = RelayMailbox()
    box.adopt_if_empty([False] * 8)
    word = [True, False, True, False, False, False, False, True]
    box.set_all(word)
    assert box.desired_copy() == word


@pytest.mark.asyncio
async def test_poll_board_adopts_empty_mailbox_from_hardware():
    board = FakeBoard()
    board.coils = [True, False, False, False, False, False, False, False]
    box = RelayMailbox()
    data = await poll_board(board, box)
    assert data["relays"] == board.coils
    assert box.desired_copy() == board.coils
    assert [c[0] for c in board.calls] == ["read_discrete_inputs", "read_coils"]


@pytest.mark.asyncio
async def test_flush_mailbox_skips_when_already_confirmed():
    board = FakeBoard()
    box = RelayMailbox()
    box.adopt_if_empty([False] * 8)
    assert await flush_mailbox(box, board) is None
    assert board.calls == []


@pytest.mark.asyncio
async def test_flush_mailbox_writes_all_eight_when_one_bit_changes():
    board = FakeBoard()
    box = RelayMailbox()
    box.adopt_if_empty([False] * 8)
    box.set_bit(2, True)
    result = await flush_mailbox(box, board)
    expected = [False, False, True, False, False, False, False, False]
    assert result == expected
    assert [c[0] for c in board.calls] == ["write_coils", "read_coils"]
    assert board.calls[0][2] == expected


def test_parse_set_relays_payload_from_relay_fields():
    data = {f"relay_{n}": n == 3 for n in range(1, 9)}
    bits = parse_set_relays_payload(data)
    assert bits[2] is True
    assert bits.count(True) == 1


def test_parse_set_relays_payload_from_states():
    bits = [False, True, False, True, False, False, False, False]
    assert parse_set_relays_payload({"states": bits}) == bits


def test_parse_set_relays_payload_missing_field():
    data = {f"relay_{n}": False for n in range(1, 8)}
    with pytest.raises(ValueError, match="missing"):
        parse_set_relays_payload(data)


def test_parse_set_relays_rejects_both_states_and_fields():
    data = {"states": [False] * 8, "relay_1": True}
    with pytest.raises(ValueError, match="either"):
        parse_set_relays_payload(data)


def test_poll_stagger_ms_uses_crc32_not_python_hash():
    host = "192.168.20.10"
    interval = 300
    expected = zlib.crc32(host.encode("utf-8")) % interval
    assert poll_stagger_ms(host, interval) == expected
    assert poll_stagger_ms(host, interval) == poll_stagger_ms(host, interval)


def test_migrate_v1_seconds_and_evw_ms():
    assert migrate_scan_interval_from_v1(5) == 5000
    assert migrate_scan_interval_from_v1(300) == 300
    assert migrate_scan_interval_from_v1(60) == 60_000


def test_clamp_scan_interval_ms_rejects_legacy_heuristic():
    assert clamp_scan_interval_ms(300) == 300
    assert clamp_scan_interval_ms(50) == 50
    with pytest.raises(ValueError):
        clamp_scan_interval_ms(20)
    with pytest.raises(ValueError):
        clamp_scan_interval_ms(0)
    with pytest.raises(ValueError):
        clamp_scan_interval_ms(61_000)


def test_should_skip_poll():
    assert should_skip_poll(command_in_flight=True, has_data=True) is True
    assert should_skip_poll(command_in_flight=True, has_data=False) is False
    assert should_skip_poll(command_in_flight=False, has_data=True) is False


def test_should_restore_after_poll():
    assert should_restore_after_poll(
        force_restore=True, restore_on_mismatch=False, mismatch=True
    ) is True
    assert should_restore_after_poll(
        force_restore=False, restore_on_mismatch=True, mismatch=True
    ) is True
    assert should_restore_after_poll(
        force_restore=False, restore_on_mismatch=False, mismatch=True
    ) is False
    assert should_restore_after_poll(
        force_restore=True, restore_on_mismatch=True, mismatch=False
    ) is False


@pytest.mark.asyncio
async def test_poll_without_restore_does_not_write():
    board = FakeBoard()
    board.coils = [True] * 8
    box = RelayMailbox()
    box.adopt_if_empty([False] * 8)
    data = await poll_board(board, box)
    assert data["relays"] == [True] * 8
    assert box.desired_copy() == [False] * 8
    assert should_restore_after_poll(
        force_restore=False, restore_on_mismatch=False, mismatch=True
    ) is False
    assert [c[0] for c in board.calls] == ["read_discrete_inputs", "read_coils"]


@pytest.mark.asyncio
async def test_flush_after_poll_restores_when_policy_says_so():
    board = FakeBoard()
    board.coils = [True] * 8
    box = RelayMailbox()
    box.adopt_if_empty([False] * 8)
    await poll_board(board, box)
    assert should_restore_after_poll(
        force_restore=True, restore_on_mismatch=False, mismatch=True
    ) is True
    repaired = await flush_mailbox(box, board)
    assert repaired == [False] * 8


@pytest.mark.asyncio
async def test_flush_skipped_when_option_on_but_bits_already_match():
    board = FakeBoard()
    board.coils = [False] * 8
    box = RelayMailbox()
    box.adopt_if_empty([False] * 8)
    await poll_board(board, box)
    assert should_restore_after_poll(
        force_restore=False, restore_on_mismatch=True, mismatch=False
    ) is False
    assert await flush_mailbox(box, board) is None
    assert [c[0] for c in board.calls] == ["read_discrete_inputs", "read_coils"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_relay_write.py -v
```

Expected: FAIL on missing names (`RelayMailbox`, `flush_mailbox`, `poll_board`, migrate/clamp helpers).

- [ ] **Step 3: Write minimal implementation**

Append to `relay_write.py`. Add `import zlib` at the top. Then append:

```python
RELAY_FIELD_NAMES: tuple[str, ...] = tuple(f"relay_{n}" for n in range(1, DEFAULT_CHANNELS + 1))

MIN_SCAN_INTERVAL_MS = 50
MAX_SCAN_INTERVAL_MS = 60_000
LEGACY_SECONDS_MAX = 60


def clamp_scan_interval_ms(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("scan_interval must be a positive integer")
    if value < MIN_SCAN_INTERVAL_MS or value > MAX_SCAN_INTERVAL_MS:
        raise ValueError(
            f"scan_interval must be {MIN_SCAN_INTERVAL_MS}–{MAX_SCAN_INTERVAL_MS} ms "
            f"(got {value})"
        )
    return value


def migrate_scan_interval_from_v1(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("scan_interval must be a positive integer")
    ms = value * 1000 if value <= LEGACY_SECONDS_MAX else value
    return clamp_scan_interval_ms(ms)


def poll_stagger_ms(host: str, interval_ms: int) -> int:
    return zlib.crc32(host.encode("utf-8")) % interval_ms


def should_skip_poll(*, command_in_flight: bool, has_data: bool) -> bool:
    return command_in_flight and has_data


def should_restore_after_poll(
    *, force_restore: bool, restore_on_mismatch: bool, mismatch: bool
) -> bool:
    if not mismatch:
        return False
    return force_restore or restore_on_mismatch


def parse_set_relays_payload(data: dict) -> list[bool]:
    if "states" in data:
        if any(name in data for name in RELAY_FIELD_NAMES):
            raise ValueError("pass either states or relay_1…relay_8, not both")
        return require_channel_bits(data["states"])
    missing = [name for name in RELAY_FIELD_NAMES if name not in data]
    if missing:
        raise ValueError(f"missing relay fields: {', '.join(missing)}")
    return [bool(data[name]) for name in RELAY_FIELD_NAMES]


class RelayMailbox:
    """Desired command image plus last confirmed hardware bits."""

    def __init__(self) -> None:
        self._desired: list[bool] | None = None
        self._confirmed: list[bool] | None = None

    def adopt_if_empty(self, bits: Sequence[bool]) -> None:
        payload = require_channel_bits(bits)
        if self._desired is None:
            self._desired = list(payload)
        if self._confirmed is None:
            self._confirmed = list(payload)

    def observe_hardware(self, bits: Sequence[bool]) -> None:
        self._confirmed = require_channel_bits(bits)
        if self._desired is None:
            self._desired = list(self._confirmed)

    def set_bit(self, index: int, value: bool) -> None:
        if self._desired is None:
            raise RuntimeError("relay mailbox has no hardware baseline yet")
        self._desired[index] = bool(value)

    def set_all(self, bits: Sequence[bool]) -> None:
        self._desired = require_channel_bits(bits)

    def desired_copy(self) -> list[bool] | None:
        return None if self._desired is None else list(self._desired)

    def confirmed_matches(self, bits: Sequence[bool]) -> bool:
        return self._confirmed == list(bits)


async def poll_board(
    client,
    mailbox: RelayMailbox,
    *,
    device_id: int = 1,
    count: int = DEFAULT_CHANNELS,
) -> dict[str, list[bool]]:
    try:
        di = await client.read_discrete_inputs(0, count=count, device_id=device_id)
        co = await client.read_coils(0, count=count, device_id=device_id)
    except Exception as err:
        raise RelayIoError(str(err)) from err
    if di is None or co is None or any_error(di, co):
        raise RelayIoError("Modbus poll reported an error")
    inputs = coalesce_bits(di.bits, count)
    relays = coalesce_bits(co.bits, count)
    mailbox.adopt_if_empty(relays)
    mailbox.observe_hardware(relays)
    return {"inputs": inputs, "relays": relays}


async def flush_mailbox(
    mailbox: RelayMailbox, client, *, device_id: int = 1
) -> list[bool] | None:
    desired = mailbox.desired_copy()
    if desired is None:
        raise RuntimeError("relay mailbox has no hardware baseline yet")
    if mailbox.confirmed_matches(desired):
        return None

    async def _once() -> list[bool]:
        return await write_coils_confirmed(client, desired, device_id=device_id)

    confirmed = await retry_io(_once)
    mailbox.observe_hardware(confirmed)
    return confirmed
```

Replace `custom_components/waveshare_relay_b/const.py` with:

```python
"""Constants for the Waveshare Relay B integration."""
from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform

DOMAIN = "waveshare_relay_b"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_RESTORE_ON_MISMATCH = "restore_on_mismatch"
CONF_OPTIMISTIC = "optimistic"

DEFAULT_PORT = 502
UNIT_ID = 1
DEFAULT_SCAN_INTERVAL = 300
LEGACY_DEFAULT_SCAN_INTERVAL_S = 5
MIN_SCAN_INTERVAL_MS = 50
MAX_SCAN_INTERVAL_MS = 60_000
CHANNELS = 8
SERVICE_SET_RELAYS = "set_relays"
DEFAULT_RESTORE_ON_MISMATCH = True
DEFAULT_OPTIMISTIC = False
MODBUS_TIMEOUT = 1
CONFIG_VERSION = 2

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SWITCH]

__all__ = [
    "DOMAIN",
    "CONF_HOST",
    "CONF_PORT",
    "CONF_SCAN_INTERVAL",
    "CONF_RESTORE_ON_MISMATCH",
    "CONF_OPTIMISTIC",
    "DEFAULT_PORT",
    "UNIT_ID",
    "DEFAULT_SCAN_INTERVAL",
    "LEGACY_DEFAULT_SCAN_INTERVAL_S",
    "MIN_SCAN_INTERVAL_MS",
    "MAX_SCAN_INTERVAL_MS",
    "CHANNELS",
    "SERVICE_SET_RELAYS",
    "DEFAULT_RESTORE_ON_MISMATCH",
    "DEFAULT_OPTIMISTIC",
    "MODBUS_TIMEOUT",
    "CONFIG_VERSION",
    "PLATFORMS",
]
```

Do not import `relay_write` from `const.py`. Duplicate the 50 / 60_000 numbers; that is intentional so `const.py` can still import Home Assistant.

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/waveshare_relay_b/relay_write.py custom_components/waveshare_relay_b/const.py tests/test_relay_write.py
git commit -m "$(cat <<'EOF'
feat: keep a per-board desired coil image and skip unchanged writes

EOF
)"
```

---

### Task 3: Coordinator, options, service response, switches

**Files:**
- Modify: `custom_components/waveshare_relay_b/coordinator.py`
- Modify: `custom_components/waveshare_relay_b/__init__.py`
- Modify: `custom_components/waveshare_relay_b/switch.py`
- Modify: `custom_components/waveshare_relay_b/config_flow.py`
- Create: `custom_components/waveshare_relay_b/services.yaml`
- Modify: `custom_components/waveshare_relay_b/strings.json`
- Modify: `custom_components/waveshare_relay_b/translations/en.json`

**Interfaces:**
- Consumes: Task 2 helpers
- Produces: `WaveshareCoordinator.async_set_relay` → `list[bool]`, `async_set_relays` → `list[bool]`, `optimistic: bool`, HA action with response, `async_migrate_entry`

Coordinator wiring (must match the file below):

1. Client is constructed in `async_setup_entry` with `timeout=MODBUS_TIMEOUT`, `retries=0`, `reconnect_delay=0`.
2. `always_update=False`; interval is `timedelta(milliseconds=clamp_scan_interval_ms(scan_interval))`.
3. Increment `_commands_in_flight` **before** acquiring `_io`; decrement in `finally`.
4. Stagger sleep **outside** the lock. Skip poll via `should_skip_poll`. Restore via `should_restore_after_poll` then `_flush`.
5. `_flush` calls `flush_mailbox` only. **Do not** call `retry_io` here. If a pymodbus exception still escapes, map it to `UpdateFailed` — retry already happened inside `write_coils_confirmed` → `retry_io`. Clear `_force_restore` only after a successful restore or an explicit decision not to restore. Pass `mismatch=` into `should_restore_after_poll` so a matching poll does not call `_flush`.
6. Do not call `async_request_refresh()` after a write.
7. Service schema is the simple voluptuous block (`device_id` optional). Resolve devices with `device_ids_from_service_call(call.data, getattr(call, "target", None))`. Do **not** call `call.target.get` (ServiceCall has no `.target`). Do **not** call `make_entity_service_schema`.
8. `supports_response=SupportsResponse.OPTIONAL`.

Task 3 pytest stays HA-free. Add the coordinator composition tests in Task 2 (`should_restore_after_poll(..., mismatch=)`). After wiring, run pytest + the `write_coil` grep, then tick the HIL checklist in Step 4 — green `tests/` is not proof of skip-poll, migrate, or action response.

- [ ] **Step 1: Replace `coordinator.py`**

Write this entire file:

```python
"""DataUpdateCoordinator for the Waveshare Relay B integration."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import timedelta

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CHANNELS, DOMAIN, UNIT_ID
from .relay_write import (
    RelayIoError,
    RelayMailbox,
    clamp_scan_interval_ms,
    flush_mailbox,
    poll_board,
    poll_stagger_ms,
    should_restore_after_poll,
    should_skip_poll,
)

_LOGGER = logging.getLogger(__name__)

type WaveshareConfigEntry = ConfigEntry["WaveshareCoordinator"]


class WaveshareCoordinator(DataUpdateCoordinator[dict[str, list[bool]]]):
    """Polls the board with two batched reads; writes all 8 coils in one FC15."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: AsyncModbusTcpClient,
        scan_interval: int,
        host: str,
        *,
        restore_on_mismatch: bool,
        optimistic: bool,
    ) -> None:
        self._interval_ms = clamp_scan_interval_ms(scan_interval)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(milliseconds=self._interval_ms),
            always_update=False,
        )
        self.client = client
        self._host = host
        self._io = asyncio.Lock()
        self._mailbox = RelayMailbox()
        self._staggered = False
        self._commands_in_flight = 0
        self._force_restore = False
        self._restore_on_mismatch = restore_on_mismatch
        self.optimistic = optimistic

    async def _connect(self) -> None:
        if not self.client.connected:
            await self.client.connect()
            self._force_restore = True

    async def _flush(self) -> list[bool] | None:
        try:
            return await flush_mailbox(self._mailbox, self.client, device_id=UNIT_ID)
        except (ModbusException, ConnectionError) as err:
            raise RelayIoError(str(err)) from err

    async def _async_update_data(self) -> dict[str, list[bool]]:
        if not self._staggered:
            await asyncio.sleep(poll_stagger_ms(self._host, self._interval_ms) / 1000)
            self._staggered = True
        if should_skip_poll(
            command_in_flight=self._commands_in_flight > 0,
            has_data=self.data is not None,
        ):
            return self.data
        try:
            async with self._io:
                await self._connect()
                data = await poll_board(self.client, self._mailbox, device_id=UNIT_ID)
                desired = self._mailbox.desired_copy()
                mismatch = desired is not None and data["relays"] != desired
                restore = should_restore_after_poll(
                    force_restore=self._force_restore,
                    restore_on_mismatch=self._restore_on_mismatch,
                    mismatch=mismatch,
                )
                if not restore:
                    self._force_restore = False
                    return data
                repaired = await self._flush()
                self._force_restore = False
        except (ModbusException, ConnectionError, RelayIoError) as err:
            raise UpdateFailed(f"Modbus poll failed: {err}") from err
        if repaired is not None:
            _LOGGER.warning(
                "%s: relay read-back did not match commanded state; restored",
                self._host,
            )
            return {"inputs": data["inputs"], "relays": repaired}
        return data

    async def _command(self, mutate: Callable[[], None]) -> list[bool]:
        self._commands_in_flight += 1
        try:
            async with self._io:
                mutate()
                await self._connect()
                confirmed = await self._flush()
                bits = self._mailbox.desired_copy()
                if bits is None:
                    raise RuntimeError("relay mailbox has no hardware baseline yet")
                if confirmed is not None:
                    inputs = (
                        list(self.data["inputs"])
                        if self.data is not None
                        else [False] * CHANNELS
                    )
                    self.async_set_updated_data({"inputs": inputs, "relays": confirmed})
                return bits
        except RuntimeError as err:
            raise UpdateFailed("no hardware baseline yet") from err
        except (ModbusException, ConnectionError, RelayIoError) as err:
            raise UpdateFailed(f"Modbus write failed: {err}") from err
        finally:
            self._commands_in_flight -= 1

    async def async_set_relay(self, index: int, value: bool) -> list[bool]:
        return await self._command(lambda: self._mailbox.set_bit(index, value))

    async def async_set_relays(self, bits: Sequence[bool]) -> list[bool]:
        return await self._command(lambda: self._mailbox.set_all(bits))
```

- [ ] **Step 2: Replace `__init__.py`**

Write this entire file (includes today’s unload / reload listener):

```python
"""The Waveshare Relay B integration."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from pymodbus.client import AsyncModbusTcpClient

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_OPTIMISTIC,
    CONF_RESTORE_ON_MISMATCH,
    CONF_SCAN_INTERVAL,
    DEFAULT_OPTIMISTIC,
    DEFAULT_PORT,
    DEFAULT_RESTORE_ON_MISMATCH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LEGACY_DEFAULT_SCAN_INTERVAL_S,
    MODBUS_TIMEOUT,
    PLATFORMS,
    SERVICE_SET_RELAYS,
)
from .coordinator import WaveshareConfigEntry, WaveshareCoordinator
from .relay_write import (
    RELAY_FIELD_NAMES,
    migrate_scan_interval_from_v1,
    parse_set_relays_payload,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_RELAYS_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("states"): vol.All(cv.ensure_list, [cv.boolean]),
        **{vol.Optional(name): cv.boolean for name in RELAY_FIELD_NAMES},
    },
    extra=vol.ALLOW_EXTRA,
)


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_RELAYS):
        return

    async def async_set_relays(call: ServiceCall) -> ServiceResponse:
        try:
            bits = parse_set_relays_payload(dict(call.data))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        device_ids = device_ids_from_service_call(
            dict(call.data),
            getattr(call, "target", None),
        )
        if not device_ids:
            raise ServiceValidationError("Select a Waveshare Relay B device")
        registry = dr.async_get(hass)
        coordinators: list[WaveshareCoordinator] = []
        for device_id in device_ids:
            device = registry.async_get(device_id)
            if device is None:
                raise ServiceValidationError(f"Unknown device {device_id}")
            entry = None
            for entry_id in device.config_entries:
                candidate = hass.config_entries.async_get_entry(entry_id)
                if (
                    candidate is not None
                    and candidate.domain == DOMAIN
                    and candidate.state is ConfigEntryState.LOADED
                ):
                    entry = candidate
                    break
            if entry is None:
                raise ServiceValidationError(
                    f"Device {device_id} has no loaded {DOMAIN} entry"
                )
            coordinators.append(entry.runtime_data)
        results = await asyncio.gather(
            *(coord.async_set_relays(bits) for coord in coordinators),
            return_exceptions=True,
        )
        confirmed: list[list[bool]] = []
        for result in results:
            if isinstance(result, Exception):
                raise HomeAssistantError(str(result)) from result
            confirmed.append(result)
        return {
            "relays": [
                {"device_id": device_id, "states": states}
                for device_id, states in zip(device_ids, confirmed, strict=True)
            ]
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_RELAYS,
        async_set_relays,
        schema=SET_RELAYS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    _register_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version == 1:
        options = dict(entry.options)
        raw = options.get(CONF_SCAN_INTERVAL, LEGACY_DEFAULT_SCAN_INTERVAL_S)
        options[CONF_SCAN_INTERVAL] = migrate_scan_interval_from_v1(int(raw))
        options.setdefault(CONF_RESTORE_ON_MISMATCH, DEFAULT_RESTORE_ON_MISMATCH)
        options.setdefault(CONF_OPTIMISTIC, DEFAULT_OPTIMISTIC)
        hass.config_entries.async_update_entry(entry, options=options, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WaveshareConfigEntry) -> bool:
    _register_services(hass)
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    restore = entry.options.get(CONF_RESTORE_ON_MISMATCH, DEFAULT_RESTORE_ON_MISMATCH)
    optimistic = entry.options.get(CONF_OPTIMISTIC, DEFAULT_OPTIMISTIC)
    client = AsyncModbusTcpClient(
        host, port=port, timeout=MODBUS_TIMEOUT, retries=0, reconnect_delay=0
    )
    coordinator = WaveshareCoordinator(
        hass,
        client,
        scan_interval,
        host,
        restore_on_mismatch=restore,
        optimistic=optimistic,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: WaveshareConfigEntry
) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.client.close()
    return unloaded


async def _async_update_listener(
    hass: HomeAssistant, entry: WaveshareConfigEntry
) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
```

- [ ] **Step 3: config_flow, switch, services.yaml, strings**

Replace `config_flow.py` with:

```python
"""Config and options flow for Waveshare Relay B."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback

from .const import (
    CONFIG_VERSION,
    CONF_OPTIMISTIC,
    CONF_RESTORE_ON_MISMATCH,
    CONF_SCAN_INTERVAL,
    DEFAULT_OPTIMISTIC,
    DEFAULT_PORT,
    DEFAULT_RESTORE_ON_MISMATCH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL_MS,
    MIN_SCAN_INTERVAL_MS,
)


class WaveshareConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = CONFIG_VERSION

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_HOST],
                data={
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                },
                options={CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL]},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): vol.All(
                    int,
                    vol.Range(min=MIN_SCAN_INTERVAL_MS, max=MAX_SCAN_INTERVAL_MS),
                ),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "WaveshareOptionsFlow":
        return WaveshareOptionsFlow()


class WaveshareOptionsFlow(OptionsFlow):
    """Edit poll interval and write behaviour after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_scan = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current_scan): vol.All(
                    int,
                    vol.Range(min=MIN_SCAN_INTERVAL_MS, max=MAX_SCAN_INTERVAL_MS),
                ),
                vol.Required(
                    CONF_RESTORE_ON_MISMATCH,
                    default=self.config_entry.options.get(
                        CONF_RESTORE_ON_MISMATCH, DEFAULT_RESTORE_ON_MISMATCH
                    ),
                ): bool,
                vol.Required(
                    CONF_OPTIMISTIC,
                    default=self.config_entry.options.get(
                        CONF_OPTIMISTIC, DEFAULT_OPTIMISTIC
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
```

Replace `switch.py` with:

```python
"""Switch (relay) platform for Waveshare Relay B."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CHANNELS, DOMAIN
from .coordinator import WaveshareConfigEntry, WaveshareCoordinator
from .helpers import channel_number


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WaveshareConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        WaveshareRelay(coordinator, entry.entry_id, i) for i in range(CHANNELS)
    )


class WaveshareRelay(CoordinatorEntity[WaveshareCoordinator], SwitchEntity):
    """One relay output channel."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: WaveshareCoordinator, entry_id: str, index: int
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_assumed_state = coordinator.optimistic
        self._attr_unique_id = f"{entry_id}_relay_{channel_number(index)}"
        self._attr_name = f"Relay {channel_number(index)}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Waveshare Relay B",
            manufacturer="Waveshare",
            model="Modbus POE ETH Relay (B)",
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data["relays"][self._index]

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_relay(self._index, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_relay(self._index, False)
```

Create `custom_components/waveshare_relay_b/services.yaml`:

```yaml
set_relays:
  name: Set all relays
  description: Write all eight relay coils in one Modbus request.
  target:
    device:
      integration: waveshare_relay_b
  fields:
    relay_1:
      name: Relay 1
      description: Channel 1
      required: true
      selector:
        boolean:
    relay_2:
      name: Relay 2
      description: Channel 2
      required: true
      selector:
        boolean:
    relay_3:
      name: Relay 3
      description: Channel 3
      required: true
      selector:
        boolean:
    relay_4:
      name: Relay 4
      description: Channel 4
      required: true
      selector:
        boolean:
    relay_5:
      name: Relay 5
      description: Channel 5
      required: true
      selector:
        boolean:
    relay_6:
      name: Relay 6
      description: Channel 6
      required: true
      selector:
        boolean:
    relay_7:
      name: Relay 7
      description: Channel 7
      required: true
      selector:
        boolean:
    relay_8:
      name: Relay 8
      description: Channel 8
      required: true
      selector:
        boolean:
```

Do not add `states` to `services.yaml`. The `states` list is a plant-only extra accepted by the voluptuous schema.

Replace `strings.json` and `translations/en.json` with the same content:

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Waveshare Modbus Relay B",
        "description": "Connect to the board over Modbus TCP.",
        "data": {
          "host": "Host",
          "port": "Port",
          "scan_interval": "Scan interval (milliseconds)"
        }
      }
    },
    "abort": {
      "already_configured": "This board is already configured."
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Options",
        "data": {
          "scan_interval": "Scan interval (milliseconds)",
          "restore_on_mismatch": "Restore commanded relays if the board disagrees",
          "optimistic": "Optimistic dashboard switches (flip before the board confirms)"
        }
      }
    }
  },
  "services": {
    "set_relays": {
      "name": "Set all relays",
      "description": "Write all eight relay coils in one Modbus request.",
      "fields": {
        "relay_1": { "name": "Relay 1", "description": "Channel 1" },
        "relay_2": { "name": "Relay 2", "description": "Channel 2" },
        "relay_3": { "name": "Relay 3", "description": "Channel 3" },
        "relay_4": { "name": "Relay 4", "description": "Channel 4" },
        "relay_5": { "name": "Relay 5", "description": "Channel 5" },
        "relay_6": { "name": "Relay 6", "description": "Channel 6" },
        "relay_7": { "name": "Relay 7", "description": "Channel 7" },
        "relay_8": { "name": "Relay 8", "description": "Channel 8" }
      }
    }
  }
}
```

- [ ] **Step 4: Run logic tests and grep**

```bash
python3 -m pytest tests/ -v
rg -n '\bwrite_coil\b|make_entity_service_schema' custom_components tests
```

Expected: tests PASS. `\bwrite_coil\b` hits **only** the FakeBoard assertion in `tests/test_relay_write.py` (not `write_coils`). **Zero** `make_entity_service_schema`.

HIL checklist (not pytest — tick after installing the fork on HA):

- [ ] Config entry v1 with `scan_interval: 5` becomes 5000 ms after migrate; EVW `300` stays 300.
- [ ] v2 form rejects `20` (below 50) instead of storing it and crashing setup.
- [ ] Toggle a switch while a poll would run: write still completes; next poll is skipped while the command is in flight.
- [ ] Disconnect/reconnect Ethernet: commanded coils are restored (`_force_restore`).
- [ ] `set_relays` with `target.device_id` (Actions UI / plant `target`) and `return_response: true` returns eight bools in `relays[].states`. A payload-only `device_id` still works.
- [ ] `rg` shows no `async_request_refresh` in `coordinator.py`.

- [ ] **Step 5: Commit**

```bash
git add custom_components/waveshare_relay_b/
git commit -m "$(cat <<'EOF'
feat: serialize each board and return confirmed bits from set_relays

EOF
)"
```

---

### Task 4: README, manifest, plant contract

**Files:**
- Modify: `README.md`
- Modify: `custom_components/waveshare_relay_b/manifest.json`

- [ ] **Step 1: Replace `manifest.json`**

```json
{
  "domain": "waveshare_relay_b",
  "name": "Waveshare Modbus Relay B",
  "codeowners": ["@sacherjj", "@SigiBaes"],
  "config_flow": true,
  "documentation": "https://github.com/SigiBaes/waveshare-modbus-eth-relay-b-HACS",
  "integration_type": "device",
  "iot_class": "local_polling",
  "issue_tracker": "https://github.com/SigiBaes/waveshare-modbus-eth-relay-b-HACS/issues",
  "requirements": ["pymodbus>=3.11.2"],
  "version": "0.2.0"
}
```

- [ ] **Step 2: Replace `README.md`**

```markdown
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

Action **Set all relays** (`waveshare_relay_b.set_relays`): eight checkboxes in Developer Tools → Actions, or a `states` list of 8 booleans for automations/plants. On success the action returns:

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
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md custom_components/waveshare_relay_b/manifest.json
git commit -m "$(cat <<'EOF'
docs: describe atomic coil writes, options, and the plant confirm contract

EOF
)"
```

- [ ] **Step 5: Push the fork**

```bash
git push -u origin main
git tag v0.2.0
git push origin v0.2.0
```

Expected: `https://github.com/SigiBaes/waveshare-modbus-eth-relay-b-HACS` has `0.2.0` on `main` and tag `v0.2.0` for HACS.

If work happened on a feature branch, merge to `main` first, then push. Do not force-push.

---

## Self-review

**Spec coverage**

- FC15+FC01, no mismatch retry, IO retry → Task 1
- Mailbox, skip write, v1 migrate vs v2 clamp, skip-poll predicate, restore policy → Task 2
- Lock, in-flight skip, 1 s timeout, `always_update=False`, optimistic option, simple schema, action response, config v2 → Task 3
- Docs + plant contract → Task 4
- S1–S23 mapped: S4 skip-poll tests, S5 no retry, S6 retry_io, S8/S14a/S14b restore helpers, S18/S19 migrate vs clamp, S21 response, S22 assumed_state, S23 always_update

**Placeholder scan:** no “previous plan”, “as today”, or “same as before”. FakeBoard, RelayMailbox, poll_board, unload/reload, services.yaml, strings, README, and manifest are inlined.

**Type consistency:** `async_set_relays` / `async_set_relay` return `list[bool]`; action response `relays: list[{device_id, states}]`; `flush_mailbox` → `list[bool] | None`; `should_skip_poll` / `should_restore_after_poll(*, mismatch=)` are keyword-only; `clamp_scan_interval_ms` vs `migrate_scan_interval_from_v1`; form `vol.Range` uses the same 50–60000 bounds.

**Review follow-up (2026-08-31):** pymodbus-shaped client errors wrap to `RelayIoError` inside `write_coils_confirmed` so `retry_io` sees them; coordinator `_flush` does not call `retry_io`; restore helper requires `mismatch=` (Produces line included); config/options forms clamp scan ms; grep uses word-boundary `write_coil`; Task 3 HIL checklist is required. **2026-09-01:** Bugbot asked for `call.target["device_id"]`, but `ServiceCall` has no `.target` — HA merges websocket target into `call.data`. Use `device_ids_from_service_call`. Do not push `main` until the user asks.

**Not in this repo:** EVW `aux-writer.ts` changes. README plant contract is the handoff. Installing this fork via HACS custom repositories replaces the stock `sacherjj` URL; updating the stock repo would drop these changes.
