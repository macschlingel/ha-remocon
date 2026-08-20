"""Focused tests for the Remocon-Net API parser and verified write control."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


PACKAGE_ROOT = (
    Path(__file__).parents[1] / "custom_components" / "elco_remocon"
)


def _load_api_module():
    """Load the API without importing Home Assistant integration setup code."""
    if "requests" not in sys.modules:
        requests_stub = types.ModuleType("requests")

        class RequestException(Exception):
            """Test substitute for requests.RequestException."""

        class Session:
            """Test substitute; network access is not used by parser tests."""

        requests_stub.RequestException = RequestException
        requests_stub.Session = Session
        sys.modules["requests"] = requests_stub

    package = types.ModuleType("elco_remocon")
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules["elco_remocon"] = package

    for name in ("const", "api"):
        spec = importlib.util.spec_from_file_location(
            f"elco_remocon.{name}", PACKAGE_ROOT / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

    return sys.modules["elco_remocon.api"]


def test_read_parser_preserves_zero_missing_and_boolean_values() -> None:
    """The API must not confuse missing values, zeroes and string booleans."""
    api = _load_api_module()
    client = api.RemoconClient("user@example.invalid", "secret", "gateway")
    client._get_raw = lambda: {
        "OutsideTemp": {"value": "0"},
        "DhwStorageTemperature": {"value": "48.5"},
        "DhwTemp": {"value": "50"},
        "DhwTimeProgComfortTemp": {"value": "50"},
        "DhwTimeProgEconomyTemp": {"value": None},
        "DhwMode": {
            "value": "0",
            "options": [0, 1, 2],
            "optTexts": ["Disabled", "Timed", "Continuous"],
        },
        "IsHeatingPumpOn": {"value": "1"},
        "ZoneComfortTemp": {
            "value": "21.5",
            "min": 5,
            "max": 30,
            "step": 0.5,
        },
        "ZoneEconomyTemp": {"value": "18"},
        "ZoneDesiredTemp": {"value": None},
        "ZoneMeasuredTemp": {"value": "20.25"},
        "ZoneMode": {
            "value": "3",
            "options": [0, 2, 3],
            "optTexts": ["Off", "Manual", "Schedule"],
        },
        "PlantMode": {
            "value": "1",
            "options": [0, 1, 2, 3, 5],
            "optTexts": ["Summer", "Winter", "Heating", "Cooling", "Off"],
        },
        "ZoneHeatRequest": {"value": "0"},
        "HeatingCircuitPressure": {"value": "1.4"},
        "ChFlowSetpointTemp": {"value": "31.2"},
    }

    data = client.get_data()

    assert data.outside_temp == 0.0
    assert data.desired_temp is None
    assert data.room_temp == 20.25
    assert data.zone_mode == 3
    assert data.zone_mode_text == "Schedule"
    assert data.plant_mode_text == "Winter"
    assert data.heating_active is False
    assert data.cooling_active is False
    assert data.heat_pump_on is True
    assert data.dhw_enabled is False
    assert data.system_pressure == 1.4
    assert data.flow_temperature == 31.2


def test_dhw_comfort_write_uses_previous_state_and_confirms() -> None:
    """The verified write must preserve sibling items and confirm its result."""
    api = _load_api_module()
    client = api.RemoconClient("user@example.invalid", "secret", "gateway")
    previous = {
        "DhwTimeProgComfortTemp": {
            "id": "DhwTimeProgComfortTemp",
            "value": 50,
            "zone": 0,
            "kind": 1,
            "unit": "°C",
            "min": 35,
            "max": 65,
            "step": 1,
            "decimals": 0,
            "readOnly": False,
        },
        "DhwTimeProgEconomyTemp": {
            "id": "DhwTimeProgEconomyTemp",
            "value": 36,
            "zone": 0,
        },
        "DhwMode": {"id": "DhwMode", "value": 1, "zone": 0},
        "DhwStorageTemperature": {
            "id": "DhwStorageTemperature",
            "value": 49,
            "zone": 0,
            "readOnly": True,
        },
        "IsDhwBoost": {"id": "IsDhwBoost", "value": 0, "zone": 0},
    }
    confirmed = {key: dict(value) for key, value in previous.items()}
    confirmed["DhwTimeProgComfortTemp"]["value"] = 51
    reads = [previous, confirmed]
    sent: dict = {}

    client._get_raw = lambda **_: reads.pop(0)

    def request(method, path, **kwargs):
        sent.update(method=method, path=path, payload=kwargs["json"])
        return {"ok": True}

    client._request = request
    client.set_dhw_comfort_temperature(51)

    assert sent["method"] == "POST"
    assert sent["path"] == "/R2/PlantDhw/Save/gateway"
    request_values = {
        item["itemId"]: item["value"] for item in sent["payload"]["requestItems"]
    }
    assert request_values == {
        "DhwTimeProgComfortTemp": 51,
        "DhwTimeProgEconomyTemp": 36,
        "DhwMode": 1,
        "DhwStorageTemperature": 49,
        "IsDhwBoost": 0,
    }


def test_dhw_comfort_write_rejects_invalid_values() -> None:
    """Only whole-degree values inside the observed API range are accepted."""
    api = _load_api_module()
    client = api.RemoconClient("user@example.invalid", "secret", "gateway")
    for value in (34, 35.5, 66):
        try:
            client.set_dhw_comfort_temperature(value)
        except api.RemoconDataError:
            continue
        raise AssertionError(f"Invalid DHW comfort value accepted: {value}")


if __name__ == "__main__":
    test_read_parser_preserves_zero_missing_and_boolean_values()
    test_dhw_comfort_write_uses_previous_state_and_confirms()
    test_dhw_comfort_write_rejects_invalid_values()
    print("API parser and write tests passed")
