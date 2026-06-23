"""Pure, Home-Assistant-free transforms for the Waveshare Relay B integration."""
from __future__ import annotations


def coalesce_bits(bits: list[bool], count: int = 8) -> list[bool]:
    """Return the first ``count`` bits as a fresh list.

    pymodbus rounds bit reads up to a whole byte, so a read of 8 channels may
    return more than 8 bits. Order is LSB-first: index 0 is channel 1.
    """
    if len(bits) < count:
        raise ValueError(f"expected at least {count} bits, got {len(bits)}")
    return list(bits[:count])


def any_error(*responses) -> bool:
    """True if any pymodbus response reports an error."""
    return any(r.isError() for r in responses)


def channel_number(index: int) -> int:
    """Map a 0-based channel index to its 1-based label."""
    return index + 1
