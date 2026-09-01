"""The Waveshare Relay B integration."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from pymodbus.client import AsyncModbusTcpClient

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_OPTIMISTIC,
    CONF_RESTORE_ON_MISMATCH,
    CONF_SCAN_INTERVAL,
    DEFAULT_OPTIMISTIC,
    DEFAULT_PORT,
    DEFAULT_RESTORE_ON_MISMATCH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LEGACY_DEFAULT_SCAN_INTERVAL_S,
    MODBUS_TIMEOUT,
    PLATFORMS,
    SERVICE_SET_RELAYS,
)
from .coordinator import WaveshareConfigEntry, WaveshareCoordinator
from .relay_write import (
    RELAY_FIELD_NAMES,
    migrate_scan_interval_from_v1,
    normalize_device_ids,
    parse_set_relays_payload,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_RELAYS_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("states"): vol.All(cv.ensure_list, [cv.boolean]),
        **{vol.Optional(name): cv.boolean for name in RELAY_FIELD_NAMES},
    },
    extra=vol.ALLOW_EXTRA,
)


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_RELAYS):
        return

    async def async_set_relays(call: ServiceCall) -> ServiceResponse:
        try:
            bits = parse_set_relays_payload(dict(call.data))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        target_device_ids = normalize_device_ids(call.target.get("device_id"))
        device_ids = target_device_ids or normalize_device_ids(
            call.data.get("device_id")
        )
        if not device_ids:
            raise ServiceValidationError("Select a Waveshare Relay B device")
        registry = dr.async_get(hass)
        coordinators: list[WaveshareCoordinator] = []
        for device_id in device_ids:
            device = registry.async_get(device_id)
            if device is None:
                raise ServiceValidationError(f"Unknown device {device_id}")
            entry = None
            for entry_id in device.config_entries:
                candidate = hass.config_entries.async_get_entry(entry_id)
                if (
                    candidate is not None
                    and candidate.domain == DOMAIN
                    and candidate.state is ConfigEntryState.LOADED
                ):
                    entry = candidate
                    break
            if entry is None:
                raise ServiceValidationError(
                    f"Device {device_id} has no loaded {DOMAIN} entry"
                )
            coordinators.append(entry.runtime_data)
        results = await asyncio.gather(
            *(coord.async_set_relays(bits) for coord in coordinators),
            return_exceptions=True,
        )
        confirmed: list[list[bool]] = []
        for result in results:
            if isinstance(result, Exception):
                raise HomeAssistantError(str(result)) from result
            confirmed.append(result)
        return {
            "relays": [
                {"device_id": device_id, "states": states}
                for device_id, states in zip(device_ids, confirmed, strict=True)
            ]
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_RELAYS,
        async_set_relays,
        schema=SET_RELAYS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    _register_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version == 1:
        options = dict(entry.options)
        raw = options.get(CONF_SCAN_INTERVAL, LEGACY_DEFAULT_SCAN_INTERVAL_S)
        options[CONF_SCAN_INTERVAL] = migrate_scan_interval_from_v1(int(raw))
        options.setdefault(CONF_RESTORE_ON_MISMATCH, DEFAULT_RESTORE_ON_MISMATCH)
        options.setdefault(CONF_OPTIMISTIC, DEFAULT_OPTIMISTIC)
        hass.config_entries.async_update_entry(entry, options=options, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WaveshareConfigEntry) -> bool:
    _register_services(hass)
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    restore = entry.options.get(CONF_RESTORE_ON_MISMATCH, DEFAULT_RESTORE_ON_MISMATCH)
    optimistic = entry.options.get(CONF_OPTIMISTIC, DEFAULT_OPTIMISTIC)
    client = AsyncModbusTcpClient(
        host, port=port, timeout=MODBUS_TIMEOUT, retries=0, reconnect_delay=0
    )
    coordinator = WaveshareCoordinator(
        hass,
        client,
        scan_interval,
        host,
        restore_on_mismatch=restore,
        optimistic=optimistic,
    )
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
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
