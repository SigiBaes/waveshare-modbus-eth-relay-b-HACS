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
