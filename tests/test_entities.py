"""Exercise platform setup and entity values with small Home Assistant stubs."""

import asyncio
import importlib
import sys
import types
from dataclasses import dataclass
from enum import IntFlag, StrEnum
from unittest.mock import patch

import pytest

from custom_components.elco_remocon.api import RemoconClient, RemoconData
from custom_components.elco_remocon.const import DOMAIN, MODE_AUTOMATIC


NEW_SENSOR_KEYS = {"dhw_comfort_temp", "dhw_reduced_temp"}
NEW_BINARY_KEYS = {"flame_sensor", "heat_or_cool_request"}
EXISTING_SENSOR_KEYS = {
    "outside_temp", "desired_temp", "reduced_temp", "flow_temp",
    "system_pressure", "dhw_temp",
}
EXISTING_BINARY_KEYS = {
    "heating_active", "cooling_active", "heat_pump_on", "dhw_enabled",
}


@pytest.fixture(scope="module")
def platforms():
    """Provide only the HA imports required by the three platform modules."""

    class Entity:
        @property
        def unique_id(self):
            return self._attr_unique_id

    class CoordinatorEntity(Entity):
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator

    @dataclass(kw_only=True)
    class EntityDescription:
        key: str
        translation_key: str

    class ClimateEntityFeature(IntFlag):
        TARGET_TEMPERATURE = 1
        PRESET_MODE = 2

    class HVACMode(StrEnum):
        OFF = "off"
        AUTO = "auto"
        HEAT = "heat"

    class HVACAction(StrEnum):
        OFF = "off"
        HEATING = "heating"
        COOLING = "cooling"
        IDLE = "idle"

    class WaterHeaterEntityFeature(IntFlag):
        TARGET_TEMPERATURE = 1
        OPERATION_MODE = 2

    with pytest.MonkeyPatch.context() as monkeypatch:
        def module(name, **attributes):
            value = types.ModuleType(name)
            value.__dict__.update(attributes)
            monkeypatch.setitem(sys.modules, name, value)
            return value

        module("homeassistant.components", __path__=[])
        module(
            "homeassistant.components.sensor",
            SensorDeviceClass=types.SimpleNamespace(TEMPERATURE="temperature", PRESSURE="pressure"),
            SensorStateClass=types.SimpleNamespace(MEASUREMENT="measurement"),
            SensorEntity=type("SensorEntity", (Entity,), {}),
            SensorEntityDescription=EntityDescription,
        )
        module(
            "homeassistant.components.binary_sensor",
            BinarySensorDeviceClass=types.SimpleNamespace(HEAT="heat", COLD="cold", RUNNING="running"),
            BinarySensorEntity=type("BinarySensorEntity", (Entity,), {}),
            BinarySensorEntityDescription=EntityDescription,
        )
        module(
            "homeassistant.components.climate",
            ClimateEntity=type("ClimateEntity", (Entity,), {}),
            ClimateEntityFeature=ClimateEntityFeature,
            HVACMode=HVACMode,
            HVACAction=HVACAction,
        )
        module(
            "homeassistant.components.select",
            SelectEntity=type("SelectEntity", (Entity,), {}),
            SelectEntityDescription=EntityDescription,
        )
        module(
            "homeassistant.components.number",
            NumberEntity=type("NumberEntity", (Entity,), {}),
            NumberEntityDescription=EntityDescription,
        )
        module(
            "homeassistant.components.switch",
            SwitchEntity=type("SwitchEntity", (Entity,), {}),
            SwitchEntityDescription=EntityDescription,
        )
        module(
            "homeassistant.components.water_heater",
            WaterHeaterEntity=type("WaterHeaterEntity", (Entity,), {}),
            WaterHeaterEntityFeature=WaterHeaterEntityFeature,
        )
        module("homeassistant.helpers.entity_platform", AddConfigEntryEntitiesCallback=object)
        module("homeassistant.helpers.typing", StateType=object)
        const = sys.modules["homeassistant.const"]
        monkeypatch.setattr(const, "EntityCategory", types.SimpleNamespace(DIAGNOSTIC="diagnostic", CONFIG="config"), raising=False)
        monkeypatch.setattr(const, "UnitOfPressure", types.SimpleNamespace(BAR="bar"), raising=False)
        monkeypatch.setattr(const, "UnitOfTemperature", types.SimpleNamespace(CELSIUS="°C"), raising=False)
        monkeypatch.setattr(const, "ATTR_TEMPERATURE", "temperature", raising=False)
        monkeypatch.setattr(
            sys.modules["homeassistant.helpers.update_coordinator"],
            "CoordinatorEntity", CoordinatorEntity, raising=False,
        )
        names = ("sensor", "binary_sensor", "climate", "select", "number", "switch", "water_heater")
        module_names = [f"custom_components.elco_remocon.{name}" for name in names]
        for name in module_names:
            monkeypatch.delitem(sys.modules, name, raising=False)
        loaded = {
            name: importlib.import_module(module_name)
            for name, module_name in zip(names, module_names)
        }
        try:
            yield types.SimpleNamespace(**loaded)
        finally:
            for name in module_names:
                sys.modules.pop(name, None)


def setup_entities(platforms, data, zone="1", flat_api=False):
    coordinator = types.SimpleNamespace(
        data=data, client=types.SimpleNamespace(is_flat_api=flat_api),
    )
    entry_data = {"gateway_id": "GW123"}
    if zone is not None:
        entry_data["zone"] = zone
    entry = types.SimpleNamespace(entry_id="entry", data=entry_data)
    hass = types.SimpleNamespace(data={DOMAIN: {entry.entry_id: coordinator}})
    sensors = []
    binaries = []
    selects = []
    numbers = []
    switches = []
    water_heaters = []
    asyncio.run(platforms.sensor.async_setup_entry(hass, entry, sensors.extend))
    asyncio.run(platforms.binary_sensor.async_setup_entry(hass, entry, binaries.extend))
    asyncio.run(platforms.select.async_setup_entry(hass, entry, selects.extend))
    asyncio.run(platforms.number.async_setup_entry(hass, entry, numbers.extend))
    asyncio.run(platforms.switch.async_setup_entry(hass, entry, switches.extend))
    asyncio.run(platforms.water_heater.async_setup_entry(hass, entry, water_heaters.extend))
    return types.SimpleNamespace(
        coordinator=coordinator, entry=entry, hass=hass,
        sensors={entity.entity_description.key: entity for entity in sensors},
        binaries={entity.entity_description.key: entity for entity in binaries},
        selects={entity.entity_description.key: entity for entity in selects},
        numbers={entity.entity_description.key: entity for entity in numbers},
        switches={entity.entity_description.key: entity for entity in switches},
        water_heaters=water_heaters,
    )


def test_existing_sensor_and_binary_entities_are_preserved(platforms):
    state = setup_entities(platforms, RemoconData(
        outside_temp=-4.5, desired_temp=21.5, reduced_temp=18.0,
        flow_temperature=32.5, system_pressure=1.8, dhw_temp=48.0,
        dhw_enabled=True, dhw_available=True, heating_active=True,
        cooling_active=False, heat_pump_on=True,
    ))

    assert set(state.sensors) == EXISTING_SENSOR_KEYS
    assert set(state.binaries) == EXISTING_BINARY_KEYS
    assert {key: entity.native_value for key, entity in state.sensors.items()} == {
        "outside_temp": -4.5, "desired_temp": 21.5, "reduced_temp": 18.0,
        "flow_temp": 32.5, "system_pressure": 1.8, "dhw_temp": 48.0,
    }
    assert {key: entity.is_on for key, entity in state.binaries.items()} == {
        "heating_active": True, "cooling_active": False,
        "heat_pump_on": True, "dhw_enabled": True,
    }


def test_unknown_optional_fields_do_not_create_new_entities(platforms):
    state = setup_entities(platforms, RemoconData())

    assert not (set(state.sensors) & NEW_SENSOR_KEYS)
    assert not (set(state.binaries) & NEW_BINARY_KEYS)
    assert set(state.binaries) == EXISTING_BINARY_KEYS


@pytest.mark.parametrize("field, value", [
    ("flame_sensor", True),
    ("flame_sensor", False),
    ("heat_or_cool_request", True),
    ("heat_or_cool_request", False),
    ("dhw_comfort_temp", 0.0),
    ("dhw_reduced_temp", 0.0),
])
def test_each_optional_entity_is_created_independently_for_reported_values(platforms, field, value):
    state = setup_entities(platforms, RemoconData(**{field: value}))

    created_new = (set(state.sensors) & NEW_SENSOR_KEYS) | (set(state.binaries) & NEW_BINARY_KEYS)
    assert created_new == {field}
    assert state.coordinator.data.dhw_enabled is False
    if field in NEW_SENSOR_KEYS:
        assert state.sensors[field].native_value == value
    else:
        assert state.binaries[field].is_on is value


@pytest.mark.parametrize("zone, prefix", [
    (None, "GW123_"),
    ("1", "GW123_"),
    ("2", "GW123_zone_2_"),
])
def test_new_entities_use_stable_zone_ids_and_exact_translation_keys(platforms, zone, prefix):
    state = setup_entities(platforms, RemoconData(
        flame_sensor=False, heat_or_cool_request=True,
        dhw_comfort_temp=50.5, dhw_reduced_temp=40.5,
    ), zone=zone)

    for key in NEW_SENSOR_KEYS | NEW_BINARY_KEYS:
        entity = state.sensors.get(key) or state.binaries[key]
        assert entity.unique_id == f"{prefix}{key}"
        assert entity.entity_description.translation_key == key
    for key in NEW_SENSOR_KEYS:
        description = state.sensors[key].entity_description
        assert description.device_class == "temperature"
        assert description.native_unit_of_measurement == "°C"
        assert description.state_class is None


def test_optional_entities_return_unknown_when_values_disappear(platforms):
    state = setup_entities(platforms, RemoconData(
        flame_sensor=True, heat_or_cool_request=False,
        dhw_comfort_temp=50.5, dhw_reduced_temp=40.5,
    ))
    assert state.binaries["flame_sensor"].is_on is True
    assert state.binaries["heat_or_cool_request"].is_on is False
    assert state.sensors["dhw_comfort_temp"].native_value == 50.5
    assert state.sensors["dhw_reduced_temp"].native_value == 40.5

    state.coordinator.data = RemoconData()

    assert all(state.binaries[key].is_on is None for key in NEW_BINARY_KEYS)
    assert all(state.sensors[key].native_value is None for key in NEW_SENSOR_KEYS)


@pytest.mark.parametrize("flat_api", [False, True], ids=["legacy", "flat"])
@pytest.mark.parametrize("reported_state", [True, False, None], ids=["on", "off", "missing"])
def test_api_values_create_supported_entities_while_dhw_is_off(platforms, flat_api, reported_state):
    client = RemoconClient("e@example.com", "pw", "GW123")
    client._flat_api = flat_api
    response = (
        {"ok": True, "data": {"items": [
            {"id": "DhwMode", "value": "0"},
            *([] if reported_state is None else [
                {"id": "ZoneHeatRequest", "value": reported_state},
            ]),
            {"id": "DhwTimeProgComfortTemp", "value": "50.5"},
            {"id": "DhwTimeProgEconomyTemp", "value": "0"},
        ]}}
        if flat_api else {"ok": True, "data": {
            "plantData": {
                **({} if reported_state is None else {"flameSensor": reported_state}),
                "dhwEnabled": False,
                "dhwComfortTemp": {"value": "50.5"},
                "dhwReducedTemp": {"value": "0"},
            },
            "zoneData": (
                {} if reported_state is None else {"heatOrCoolRequest": reported_state}
            ),
        }}
    )
    with patch.object(client, "_request", return_value=response), patch.object(
        client, "_get_system_items", return_value={}
    ):
        data = client.get_data()
    state = setup_entities(platforms, data, flat_api=flat_api)

    assert data.dhw_enabled is False
    if reported_state is None:
        assert "heat_or_cool_request" not in state.binaries
    else:
        assert state.binaries["heat_or_cool_request"].is_on is reported_state
    assert state.sensors["dhw_comfort_temp"].native_value == 50.5
    assert state.sensors["dhw_reduced_temp"].native_value == 0.0
    if flat_api or reported_state is None:
        assert "flame_sensor" not in state.binaries
    else:
        assert state.binaries["flame_sensor"].is_on is reported_state


@pytest.mark.parametrize("heating, cooling, expected_action", [
    (False, False, "idle"),
    (True, False, "heating"),
    (False, True, "cooling"),
])
def test_climate_keeps_working_with_unknown_heat_or_cool_request(platforms, heating, cooling, expected_action):
    state = setup_entities(platforms, RemoconData(
        zone_mode=MODE_AUTOMATIC, room_temp=20.5, comfort_temp=21.5,
        heat_or_cool_request=None, heating_active=heating, cooling_active=cooling,
    ))
    entities = []
    asyncio.run(platforms.climate.async_setup_entry(state.hass, state.entry, entities.extend))

    assert len(entities) == 1
    climate = entities[0]
    assert climate.current_temperature == 20.5
    assert climate.target_temperature == 21.5
    assert climate.hvac_mode == "auto"
    assert climate.hvac_action == expected_action


DHW_CONTROL_KEYS = {
    "selects": {"dhw_mode"},
    "numbers": {"dhw_comfort_temperature", "dhw_reduced_temperature"},
    "switches": {"dhw_boost"},
}


def test_dhw_controls_are_created_while_dhw_mode_is_off(platforms):
    """DHW controls must exist whenever the gateway supports DHW, even if the
    current DHW mode is "disabled" — otherwise DHW can never be re-enabled."""
    state = setup_entities(platforms, RemoconData(
        dhw_available=True, dhw_enabled=False, dhw_mode=0,
        dhw_boost_available=True, dhw_boost=False,
        dhw_comfort_temp=50.5, dhw_reduced_temp=40.5, dhw_temp=48.0,
    ), flat_api=True)

    for attr, keys in DHW_CONTROL_KEYS.items():
        assert keys <= set(getattr(state, attr)), attr
    assert "quiet_mode" in state.switches
    assert len(state.water_heaters) == 1
    assert state.sensors["dhw_temp"].native_value == 48.0
    assert state.binaries["dhw_enabled"].is_on is False


def test_dhw_controls_are_skipped_when_gateway_has_no_dhw(platforms):
    state = setup_entities(platforms, RemoconData(dhw_available=False), flat_api=True)

    for attr, keys in DHW_CONTROL_KEYS.items():
        assert not (keys & set(getattr(state, attr))), attr
    assert state.water_heaters == []
    assert "dhw_temp" not in state.sensors
    # Unrelated controls are unaffected by the DHW capability flag.
    assert "zone_mode" in state.selects
    assert "zone_comfort_temperature" in state.numbers
    assert "quiet_mode" in state.switches


@pytest.mark.parametrize("flat_api", [False, True], ids=["legacy", "flat"])
def test_api_dhw_mode_off_keeps_controls_available(platforms, flat_api):
    client = RemoconClient("e@example.com", "pw", "GW123")
    client._flat_api = flat_api
    response = (
        {"ok": True, "data": {"items": [
            {"id": "ZoneComfortTemp", "value": 21},
            {"id": "DhwMode", "value": "0"},
            {"id": "IsDhwBoost", "value": "0"},
            {"id": "DhwStorageTemperature", "value": "48"},
            {"id": "DhwTimeProgComfortTemp", "value": "50.5"},
            {"id": "DhwTimeProgEconomyTemp", "value": "40.5"},
        ]}}
        if flat_api else {"ok": True, "data": {
            "plantData": {
                "dhwEnabled": False,
                "dhwMode": {"value": 0},
                "dhwStorageTemp": 48,
                "dhwComfortTemp": {"value": "50.5"},
                "dhwReducedTemp": {"value": "40.5"},
            },
            "zoneData": {},
        }}
    )
    with patch.object(client, "_request", return_value=response), patch.object(
        client, "_get_system_items", return_value={}
    ):
        data = client.get_data()

    assert data.dhw_available is True
    assert data.dhw_enabled is False
    state = setup_entities(platforms, data, flat_api=flat_api)
    # The DHW temperature sensor is created for both API families...
    assert "dhw_temp" in state.sensors
    if flat_api:
        # ...while the writable controls only exist for flat R2 gateways.
        for attr, keys in DHW_CONTROL_KEYS.items():
            assert keys <= set(getattr(state, attr)), attr
        assert len(state.water_heaters) == 1


def test_dhw_mode_select_switches_from_off_to_timed_after_reload(platforms):
    """Post-reload with DHW off, choosing "timed" must send DhwMode=1 to the gateway."""
    state = setup_entities(platforms, RemoconData(
        dhw_available=True, dhw_enabled=False, dhw_mode=0,
    ), flat_api=True)
    select = state.selects["dhw_mode"]
    assert select.current_option == "disabled"

    written_modes = []
    state.coordinator.client = types.SimpleNamespace(
        is_flat_api=True, set_dhw_mode=written_modes.append,
    )

    async def add_executor_job(fn, *args):
        return fn(*args)

    state.hass.async_add_executor_job = add_executor_job
    state.coordinator.async_request_refresh = lambda: asyncio.sleep(0)
    select.hass = state.hass

    asyncio.run(select.async_select_option("timed"))

    assert written_modes == [1]
