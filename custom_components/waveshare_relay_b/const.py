"""Constants for the Waveshare Relay B integration."""
from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform

DOMAIN = "waveshare_relay_b"

# Config keys
CONF_SCAN_INTERVAL = "scan_interval"

# Defaults / fixed values
DEFAULT_PORT = 502
UNIT_ID = 1  # board Modbus address is fixed; not user-configurable
DEFAULT_SCAN_INTERVAL = 5
CHANNELS = 8

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SWITCH]

__all__ = [
    "DOMAIN",
    "CONF_HOST",
    "CONF_PORT",
    "CONF_SCAN_INTERVAL",
    "DEFAULT_PORT",
    "UNIT_ID",
    "DEFAULT_SCAN_INTERVAL",
    "CHANNELS",
    "PLATFORMS",
]
