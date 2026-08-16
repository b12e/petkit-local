"""Numbers for PetKit BLE fountains.

These configure the smart-mode duty cycle: how long the pump runs and how
long it sleeps, separately for mains and battery operation.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PetkitBleConfigEntry
from .entity import PetkitBleEntity


@dataclass(frozen=True, kw_only=True)
class PetkitNumberDescription(NumberEntityDescription):
    """Describes a writable settings-block number."""

    setting_key: str


NUMBERS: tuple[PetkitNumberDescription, ...] = (
    PetkitNumberDescription(
        key="smart_working_time",
        setting_key="smart_working_time",
        translation_key="smart_working_time",
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer-play",
    ),
    PetkitNumberDescription(
        key="smart_sleep_time",
        setting_key="smart_sleep_time",
        translation_key="smart_sleep_time",
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer-pause",
    ),
    PetkitNumberDescription(
        key="battery_working_time",
        setting_key="battery_working_time",
        translation_key="battery_working_time",
        native_min_value=15,
        native_max_value=300,
        native_step=15,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer-play-outline",
    ),
    PetkitNumberDescription(
        key="battery_sleep_time",
        setting_key="battery_sleep_time",
        translation_key="battery_sleep_time",
        native_min_value=1,
        native_max_value=180,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer-pause-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PetkitBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up numbers."""
    async_add_entities(
        PetkitBleNumber(entry.runtime_data, description) for description in NUMBERS
    )


class PetkitBleNumber(PetkitBleEntity, NumberEntity):
    """One writable field of the settings block."""

    entity_description: PetkitNumberDescription

    def __init__(self, coordinator, description: PetkitNumberDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        value = self._value(self.entity_description.setting_key)
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_run(
            "settings", changes={self.entity_description.setting_key: int(value)}
        )
