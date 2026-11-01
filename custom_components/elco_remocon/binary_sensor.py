"""Binary sensor entities for Elco Remocon-Net."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RemoconData
from .const import CONF_ZONE, DEFAULT_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator
from .identifiers import entity_unique_id


@dataclass(kw_only=True)
class ElcoBinarySensorDescription(BinarySensorEntityDescription):
    """Describe an Elco binary sensor entity."""

    key: str
    translation_key: str
    device_class: BinarySensorDeviceClass | None = None
    value_fn: Callable[[RemoconData], bool | None]
    exists_fn: Callable[[RemoconData], bool] = lambda _: True


BINARY_SENSORS: tuple[ElcoBinarySensorDescription, ...] = (
    ElcoBinarySensorDescription(
        key="heating_active",
        translation_key="heating_active",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda d: d.heating_active,
    ),
    ElcoBinarySensorDescription(
        key="cooling_active",
        translation_key="cooling_active",
        device_class=BinarySensorDeviceClass.COLD,
        value_fn=lambda d: d.cooling_active,
    ),
    ElcoBinarySensorDescription(
        key="heat_pump_on",
        translation_key="heat_pump_on",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: d.heat_pump_on,
    ),
    ElcoBinarySensorDescription(
        key="dhw_enabled",
        translation_key="dhw_enabled",
        value_fn=lambda d: d.dhw_enabled,
    ),
    ElcoBinarySensorDescription(
        key="flame_sensor",
        translation_key="flame_sensor",
        value_fn=lambda d: d.flame_sensor,
        exists_fn=lambda d: d.flame_sensor is not None,
    ),
    ElcoBinarySensorDescription(
        key="heat_or_cool_request",
        translation_key="heat_or_cool_request",
        value_fn=lambda d: d.heat_or_cool_request,
        exists_fn=lambda d: d.heat_or_cool_request is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elco binary sensors."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]
    gw_id = entry.data["gateway_id"]
    zone = entry.data.get(CONF_ZONE, DEFAULT_ZONE)

    entities = [
        ElcoBinarySensor(coordinator, gw_id, zone, desc)
        for desc in BINARY_SENSORS
        if desc.exists_fn(coordinator.data)
    ]
    async_add_entities(entities)


class ElcoBinarySensor(CoordinatorEntity[ElcoRemoconCoordinator], BinarySensorEntity):
    """Elco binary sensor entity."""

    _attr_has_entity_name = True
    entity_description: ElcoBinarySensorDescription

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        gw_id: str,
        zone: str,
        description: ElcoBinarySensorDescription,
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
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return self.entity_description.value_fn(self.coordinator.data)
