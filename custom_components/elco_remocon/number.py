"""Writable number entities for explicitly verified Remocon-Net controls."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ElcoRemoconCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the verified DHW comfort-temperature control."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.data.dhw_comfort_temp is not None:
        async_add_entities([ElcoDhwComfortTemperature(coordinator, entry)])


class ElcoDhwComfortTemperature(
    CoordinatorEntity[ElcoRemoconCoordinator], NumberEntity
):
    """Control the DHW time-program comfort temperature."""

    _attr_has_entity_name = True
    _attr_translation_key = "dhw_comfort_temperature"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = 35.0
    _attr_native_max_value = 65.0
    _attr_native_step = 1.0
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        gateway_id = entry.data["gateway_id"]
        self._attr_unique_id = f"{gateway_id}_dhw_comfort_temperature_control"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, gateway_id)},
            "name": "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": "Aerotop Split 12.2 M-RX",
        }

    @property
    def native_value(self) -> float | None:
        """Return the currently confirmed DHW comfort temperature."""
        return self.coordinator.data.dhw_comfort_temp

    async def async_set_native_value(self, value: float) -> None:
        """Set and verify a new DHW comfort temperature."""
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_dhw_comfort_temperature, value
        )
        await self.coordinator.async_request_refresh()
