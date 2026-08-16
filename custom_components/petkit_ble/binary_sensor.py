"""Binary sensors for PetKit BLE fountains."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PetkitBleConfigEntry
from .entity import PetkitBleEntity

BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="warning_water_missing",
        translation_key="water_missing",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:water-alert",
    ),
    BinarySensorEntityDescription(
        key="warning_filter",
        translation_key="filter_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:air-filter",
    ),
    BinarySensorEntityDescription(
        key="warning_breakdown",
        translation_key="breakdown",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    BinarySensorEntityDescription(
        key="warning_low_battery",
        translation_key="low_battery",
        device_class=BinarySensorDeviceClass.BATTERY,
    ),
    BinarySensorEntityDescription(
        key="detect_status",
        translation_key="pet_detected",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        icon="mdi:cat",
    ),
    BinarySensorEntityDescription(
        key="electric_status",
        translation_key="mains_powered",
        device_class=BinarySensorDeviceClass.PLUG,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key="night_dnd_active",
        translation_key="night_dnd_active",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:sleep",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PetkitBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    async_add_entities(
        PetkitBleBinarySensor(entry.runtime_data, description)
        for description in BINARY_SENSORS
    )


class PetkitBleBinarySensor(PetkitBleEntity, BinarySensorEntity):
    """A flag read off the fountain."""

    def __init__(self, coordinator, description: BinarySensorEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        value = self._value(self.entity_description.key)
        return None if value is None else bool(value)
