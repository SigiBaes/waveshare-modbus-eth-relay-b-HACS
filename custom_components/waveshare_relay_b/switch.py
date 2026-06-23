"""Switch (relay) platform for Waveshare Relay B."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_info import DeviceInfo
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
