from custom_components.waveshare_relay_b.helpers import (
    coalesce_bits,
    any_error,
    channel_number,
)


def test_coalesce_trims_byte_padding():
    # pymodbus pads reads up to a byte boundary; we keep the first `count`.
    padded = [True, False, True, False, False, False, False, False, False, False]
    assert coalesce_bits(padded, 8) == [True, False, True, False, False, False, False, False]


def test_coalesce_exact_length():
    bits = [False, True, False, True, False, True, False, True]
    assert coalesce_bits(bits, 8) == bits
    assert coalesce_bits(bits, 8) is not bits  # returns a copy


def test_coalesce_preserves_lsb_first_order():
    # bit0 -> channel 1, bit7 -> channel 8
    bits = [True, False, False, False, False, False, False, False]
    result = coalesce_bits(bits, 8)
    assert result[0] is True  # channel 1 on
    assert all(b is False for b in result[1:])


def test_coalesce_too_few_raises():
    import pytest
    with pytest.raises(ValueError):
        coalesce_bits([True, False], 8)


class _Resp:
    def __init__(self, err):
        self._err = err

    def isError(self):
        return self._err


def test_any_error_true_when_one_errors():
    assert any_error(_Resp(False), _Resp(True)) is True


def test_any_error_false_when_all_ok():
    assert any_error(_Resp(False), _Resp(False)) is False


def test_channel_number_is_one_based():
    assert channel_number(0) == 1
    assert channel_number(7) == 8
