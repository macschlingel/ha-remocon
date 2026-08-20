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
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RemoconData
from .const import DOMAIN
from .coordinator import ElcoRemoconCoordinator


@dataclass(kw_only=True)
class ElcoBinarySensorDescription(BinarySensorEntityDescription):
    """Describe an Elco binary sensor entity."""

    key: str
    translation_key: str
    device_class: BinarySensorDeviceClass | None = None
    value_fn: Callable[[RemoconData], bool]
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
        key="automatic_thermoregulation",
        translation_key="automatic_thermoregulation",
        value_fn=lambda d: d.automatic_thermoregulation,
    ),
    ElcoBinarySensorDescription(
        key="zone_pilot_on",
        translation_key="zone_pilot_on",
        value_fn=lambda d: d.zone_pilot_on,
    ),
    ElcoBinarySensorDescription(
        key="holiday_active",
        translation_key="holiday_active",
        value_fn=lambda d: d.holiday_active,
    ),
    ElcoBinarySensorDescription(
        key="quiet_mode",
        translation_key="quiet_mode",
        value_fn=lambda d: d.quiet_mode,
    ),
    ElcoBinarySensorDescription(
        key="dhw_boost",
        translation_key="dhw_boost",
        value_fn=lambda d: d.dhw_boost,
    ),
    ElcoBinarySensorDescription(
        key="resistor_on",
        translation_key="resistor_on",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: d.resistor_on,
    ),
    ElcoBinarySensorDescription(
        key="heating_auto_function",
        translation_key="heating_auto_function",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.settings.get("U6_3_3")),
        exists_fn=lambda d: "U6_3_3" in d.settings,
    ),
    ElcoBinarySensorDescription(
        key="summer_winter_automatic",
        translation_key="summer_winter_automatic",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.settings.get("U6_3_5_0_0")),
        exists_fn=lambda d: "U6_3_5_0_0" in d.settings,
    ),
    ElcoBinarySensorDescription(
        key="legionella_protection",
        translation_key="legionella_protection",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.settings.get("U6_9_5_0")),
        exists_fn=lambda d: "U6_9_5_0" in d.settings,
    ),
    ElcoBinarySensorDescription(
        key="buffer_charging",
        translation_key="buffer_charging",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.settings.get("U6_10_0")),
        exists_fn=lambda d: "U6_10_0" in d.settings,
    ),
    ElcoBinarySensorDescription(
        key="internet_time",
        translation_key="internet_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.settings.get("U6_16_6")),
        exists_fn=lambda d: "U6_16_6" in d.settings,
    ),
    ElcoBinarySensorDescription(
        key="internet_weather",
        translation_key="internet_weather",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.settings.get("U6_16_7")),
        exists_fn=lambda d: "U6_16_7" in d.settings,
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

    entities = [
        ElcoBinarySensor(coordinator, gw_id, desc)
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
        description: ElcoBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{gw_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, gw_id)},
            "name": "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": "Aerotop Split 12.2 M-RX",
        }

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return self.entity_description.value_fn(self.coordinator.data)
