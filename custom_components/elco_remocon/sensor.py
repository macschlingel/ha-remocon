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
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RemoconData
from .const import DOMAIN
from .coordinator import ElcoRemoconCoordinator


def _energy_ratio(data: RemoconData, period: str, series: str) -> float | None:
    """Return produced energy divided by consumed electricity."""
    consumed = data.energy.get(f"consumed_{period}_{series}")
    produced = data.energy.get(f"produced_{period}_{series}")
    if not isinstance(consumed, (int, float)) or consumed <= 0:
        return None
    if not isinstance(produced, (int, float)):
        return None
    return round(produced / consumed, 2)


def _setting_int(data: RemoconData, key: str) -> int:
    """Return a menu setting as an integer enum value."""
    try:
        return int(data.settings.get(key, -1))
    except (TypeError, ValueError):
        return -1


@dataclass(kw_only=True)
class ElcoSensorDescription(SensorEntityDescription):
    """Describe an Elco sensor entity."""

    key: str
    translation_key: str
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    native_unit_of_measurement: str | None = None
    entity_category: EntityCategory | None = None
    suggested_display_precision: int | None = None
    value_fn: Callable[[RemoconData], StateType]
    exists_fn: Callable[[RemoconData], bool] = lambda _: True
    attributes_fn: Callable[[RemoconData], dict[str, Any]] | None = None


SENSORS: tuple[ElcoSensorDescription, ...] = (
    ElcoSensorDescription(
        key="room_temp",
        translation_key="room_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.room_temp,
        exists_fn=lambda d: d.has_room_sensor and d.room_temp is not None,
    ),
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
        value_fn=lambda d: d.desired_temp,
    ),
    ElcoSensorDescription(
        key="comfort_temp",
        translation_key="comfort_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.comfort_temp,
    ),
    ElcoSensorDescription(
        key="reduced_temp",
        translation_key="reduced_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.reduced_temp,
    ),
    ElcoSensorDescription(
        key="cooling_comfort_temp",
        translation_key="cooling_comfort_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.cooling_comfort_temp,
        exists_fn=lambda d: d.cooling_comfort_temp is not None,
    ),
    ElcoSensorDescription(
        key="cooling_reduced_temp",
        translation_key="cooling_reduced_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.cooling_reduced_temp,
        exists_fn=lambda d: d.cooling_reduced_temp is not None,
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
        value_fn=lambda d: d.dhw_temp,
        exists_fn=lambda d: d.dhw_temp is not None,
    ),
    ElcoSensorDescription(
        key="dhw_target_temp",
        translation_key="dhw_target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.dhw_target_temp,
        exists_fn=lambda d: d.dhw_target_temp is not None,
    ),
    ElcoSensorDescription(
        key="dhw_comfort_temp",
        translation_key="dhw_comfort_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.dhw_comfort_temp,
        exists_fn=lambda d: d.dhw_comfort_temp is not None,
    ),
    ElcoSensorDescription(
        key="dhw_reduced_temp",
        translation_key="dhw_reduced_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.dhw_reduced_temp,
        exists_fn=lambda d: d.dhw_reduced_temp is not None,
    ),
    ElcoSensorDescription(
        key="zone_mode",
        translation_key="zone_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["off", "manual", "time_program", "unknown"],
        value_fn=lambda d: {
            0: "off",
            2: "manual",
            3: "time_program",
        }.get(d.zone_mode, "unknown"),
    ),
    ElcoSensorDescription(
        key="plant_mode",
        translation_key="plant_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["summer", "winter", "heating_only", "cooling", "off", "unknown"],
        value_fn=lambda d: {
            0: "summer",
            1: "winter",
            2: "heating_only",
            3: "cooling",
            5: "off",
        }.get(d.plant_mode, "unknown"),
        exists_fn=lambda d: d.plant_mode is not None,
    ),
    ElcoSensorDescription(
        key="dhw_mode",
        translation_key="dhw_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["disabled", "time_based", "continuous", "unknown"],
        value_fn=lambda d: {
            0: "disabled",
            1: "time_based",
            2: "continuous",
        }.get(d.dhw_mode, "unknown"),
    ),
    ElcoSensorDescription(
        key="summer_winter_threshold",
        translation_key="summer_winter_threshold",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.settings.get("U6_3_5_0_1"),
        exists_fn=lambda d: "U6_3_5_0_1" in d.settings,
    ),
    ElcoSensorDescription(
        key="summer_winter_delay",
        translation_key="summer_winter_delay",
        native_unit_of_measurement="min",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.settings.get("U6_3_5_0_2"),
        exists_fn=lambda d: "U6_3_5_0_2" in d.settings,
    ),
    ElcoSensorDescription(
        key="legionella_interval",
        translation_key="legionella_interval",
        native_unit_of_measurement="h",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.settings.get("U6_9_5_1"),
        exists_fn=lambda d: "U6_9_5_1" in d.settings,
    ),
    ElcoSensorDescription(
        key="buffer_heating_comfort_temp",
        translation_key="buffer_heating_comfort_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.settings.get("U6_10_1"),
        exists_fn=lambda d: "U6_10_1" in d.settings,
    ),
    ElcoSensorDescription(
        key="buffer_heating_reduced_temp",
        translation_key="buffer_heating_reduced_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.settings.get("U6_10_2"),
        exists_fn=lambda d: "U6_10_2" in d.settings,
    ),
    ElcoSensorDescription(
        key="buffer_cooling_comfort_temp",
        translation_key="buffer_cooling_comfort_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.settings.get("U6_10_3"),
        exists_fn=lambda d: "U6_10_3" in d.settings,
    ),
    ElcoSensorDescription(
        key="buffer_cooling_reduced_temp",
        translation_key="buffer_cooling_reduced_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.settings.get("U6_10_4"),
        exists_fn=lambda d: "U6_10_4" in d.settings,
    ),
    ElcoSensorDescription(
        key="pv_dhw_temperature_increase",
        translation_key="pv_dhw_temperature_increase",
        native_unit_of_measurement="K",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.settings.get("U6_15_2"),
        exists_fn=lambda d: "U6_15_2" in d.settings,
    ),
    ElcoSensorDescription(
        key="wifi_signal_level",
        translation_key="wifi_signal_level",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.settings.get("U6_16_5"),
        exists_fn=lambda d: "U6_16_5" in d.settings,
    ),
    ElcoSensorDescription(
        key="buffer_setpoint_mode",
        translation_key="buffer_setpoint_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["fixed", "variable", "unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: {0: "fixed", 1: "variable"}.get(
            _setting_int(d, "U6_10_5"), "unknown"
        ),
        exists_fn=lambda d: "U6_10_5" in d.settings,
    ),
    ElcoSensorDescription(
        key="external_heat_source_heating_mode",
        translation_key="external_heat_source_heating_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["heat_integral_backup", "backup_only", "unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: {0: "heat_integral_backup", 1: "backup_only"}.get(
            _setting_int(d, "U6_13_0"), "unknown"
        ),
        exists_fn=lambda d: "U6_13_0" in d.settings,
    ),
    ElcoSensorDescription(
        key="external_heat_source_dhw_mode",
        translation_key="external_heat_source_dhw_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["heat_integral_backup", "backup_only", "unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: {0: "heat_integral_backup", 1: "backup_only"}.get(
            _setting_int(d, "U6_13_1"), "unknown"
        ),
        exists_fn=lambda d: "U6_13_1" in d.settings,
    ),
    ElcoSensorDescription(
        key="dhw_heat_pump_mode",
        translation_key="dhw_heat_pump_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["standard", "ecological", "heat_pump", "heat_pump_40", "unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: {
            0: "standard",
            1: "ecological",
            2: "heat_pump",
            3: "heat_pump_40",
        }.get(_setting_int(d, "U6_13_2"), "unknown"),
        exists_fn=lambda d: "U6_13_2" in d.settings,
    ),
) + tuple(
    ElcoSensorDescription(
        key=f"energy_{direction}_{period}_{series}",
        translation_key=f"energy_{direction}_{period}_{series}",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d, data_key=f"{direction}_{period}_{series}": d.energy.get(data_key),
        exists_fn=lambda d, data_key=f"{direction}_{period}_{series}": data_key in d.energy,
        attributes_fn=(
            (lambda d, data_key=f"{direction}_monthly_{series}": {"monthly_values": d.energy.get(data_key, [])})
            if period == "current_year" and series != "total"
            else None
        ),
    )
    for direction in ("consumed", "produced")
    for period in ("current_month", "current_year")
    for series in ("heating", "cooling", "dhw", "total")
) + tuple(
    ElcoSensorDescription(
        key=f"performance_factor_{period}_{series}",
        translation_key=f"performance_factor_{period}_{series}",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d, period_key=period, series_key=series: _energy_ratio(
            d, period_key, series_key
        ),
        exists_fn=lambda d, period_key=period, series_key=series: (
            f"consumed_{period_key}_{series_key}" in d.energy
            and f"produced_{period_key}_{series_key}" in d.energy
        ),
    )
    for period in ("current_month", "current_year")
    for series in ("heating", "cooling", "dhw", "total")
) + tuple(
    ElcoSensorDescription(
        key=f"time_program_{program}",
        translation_key=f"time_program_{program}",
        value_fn=lambda d, program_key=program: sum(
            len(entries) for entries in d.time_programs.get(program_key, {}).values()
        ),
        exists_fn=lambda d, program_key=program: program_key in d.time_programs,
        attributes_fn=lambda d, program_key=program: d.time_programs.get(program_key, {}),
        entity_category=EntityCategory.DIAGNOSTIC,
    )
    for program in ("zone", "dhw", "buffer")
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elco sensors."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]
    gw_id = entry.data["gateway_id"]

    entities = [
        ElcoSensor(coordinator, gw_id, desc)
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
        description: ElcoSensorDescription,
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
    def native_value(self) -> StateType:
        """Return sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return monthly energy series or normalized weekly plans."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)
