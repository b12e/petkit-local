"""Sensors for PetKit BLE fountains."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PetkitBleConfigEntry
from .const import BRIGHTNESS_OPTIONS, MODE_OPTIONS
from .entity import PetkitBleEntity

RUN_STATES = {0: "idle", 1: "running", 2: "paused"}


@dataclass(frozen=True, kw_only=True)
class PetkitSensorDescription(SensorEntityDescription):
    """Describes a PetKit sensor."""

    value_fn: Callable[[dict[str, Any]], Any] | None = None


SENSORS: tuple[PetkitSensorDescription, ...] = (
    PetkitSensorDescription(
        key="battery_percent",
        translation_key="battery_percent",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    PetkitSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    PetkitSensorDescription(
        key="supply_voltage",
        translation_key="supply_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    PetkitSensorDescription(
        key="filter_percent",
        translation_key="filter_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:air-filter",
    ),
    PetkitSensorDescription(
        key="filter_days_left",
        translation_key="filter_days_left",
        native_unit_of_measurement=UnitOfTime.DAYS,
        icon="mdi:calendar-clock",
    ),
    PetkitSensorDescription(
        key="pump_runtime_today",
        translation_key="pump_runtime_today",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:pump",
    ),
    PetkitSensorDescription(
        key="pump_runtime",
        translation_key="pump_runtime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:pump",
    ),
    PetkitSensorDescription(
        key="water_purified_today",
        translation_key="water_purified_today",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:water-check",
    ),
    PetkitSensorDescription(
        key="water_purified",
        translation_key="water_purified",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:water-check",
    ),
    PetkitSensorDescription(
        key="energy_consumed",
        translation_key="energy_consumed",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=4,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    PetkitSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(MODE_OPTIONS.values()),
        value_fn=lambda data: MODE_OPTIONS.get(data.get("mode")),
        icon="mdi:auto-mode",
    ),
    PetkitSensorDescription(
        key="running_status",
        translation_key="running_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(RUN_STATES.values()),
        value_fn=lambda data: RUN_STATES.get(data.get("running_status")),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    PetkitSensorDescription(
        key="light_brightness_level",
        translation_key="light_brightness_level",
        device_class=SensorDeviceClass.ENUM,
        options=list(BRIGHTNESS_OPTIONS.values()),
        value_fn=lambda data: BRIGHTNESS_OPTIONS.get(data.get("light_brightness")),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:brightness-6",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PetkitBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        PetkitBleSensor(coordinator, description) for description in SENSORS
    ]
    entities.append(PetkitBleRssiSensor(coordinator))
    async_add_entities(entities)


class PetkitBleSensor(PetkitBleEntity, SensorEntity):
    """A value read off the fountain."""

    entity_description: PetkitSensorDescription

    def __init__(self, coordinator, description: PetkitSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        if self.entity_description.value_fn is not None:
            return self.entity_description.value_fn(data)
        return data.get(self.entity_description.key)


class PetkitBleRssiSensor(PetkitBleEntity, SensorEntity):
    """Signal strength of the last advertisement we saw."""

    _attr_translation_key = "rssi"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "rssi")

    @property
    def native_value(self) -> int | None:
        return self.coordinator.rssi
