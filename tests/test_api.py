"""Tests for gateway request variants, fallback, caching and data mapping.

All HTTP responses are simulated; these tests never contact Remocon-Net.
"""
import json
import logging
from unittest.mock import Mock, patch

import pytest
import requests

from custom_components.elco_remocon.api import (
    BASE_URL,
    FEATURES_PAYLOAD,
    RemoconClient,
    RemoconAuthError,
    RemoconConnectionError,
    RemoconData,
    RemoconDataError,
)

GOOD_RESPONSE = {"ok": True, "data": {"plantData": {}, "zoneData": {}}}


def make_client() -> RemoconClient:
    return RemoconClient(email="e@example.com", password="pw", gateway_id="GW123", zone="1")


def test_first_prefix_succeeds_makes_one_request():
    client = make_client()
    with patch.object(client, "_request", return_value=GOOD_RESPONSE) as mock_request:
        client._get_raw()

    assert mock_request.call_count == 1
    called_path = mock_request.call_args[0][1]
    assert called_path.startswith("/R2/PlantHome/GetData/")
    assert client._planthome_variant == "planthome_si"


def test_falls_back_to_bsb_and_caches_it():
    client = make_client()
    with patch.object(
        client,
        "_request",
        side_effect=[RemoconConnectionError("500"), GOOD_RESPONSE],
    ) as mock_request:
        client._get_raw()

    assert mock_request.call_count == 2
    first_path = mock_request.call_args_list[0][0][1]
    second_path = mock_request.call_args_list[1][0][1]
    assert first_path.startswith("/R2/PlantHome/GetData/")
    assert second_path.startswith("/R2/PlantHomeBsb/GetData/")
    assert client._planthome_variant == "bsb_si"


def test_legacy_non_bsb_payload_keeps_legacy_mapping():
    client = make_client()
    legacy = {"ok": True, "data": {
        "plantData": {"outsideTemp": -2.0},
        "zoneData": {"chComfortTemp": {"value": 20.5}, "roomTemp": 19.0},
    }}
    with patch.object(client, "_request", return_value=legacy), patch.object(
        client, "_get_system_items", return_value={}
    ):
        data = client.get_data()

    assert data.comfort_temp == 20.5
    assert data.room_temp == 19.0
    assert data.outside_temp == -2.0
    assert client._flat_api is False


def test_all_variants_fail_reports_flat_error_and_caches_nothing():
    client = make_client()
    first_error = RemoconConnectionError("500 non-bsb")
    second_error = RemoconConnectionError("500 bsb")
    browser_error = RemoconConnectionError("500 browser bsb")
    flat_error = RemoconConnectionError("500 flat")
    with patch.object(
        client, "_request", side_effect=[first_error, second_error, browser_error, flat_error]
    ) as request:
        with pytest.raises(RemoconConnectionError) as exc_info:
            client._get_raw()

    assert request.call_count == 4
    assert request.call_args.args[1] == "/R2/PlantHome/GetData/GW123"
    assert exc_info.value is flat_error
    assert client._planthome_variant is None
    assert client.is_flat_api is False


def test_cached_prefix_is_used_on_subsequent_call_without_reprobing():
    client = make_client()
    client._planthome_variant = "bsb_si"
    with patch.object(client, "_request", return_value=GOOD_RESPONSE) as mock_request:
        client._get_raw()

    assert mock_request.call_count == 1
    called_path = mock_request.call_args[0][1]
    assert called_path.startswith("/R2/PlantHomeBsb/GetData/")


def test_cached_prefix_failing_clears_cache_for_next_call():
    client = make_client()
    client._planthome_variant = "bsb_si"
    with patch.object(client, "_request", side_effect=RemoconConnectionError("500 again")):
        with pytest.raises(RemoconConnectionError):
            client._get_raw()

    assert client._planthome_variant is None


def test_cached_prefix_failure_probes_alternate_legacy_prefix():
    client = make_client()
    client._planthome_variant = "bsb_si"
    with patch.object(
        client,
        "_request",
        side_effect=[RemoconConnectionError("cached prefix failed"), GOOD_RESPONSE],
    ) as request:
        client._get_raw()

    assert request.call_count == 2
    assert request.call_args_list[0].args[1].startswith("/R2/PlantHomeBsb/")
    assert request.call_args_list[1].args[1].startswith("/R2/PlantHome/")
    assert client._planthome_variant == "planthome_si"


def flat_response():
    return {"ok": True, "data": {"items": [
        {"id": "ZoneComfortTemp", "value": "21.5", "min": 5, "max": 30, "step": 0.5},
        {"id": "ZoneEconomyTemp", "value": "18"},
        {"id": "ZoneMeasuredTemp", "value": "20.25"},
        {"id": "ZoneDesiredTemp", "value": "22"},
        {"id": "ZoneMode", "value": "3", "optTexts": ["Off", "Manual", "Schedule"]},
        {"id": "ZoneHeatRequest", "value": "1"},
        {"id": "OutsideTemp", "value": "-4.5"},
        {"id": "DhwStorageTemperature", "value": "48"},
        {"id": "DhwTimeProgComfortTemp", "value": "50"},
        {"id": "DhwTimeProgEconomyTemp", "value": "40"},
        {"id": "DhwMode", "value": "1"},
        {"id": "IsHeatingPumpOn", "value": "true"},
    ]}}


def test_http_200_flat_response_is_detected_and_mapped():
    client = make_client()
    with patch.object(
        client,
        "_request",
        side_effect=[flat_response(), flat_response(), flat_response()],
    ) as request:
        data = client.get_data()

    assert request.call_count == 3
    assert request.call_args.args[1].startswith("/R2/PlantHome/GetData/")
    assert "?umsys=si" not in request.call_args.args[1]
    assert request.call_args.kwargs["json"]["features"]["hpSys"] is True
    assert data.comfort_temp == 21.5
    assert data.reduced_temp == 18
    assert data.room_temp == 20.25
    assert data.zone_mode == 3
    assert data.outside_temp == -4.5
    assert data.dhw_temp == 48
    assert data.heat_pump_on is True
    assert client._flat_api is True


def test_flat_protocol_is_cached_for_followup_polls():
    client = make_client()
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("legacy 500"), RemoconConnectionError("BSB 500"),
        RemoconConnectionError("browser BSB 500"),
        flat_response(), flat_response(), flat_response(), flat_response(),
    ]) as request:
        client.get_data()
        client.get_data()

    assert request.call_count == 7
    assert request.call_args_list[3].args[1] == "/R2/PlantHome/GetData/GW123"
    assert request.call_args_list[4].args[1] == "/R2/PlantHome/GetData/GW123"
    assert request.call_args_list[5].kwargs["json"]["filter"]["notEssentials"] is False
    assert request.call_args_list[6].kwargs["json"]["filter"]["notEssentials"] is True


def test_http_500_legacy_families_use_flat_fallback():
    client = make_client()
    legacy_error = RemoconConnectionError("500")
    with patch.object(
        client,
        "_request",
        side_effect=[legacy_error, legacy_error, legacy_error, flat_response(), flat_response()],
    ) as request:
        data = client.get_data()

    assert request.call_count == 5
    fallback_path = request.call_args_list[3].args[1]
    assert fallback_path == "/R2/PlantHome/GetData/GW123"
    payload = request.call_args_list[3].kwargs["json"]
    assert payload["features"]["gatewayId"] == "GW123"
    assert payload["features"]["zones"][0]["num"] == 1
    assert data.comfort_temp == 21.5


def test_flat_response_disables_unverified_writes():
    client = make_client()
    with patch.object(client, "_request", return_value=flat_response()) as request:
        with pytest.raises(RemoconDataError, match="Control is disabled"):
            client.set_zone_temperatures(comfort=22)

    # Only the read request was made; no control request escaped.
    assert request.call_count == 3


def test_flat_fallback_error_is_reported():
    client = make_client()
    error = RemoconConnectionError("flat endpoint unavailable")
    with patch.object(
        client,
        "_request",
        side_effect=[
            RemoconConnectionError("legacy 500"),
            RemoconConnectionError("BSB 500"),
            RemoconConnectionError("browser BSB 500"),
            error,
        ],
    ):
        with pytest.raises(RemoconConnectionError) as exc_info:
            client.get_data()

    assert exc_info.value is error


def test_flat_fallback_requires_an_item_used_by_this_integration():
    client = make_client()
    irrelevant = {"ok": True, "data": {"items": [{"id": "UnrelatedItem", "value": 1}]}}
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("legacy 500"),
        RemoconConnectionError("BSB 500"),
        RemoconConnectionError("browser BSB 500"),
        irrelevant,
    ]):
        with pytest.raises(RemoconDataError, match="no items used"):
            client._get_raw()

    assert client.is_flat_api is False

    client = make_client()
    no_value = {"ok": True, "data": {"items": [{"id": "ZoneComfortTemp", "value": None}]}}
    with patch.object(client, "_request", return_value=no_value):
        with pytest.raises(RemoconDataError, match="no items used"):
            client._get_raw()


def test_flat_status_group_is_optional_and_supplies_heat_pump_state():
    client = make_client()
    client._flat_api = True
    essential = {"ok": True, "data": {"items": [
        {"id": "ZoneComfortTemp", "value": 21},
        {"id": "ZoneEconomyTemp", "value": 18},
    ]}}
    status = {"ok": True, "data": {"items": [
        {"id": "IsHeatingPumpOn", "value": 1},
        {"id": "ZoneHeatRequest", "value": 1},
        {"id": "PlantMode", "value": 1},
    ]}}
    with patch.object(client, "_request", side_effect=[essential, status]) as request:
        data = client.get_data()

    assert request.call_count == 2
    assert request.call_args_list[1].kwargs["json"]["filter"] == {
        "dhw": False,
        "notEssentials": True,
        "plant": False,
        "progId": 9,
        "zone": False,
    }
    assert data.heat_pump_on is True
    assert data.heating_active is True


@pytest.mark.parametrize("status_error", [
    RemoconConnectionError("status down"),
    RemoconDataError("invalid status response"),
])
def test_flat_status_group_error_does_not_reject_essential_data(status_error):
    client = make_client()
    client._flat_api = True
    essential = {"ok": True, "data": {"items": [
        {"id": "ZoneComfortTemp", "value": 21},
    ]}}
    with patch.object(
        client, "_request", side_effect=[essential, status_error]
    ):
        data = client.get_data()

    assert data.comfort_temp == 21
    assert data.heating_active is None
    assert data.cooling_active is None
    assert data.heat_pump_on is None


@pytest.mark.parametrize("response", [
    {"ok": False, "message": "rejected", "data": {"items": [{"id": "ZoneMode"}]}},
    {"ok": True, "data": {"items": []}},
    {"ok": True, "data": {"items": [None, {"value": 1}]}},
])
def test_invalid_flat_fallback_response_is_rejected(response):
    client = make_client()
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("legacy 500"),
        RemoconConnectionError("BSB 500"),
        RemoconConnectionError("browser BSB 500"),
        response,
    ]):
        with pytest.raises(RemoconDataError):
            client._get_raw()

    assert client.is_flat_api is False


@pytest.mark.parametrize("zone", ["1", "3"])
def test_browser_bsb_request_matches_browser_and_caches_legacy_variant(zone):
    client = RemoconClient(
        email="e@example.com", password="pw", gateway_id="GW123", zone=zone
    )
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("PlantHome unavailable"),
        RemoconConnectionError("BSB SI unavailable"),
        GOOD_RESPONSE,
    ]) as request:
        raw = client._get_raw()

    assert [entry.args for entry in request.call_args_list] == [
        ("POST", "/R2/PlantHome/GetData/GW123?umsys=si"),
        ("POST", "/R2/PlantHomeBsb/GetData/GW123?umsys=si"),
        ("POST", "/R2/PlantHomeBsb/GetData/GW123"),
    ]
    assert request.call_args.kwargs == {"api_variant": "bsb_browser", "json": {
        "useCache": True,
        "zone": int(zone),
        "filter": {"progIds": None, "plant": True, "zone": True},
    }}
    assert raw == GOOD_RESPONSE["data"]
    assert client._planthome_variant == "bsb_browser"
    assert client.is_flat_api is False


@pytest.mark.parametrize("variant,prefix", [
    ("planthome_si", "/R2/PlantHome"),
    ("bsb_si", "/R2/PlantHomeBsb"),
])
def test_existing_variants_keep_exact_request_body_and_query(variant, prefix):
    client = make_client()
    responses = [GOOD_RESPONSE]
    if variant == "bsb_si":
        responses.insert(0, RemoconConnectionError("PlantHome unavailable"))
    with patch.object(client, "_request", side_effect=responses) as request:
        client._get_raw()

    assert request.call_count == len(responses)
    assert request.call_args.args == ("POST", f"{prefix}/GetData/GW123?umsys=si")
    assert request.call_args.kwargs == {"api_variant": variant, "json": {
        "useCache": True,
        "zone": 1,
        "filter": {"notEssentials": False, "plant": True, "zone": True, "dhw": True},
        "features": FEATURES_PAYLOAD,
    }}
    assert client._planthome_variant == variant


def test_browser_bsb_followup_poll_reuses_complete_request_without_probing():
    client = make_client()
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("PlantHome unavailable"),
        RemoconConnectionError("BSB SI unavailable"),
        GOOD_RESPONSE,
        GOOD_RESPONSE,
    ]) as request:
        client._get_raw()
        client._get_raw()

    assert request.call_count == 4
    assert request.call_args_list[3] == request.call_args_list[2]
    assert client._planthome_variant == "bsb_browser"


def test_browser_bsb_response_uses_existing_legacy_mapper():
    client = make_client()
    response = {"ok": True, "data": {
        "plantData": {
            "outsideTemp": -2.5,
            "dhwStorageTemp": 48,
            "dhwComfortTemp": {"value": 55},
            "dhwReducedTemp": {"value": 42},
            "dhwMode": {"value": 1},
            "dhwEnabled": True,
            "flameSensor": True,
        },
        "zoneData": {
            "chComfortTemp": {"value": 21.5, "min": 5, "max": 30, "step": 0.5},
            "chReducedTemp": {"value": 18},
            "desiredRoomTemp": 21.5,
            "roomTemp": 20.25,
            "mode": {"value": 3, "allowedOptionTexts": ["Off", "Comfort", "Auto"]},
            "isHeatingActive": True,
            "isCoolingActive": False,
            "heatOrCoolRequest": True,
            "hasRoomSensor": True,
        },
        "timeProgs": [{"id": 1, "days": []}],
    }}
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("PlantHome unavailable"),
        RemoconConnectionError("BSB SI unavailable"),
        response,
    ]) as request, patch.object(client, "_get_system_items", return_value={
        "HeatingCircuitPressure": "1.6", "ChFlowTemp": "43.5",
    }):
        data = client.get_data()

    assert request.call_count == 3
    assert data.comfort_temp == 21.5
    assert data.reduced_temp == 18
    assert data.room_temp == 20.25
    assert data.zone_mode == 3
    assert data.zone_mode_texts == ["Off", "Comfort", "Auto"]
    assert data.heating_active is True
    assert data.cooling_active is False
    assert data.heat_or_cool_request is True
    assert data.flame_sensor is True
    assert data.outside_temp == -2.5
    assert data.dhw_temp == 48
    assert data.dhw_comfort_temp == 55
    assert data.dhw_reduced_temp == 42
    assert data.dhw_mode == 1
    assert data.dhw_enabled is True
    assert data.system_pressure == 1.6
    assert data.flow_temperature == 43.5
    assert data.has_room_sensor is True
    assert client.is_flat_api is False


@pytest.mark.parametrize("response", [
    {"ok": False, "message": "unsupported profile"},
    {"ok": True, "data": {"unsupported": True}},
])
def test_legacy_break_still_reaches_browser_without_probing_other_old_variant(response):
    client = make_client()
    with patch.object(client, "_request", side_effect=[response, GOOD_RESPONSE]) as request:
        client._get_raw()

    assert [entry.args[1] for entry in request.call_args_list] == [
        "/R2/PlantHome/GetData/GW123?umsys=si",
        "/R2/PlantHomeBsb/GetData/GW123",
    ]
    assert client._planthome_variant == "bsb_browser"


@pytest.mark.parametrize("response", [
    {"ok": False, "message": "unsupported profile", "data": GOOD_RESPONSE["data"]},
    {"ok": True, "data": {"unsupported": True}},
])
def test_invalid_browser_response_is_not_cached_and_reaches_flat(response):
    client = make_client()
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("PlantHome unavailable"),
        RemoconConnectionError("BSB SI unavailable"),
        response,
        flat_response(),
        flat_response(),
    ]) as request:
        data = client.get_data()

    assert request.call_count == 5
    assert request.call_args_list[2].args[1] == "/R2/PlantHomeBsb/GetData/GW123"
    assert request.call_args_list[3].args[1] == "/R2/PlantHome/GetData/GW123"
    assert data.comfort_temp == 21.5
    assert client._planthome_variant is None
    assert client.is_flat_api is True


@pytest.mark.parametrize("variant", ["bsb_si", "bsb_browser"])
def test_flat_response_from_cached_variant_goes_directly_to_flat(variant):
    client = make_client()
    client._planthome_variant = variant
    with patch.object(client, "_request", return_value=flat_response()) as request:
        client.get_data()

    assert request.call_count == 3
    assert [entry.args[1] for entry in request.call_args_list[1:]] == [
        "/R2/PlantHome/GetData/GW123", "/R2/PlantHome/GetData/GW123",
    ]
    assert all("features" in entry.kwargs["json"] for entry in request.call_args_list[1:])
    assert client._planthome_variant is None
    assert client.is_flat_api is True


def http_response(status=200, payload=GOOD_RESPONSE):
    """Build a requests response without opening any network connection."""
    response = requests.Response()
    response.status_code = status
    response.url = f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123"
    response._content = json.dumps(payload).encode("utf-8")
    return response


@pytest.mark.parametrize("failure", [
    "http_500", "http_501", "http_502", "http_503", "http_504", "http_599", "timeout",
])
def test_cached_browser_temporary_failure_preserves_variant_for_next_poll(failure):
    client = make_client()
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("PlantHome unavailable"),
        RemoconConnectionError("BSB SI unavailable"),
        GOOD_RESPONSE,
    ]):
        client._get_raw()

    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        (
            http_response(int(failure.removeprefix("http_")))
            if failure.startswith("http_") else requests.Timeout("read timed out")
        ),
        http_response(),
    ]
    client._session = session

    with pytest.raises(RemoconConnectionError) as exc_info:
        client._get_raw()

    assert session.request.call_count == 1
    assert client._planthome_variant == "bsb_browser"
    assert client.is_flat_api is False
    if failure.startswith("http_"):
        assert isinstance(exc_info.value.__cause__, requests.HTTPError)
        assert exc_info.value.__cause__.response.status_code == int(failure.removeprefix("http_"))
    else:
        assert isinstance(exc_info.value.__cause__, requests.Timeout)

    assert client._get_raw() == GOOD_RESPONSE["data"]
    assert session.request.call_count == 2
    assert session.request.call_args_list[0] == session.request.call_args_list[1]
    assert session.request.call_args.args == (
        "POST", f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123",
    )
    assert client._planthome_variant == "bsb_browser"


@pytest.mark.parametrize("failure", ["http_404", "connection_error"])
def test_other_cached_browser_connection_errors_allow_fallback_without_duplicate_probe(failure):
    client = make_client()
    client._planthome_variant = "bsb_browser"
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        (
            http_response(int(failure.removeprefix("http_")))
            if failure.startswith("http_") else requests.ConnectionError("offline")
        ),
        http_response(500),
        http_response(500),
        http_response(payload=flat_response()),
        http_response(payload=flat_response()),
    ]
    client._session = session

    data = client.get_data()

    assert [entry.args[1] for entry in session.request.call_args_list] == [
        f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123",
        f"{BASE_URL}/R2/PlantHome/GetData/GW123?umsys=si",
        f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123?umsys=si",
        f"{BASE_URL}/R2/PlantHome/GetData/GW123",
        f"{BASE_URL}/R2/PlantHome/GetData/GW123",
    ]
    assert client._planthome_variant is None
    assert client.is_flat_api is True
    assert data.comfort_temp == 21.5


@pytest.mark.parametrize("variant,alternate", [
    ("planthome_si", "bsb_si"),
    ("bsb_si", "planthome_si"),
])
@pytest.mark.parametrize("failure", ["http_500", "http_502", "http_503", "http_504", "timeout"])
def test_existing_cached_variants_still_rediscover_after_server_error_or_timeout(
    variant, alternate, failure
):
    client = make_client()
    client._planthome_variant = variant
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        (
            http_response(int(failure.removeprefix("http_")))
            if failure.startswith("http_") else requests.Timeout("read timed out")
        ),
        http_response(),
    ]
    client._session = session

    assert client._get_raw() == GOOD_RESPONSE["data"]

    assert session.request.call_count == 2
    expected_paths = {
        "planthome_si": f"{BASE_URL}/R2/PlantHome/GetData/GW123?umsys=si",
        "bsb_si": f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123?umsys=si",
    }
    assert [entry.args[1] for entry in session.request.call_args_list] == [
        expected_paths[variant], expected_paths[alternate],
    ]
    assert client._planthome_variant == alternate


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("failure,error_type", [
    ("http_401", RemoconAuthError),
    ("http_403", RemoconAuthError),
    ("invalid_json", RemoconDataError),
])
def test_authentication_and_invalid_json_errors_do_not_trigger_fallback(cached, failure, error_type):
    client = make_client()
    if cached:
        client._planthome_variant = "bsb_browser"
    response = http_response()
    if failure == "invalid_json":
        response._content = b"not json"
    else:
        response.status_code = int(failure.removeprefix("http_"))
    session = Mock(spec=requests.Session)
    session.request.side_effect = [response, http_response()]
    client._session = session

    with pytest.raises(error_type):
        client._get_raw()

    assert session.request.call_count == 1
    assert client._planthome_variant == ("bsb_browser" if cached else None)
    assert client.is_flat_api is False

    assert client._get_raw() == GOOD_RESPONSE["data"]
    assert session.request.call_count == 2
    assert session.request.call_args_list[0] == session.request.call_args_list[1]
    assert client._planthome_variant == ("bsb_browser" if cached else "planthome_si")


@pytest.mark.parametrize("failure", ["http_500", "http_502", "http_503", "http_504", "timeout"])
def test_unrecognized_browser_temporary_failure_still_uses_flat_fallback(failure):
    client = make_client()
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        http_response(500),
        http_response(500),
        (
            http_response(int(failure.removeprefix("http_")))
            if failure.startswith("http_") else requests.Timeout("read timed out")
        ),
        http_response(payload=flat_response()),
        http_response(payload=flat_response()),
    ]
    client._session = session

    data = client.get_data()

    assert session.request.call_count == 5
    assert [entry.args[1] for entry in session.request.call_args_list] == [
        f"{BASE_URL}/R2/PlantHome/GetData/GW123?umsys=si",
        f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123?umsys=si",
        f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123",
        f"{BASE_URL}/R2/PlantHome/GetData/GW123",
        f"{BASE_URL}/R2/PlantHome/GetData/GW123",
    ]
    assert client._planthome_variant is None
    assert client.is_flat_api is True
    assert data.comfort_temp == 21.5


@pytest.mark.parametrize("variant,failed_probes", [
    ("planthome_si", 0),
    ("bsb_si", 1),
    ("bsb_browser", 2),
    ("flat", 0),
])
def test_api_variant_logs_first_success_once_without_sensitive_data(
    caplog, variant, failed_probes
):
    private_values = (
        "private-email@example.com", "private-password", "private-gateway",
        "private-session-cookie", "private-token", "private-response-body",
    )
    client = RemoconClient(
        email=private_values[0], password=private_values[1], gateway_id=private_values[2]
    )
    client._session = Mock(spec=requests.Session)
    client._session.cookies = {"session": private_values[3]}
    client._session.headers = {"Authorization": private_values[4]}
    if variant == "flat":
        response = flat_response()
        response["data"]["private"] = private_values[5]
        responses = [response] * 5  # Detection, two flat groups, then two cached groups.
    else:
        response = {"ok": True, "data": {
            "plantData": {"private": private_values[5]}, "zoneData": {},
        }}
        responses = [RemoconConnectionError("unavailable")] * failed_probes
        responses += [response, response]
    expected_record = (
        RemoconClient.__module__, logging.INFO, f"Selected REMOCON API variant: {variant}",
    )

    with caplog.at_level(logging.INFO, logger=RemoconClient.__module__), patch.object(
        client, "_request", side_effect=responses
    ) as request:
        client._get_raw()
        assert caplog.record_tuples == [expected_record]
        client._get_raw()

    assert request.call_count == len(responses)
    assert caplog.record_tuples == [expected_record]
    assert all(value not in caplog.text for value in private_values)


@pytest.mark.parametrize("new_variant", ["bsb_si", "flat"])
def test_api_variant_logs_later_successful_switch(caplog, new_variant):
    client = make_client()
    responses = [GOOD_RESPONSE]
    if new_variant == "flat":
        responses += [flat_response()] * 3
    else:
        responses += [RemoconConnectionError("cached endpoint unavailable"), GOOD_RESPONSE]

    with caplog.at_level(logging.INFO, logger=RemoconClient.__module__), patch.object(
        client, "_request", side_effect=responses
    ) as request:
        client._get_raw()
        client._get_raw()

    assert request.call_count == len(responses)
    assert caplog.record_tuples == [
        (RemoconClient.__module__, logging.INFO, "Selected REMOCON API variant: planthome_si"),
        (RemoconClient.__module__, logging.INFO, f"Selected REMOCON API variant: {new_variant}"),
    ]


def test_api_variant_cache_clear_does_not_repeat_same_selection_log(caplog):
    client = make_client()
    with caplog.at_level(logging.INFO, logger=RemoconClient.__module__):
        with patch.object(client, "_request", return_value=GOOD_RESPONSE):
            client._get_raw()
        with patch.object(
            client, "_request", side_effect=RemoconConnectionError("unavailable")
        ) as request:
            with pytest.raises(RemoconConnectionError):
                client._get_raw()
        assert request.call_count == 4
        assert client._planthome_variant is None
        with patch.object(client, "_request", return_value=GOOD_RESPONSE):
            client._get_raw()

    assert client._planthome_variant == "planthome_si"
    assert caplog.record_tuples == [(
        RemoconClient.__module__, logging.INFO, "Selected REMOCON API variant: planthome_si",
    )]


def test_api_variant_failed_detection_does_not_log_selection(caplog):
    client = make_client()
    with caplog.at_level(logging.INFO, logger=RemoconClient.__module__), patch.object(
        client, "_request", side_effect=RemoconConnectionError("unavailable")
    ) as request:
        with pytest.raises(RemoconConnectionError):
            client._get_raw()

    assert request.call_count == 4
    assert caplog.record_tuples == []


@pytest.mark.parametrize("failure", ["http_500", "http_502", "http_503", "http_504", "timeout"])
def test_api_variant_browser_temporary_failure_and_recovery_do_not_repeat_log(caplog, failure):
    client = make_client()
    error = RemoconConnectionError("temporary failure")
    error.__cause__ = (
        requests.HTTPError(response=http_response(int(failure.removeprefix("http_"))))
        if failure.startswith("http_") else requests.Timeout("read timed out")
    )
    with caplog.at_level(logging.INFO, logger=RemoconClient.__module__), patch.object(
        client, "_request", side_effect=[
            RemoconConnectionError("PlantHome unavailable"),
            RemoconConnectionError("BSB SI unavailable"),
            GOOD_RESPONSE,
            error,
            GOOD_RESPONSE,
        ]
    ) as request:
        client._get_raw()
        with pytest.raises(RemoconConnectionError):
            client._get_raw()
        assert client._planthome_variant == "bsb_browser"
        client._get_raw()

    assert request.call_count == 5
    assert client._planthome_variant == "bsb_browser"
    assert caplog.record_tuples == [(
        RemoconClient.__module__, logging.INFO, "Selected REMOCON API variant: bsb_browser",
    )]


def request_duration_records(caplog):
    """Select the new diagnostics separately from existing error/selection logs."""
    return [record for record in caplog.records
            if record.name == RemoconClient.__module__
            and record.getMessage().startswith("REMOCON API ")]


@pytest.mark.parametrize("variant,expected_timeout", [
    ("planthome_si", 15),
    ("bsb_si", 15),
    ("bsb_browser", (15, 30)),
    ("flat", 15),
])
def test_read_variants_apply_only_browser_read_timeout(variant, expected_timeout):
    client = make_client()
    session = Mock(spec=requests.Session)
    session.request.return_value = http_response(
        payload=flat_response() if variant == "flat" else GOOD_RESPONSE
    )
    client._session = session

    if variant == "flat":
        result = client._get_flat_item_call({"plant": True})
        assert result["ZoneComfortTemp"]["value"] == "21.5"
    else:
        assert client._request_planthome_variant(variant) == GOOD_RESPONSE

    assert session.request.call_count == 1
    assert session.request.call_args.kwargs["timeout"] == expected_timeout
    assert set(session.request.call_args.kwargs) == {"json", "timeout"}


def test_login_keeps_original_timeout_and_is_not_timed():
    client = make_client()
    session = Mock(spec=requests.Session)
    session.post.return_value = http_response()
    with patch(
        f"{RemoconClient.__module__}.requests.Session", return_value=session
    ), patch(f"{RemoconClient.__module__}.time") as clock:
        client.login()

    assert session.post.call_args.kwargs["timeout"] == 15
    assert client._session is session
    clock.monotonic.assert_not_called()


def test_optional_v2_read_keeps_original_timeout_and_is_not_timed():
    client = make_client()
    client._planthome_variant = "bsb_browser"
    session = Mock(spec=requests.Session)
    session.request.return_value = http_response(payload={
        "items": [{"id": "HeatingCircuitPressure", "value": "1.6"}],
    })
    client._session = session
    with patch(f"{RemoconClient.__module__}.time") as clock:
        data = client._get_system_items([{"id": "HeatingCircuitPressure", "zn": 0}])

    assert data == {"HeatingCircuitPressure": "1.6"}
    assert session.request.call_args.kwargs["timeout"] == 15
    assert set(session.request.call_args.kwargs) == {"json", "timeout"}
    clock.monotonic.assert_not_called()


@pytest.mark.parametrize("method,arguments", [
    ("set_zone_temperatures", {"comfort": 22}),
    ("set_zone_mode", {"mode": 3}),
    ("set_dhw_temperature", {"comfort": 55}),
    ("set_dhw_mode", {"mode": 1}),
])
def test_writes_keep_original_timeout_and_are_not_timed(method, arguments):
    client = make_client()
    client._planthome_variant = "bsb_browser"
    session = Mock(spec=requests.Session)
    session.request.return_value = http_response()
    client._session = session
    with patch.object(
        client, "_get_raw", return_value={
            "zoneData": {
                "chComfortTemp": {"value": 21},
                "chReducedTemp": {"value": 18},
            },
            "plantData": {
                "dhwComfortTemp": {"value": 50},
                "dhwReducedTemp": {"value": 40},
            },
        }
    ), patch(f"{RemoconClient.__module__}.time") as clock:
        getattr(client, method)(**arguments)

    assert session.request.call_count == 1
    assert session.request.call_args.kwargs["timeout"] == 15
    assert set(session.request.call_args.kwargs) == {"json", "timeout"}
    clock.monotonic.assert_not_called()


@pytest.mark.parametrize("variant", ["planthome_si", "bsb_si", "bsb_browser", "flat"])
def test_each_read_variant_logs_successful_duration_at_debug(caplog, variant):
    client = make_client()
    session = Mock(spec=requests.Session)
    session.request.return_value = http_response(
        payload=flat_response() if variant == "flat" else GOOD_RESPONSE
    )
    client._session = session
    with caplog.at_level(logging.DEBUG, logger=RemoconClient.__module__), patch(
        f"{RemoconClient.__module__}.time"
    ) as clock:
        clock.monotonic.side_effect = [100.0, 102.4]
        if variant == "flat":
            client._get_flat_item_call({"plant": True})
        else:
            client._request_planthome_variant(variant)

    assert caplog.record_tuples == [(
        RemoconClient.__module__, logging.DEBUG,
        f"REMOCON API {variant}: request completed in 2.4s",
    )]
    assert clock.monotonic.call_count == 2


@pytest.mark.parametrize("duration,level,description", [
    (9.9, logging.DEBUG, "request completed"),
    (10.0, logging.INFO, "slow request completed"),
    (24.8, logging.INFO, "slow request completed"),
])
def test_browser_duration_log_level_changes_at_ten_seconds(
    caplog, duration, level, description
):
    client = make_client()
    session = Mock(spec=requests.Session)
    session.request.return_value = http_response()
    client._session = session
    with caplog.at_level(logging.DEBUG, logger=RemoconClient.__module__), patch(
        f"{RemoconClient.__module__}.time"
    ) as clock:
        clock.monotonic.side_effect = [100.0, 100.0 + duration]
        assert client._request_planthome_variant("bsb_browser") == GOOD_RESPONSE

    assert caplog.record_tuples == [(
        RemoconClient.__module__, level,
        f"REMOCON API bsb_browser: {description} in {duration:.1f}s",
    )]


def test_slow_http_200_api_failure_is_logged_as_failed_and_preserves_cached_variant(caplog):
    client = make_client()
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("PlantHome unavailable"),
        RemoconConnectionError("BSB SI unavailable"),
        GOOD_RESPONSE,
    ]):
        client._get_raw()
    response = {"ok": False, "message": "Communication failed", "data": GOOD_RESPONSE["data"]}
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        http_response(payload=response),
        http_response(),
    ]
    client._session = session
    with caplog.at_level(logging.DEBUG, logger=RemoconClient.__module__), patch(
        f"{RemoconClient.__module__}.time"
    ) as clock:
        clock.monotonic.side_effect = [100.0, 124.8, 200.0, 202.4]
        with pytest.raises(RemoconDataError, match="^REMOCON API rejected data request$"):
            client._get_raw()

        assert session.request.call_count == 1
        assert client._planthome_variant == "bsb_browser"
        assert client.is_flat_api is False
        assert client._get_raw() == GOOD_RESPONSE["data"]

    assert session.request.call_count == 2
    assert [entry.args[1] for entry in session.request.call_args_list] == [
        f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123",
        f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123",
    ]
    assert client._planthome_variant == "bsb_browser"
    assert client.is_flat_api is False
    durations = request_duration_records(caplog)
    assert durations[0].levelno == logging.INFO
    assert durations[0].getMessage() == (
        "REMOCON API bsb_browser: slow request failed in 24.8s"
    )
    assert [record.getMessage() for record in caplog.records
            if record.getMessage().startswith("Selected REMOCON API variant:")] == []


@pytest.mark.parametrize("timeout_type,duration", [
    (requests.ReadTimeout, 30.4),
    (requests.ConnectTimeout, 15.2),
])
def test_cached_browser_timeout_logs_actual_wait_and_preserves_next_poll(
    caplog, timeout_type, duration
):
    client = make_client()
    client._planthome_variant = "bsb_browser"
    error = timeout_type("read or connection timed out")
    session = Mock(spec=requests.Session)
    session.request.side_effect = [error, http_response()]
    client._session = session
    with caplog.at_level(logging.DEBUG, logger=RemoconClient.__module__), patch(
        f"{RemoconClient.__module__}.time"
    ) as clock:
        clock.monotonic.side_effect = [100.0, 100.0 + duration, 200.0, 202.4]
        with pytest.raises(RemoconConnectionError) as exc_info:
            client._get_raw()
        assert exc_info.value.__cause__ is error
        assert session.request.call_count == 1
        assert client._planthome_variant == "bsb_browser"
        assert client.is_flat_api is False
        assert client._get_raw() == GOOD_RESPONSE["data"]

    assert session.request.call_count == 2
    assert session.request.call_args_list[0] == session.request.call_args_list[1]
    assert session.request.call_args.kwargs["timeout"] == (15, 30)
    durations = request_duration_records(caplog)
    assert durations[0].levelno == logging.INFO
    assert durations[0].getMessage() == (
        f"REMOCON API bsb_browser: request timed out after {duration:.1f}s"
    )
    assert durations[1].levelno == logging.DEBUG
    assert durations[1].getMessage() == "REMOCON API bsb_browser: request completed in 2.4s"


@pytest.mark.parametrize("failure,error_type", [
    ("http_500", RemoconConnectionError),
    ("http_401", RemoconAuthError),
    ("invalid_json", RemoconDataError),
])
def test_failed_requests_log_duration_without_changing_error_type(caplog, failure, error_type):
    client = make_client()
    response = http_response()
    if failure == "invalid_json":
        response._content = b"not json"
    else:
        response.status_code = int(failure.removeprefix("http_"))
    session = Mock(spec=requests.Session)
    session.request.return_value = response
    client._session = session
    with caplog.at_level(logging.DEBUG, logger=RemoconClient.__module__), patch(
        f"{RemoconClient.__module__}.time"
    ) as clock:
        clock.monotonic.side_effect = [100.0, 102.4]
        with pytest.raises(error_type):
            client._request_planthome_variant("bsb_browser")

    assert session.request.call_count == 1
    durations = request_duration_records(caplog)
    assert len(durations) == 1
    assert durations[0].levelno == logging.DEBUG
    assert durations[0].getMessage() == "REMOCON API bsb_browser: request failed in 2.4s"


@pytest.mark.parametrize("outcome", ["completed", "failed", "http_error", "timeout"])
def test_duration_diagnostics_never_include_sensitive_values(caplog, outcome):
    private_values = (
        "private-email@example.com", "private-password", "private-gateway",
        "private-cookie", "private-token", "private-response-body",
    )
    client = RemoconClient(
        email=private_values[0], password=private_values[1], gateway_id=private_values[2]
    )
    session = Mock(spec=requests.Session)
    session.cookies = {"session": private_values[3]}
    session.headers = {"Authorization": private_values[4]}
    response = http_response(payload={
        "ok": outcome != "failed",
        "data": {"plantData": {"private": private_values[5]}, "zoneData": {}},
    })
    response.url = f"{BASE_URL}/R2/PlantHomeBsb/GetData/{private_values[2]}"
    if outcome == "timeout":
        session.request.side_effect = requests.ReadTimeout(" ".join(private_values))
    else:
        if outcome == "http_error":
            response.status_code = 500
        session.request.return_value = response
    client._session = session
    with caplog.at_level(logging.DEBUG, logger=RemoconClient.__module__), patch(
        f"{RemoconClient.__module__}.time"
    ) as clock:
        clock.monotonic.side_effect = [100.0, 124.8]
        if outcome in ("http_error", "timeout"):
            with pytest.raises(RemoconConnectionError):
                client._request_planthome_variant("bsb_browser")
        else:
            client._request_planthome_variant("bsb_browser")

    durations = request_duration_records(caplog)
    assert len(durations) == 1
    assert durations[0].levelno == logging.INFO
    diagnostic = durations[0].getMessage()
    assert all(value not in diagnostic for value in private_values)
    if outcome == "timeout":
        assert diagnostic == "REMOCON API bsb_browser: request timed out after 24.8s"
    else:
        result = "completed" if outcome == "completed" else "failed"
        assert diagnostic == f"REMOCON API bsb_browser: slow request {result} in 24.8s"


@pytest.mark.parametrize("message,debug_prefix", [
    ("Kommunikationsfehler", "M2M platform error..."),
    ("  KOMMUNIKATIONSFEHLER  ", "  m2m PLATFORM ERROR..."),
])
def test_cached_browser_m2m_failure_preserves_variant_and_recovers_without_fallback(
    caplog, message, debug_prefix
):
    private_values = (
        "private-email@example.com", "private-password", "private-gateway",
        "private-cookie", "private-token", "private-response-body",
    )
    client = RemoconClient(
        email=private_values[0], password=private_values[1], gateway_id=private_values[2]
    )
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("PlantHome unavailable"),
        RemoconConnectionError("BSB SI unavailable"),
        GOOD_RESPONSE,
    ]):
        assert client._get_raw() == GOOD_RESPONSE["data"]

    session = Mock(spec=requests.Session)
    session.cookies = {"session": private_values[3]}
    session.headers = {"Authorization": private_values[4]}
    session.request.side_effect = [
        http_response(payload={
            "ok": False,
            "message": message,
            "debugMessage": f"{debug_prefix} {' '.join(private_values)}",
            "data": {"plantData": {"private": private_values[5]}, "zoneData": {}},
        }),
        http_response(),
    ]
    client._session = session
    with caplog.at_level(logging.DEBUG, logger=RemoconClient.__module__), patch(
        f"{RemoconClient.__module__}.time"
    ) as clock:
        clock.monotonic.side_effect = [100.0, 124.8, 200.0, 202.4]
        with pytest.raises(RemoconConnectionError) as exc_info:
            client._get_raw()

        assert str(exc_info.value) == "REMOCON M2M communication error"
        assert exc_info.value.__cause__ is None
        assert session.request.call_count == 1
        assert client._planthome_variant == "bsb_browser"
        assert client.is_flat_api is False
        assert client._get_raw() == GOOD_RESPONSE["data"]

    assert session.request.call_count == 2
    assert session.request.call_args_list[0] == session.request.call_args_list[1]
    assert session.request.call_args.args == (
        "POST", f"{BASE_URL}/R2/PlantHomeBsb/GetData/{private_values[2]}",
    )
    assert client._planthome_variant == "bsb_browser"
    assert client.is_flat_api is False
    assert caplog.record_tuples == [
        (RemoconClient.__module__, logging.INFO,
         "REMOCON API bsb_browser: slow request failed in 24.8s"),
        (RemoconClient.__module__, logging.DEBUG,
         "REMOCON API bsb_browser: request completed in 2.4s"),
    ]
    assert all(value not in caplog.text for value in private_values)


@pytest.mark.parametrize("first_failure", ["initial_si", "browser"])
@pytest.mark.parametrize("message,debug_message", [
    ("Kommunikationsfehler", "M2M platform error..."),
    ("Unknown server failure", "Unexpected backend error"),
])
def test_api_rejection_during_initial_detection_keeps_existing_fallback(
    first_failure, message, debug_message
):
    client = make_client()
    rejection = {
        "ok": False,
        "message": message,
        "debugMessage": debug_message,
    }
    responses = [rejection, rejection]
    expected_paths = [
        "/R2/PlantHome/GetData/GW123?umsys=si",
        "/R2/PlantHomeBsb/GetData/GW123",
    ]
    if first_failure == "browser":
        responses = [
            RemoconConnectionError("PlantHome unavailable"),
            RemoconConnectionError("BSB SI unavailable"),
            rejection,
        ]
        expected_paths.insert(1, "/R2/PlantHomeBsb/GetData/GW123?umsys=si")
    responses += [flat_response(), flat_response()]
    expected_paths += ["/R2/PlantHome/GetData/GW123"] * 2

    with patch.object(client, "_request", side_effect=responses) as request:
        raw = client._get_raw()

    assert [entry.args[1] for entry in request.call_args_list] == expected_paths
    assert raw["ZoneComfortTemp"]["value"] == "21.5"
    assert client._planthome_variant is None
    assert client.is_flat_api is True


@pytest.mark.parametrize("variant,prefix", [
    ("planthome_si", "/R2/PlantHome"),
    ("bsb_si", "/R2/PlantHomeBsb"),
])
@pytest.mark.parametrize("message,debug_message", [
    ("Kommunikationsfehler", "M2M platform error..."),
    ("Unknown server failure", "Unexpected backend error"),
])
def test_existing_cached_variants_keep_api_rejection_fallback(
    variant, prefix, message, debug_message
):
    client = make_client()
    client._planthome_variant = variant
    rejection = {
        "ok": False,
        "message": message,
        "debugMessage": debug_message,
    }
    with patch.object(client, "_request", side_effect=[
        rejection, rejection, flat_response(), flat_response(),
    ]) as request:
        raw = client._get_raw()

    assert [entry.args[1] for entry in request.call_args_list] == [
        f"{prefix}/GetData/GW123?umsys=si",
        "/R2/PlantHomeBsb/GetData/GW123",
        "/R2/PlantHome/GetData/GW123",
        "/R2/PlantHome/GetData/GW123",
    ]
    assert raw["ZoneComfortTemp"]["value"] == "21.5"
    assert client._planthome_variant is None
    assert client.is_flat_api is True


@pytest.mark.parametrize("message,debug_message", [
    ("unsupported profile", "Unknown controller"),
    ("Session expired", "M2M platform error..."),
    ("Kommunikationsfehler", "Session expired"),
    (None, "M2M platform error..."),
    ("Kommunikationsfehler", {"error": "M2M platform error..."}),
])
def test_other_cached_browser_rejections_preserve_variant_and_recover_without_fallback(
    caplog, message, debug_message
):
    private_values = (
        "private-email@example.com", "private-password", "private-gateway",
        "private-cookie", "private-token", "private-response-body",
    )
    client = RemoconClient(
        email=private_values[0], password=private_values[1], gateway_id=private_values[2]
    )
    client._planthome_variant = "bsb_browser"
    response = {
        "ok": False,
        "message": message,
        "debugMessage": debug_message,
        "data": {"private": " ".join(private_values)},
    }
    with caplog.at_level(logging.DEBUG, logger=RemoconClient.__module__), patch.object(
        client, "_request", side_effect=[response, GOOD_RESPONSE]
    ) as request:
        with pytest.raises(RemoconDataError) as exc_info:
            client._get_raw()

        assert str(exc_info.value) == "REMOCON API rejected data request"
        assert exc_info.value.__cause__ is None
        assert request.call_count == 1
        assert client._planthome_variant == "bsb_browser"
        assert client.is_flat_api is False
        assert client._get_raw() == GOOD_RESPONSE["data"]

    assert request.call_count == 2
    assert request.call_args_list[0] == request.call_args_list[1]
    assert request.call_args.args == (
        "POST", f"/R2/PlantHomeBsb/GetData/{private_values[2]}",
    )
    assert client._planthome_variant == "bsb_browser"
    assert client.is_flat_api is False
    assert all(value not in str(exc_info.value) for value in private_values)
    assert all(value not in caplog.text for value in private_values)


def test_cached_browser_rejection_with_flat_items_does_not_switch_to_flat():
    client = make_client()
    client._planthome_variant = "bsb_browser"
    response = flat_response()
    response["ok"] = False
    with patch.object(client, "_request", side_effect=[response, GOOD_RESPONSE]) as request:
        with pytest.raises(RemoconDataError, match="^REMOCON API rejected data request$"):
            client._get_raw()

        assert request.call_count == 1
        assert client._planthome_variant == "bsb_browser"
        assert client.is_flat_api is False
        assert client._get_raw() == GOOD_RESPONSE["data"]

    assert request.call_count == 2
    assert request.call_args_list[0] == request.call_args_list[1]
    assert client._planthome_variant == "bsb_browser"
    assert client.is_flat_api is False


def test_new_entity_values_default_to_unknown():
    data = RemoconData()

    assert data.flame_sensor is None
    assert data.heat_or_cool_request is None
    assert data.dhw_comfort_temp is None
    assert data.dhw_reduced_temp is None


@pytest.mark.parametrize("flat_api", [False, True], ids=["legacy", "flat"])
def test_absent_optional_fields_remain_unknown(flat_api):
    client = make_client()
    client._flat_api = flat_api
    response = (
        {"ok": True, "data": {"items": [{"id": "ZoneComfortTemp", "value": 21}]}}
        if flat_api else GOOD_RESPONSE
    )
    with patch.object(client, "_request", return_value=response), patch.object(
        client, "_get_system_items", return_value={}
    ):
        data = client.get_data()

    assert data.flame_sensor is None
    assert data.heat_or_cool_request is None
    assert data.dhw_comfort_temp is None
    assert data.dhw_reduced_temp is None


@pytest.mark.parametrize("flat_api", [False, True], ids=["legacy", "flat"])
@pytest.mark.parametrize("raw_value, expected", [
    pytest.param(True, True, id="true"),
    pytest.param(False, False, id="false"),
    pytest.param(1, True, id="one"),
    pytest.param(0, False, id="zero"),
    pytest.param("true", True, id="true-string"),
    pytest.param("false", False, id="false-string"),
    pytest.param("1", True, id="one-string"),
    pytest.param("0", False, id="zero-string"),
    pytest.param(" TRUE ", True, id="normalized-true-string"),
    pytest.param(" FALSE ", False, id="normalized-false-string"),
    pytest.param("yes", True, id="yes"),
    pytest.param("no", False, id="no"),
    pytest.param("on", True, id="on"),
    pytest.param("off", False, id="off"),
    pytest.param(None, None, id="null"),
    pytest.param("", None, id="empty"),
    pytest.param(" \t ", None, id="whitespace"),
    pytest.param("unknown", None, id="unknown-string"),
    pytest.param(2, None, id="other-number"),
    pytest.param([], None, id="list"),
    pytest.param({}, None, id="object"),
    pytest.param(float("nan"), None, id="nan"),
    pytest.param(float("inf"), None, id="infinity"),
])
def test_optional_binary_fields_preserve_false_and_reject_unknown(flat_api, raw_value, expected):
    client = make_client()
    client._flat_api = flat_api
    response = (
        {"ok": True, "data": {"items": [
            {"id": "ZoneComfortTemp", "value": 21},
            {"id": "ZoneHeatRequest", "value": raw_value},
        ]}}
        if flat_api else {"ok": True, "data": {
            "plantData": {"flameSensor": raw_value},
            "zoneData": {"heatOrCoolRequest": raw_value},
        }}
    )
    with patch.object(client, "_request", return_value=response), patch.object(
        client, "_get_system_items", return_value={}
    ):
        data = client.get_data()

    assert data.heat_or_cool_request is expected
    assert data.flame_sensor is (None if flat_api else expected)


@pytest.mark.parametrize("flat_api", [False, True], ids=["legacy", "flat"])
@pytest.mark.parametrize("raw_value, expected", [
    pytest.param(50.5, 50.5, id="number"),
    pytest.param(" 50.5 ", 50.5, id="numeric-string"),
    pytest.param(0, 0.0, id="zero"),
    pytest.param("0", 0.0, id="zero-string"),
    pytest.param(None, None, id="null"),
    pytest.param("", None, id="empty"),
    pytest.param(" \t ", None, id="whitespace"),
    pytest.param("invalid", None, id="malformed"),
    pytest.param(False, None, id="false"),
    pytest.param(True, None, id="true"),
    pytest.param([], None, id="list"),
    pytest.param({}, None, id="object"),
    pytest.param(float("nan"), None, id="nan"),
    pytest.param(float("inf"), None, id="infinity"),
    pytest.param(float("-inf"), None, id="negative-infinity"),
    pytest.param("NaN", None, id="nan-string"),
    pytest.param("inf", None, id="infinity-string"),
    pytest.param("-inf", None, id="negative-infinity-string"),
])
def test_optional_dhw_targets_accept_only_finite_numbers(flat_api, raw_value, expected):
    client = make_client()
    client._flat_api = flat_api
    response = (
        {"ok": True, "data": {"items": [
            {"id": "ZoneComfortTemp", "value": 21},
            {"id": "DhwTimeProgComfortTemp", "value": raw_value},
            {"id": "DhwTimeProgEconomyTemp", "value": raw_value},
        ]}}
        if flat_api else {"ok": True, "data": {
            "plantData": {
                "dhwComfortTemp": {"value": raw_value},
                "dhwReducedTemp": {"value": raw_value},
            },
            "zoneData": {},
        }}
    )
    with patch.object(client, "_request", return_value=response), patch.object(
        client, "_get_system_items", return_value={}
    ):
        data = client.get_data()

    assert data.dhw_comfort_temp == expected
    assert data.dhw_reduced_temp == expected


@pytest.mark.parametrize("target", [None, {}, [], 50, "50"])
def test_malformed_legacy_dhw_target_objects_remain_unknown(target):
    client = make_client()
    response = {"ok": True, "data": {
        "plantData": {"dhwComfortTemp": target, "dhwReducedTemp": target},
        "zoneData": {},
    }}
    with patch.object(client, "_request", return_value=response), patch.object(
        client, "_get_system_items", return_value={}
    ):
        data = client.get_data()

    assert data.dhw_comfort_temp is None
    assert data.dhw_reduced_temp is None


@pytest.mark.parametrize("flat_api", [False, True], ids=["legacy", "flat"])
def test_dhw_targets_are_mapped_independently_while_dhw_is_off(flat_api):
    client = make_client()
    client._flat_api = flat_api
    response = (
        {"ok": True, "data": {"items": [
            {"id": "DhwMode", "value": "0"},
            {"id": "DhwTimeProgComfortTemp", "value": "50.5"},
            {"id": "DhwTimeProgEconomyTemp", "value": "40.5"},
        ]}}
        if flat_api else {"ok": True, "data": {
            "plantData": {
                "dhwEnabled": False,
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

    assert data.dhw_enabled is False
    assert data.dhw_comfort_temp == 50.5
    assert data.dhw_reduced_temp == 40.5


@pytest.mark.parametrize("request_value", ["1", "true", "yes", "on"])
@pytest.mark.parametrize("plant_mode, heating, cooling", [(1, True, False), (3, False, True)])
def test_flat_active_states_retain_existing_request_behavior(request_value, plant_mode, heating, cooling):
    client = make_client()
    client._flat_api = True
    response = {"ok": True, "data": {"items": [
        {"id": "ZoneHeatRequest", "value": request_value},
        {"id": "PlantMode", "value": plant_mode},
    ]}}
    with patch.object(client, "_request", return_value=response):
        data = client.get_data()

    assert data.heating_active is heating
    assert data.cooling_active is cooling


@pytest.mark.parametrize("changes, expected", [
    pytest.param({"comfort": 55}, {"comf": 55, "econ": 40.5}, id="comfort-only"),
    pytest.param({"reduced": 38}, {"comf": 50.5, "econ": 38}, id="reduced-only"),
    pytest.param({"comfort": 55, "reduced": 38}, {"comf": 55, "econ": 38}, id="both"),
])
def test_legacy_dhw_writes_preserve_untouched_target(changes, expected):
    client = make_client()
    response = {"ok": True, "data": {
        "plantData": {
            "dhwComfortTemp": {"value": "50.5"},
            "dhwReducedTemp": {"value": "40.5"},
        },
        "zoneData": {},
    }}
    with patch.object(client, "_request", side_effect=[response, {"ok": True}]) as request:
        client.set_dhw_temperature(**changes)

    assert request.call_count == 2
    assert request.call_args.args == ("POST", "/api/v2/remote/bsbPlantData/GW123/dhwTemp")
    assert request.call_args.kwargs["json"] == {
        "new": expected,
        "old": {"comf": 50.5, "econ": 40.5},
    }


def test_flat_dhw_target_writes_remain_blocked():
    client = make_client()
    with patch.object(client, "_request", return_value=flat_response()) as request:
        with pytest.raises(RemoconDataError, match="Control is disabled"):
            client.set_dhw_temperature(comfort=55, reduced=38)

    assert request.call_count == 3
    assert all("/GetData/" in call.args[1] for call in request.call_args_list)
