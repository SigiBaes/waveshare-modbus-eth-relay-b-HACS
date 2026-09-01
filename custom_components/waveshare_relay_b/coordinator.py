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
    RelayMismatchError,
    clamp_scan_interval_ms,
    flush_mailbox,
    poll_board,
    poll_data_after_failed_restore,
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

    async def _recover_transport(self) -> None:
        self.client.close()
        await self.client.connect()
        self._force_restore = True

    async def _flush(self) -> list[bool] | None:
        try:
            return await flush_mailbox(
                self._mailbox,
                self.client,
                device_id=UNIT_ID,
                on_retry=self._recover_transport,
            )
        except (ModbusException, ConnectionError) as err:
            self.client.close()
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
                try:
                    repaired = await self._flush()
                except (ModbusException, ConnectionError, RelayIoError) as err:
                    _LOGGER.warning(
                        "%s: relay restore failed (%s); publishing poll data",
                        self._host,
                        err,
                    )
                    return poll_data_after_failed_restore(data)
                self._force_restore = False
        except (ModbusException, ConnectionError, RelayIoError) as err:
            self.client.close()
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
                inputs = (
                    list(self.data["inputs"])
                    if self.data is not None
                    else [False] * CHANNELS
                )
                relays = confirmed if confirmed is not None else bits
                self.async_set_updated_data({"inputs": inputs, "relays": relays})
                return bits
        except RuntimeError as err:
            raise UpdateFailed("no hardware baseline yet") from err
        except RelayMismatchError as err:
            raise UpdateFailed(f"Modbus write failed: {err}") from err
        except (ModbusException, ConnectionError, RelayIoError) as err:
            self.client.close()
            raise UpdateFailed(f"Modbus write failed: {err}") from err
        finally:
            self._commands_in_flight -= 1

    async def async_set_relay(self, index: int, value: bool) -> list[bool]:
        return await self._command(lambda: self._mailbox.set_bit(index, value))

    async def async_set_relays(self, bits: Sequence[bool]) -> list[bool]:
        return await self._command(lambda: self._mailbox.set_all(bits))
