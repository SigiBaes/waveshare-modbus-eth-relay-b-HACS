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
