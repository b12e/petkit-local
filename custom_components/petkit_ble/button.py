"""Buttons for PetKit BLE fountains."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PetkitBleConfigEntry
from .entity import PetkitBleEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PetkitBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons."""
    async_add_entities([PetkitBleResetFilterButton(entry.runtime_data)])


class PetkitBleResetFilterButton(PetkitBleEntity, ButtonEntity):
    """Reset filter life back to 100% after a replacement."""

    _attr_translation_key = "reset_filter"
    _attr_icon = "mdi:air-filter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "reset_filter")

    async def async_press(self) -> None:
        await self.coordinator.async_run("reset_filter")
