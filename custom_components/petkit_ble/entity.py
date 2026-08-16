"""Base entity for PetKit BLE fountains."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MODEL, DOMAIN
from .coordinator import PetkitBleCoordinator


class PetkitBleEntity(CoordinatorEntity[PetkitBleCoordinator]):
    """Common device wiring for every PetKit BLE entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PetkitBleCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        address = coordinator.fountain.address
        self._attr_unique_id = f"{address}_{key}"

        entry = coordinator.config_entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer="PetKit",
            model=entry.data.get(CONF_MODEL) if entry else None,
            name=entry.title if entry else address,
            serial_number=coordinator.fountain.serial,
            sw_version=coordinator.fountain.firmware,
        )

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data)

    def _value(self, key: str | None = None):
        """Read a field from the last poll."""
        data = self.coordinator.data or {}
        return data.get(key or self._key)
