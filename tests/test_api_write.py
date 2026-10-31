"""Tests for flat R2 write controls."""

from unittest.mock import patch

import pytest

from custom_components.elco_remocon.api import RemoconClient, RemoconDataError


ITEMS = {
    "DhwTimeProgComfortTemp": {"id": "DhwTimeProgComfortTemp", "value": 50, "min": 35, "max": 65, "step": 1},
    "DhwTimeProgEconomyTemp": {"id": "DhwTimeProgEconomyTemp", "value": 40, "min": 30, "max": 55, "step": 1},
    "DhwMode": {"id": "DhwMode", "value": 1},
    "DhwStorageTemperature": {"id": "DhwStorageTemperature", "value": 48},
    "IsDhwBoost": {"id": "IsDhwBoost", "value": 0},
    "ZoneComfortTemp": {"id": "ZoneComfortTemp", "value": 21, "min": 5, "max": 35, "step": 0.5},
    "ZoneEconomyTemp": {"id": "ZoneEconomyTemp", "value": 18, "min": 5, "max": 35, "step": 0.5},
    "ZoneMode": {"id": "ZoneMode", "value": 3},
    "PlantMode": {"id": "PlantMode", "value": 1},
    "IsQuite": {"id": "IsQuite", "value": 0},
}


def make_client() -> RemoconClient:
    client = RemoconClient("e@example.com", "pw", "GW123")
    client._flat_api = True
    return client


def assert_save_call(request, endpoint: str, item_id: str, value):
    assert request.call_args.args[0] == "POST"
    assert request.call_args.args[1] == endpoint
    payload = request.call_args.kwargs["json"]
    request_key = "itemId" if endpoint.endswith("/PlantDhw/Save/GW123") else "id"
    changed = next(item for item in payload["requestItems"] if item[request_key] == item_id)
    assert changed["value"] == value
    assert {item["id"] for item in payload["prevDataItems"]} >= {
        item_id,
        "DhwStorageTemperature",
    }
    if endpoint.endswith("/PlantDhw/Save/GW123"):
        assert len(payload["requestItems"]) == 5
        assert {item["itemId"] for item in payload["requestItems"]} == {
            "DhwTimeProgComfortTemp",
            "DhwTimeProgEconomyTemp",
            "DhwMode",
            "DhwStorageTemperature",
            "IsDhwBoost",
        }
        assert payload["features"]["gatewayId"] == "GW123"
        assert all(item["gatewayId"] == "GW123" for item in payload["prevDataItems"])


def test_dhw_comfort_temperature_writes_and_preserves_siblings():
    client = make_client()
    with patch.object(client, "_get_flat_raw", return_value=ITEMS), patch.object(
        client, "_confirm_write"
    ), patch.object(client, "_request") as request:
        client.set_dhw_comfort_temperature(52)

    assert_save_call(request, "/R2/PlantDhw/Save/GW123", "DhwTimeProgComfortTemp", 52)


def test_dhw_reduced_temperature_writes_expected_endpoint():
    client = make_client()
    with patch.object(client, "_get_flat_raw", return_value=ITEMS), patch.object(
        client, "_confirm_write"
    ), patch.object(client, "_request") as request:
        client.set_dhw_reduced_temperature(42)

    assert_save_call(request, "/R2/PlantDhw/Save/GW123", "DhwTimeProgEconomyTemp", 42)


def test_dhw_comfort_temperature_allows_gateways_without_boost_item():
    client = make_client()
    items_without_boost = {
        item_id: item
        for item_id, item in ITEMS.items()
        if item_id != "IsDhwBoost"
    }
    with patch.object(client, "_get_flat_raw", return_value=items_without_boost), patch.object(
        client, "_confirm_write"
    ), patch.object(client, "_request") as request:
        client.set_dhw_comfort_temperature(52)

    payload = request.call_args.kwargs["json"]
    assert {item["itemId"] for item in payload["requestItems"]} == {
        "DhwTimeProgComfortTemp",
        "DhwTimeProgEconomyTemp",
        "DhwMode",
        "DhwStorageTemperature",
    }


def _v2_get_response(item_id: str, value, zone: int = 1):
    return {
        "items": [
            {
                "id": item_id,
                "value": value,
                "min": 5,
                "max": 35,
                "step": 0.5,
                "zn": zone,
            }
        ]
    }


def assert_v2_set_call(request, item_id: str, value, zone: int):
    assert request.call_args.args[0] == "POST"
    assert request.call_args.args[1] == "/api/v2/remote/dataItems/GW123/set?umsys=si"
    payload = request.call_args.kwargs["json"]
    assert payload["features"]["gatewayId"] == "GW123"
    changed = next(item for item in payload["items"] if item["id"] == item_id)
    assert changed["value"] == value
    assert changed["zone"] == zone
    assert "prevValue" in changed


def test_zone_and_plant_writes_use_v2_dataitems_set():
    client = make_client()

    def fake_request(method, path, **kwargs):
        if path.endswith("/get?umsys=si"):
            requested = kwargs["json"]["items"][0]["id"]
            zone = kwargs["json"]["items"][0]["zn"]
            assert kwargs["json"]["useCache"] is False
            return _v2_get_response(requested, ITEMS[requested]["value"], zone)
        assert path.endswith("/set?umsys=si")
        return {"success": True}

    with patch.object(client, "_request", side_effect=fake_request) as request, \
            patch.object(client, "_confirm_v2_write"):
        client.set_zone_comfort_temperature(21.5)
        assert_v2_set_call(request, "ZoneComfortTemp", 21.5, 1)

        client.set_zone_mode(2)
        assert_v2_set_call(request, "ZoneMode", 2, 1)

        client.set_plant_mode(3)
        assert_v2_set_call(request, "PlantMode", 3, 0)


def test_quiet_mode_uses_plant_advanced_settings_save_contract():
    client = make_client()
    advanced_items = {
        "IsQuite": {"id": "IsQuite", "zone": 0, "kind": 3, "value": 0, "options": [0, 1]},
        "QuietModeStart": {"id": "QuietModeStart", "zone": 0, "kind": 1, "value": 5632},
        "QuietModeEnd": {"id": "QuietModeEnd", "zone": 0, "kind": 1, "value": 1536},
    }

    def fake_request(method, path, **kwargs):
        if path.endswith("/PlantAdvancedSettings/Refresh/GW123"):
            return {"ok": True, "data": {"dataItems": list(advanced_items.values())}}
        assert path == "/R2/PlantAdvancedSettings/Save/GW123"
        assert kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"
        return {"ok": True, "data": {"dataItems": [advanced_items["IsQuite"]]}}

    with patch.object(client, "_request", side_effect=fake_request) as request, \
            patch.object(client, "_confirm_write"):
        client.set_quiet_mode(True)

    payload = request.call_args.kwargs["json"]
    assert payload["features"]["gatewayId"] == "GW123"
    request_ids = {item["itemId"]: item["value"] for item in payload["requestItems"]}
    assert request_ids == {"IsQuite": 1, "QuietModeStart": 5632, "QuietModeEnd": 1536}
    prev_by_id = {item["id"]: item for item in payload["prevDataItems"]}
    assert prev_by_id["IsQuite"]["value"] == 0
    assert prev_by_id["IsQuite"]["gatewayId"] == "GW123"
    assert prev_by_id["QuietModeStart"]["value"] == 5632


def test_dhw_mode_and_boost_stay_on_plant_dhw_save():
    client = make_client()
    with patch.object(client, "_get_flat_raw", return_value=ITEMS), patch.object(
        client, "_confirm_write"
    ), patch.object(client, "_request") as request:
        client.set_dhw_mode(2)
        assert_save_call(request, "/R2/PlantDhw/Save/GW123", "DhwMode", 2)

        client.set_dhw_boost(True)
        assert_save_call(request, "/R2/PlantDhw/Save/GW123", "IsDhwBoost", 1)


def test_zone_reduced_temperature_rejects_value_above_comfort():
    client = make_client()

    def fake_request(method, path, **kwargs):
        if path.endswith("/get?umsys=si"):
            ids = [item["id"] for item in kwargs["json"]["items"]]
            return {"items": [ITEMS[item_id] for item_id in ids]}
        return {"success": True}

    with patch.object(client, "_request", side_effect=fake_request):
        with pytest.raises(RemoconDataError, match="must not exceed comfort"):
            client.set_zone_reduced_temperature(25)


def test_v2_set_rejection_raises():
    client = make_client()
    with patch.object(
        client, "_get_v2_data_items", return_value={"ZoneMode": {"value": 3}}
    ), patch.object(client, "_request", return_value={"success": False}):
        with pytest.raises(RemoconDataError, match="rejected"):
            client.set_zone_mode(2)


@pytest.mark.parametrize(
    ("method", "value"),
    [
        ("set_dhw_comfort_temperature", 34),
        ("set_dhw_comfort_temperature", 50.5),
        ("set_dhw_reduced_temperature", 56),
        ("set_zone_mode", 1),
        ("set_dhw_mode", 3),
        ("set_plant_mode", 4),
    ],
)
def test_write_values_are_validated(method, value):
    client = make_client()
    with patch.object(client, "_get_flat_raw", return_value=ITEMS), patch.object(
        client, "_get_v2_data_items", return_value=ITEMS
    ), patch.object(client, "_confirm_write"), patch.object(client, "_request"):
        with pytest.raises(RemoconDataError):
            getattr(client, method)(value)


def test_write_confirmation_uses_uncached_reread():
    client = make_client()
    response = {
        "ok": True,
        "data": {"items": [{"id": "DhwMode", "value": "2"}]},
    }
    with patch.object(client, "_request", return_value=response) as request:
        client._confirm_write("DhwMode", 2, max_retries=1)

    assert request.call_count == 1
    assert request.call_args.kwargs["json"]["useCache"] is False
