"""The PetKit BLE integration - local control for Eversweet fountains."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ADDRESS,
    CONF_ALIAS,
    CONF_DEVICE_ID,
    CONF_SCAN_INTERVAL,
    CONF_SECRET,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import PetkitBleCoordinator
from .device import PetkitFountain

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type PetkitBleConfigEntry = ConfigEntry[PetkitBleCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: PetkitBleConfigEntry) -> bool:
    """Set up a fountain from a config entry."""
    address: str = entry.unique_id or entry.data[CONF_ADDRESS]
    secret_hex: str | None = entry.data.get(CONF_SECRET)
    fountain = PetkitFountain(
        address=address,
        alias=entry.data.get(CONF_ALIAS, "CTW3"),
        secret=bytes.fromhex(secret_hex) if secret_hex else None,
        device_id=entry.data.get(CONF_DEVICE_ID),
    )

    coordinator = PetkitBleCoordinator(
        hass,
        entry,
        fountain,
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    # The secret is only known after the first successful handshake; persist it
    # so a later restart reuses the same claim instead of re-writing cmd 73.
    if fountain.secret is not None and not secret_hex:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_SECRET: fountain.secret.hex(),
                CONF_DEVICE_ID: fountain.device_id,
            },
        )

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PetkitBleConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: PetkitBleConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
