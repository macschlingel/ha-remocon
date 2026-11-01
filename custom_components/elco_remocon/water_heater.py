"""Water heater entity for Elco Remocon-Net — DHW control."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ZONE, DEFAULT_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator
from .identifiers import entity_unique_id
from .select import DHW_MODE_MAP, DHW_MODE_REVERSE

_LOGGER = logging.getLogger(__name__)

# Water heater operation modes
# HA uses STATE_* constants, but we map to our select option names
OPERATION_TIMED = "timed"
OPERATION_CONTINUOUS = "continuous"
OPERATION_OFF = "off"

OPERATION_LIST = [OPERATION_TIMED, OPERATION_CONTINUOUS, OPERATION_OFF]

# Map HA operation mode -> API DHW mode int
OPERATION_TO_API: dict[str, int] = {
    OPERATION_TIMED: DHW_MODE_MAP["timed"],       # 1
    OPERATION_CONTINUOUS: DHW_MODE_MAP["continuous"],  # 2
    OPERATION_OFF: DHW_MODE_MAP["disabled"],      # 0
}

# Map API DHW mode int -> HA operation mode
API_TO_OPERATION: dict[int, str] = {
    1: OPERATION_TIMED,
    2: OPERATION_CONTINUOUS,
    0: OPERATION_OFF,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elco water heater entity."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Only create water heater for flat R2 gateways with DHW
    if not coordinator.client.is_flat_api:
        _LOGGER.info(
            "Skipping water heater for gateway %s — flat R2 write API required",
            entry.data["gateway_id"],
        )
        return

    if not coordinator.data.dhw_enabled:
        _LOGGER.info(
            "Skipping water heater for gateway %s — DHW not available",
            entry.data["gateway_id"],
        )
        return

    gw_id = entry.data["gateway_id"]
    zone = entry.data.get(CONF_ZONE, DEFAULT_ZONE)
    async_add_entities([ElcoWaterHeater(coordinator, gw_id, zone)])


class ElcoWaterHeater(CoordinatorEntity[ElcoRemoconCoordinator], WaterHeaterEntity):
    """Water heater entity for DHW control."""

    _attr_has_entity_name = True
    _attr_translation_key = "dhw_water_heater"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
    )
    _attr_operation_list = OPERATION_LIST

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        gw_id: str,
        zone: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = entity_unique_id(gw_id, zone, "dhw_water_heater")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, gw_id)},
            "name": "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": "Aerotop SPK",
        }

    @property
    def current_temperature(self) -> float | None:
        """Return the current DHW storage temperature."""
        data = self.coordinator.data
        return data.dhw_temp if data.dhw_temp > 0 else None

    @property
    def target_temperature(self) -> float | None:
        """Return the target DHW comfort temperature."""
        data = self.coordinator.data
        return data.dhw_comfort_temp if data.dhw_comfort_temp and data.dhw_comfort_temp > 0 else None

    @property
    def min_temp(self) -> float:
        """Return the minimum DHW temperature."""
        return self.coordinator.data.dhw_comfort_temp_min

    @property
    def max_temp(self) -> float:
        """Return the maximum DHW temperature."""
        return self.coordinator.data.dhw_comfort_temp_max

    @property
    def current_operation(self) -> str | None:
        """Return the current operation mode."""
        data = self.coordinator.data
        return API_TO_OPERATION.get(data.dhw_mode, OPERATION_OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target DHW temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_dhw_comfort_temperature,
            float(temperature),
        )
        await self.coordinator.async_request_refresh()

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set new DHW operation mode."""
        api_mode = OPERATION_TO_API.get(operation_mode)
        if api_mode is None:
            _LOGGER.error("Unknown operation mode: %s", operation_mode)
            return
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_dhw_mode,
            api_mode,
        )
        await self.coordinator.async_request_refresh()
