"""Sensor entities for Elco Remocon-Net."""

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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RemoconData
from .const import CONF_ZONE, DEFAULT_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator
from .identifiers import entity_unique_id


@dataclass(kw_only=True)
class ElcoSensorDescription(SensorEntityDescription):
    """Describe an Elco sensor entity."""

    key: str
    translation_key: str
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    native_unit_of_measurement: str | None = None
    entity_category: EntityCategory | None = None
    suggested_display_precision: int = 1
    value_fn: Callable[[RemoconData], StateType]
    exists_fn: Callable[[RemoconData], bool] = lambda _: True


SENSORS: tuple[ElcoSensorDescription, ...] = (
    ElcoSensorDescription(
        key="outside_temp",
        translation_key="outside_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.outside_temp,
    ),
    ElcoSensorDescription(
        key="desired_temp",
        translation_key="desired_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.desired_temp if d.desired_temp > 0 else None,
    ),
    ElcoSensorDescription(
        key="reduced_temp",
        translation_key="reduced_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.reduced_temp if d.reduced_temp > 0 else None,
    ),
    ElcoSensorDescription(
        key="flow_temp",
        translation_key="flow_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.flow_temperature,
        exists_fn=lambda d: d.flow_temperature is not None,
    ),
    ElcoSensorDescription(
        key="system_pressure",
        translation_key="system_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.system_pressure,
        exists_fn=lambda d: d.system_pressure is not None,
    ),
    ElcoSensorDescription(
        key="dhw_temp",
        translation_key="dhw_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.dhw_temp if d.dhw_temp > 0 else None,
        exists_fn=lambda d: d.dhw_enabled,
    ),
    ElcoSensorDescription(
        key="dhw_comfort_temp",
        translation_key="dhw_comfort_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.dhw_comfort_temp,
        exists_fn=lambda d: d.dhw_comfort_temp is not None,
    ),
    ElcoSensorDescription(
        key="dhw_reduced_temp",
        translation_key="dhw_reduced_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.dhw_reduced_temp,
        exists_fn=lambda d: d.dhw_reduced_temp is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elco sensors."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]
    gw_id = entry.data["gateway_id"]
    zone = entry.data.get(CONF_ZONE, DEFAULT_ZONE)

    entities = [
        ElcoSensor(coordinator, gw_id, zone, desc)
        for desc in SENSORS
        if desc.exists_fn(coordinator.data)
    ]
    async_add_entities(entities)


class ElcoSensor(CoordinatorEntity[ElcoRemoconCoordinator], SensorEntity):
    """Elco sensor entity."""

    _attr_has_entity_name = True
    entity_description: ElcoSensorDescription

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        gw_id: str,
        zone: str,
        description: ElcoSensorDescription,
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
    def native_value(self) -> StateType:
        """Return sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)
