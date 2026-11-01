"""Number entities for Elco Remocon-Net — writable temperature setpoints."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RemoconData
from .const import CONF_ZONE, DEFAULT_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator
from .identifiers import entity_unique_id

_LOGGER = logging.getLogger(__name__)


@dataclass(kw_only=True)
class ElcoNumberDescription(NumberEntityDescription):
    """Describe an Elco number entity."""

    key: str
    translation_key: str
    native_unit_of_measurement: str | None = UnitOfTemperature.CELSIUS
    entity_category: EntityCategory | None = EntityCategory.CONFIG
    value_fn: Any = None  # Callable[[RemoconData], float | None]
    min_fn: Any = None  # Callable[[RemoconData], float]
    max_fn: Any = None  # Callable[[RemoconData], float]
    step_fn: Any = None  # Callable[[RemoconData], float]
    set_fn: Any = None  # Callable[[RemoconClient, float], None]
    exists_fn: Any = None  # Callable[[RemoconData], bool]


NUMBERS: tuple[ElcoNumberDescription, ...] = (
    ElcoNumberDescription(
        key="dhw_comfort_temperature",
        translation_key="dhw_comfort_temperature",
        value_fn=lambda d: d.dhw_comfort_temp if d.dhw_comfort_temp and d.dhw_comfort_temp > 0 else None,
        min_fn=lambda d: d.dhw_comfort_temp_min,
        max_fn=lambda d: d.dhw_comfort_temp_max,
        step_fn=lambda d: d.dhw_comfort_temp_step,
        set_fn=lambda client, v: client.set_dhw_comfort_temperature(v),
        exists_fn=lambda d: d.dhw_enabled,
    ),
    ElcoNumberDescription(
        key="dhw_reduced_temperature",
        translation_key="dhw_reduced_temperature",
        value_fn=lambda d: d.dhw_reduced_temp if d.dhw_reduced_temp and d.dhw_reduced_temp > 0 else None,
        min_fn=lambda d: d.dhw_reduced_temp_min,
        max_fn=lambda d: d.dhw_reduced_temp_max,
        step_fn=lambda d: d.dhw_reduced_temp_step,
        set_fn=lambda client, v: client.set_dhw_reduced_temperature(v),
        exists_fn=lambda d: d.dhw_enabled,
    ),
    ElcoNumberDescription(
        key="zone_comfort_temperature",
        translation_key="zone_comfort_temperature",
        value_fn=lambda d: d.comfort_temp if d.comfort_temp > 0 else None,
        min_fn=lambda d: d.comfort_temp_min,
        max_fn=lambda d: d.comfort_temp_max,
        step_fn=lambda d: d.comfort_temp_step,
        set_fn=lambda client, v: client.set_zone_comfort_temperature(v),
        exists_fn=lambda _: True,
    ),
    ElcoNumberDescription(
        key="zone_reduced_temperature",
        translation_key="zone_reduced_temperature",
        value_fn=lambda d: d.reduced_temp if d.reduced_temp > 0 else None,
        min_fn=lambda d: d.comfort_temp_min,
        max_fn=lambda d: d.comfort_temp_max,
        step_fn=lambda d: d.comfort_temp_step,
        set_fn=lambda client, v: client.set_zone_reduced_temperature(v),
        exists_fn=lambda _: True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elco number entities."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Only create number entities for flat R2 gateways (Aerotop MONO etc.)
    if not coordinator.client.is_flat_api:
        _LOGGER.info(
            "Skipping number entities for gateway %s — flat R2 write API required",
            entry.data["gateway_id"],
        )
        return

    gw_id = entry.data["gateway_id"]
    zone = entry.data.get(CONF_ZONE, DEFAULT_ZONE)

    entities = [
        ElcoNumber(coordinator, gw_id, zone, desc)
        for desc in NUMBERS
        if desc.exists_fn(coordinator.data)
    ]
    async_add_entities(entities)


class ElcoNumber(CoordinatorEntity[ElcoRemoconCoordinator], NumberEntity):
    """Elco writable number entity."""

    _attr_has_entity_name = True
    entity_description: ElcoNumberDescription

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        gw_id: str,
        zone: str,
        description: ElcoNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = entity_unique_id(gw_id, zone, description.key)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, gw_id)},
            "name": "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": "Aerotop SPK",
        }

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def native_min_value(self) -> float:
        """Return the minimum value."""
        return self.entity_description.min_fn(self.coordinator.data)

    @property
    def native_max_value(self) -> float:
        """Return the maximum value."""
        return self.entity_description.max_fn(self.coordinator.data)

    @property
    def native_step(self) -> float:
        """Return the step value."""
        return self.entity_description.step_fn(self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        return self.entity_description.native_unit_of_measurement

    @property
    def entity_category(self) -> EntityCategory | None:
        """Return the entity category."""
        return self.entity_description.entity_category

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        await self.hass.async_add_executor_job(
            self.entity_description.set_fn,
            self.coordinator.client,
            value,
        )
        await self.coordinator.async_request_refresh()
