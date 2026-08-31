from __future__ import annotations

import zlib

import pytest

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
