"""DataUpdateCoordinator for the Waveshare Relay B integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CHANNELS, DOMAIN, UNIT_ID
from .helpers import any_error, coalesce_bits

_LOGGER = logging.getLogger(__name__)

type WaveshareConfigEntry = ConfigEntry["WaveshareCoordinator"]


class WaveshareCoordinator(DataUpdateCoordinator[dict[str, list[bool]]]):
    """Polls the board with two batched reads per cycle."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: AsyncModbusTcpClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, list[bool]]:
        try:
            if not self.client.connected:
                await self.client.connect()
            di = await self.client.read_discrete_inputs(
                0, count=CHANNELS, device_id=UNIT_ID
            )
            co = await self.client.read_coils(0, count=CHANNELS, device_id=UNIT_ID)
        except (ModbusException, ConnectionError) as err:
            raise UpdateFailed(f"Modbus read failed: {err}") from err

        if any_error(di, co):
            raise UpdateFailed("Modbus response reported an error")

        return {
            "inputs": coalesce_bits(di.bits, CHANNELS),
            "relays": coalesce_bits(co.bits, CHANNELS),
        }

    async def async_set_relay(self, index: int, value: bool) -> None:
        """Write a single relay coil, then refresh to confirm via read-back."""
        try:
            result = await self.client.write_coil(index, value, device_id=UNIT_ID)
        except (ModbusException, ConnectionError) as err:
            raise UpdateFailed(f"Modbus write failed: {err}") from err
        if result.isError():
            raise UpdateFailed("Modbus write reported an error")
        await self.async_request_refresh()
