"""Config and options flow for Waveshare Relay B."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback

from .const import (
    CONFIG_VERSION,
    CONF_OPTIMISTIC,
    CONF_RESTORE_ON_MISMATCH,
    CONF_SCAN_INTERVAL,
    DEFAULT_OPTIMISTIC,
    DEFAULT_PORT,
    DEFAULT_RESTORE_ON_MISMATCH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL_MS,
    MIN_SCAN_INTERVAL_MS,
)


class WaveshareConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = CONFIG_VERSION

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_HOST],
                data={
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                },
                options={CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL]},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): vol.All(
                    int,
                    vol.Range(min=MIN_SCAN_INTERVAL_MS, max=MAX_SCAN_INTERVAL_MS),
                ),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "WaveshareOptionsFlow":
        return WaveshareOptionsFlow()


class WaveshareOptionsFlow(OptionsFlow):
    """Edit poll interval and write behaviour after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_scan = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current_scan): vol.All(
                    int,
                    vol.Range(min=MIN_SCAN_INTERVAL_MS, max=MAX_SCAN_INTERVAL_MS),
                ),
                vol.Required(
                    CONF_RESTORE_ON_MISMATCH,
                    default=self.config_entry.options.get(
                        CONF_RESTORE_ON_MISMATCH, DEFAULT_RESTORE_ON_MISMATCH
                    ),
                ): bool,
                vol.Required(
                    CONF_OPTIMISTIC,
                    default=self.config_entry.options.get(
                        CONF_OPTIMISTIC, DEFAULT_OPTIMISTIC
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
