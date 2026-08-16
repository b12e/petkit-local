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
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .device import PetkitAuthError, PetkitConnectionError, PetkitFountain
from .visits import VisitTracker

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
        self.visits = VisitTracker()
        fountain.register_push_callback(self._on_push)
        fountain.register_history_callback(self._on_history)

    @callback
    def _on_push(self, state: dict[str, Any]) -> None:
        """Publish a device-initiated update without waiting for the poll."""
        self._track_visit(state)
        self.async_set_updated_data(dict(state))

    @callback
    def _on_history(self, records: list[Any]) -> None:
        """Bank visit records the fountain recorded while we were away."""
        if self.visits.ingest(records, dt_util.now()) and self.data is not None:
            self.async_set_updated_data(dict(self.data))

    @callback
    def _track_visit(self, state: dict[str, Any]) -> None:
        """Fold the detection flag into the visit statistics."""
        detected = state.get("detect_status")
        if detected is None:
            return
        self.visits.update(bool(detected), dt_util.now())

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
            state = await self.fountain.async_poll(device)
            self._track_visit(state)
            return state
        except PetkitAuthError as err:
            raise UpdateFailed(f"authentication failed: {err}") from err
        except (PetkitConnectionError, TimeoutError) as err:
            raise UpdateFailed(f"communication failed: {err}") from err

    @callback
    def async_assume(self, **values: Any) -> None:
        """Publish an expected state change straight away.

        A command is a BLE round trip, so waiting for confirmation makes every
        toggle feel broken. Show the intended value now; the refresh that
        follows the command corrects it if the fountain disagreed.
        """
        if self.data is None:
            return
        self.async_set_updated_data({**self.data, **values})

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
