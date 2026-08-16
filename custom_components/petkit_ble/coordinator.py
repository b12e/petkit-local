"""Update coordinator for PetKit BLE fountains."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .device import PetkitAuthError, PetkitConnectionError, PetkitFountain

_LOGGER = logging.getLogger(__name__)


class PetkitBleCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one fountain over BLE and relays its pushes."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        fountain: PetkitFountain,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {fountain.address}",
            update_interval=timedelta(seconds=scan_interval),
            config_entry=entry,
        )
        self.fountain = fountain
        self.rssi: int | None = None
        fountain.register_push_callback(self._on_push)

    @callback
    def _on_push(self, state: dict[str, Any]) -> None:
        """Publish a device-initiated update without waiting for the poll."""
        self.async_set_updated_data(dict(state))

    def _ble_device(self) -> BLEDevice:
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.fountain.address, connectable=True
        )
        if device is None:
            raise UpdateFailed(
                f"{self.fountain.address} is not in range of any Bluetooth adapter "
                "or proxy"
            )
        return device

    async def _async_update_data(self) -> dict[str, Any]:
        device = self._ble_device()

        service_info = bluetooth.async_last_service_info(
            self.hass, self.fountain.address, connectable=True
        )
        if service_info is not None:
            self.rssi = service_info.rssi

        try:
            return await self.fountain.async_poll(device)
        except PetkitAuthError as err:
            raise UpdateFailed(f"authentication failed: {err}") from err
        except (PetkitConnectionError, TimeoutError) as err:
            raise UpdateFailed(f"communication failed: {err}") from err

    async def async_run(self, action: str, **kwargs: Any) -> None:
        """Run a command on the fountain, then push fresh state to entities."""
        device = self._ble_device()
        fountain = self.fountain

        try:
            match action:
                case "power":
                    await fountain.async_set_power(device, kwargs["on"])
                case "mode":
                    await fountain.async_set_mode(device, kwargs["mode"])
                case "paused":
                    await fountain.async_set_paused(device, kwargs["paused"])
                case "reset_filter":
                    await fountain.async_reset_filter(device)
                case "settings":
                    await fountain.async_update_settings(device, **kwargs["changes"])
                case _:
                    raise ValueError(f"unknown action {action!r}")
        except (PetkitConnectionError, PetkitAuthError, TimeoutError) as err:
            raise UpdateFailed(f"command {action} failed: {err}") from err

        self.async_set_updated_data(dict(fountain.state))
