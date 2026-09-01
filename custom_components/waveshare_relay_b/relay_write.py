"""Home-Assistant-free relay command image and FC15 confirm path."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar
import zlib

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


def require_channel_bits(
    values: Sequence[bool], count: int = DEFAULT_CHANNELS
) -> list[bool]:
    bits = list(values)
    if len(bits) != count:
        raise ValueError(f"expected {count} coil values, got {len(bits)}")
    if not all(isinstance(bit, bool) for bit in bits):
        raise ValueError("coil values must be booleans")
    return bits


async def retry_io(
    op: Callable[[], Awaitable[T]], *, attempts: int = DEFAULT_IO_ATTEMPTS
) -> T:
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


RELAY_FIELD_NAMES: tuple[str, ...] = tuple(
    f"relay_{n}" for n in range(1, DEFAULT_CHANNELS + 1)
)

MIN_SCAN_INTERVAL_MS = 50
MAX_SCAN_INTERVAL_MS = 60_000
LEGACY_SECONDS_MAX = 60


def normalize_device_ids(value: str | Sequence[str] | None) -> list[str]:
    """Normalize a service target or payload device-id value."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return list(value)


def device_ids_from_service_call(data: dict, target: object | None = None) -> list[str]:
    """Resolve device ids the way HA actually delivers them.

    ``ServiceCall`` has no ``.target`` attribute. Websocket ``target.device_id``
    is merged into ``call.data`` by ``hass.services.async_call``. An explicit
    target dict (if a future HA version exposes one) still wins.
    """
    from_target = normalize_device_ids(
        target.get("device_id") if isinstance(target, dict) else None
    )
    return from_target or normalize_device_ids(data.get("device_id"))


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


def poll_data_after_failed_restore(
    poll_data: dict[str, list[bool]],
) -> dict[str, list[bool]]:
    return {
        "inputs": list(poll_data["inputs"]),
        "relays": list(poll_data["relays"]),
    }


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
