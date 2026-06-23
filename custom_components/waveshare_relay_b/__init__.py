"""The Waveshare Relay B integration."""
from __future__ import annotations

from pymodbus.client import AsyncModbusTcpClient

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    PLATFORMS,
)
from .coordinator import WaveshareConfigEntry, WaveshareCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: WaveshareConfigEntry
) -> bool:
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    client = AsyncModbusTcpClient(host, port=port)
    coordinator = WaveshareCoordinator(hass, client, scan_interval)
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
    """Reload the entry when options (scan interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)
