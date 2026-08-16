"""Selects for PetKit BLE fountains."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PetkitBleConfigEntry
from .const import BRIGHTNESS_OPTIONS, MODE_OPTIONS
from .entity import PetkitBleEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PetkitBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up selects."""
    coordinator = entry.runtime_data
    async_add_entities(
        [PetkitBleModeSelect(coordinator), PetkitBleBrightnessSelect(coordinator)]
    )


class PetkitBleModeSelect(PetkitBleEntity, SelectEntity):
    """Normal runs the pump continuously; smart cycles it on a duty cycle."""

    _attr_translation_key = "mode"
    _attr_icon = "mdi:auto-mode"
    _attr_options = list(MODE_OPTIONS.values())

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "mode_select")

    @property
    def current_option(self) -> str | None:
        return MODE_OPTIONS.get(self._value("mode"))

    async def async_select_option(self, option: str) -> None:
        for value, name in MODE_OPTIONS.items():
            if name == option:
                await self.coordinator.async_run("mode", mode=value)
                return
        raise ValueError(f"unknown mode {option!r}")


class PetkitBleBrightnessSelect(PetkitBleEntity, SelectEntity):
    """Light ring brightness."""

    _attr_translation_key = "light_brightness"
    _attr_icon = "mdi:brightness-6"
    _attr_options = list(BRIGHTNESS_OPTIONS.values())
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "light_brightness_select")

    @property
    def current_option(self) -> str | None:
        return BRIGHTNESS_OPTIONS.get(self._value("light_brightness"))

    async def async_select_option(self, option: str) -> None:
        for value, name in BRIGHTNESS_OPTIONS.items():
            if name == option:
                await self.coordinator.async_run(
                    "settings", changes={"light_brightness": value}
                )
                return
        raise ValueError(f"unknown brightness {option!r}")
