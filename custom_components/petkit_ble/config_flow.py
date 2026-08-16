"""Config flow for PetKit BLE."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_ADDRESS,
    CONF_ALIAS,
    CONF_KEEP_ALIVE,
    CONF_MODEL,
    CONF_SCAN_INTERVAL,
    CONF_SECRET,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_MODELS,
    DOMAIN,
    KEEP_ALIVE_OPTIONS,
    NAME_PREFIXES,
    SECRET_LENGTH,
    SUPPORTED_ALIASES,
)
from .protocol import parse_service_data

_LOGGER = logging.getLogger(__name__)


class InvalidSecret(Exception):
    """The pasted secret is not usable hex."""


def _normalise_secret(raw: str | None) -> str | None:
    """Accept the secret in the shapes people actually paste it in.

    The API hands these out shorter than the 8 bytes the firmware wants - a
    CTW3 returns 6 - and the app left-pads with zeros before sending them
    (``ByteUtil.makeUpBtyesForward``). Pad here so everything downstream sees
    a uniform 8 bytes.
    """
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip().lower().replace(" ", "").replace(":", "").replace("-", "")
    cleaned = cleaned.removeprefix("0x")
    try:
        value = bytes.fromhex(cleaned)
    except ValueError as err:
        raise InvalidSecret from err
    if not 1 <= len(value) <= SECRET_LENGTH:
        raise InvalidSecret
    return value.rjust(SECRET_LENGTH, b"\x00").hex()


def _identify(info: BluetoothServiceInfoBleak) -> dict[str, Any] | None:
    """Work out which fountain an advertisement belongs to.

    Byte 5 of the service data is the model id; the local name is the
    fallback when a proxy strips service data.
    """
    identifier = parse_service_data(info.service_data)
    if identifier is not None and identifier in DEVICE_MODELS:
        return dict(DEVICE_MODELS[identifier])

    name = info.name or ""
    for entry in DEVICE_MODELS.values():
        if name == entry["name"]:
            return dict(entry)
    if any(name.startswith(prefix) for prefix in NAME_PREFIXES):
        # Known family, unknown revision - assume the CTW3 command set.
        return {"name": name, "alias": "CTW3", "model": name, "type": 24}
    return None


class PetkitBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PetKit BLE."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, dict[str, Any]] = {}
        self._address: str | None = None
        self._info: dict[str, Any] | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a fountain found by the Bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        info = _identify(discovery_info)
        if info is None or info["alias"] not in SUPPORTED_ALIASES:
            return self.async_abort(reason="not_supported")

        self._address = discovery_info.address
        self._info = info
        self.context["title_placeholders"] = {"name": str(info["model"])}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered fountain."""
        assert self._address is not None and self._info is not None

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                return self._create_entry(self._address, self._info, user_input)
            except InvalidSecret:
                errors[CONF_SECRET] = "invalid_secret"

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Optional(CONF_SECRET, default=""): str}),
            errors=errors,
            description_placeholders={
                "name": str(self._info["model"]),
                "address": self._address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a fountain from the ones currently advertising."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            try:
                entry = self._create_entry(
                    address, self._discovered[address], user_input
                )
            except InvalidSecret:
                errors[CONF_SECRET] = "invalid_secret"
            else:
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return entry

        current = self._async_current_ids()
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in current or info.address in self._discovered:
                continue
            identified = _identify(info)
            if identified is not None and identified["alias"] in SUPPORTED_ALIASES:
                self._discovered[info.address] = identified

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{info['model']} ({address})"
                            for address, info in self._discovered.items()
                        }
                    ),
                    vol.Optional(CONF_SECRET, default=""): str,
                }
            ),
            errors=errors,
        )

    def _create_entry(
        self, address: str, info: dict[str, Any], user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        data: dict[str, Any] = {
            CONF_ADDRESS: address,
            CONF_ALIAS: info["alias"],
            CONF_MODEL: info["model"],
        }
        secret = _normalise_secret(user_input.get(CONF_SECRET))
        if secret:
            data[CONF_SECRET] = secret

        return self.async_create_entry(title=str(info["model"]), data=data)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> PetkitBleOptionsFlow:
        return PetkitBleOptionsFlow()


class PetkitBleOptionsFlow(OptionsFlow):
    """Options for a configured fountain."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=30, max=3600, step=30, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_KEEP_ALIVE,
                        default=options.get(CONF_KEEP_ALIVE, DEFAULT_KEEP_ALIVE),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list(KEEP_ALIVE_OPTIONS),
                            translation_key=CONF_KEEP_ALIVE,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )
